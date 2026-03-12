#!/usr/bin/env bash
# =============================================================================
# PYVIS v4.0 — AI Model Server (Dual GPU, Single Model at a time)
#
# Architecture: Two GPUs (RTX 4070S 12GB + RTX 3060 12GB = 24GB) combined,
#               one model loaded at a time.
#               Brain↔Hands/Judge switching via server restart.
#
# Usage:
#   ./start_model.sh stop      # Stop server
#   ./start_model.sh status    # Check current status
# =============================================================================

set -uo pipefail

LLAMA_SERVER="/Pyvis/llama.cpp/build/bin/llama-server"
PORT=8001
THREADS=4
CPU_AFFINITY="4,5,6,7"
LOG_DIR="/pyovis_memory/logs"
PID_FILE="/tmp/pyovis_server.pid"
ROLE_FILE="/tmp/pyovis_server.role"

# Model paths
PLANNER_MODEL="/pyovis_memory/models/GLM-4.7-Flash-Q4_K_M.gguf"
BRAIN_MODEL="/pyovis_memory/models/Qwen3-14B-Q5_K_M.gguf"
HANDS_MODEL="/pyovis_memory/models/mistralai_Devstral-Small-2-24B-Instruct-2512-Q4_K_M.gguf"
JUDGE_MODEL="/pyovis_memory/models/DeepSeek-R1-Distill-Qwen-14B-Q4_K_M.gguf"

# Dual GPU settings
# split-mode layer: distribute layers across GPUs
# tensor-split: 55% to RTX 4070S (Device 0), 45% to RTX 3060 (Device 1)
# 4070S is faster, so it gets slightly more
SPLIT_MODE="layer"
TENSOR_SPLIT="0.55,0.45"
NGL=60
HANDS_NGL=40
WARMUP_TIMEOUT=120
PLANNER_CTX=65536
BRAIN_CTX=40960
HANDS_CTX=80000
JUDGE_CTX=65536

mkdir -p "$LOG_DIR"

ensure_neo4j() {
    local name="pyovis-neo4j"
    if docker ps --format '{{.Names}}' | grep -q "^${name}$"; then
        return 0
    fi
    if docker ps -a --format '{{.Names}}' | grep -q "^${name}$"; then
        echo "[Neo4j] Starting existing container..."
        docker start "$name" >/dev/null
    else
        echo "[Neo4j] Creating new container..."
        docker run -d --name "$name" \
            -e NEO4J_AUTH=neo4j/testpassword \
            -p 7687:7687 -p 7474:7474 \
            --restart unless-stopped \
            neo4j:5-community >/dev/null
    fi
    echo "[Neo4j] Waiting for Neo4j to be ready..."
    for i in $(seq 1 30); do
        if docker exec "$name" cypher-shell -u neo4j -p testpassword 'RETURN 1' >/dev/null 2>&1; then
            echo "[Neo4j] Ready (took ${i}s)"
            return 0
        fi
        sleep 1
    done
    echo "[Neo4j] WARNING: Neo4j did not respond within 30 seconds"
}

stop_server() {
    if [ -f "$PID_FILE" ]; then
        local pid
        pid=$(cat "$PID_FILE")
        if kill -0 "$pid" 2>/dev/null; then
            local role="unknown"
            [ -f "$ROLE_FILE" ] && role=$(cat "$ROLE_FILE")
            echo "[Server] Stopping $role model (PID: $pid)..."
            kill "$pid"
            for i in $(seq 1 15); do
                if ! kill -0 "$pid" 2>/dev/null; then
                    echo "[Server] Stopped."
                    rm -f "$PID_FILE" "$ROLE_FILE"
                    return 0
                fi
                sleep 1
            done
            echo "[Server] Force killing..."
            kill -9 "$pid" 2>/dev/null
            rm -f "$PID_FILE" "$ROLE_FILE"
        else
            rm -f "$PID_FILE" "$ROLE_FILE"
        fi
    fi
}

start_server() {
    local role=$1
    local model=$2
    local ctx_size=$3
    local use_jinja=$4
    local ngl=${5:-$NGL}
    local cache_type=${6:-q8_0}

    # Check model file exists
    if [ ! -f "$model" ]; then
        echo "[Server] ERROR: Model not found: $model"
        exit 1
    fi

    if lsof -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
        echo "[Server] Port $PORT is in use. Stopping existing server..."
        stop_server
        for pid in $(lsof -t -iTCP:"$PORT" -sTCP:LISTEN 2>/dev/null); do
            kill "$pid" 2>/dev/null || true
        done
        for i in $(seq 1 10); do
            if ! lsof -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
                break
            fi
            sleep 1
        done
        rm -f "$PID_FILE" "$ROLE_FILE"
    fi

    # Skip if already running with same role
    if [ -f "$ROLE_FILE" ] && [ -f "$PID_FILE" ]; then
        local current_role
        current_role=$(cat "$ROLE_FILE")
        local current_pid
        current_pid=$(cat "$PID_FILE")
        if [ "$current_role" = "$role" ] && kill -0 "$current_pid" 2>/dev/null; then
            echo "[Server] $role model already running (PID: $current_pid)"
            return 0
        fi
    fi

    # Stop existing server
    stop_server

    echo "[Server] Loading $role model on dual GPU (24GB VRAM)"
    echo "[Server] Model: $(basename "$model")"
    echo "[Server] Port: $PORT | Context: $ctx_size | Split: $TENSOR_SPLIT"

    taskset -c "$CPU_AFFINITY" "$LLAMA_SERVER" \
        -m "$model" \
        --alias "$role" \
        -ngl "$ngl" \
        --ctx-size "$ctx_size" \
        --cache-type-k "$cache_type" \
        --cache-type-v "$cache_type" \
        --split-mode "$SPLIT_MODE" \
        --tensor-split "$TENSOR_SPLIT" \
        --parallel 1 \
        --threads "$THREADS" \
        --port "$PORT" \
        --host 0.0.0.0 \
        $use_jinja \
        --log-disable \
        --verbose \
        2>&1 | tee "$LOG_DIR/${role}.log" &

    local server_pid=$!
    echo "$server_pid" > "$PID_FILE"
    echo "$role" > "$ROLE_FILE"

    # Health check — wait up to WARMUP_TIMEOUT seconds (18GB model load time)
    echo "[Server] Waiting for $role to be ready..."
    for i in $(seq 1 "$WARMUP_TIMEOUT"); do
        if ! kill -0 "$server_pid" 2>/dev/null; then
            echo "[Server] ERROR: Server process died. Check $LOG_DIR/${role}.log"
            rm -f "$PID_FILE" "$ROLE_FILE"
            return 1
        fi
        if curl -s "http://localhost:$PORT/health" 2>/dev/null | grep -q '"status":\s*"ok"'; then
            echo "[Server] $role is READY on port $PORT (took ${i}s)"
            return 0
        fi
        sleep 1
    done

        echo "[Server] WARNING: $role did not respond within ${WARMUP_TIMEOUT} seconds"
    echo "[Server] Check logs: $LOG_DIR/${role}.log"
    return 1
}

show_status() {
    if [ -f "$PID_FILE" ] && [ -f "$ROLE_FILE" ]; then
        local pid
        pid=$(cat "$PID_FILE")
        local role
        role=$(cat "$ROLE_FILE")
        if kill -0 "$pid" 2>/dev/null; then
            echo "[Server] Running: $role (PID: $pid, port: $PORT)"
            # GPU usage
            nvidia-smi --query-gpu=index,name,memory.used,memory.free --format=csv,noheader 2>/dev/null
            return 0
        fi
    fi
    echo "[Server] Not running."
    return 1
}

# --- Main ---

case "${1:-help}" in
    planner)
        ensure_neo4j
        start_server "planner" "$PLANNER_MODEL" "$PLANNER_CTX" ""
        ;;
    brain)
        ensure_neo4j
        start_server "brain" "$BRAIN_MODEL" "$BRAIN_CTX" "" "$NGL" "q4_0"
        ;;
    hands)
        ensure_neo4j
        start_server "hands" "$HANDS_MODEL" "$HANDS_CTX" "--jinja" "$HANDS_NGL"
        ;;
    judge)
        ensure_neo4j
        start_server "judge" "$JUDGE_MODEL" "$JUDGE_CTX" ""
        ;;
    stop)
        stop_server
        ;;
    status)
        show_status
        ;;
    swap)
        # Cycle to next role
        if [ -f "$ROLE_FILE" ]; then
            current=$(cat "$ROLE_FILE")
            if [ "$current" = "brain" ]; then
                start_server "hands" "$HANDS_MODEL" "$HANDS_CTX" "--jinja" "$HANDS_NGL"
            elif [ "$current" = "hands" ]; then
                start_server "judge" "$JUDGE_MODEL" "$JUDGE_CTX" ""
            elif [ "$current" = "judge" ]; then
                start_server "planner" "$PLANNER_MODEL" "$PLANNER_CTX" ""
            else
                start_server "brain" "$BRAIN_MODEL" "$BRAIN_CTX" "" "$NGL" "q4_0"
            fi
        else
            echo "[Server] No server running. Specify 'brain' or 'hands'."
            exit 1
        fi
        ;;
    *)
        echo "Usage: $0 {planner|brain|hands|judge|stop|status|swap}"
        echo ""
        echo "  planner - Load GLM-4.7-Flash (plan-only)"
        echo "  brain   - Load Qwen3-14B (review, escalation)"
        echo "  hands   - Load Devstral-24B (code gen)"
        echo "  judge   - Load R1-Distill-14B (evaluation)"
        echo "  stop    - Stop the running server"
        echo "  status  - Show current server status"
        echo "  swap    - Swap to the other model"
        exit 1
        ;;
esac
