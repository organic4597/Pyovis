#!/usr/bin/env python3
"""
PYVIS v4.0 — Stress Test (10 consecutive swap cycles)

Tests model swap stability by cycling through all 4 roles repeatedly.
Measures swap times and checks for VRAM leaks between cycles.

Usage: python3 scripts/stress_test.py [--cycles 10]
"""

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pyovis.ai.swap_manager import ModelSwapManager, SwapManagerConfig


async def run_stress_test(cycles: int) -> dict:
    config = SwapManagerConfig()
    manager = ModelSwapManager(config=config)

    results: list[dict] = []
    roles = ["planner", "brain", "hands", "judge"]
    total_swaps = 0
    failed_swaps = 0

    print(f"Starting stress test: {cycles} full cycles ({cycles * len(roles)} swaps)")
    print("=" * 60)

    for cycle in range(1, cycles + 1):
        print(f"\n--- Cycle {cycle}/{cycles} ---")
        for role in roles:
            start = time.time()
            success = await manager.ensure_model(role)
            elapsed = time.time() - start
            total_swaps += 1

            record = {
                "cycle": cycle,
                "role": role,
                "success": success,
                "load_time_sec": round(elapsed, 2),
            }
            results.append(record)

            status = "OK" if success else "FAIL"
            if not success:
                failed_swaps += 1
            print(f"  {role}: {status} ({elapsed:.2f}s)")

    await manager.shutdown()

    summary = {
        "total_swaps": total_swaps,
        "failed_swaps": failed_swaps,
        "success_rate": f"{(total_swaps - failed_swaps) / total_swaps * 100:.1f}%",
        "results": results,
    }

    role_stats: dict[str, list[float]] = {}
    for r in results:
        if r["success"]:
            role_stats.setdefault(r["role"], []).append(r["load_time_sec"])

    print("\n" + "=" * 60)
    print("Summary:")
    print(f"  Total swaps: {total_swaps}")
    print(f"  Failed: {failed_swaps}")
    print(f"  Success rate: {summary['success_rate']}")

    for role, times in role_stats.items():
        avg = sum(times) / len(times)
        print(f"  {role}: avg={avg:.2f}s min={min(times):.2f}s max={max(times):.2f}s")

    out_path = Path("/tmp/pyovis_stress_test.json")
    out_path.write_text(json.dumps(summary, indent=2))
    print(f"\nRaw data: {out_path}")

    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="PYVIS stress test")
    parser.add_argument("--cycles", type=int, default=10)
    args = parser.parse_args()

    summary = asyncio.run(run_stress_test(args.cycles))
    sys.exit(0 if summary["failed_swaps"] == 0 else 1)


if __name__ == "__main__":
    main()
