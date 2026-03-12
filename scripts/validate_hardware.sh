#!/usr/bin/env bash
# =============================================================================
# PYVIS v4.0 — Hardware Validation Script
#
# Starts each role's llama-server, measures VRAM usage, runs a quick inference
# test, then moves to the next role. Reports pass/fail for each.
#
# Usage: ./scripts/validate_hardware.sh [planner|brain|hands|judge|all]
# =============================================================================

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
START_MODEL="$SCRIPT_DIR/start_model.sh"
PORT=8001
RESULTS_FILE="/tmp/pyovis_hw_validation.log"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

> "$RESULTS_FILE"

log() {
    echo -e "$1" | tee -a "$RESULTS_FILE"
}

check_gpu() {
    log "\n${YELLOW}=== GPU Status ===${NC}"
    if ! command -v nvidia-smi &>/dev/null; then
        log "${RED}FAIL: nvidia-smi not found${NC}"
        return 1
    fi
    nvidia-smi --query-gpu=index,name,memory.total,memory.free,compute_cap \
        --format=csv,noheader 2>/dev/null | tee -a "$RESULTS_FILE"
}

measure_vram() {
    local role=$1
    nvidia-smi --query-gpu=index,memory.used --format=csv,noheader,nounits 2>/dev/null | while read -r line; do
        echo "  GPU $line MiB used (role: $role)"
    done | tee -a "$RESULTS_FILE"
}

test_inference() {
    local role=$1
    local prompt="Write a Python hello world program"
    local max_tokens=64

    log "  Testing inference for ${role}..."
    local start_time
    start_time=$(date +%s%N)

    local response
    response=$(curl -s --max-time 60 "http://localhost:$PORT/v1/chat/completions" \
        -H "Content-Type: application/json" \
        -d "{
            \"model\": \"local\",
            \"messages\": [{\"role\": \"user\", \"content\": \"$prompt\"}],
            \"max_tokens\": $max_tokens,
            \"temperature\": 0.1
        }" 2>/dev/null)

    local end_time
    end_time=$(date +%s%N)
    local elapsed_ms=$(( (end_time - start_time) / 1000000 ))

    if echo "$response" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['choices'][0]['message']['content'][:100])" 2>/dev/null; then
        local tokens
        tokens=$(echo "$response" | python3 -c "import sys,json; d=json.load(sys.stdin); u=d.get('usage',{}); print(u.get('completion_tokens', '?'))" 2>/dev/null)
        log "  ${GREEN}PASS${NC}: inference OK (${elapsed_ms}ms, ~${tokens} tokens)"
        return 0
    else
        log "  ${RED}FAIL${NC}: inference failed"
        echo "  Response: ${response:0:200}" >> "$RESULTS_FILE"
        return 1
    fi
}

validate_role() {
    local role=$1
    log "\n${YELLOW}=== Validating: ${role} ===${NC}"

    log "  Starting ${role} model..."
    bash "$START_MODEL" "$role"
    local start_rc=$?

    if [ $start_rc -ne 0 ]; then
        log "  ${RED}FAIL${NC}: Server failed to start for ${role}"
        return 1
    fi

    sleep 2
    measure_vram "$role"
    test_inference "$role"
    local infer_rc=$?

    local health
    health=$(curl -s "http://localhost:$PORT/health" 2>/dev/null)
    local status
    status=$(echo "$health" | python3 -c "import sys,json; print(json.load(sys.stdin).get('status','unknown'))" 2>/dev/null || echo "unknown")
    log "  Health: ${status}"

    if [ $infer_rc -eq 0 ] && [ "$status" = "ok" ]; then
        log "  ${GREEN}=== ${role}: PASSED ===${NC}"
    else
        log "  ${RED}=== ${role}: FAILED ===${NC}"
    fi

    bash "$START_MODEL" stop
    sleep 3
    return $infer_rc
}

main() {
    local target=${1:-all}
    local pass_count=0
    local fail_count=0

    log "${YELLOW}========================================${NC}"
    log "${YELLOW} PYVIS v4.0 Hardware Validation${NC}"
    log "${YELLOW} $(date)${NC}"
    log "${YELLOW}========================================${NC}"

    check_gpu

    local roles
    if [ "$target" = "all" ]; then
        roles="planner brain hands judge"
    else
        roles="$target"
    fi

    for role in $roles; do
        if validate_role "$role"; then
            pass_count=$((pass_count + 1))
        else
            fail_count=$((fail_count + 1))
        fi
    done

    log "\n${YELLOW}========================================${NC}"
    log " Results: ${GREEN}${pass_count} PASSED${NC}, ${RED}${fail_count} FAILED${NC}"
    log " Log: ${RESULTS_FILE}"
    log "${YELLOW}========================================${NC}"

    [ $fail_count -eq 0 ]
}

main "$@"
