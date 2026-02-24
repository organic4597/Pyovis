#!/usr/bin/env bash
# =============================================================================
# PYVIS v4.0 — Model Swap Performance Profiler
#
# Measures model swap latency between roles. Runs N swap cycles and reports
# min/max/avg load times per role.
#
# Usage: ./scripts/profile_swap.sh [cycles] (default: 3)
# =============================================================================

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
START_MODEL="$SCRIPT_DIR/start_model.sh"
CYCLES=${1:-3}
PORT=8001
RESULTS_FILE="/tmp/pyovis_swap_profile.csv"

echo "role,cycle,load_time_sec,vram_gpu0_mib,vram_gpu1_mib" > "$RESULTS_FILE"

get_vram() {
    nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits 2>/dev/null | tr '\n' ',' | sed 's/,$//'
}

swap_and_measure() {
    local role=$1
    local cycle=$2

    bash "$START_MODEL" stop 2>/dev/null
    sleep 2

    local start_time
    start_time=$(date +%s%N)

    bash "$START_MODEL" "$role" >/dev/null 2>&1
    local rc=$?

    local end_time
    end_time=$(date +%s%N)
    local elapsed_sec
    elapsed_sec=$(echo "scale=2; ($end_time - $start_time) / 1000000000" | bc)

    if [ $rc -ne 0 ]; then
        echo "  FAIL: ${role} cycle ${cycle} (${elapsed_sec}s)"
        echo "${role},${cycle},FAIL,0,0" >> "$RESULTS_FILE"
        return 1
    fi

    local vram
    vram=$(get_vram)
    echo "  ${role} cycle ${cycle}: ${elapsed_sec}s (VRAM: ${vram} MiB)"
    echo "${role},${cycle},${elapsed_sec},${vram}" >> "$RESULTS_FILE"
    return 0
}

echo "========================================"
echo " PYVIS Model Swap Profiler"
echo " Cycles per role: ${CYCLES}"
echo " $(date)"
echo "========================================"

ROLES="planner brain hands judge"

for role in $ROLES; do
    echo ""
    echo "--- Profiling: ${role} ---"
    for cycle in $(seq 1 "$CYCLES"); do
        swap_and_measure "$role" "$cycle"
    done
done

bash "$START_MODEL" stop 2>/dev/null

echo ""
echo "========================================"
echo " Summary"
echo "========================================"

for role in $ROLES; do
    times=$(grep "^${role}," "$RESULTS_FILE" | grep -v FAIL | cut -d',' -f3)
    if [ -n "$times" ]; then
        count=$(echo "$times" | wc -l)
        total=$(echo "$times" | paste -sd+ | bc)
        avg=$(echo "scale=2; $total / $count" | bc)
        min_t=$(echo "$times" | sort -n | head -1)
        max_t=$(echo "$times" | sort -n | tail -1)
        echo "  ${role}: avg=${avg}s min=${min_t}s max=${max_t}s (${count} runs)"
    else
        echo "  ${role}: ALL FAILED"
    fi
done

echo ""
echo "Raw data: ${RESULTS_FILE}"
echo "========================================"
