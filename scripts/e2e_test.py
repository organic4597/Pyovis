#!/usr/bin/env python3
"""
PYVIS v4.0 E2E Test — Full Loop with Real Models

Tests the complete flow: Planner → Hands → Critic → Judge → Brain
"""

import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from pyovis.ai import ModelSwapManager, Brain, Hands, Judge, Planner
from pyovis.execution.critic_runner import CriticRunner
from pyovis.orchestration.loop_controller import LoopContext, ResearchLoopController
from pyovis.skill.skill_manager import SkillManager
from pyovis.tracking.loop_tracker import LoopTracker


async def test_simple_task():
    """Test a simple Python function generation task."""
    print("=" * 60)
    print("PYVIS v4.0 E2E Test — Simple Task")
    print("=" * 60)
    
    # Initialize components
    print("\n[1/5] Initializing components...")
    model_swap = ModelSwapManager()
    tracker = LoopTracker()
    skill_manager = SkillManager()
    critic = CriticRunner()
    
    brain = Brain(model_swap)
    hands = Hands(model_swap)
    judge = Judge(model_swap)
    planner = Planner(model_swap)
    
    controller = ResearchLoopController(
        brain, hands, judge, critic, tracker, skill_manager, planner=planner
    )
    
    # Simple test task
    task_description = """
    Write a Python function called `add_numbers(a, b)` that returns the sum of two numbers.
    The function should:
    1. Accept two numeric parameters
    2. Return their sum
    3. Handle edge cases (None, strings) by returning 0
    """
    
    print(f"\n[2/5] Task: {task_description.strip()[:50]}...")
    
    # Create context
    ctx = LoopContext(
        task_id="e2e-test-001",
        task_description=task_description,
        max_loops=3,
    )
    
    print("\n[3/5] Running full loop...")
    start_time = time.time()
    
    try:
        result = await controller.run(ctx)
        elapsed = time.time() - start_time
        
        print(f"\n[DEBUG] todo_list: {ctx.todo_list}")
        print(f"[DEBUG] todo_list type: {type(ctx.todo_list)}")
        if ctx.todo_list:
            print(f"[DEBUG] first item type: {type(ctx.todo_list[0])}")
            print(f"[DEBUG] first item: {ctx.todo_list[0]}")
        
        print(f"\n[4/5] Loop completed in {elapsed:.1f}s")
        print(f"      Status: {result.get('status', 'unknown')}")
        print(f"      Loops: {ctx.loop_count}")
        
        if result.get("status") == "escalated":
            print(f"      Reason: {result.get('message', 'unknown')}")
            print(f"      Fail reasons: {result.get('fail_reasons', [])}")
            return False
        else:
            print(f"      Score: {ctx.score}")
            if ctx.current_code:
                print(f"\n[5/5] Generated code:")
                print("-" * 40)
                print(ctx.current_code[:500])
                print("-" * 40)
            return True
            
    except Exception as e:
        print(f"\n[ERROR] {type(e).__name__}: {e}")
        print(f"[DEBUG] ctx.todo_list: {ctx.todo_list}")
        print(f"[DEBUG] ctx.current_task_index: {ctx.current_task_index}")
        if ctx.todo_list and len(ctx.todo_list) > ctx.current_task_index:
            print(f"[DEBUG] current_task type: {type(ctx.todo_list[ctx.current_task_index])}")
            print(f"[DEBUG] current_task: {ctx.todo_list[ctx.current_task_index]}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        await model_swap.shutdown()


async def test_code_execution():
    """Test code execution in Docker sandbox."""
    print("\n" + "=" * 60)
    print("PYVIS v4.0 E2E Test — Code Execution")
    print("=" * 60)
    
    critic = CriticRunner()
    
    test_code = '''
def add_numbers(a, b):
    if a is None or b is None:
        return 0
    if isinstance(a, str) or isinstance(b, str):
        return 0
    return a + b

# Test
print(add_numbers(2, 3))
print(add_numbers(10, -5))
print(add_numbers(None, 5))
'''
    
    print("\n[1/2] Executing test code in sandbox...")
    result = await critic.execute(test_code)
    
    print(f"\n[2/2] Results:")
    print(f"      Exit code: {result.exit_code}")
    print(f"      Execution time: {result.execution_time:.2f}s")
    print(f"      Error type: {result.error_type}")
    print(f"      Stdout: {result.stdout[:200] if result.stdout else '(empty)'}")
    if result.stderr:
        print(f"      Stderr: {result.stderr[:200]}")
    
    return result.exit_code == 0


async def main():
    print("\n" + "=" * 60)
    print("PYVIS v4.0 — E2E Test Suite")
    print("=" * 60)
    
    results = []
    
    # Test 1: Code execution (fast, no model needed)
    print("\n>>> Test 1: Docker Sandbox Execution")
    try:
        success = await test_code_execution()
        results.append(("Sandbox Execution", success))
    except Exception as e:
        print(f"SKIPPED: {e}")
        results.append(("Sandbox Execution", None))
    
    # Test 2: Full loop with models
    print("\n>>> Test 2: Full Loop (Planner → Hands → Critic → Judge)")
    try:
        success = await test_simple_task()
        results.append(("Full Loop", success))
    except Exception as e:
        print(f"ERROR: {e}")
        results.append(("Full Loop", False))
    
    # Summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    for name, success in results:
        status = "PASS" if success else ("SKIP" if success is None else "FAIL")
        print(f"  {name}: {status}")
    
    all_passed = all(r[1] for r in results if r[1] is not None)
    print(f"\nOverall: {'ALL PASSED' if all_passed else 'SOME FAILED'}")
    
    return 0 if all_passed else 1


if __name__ == "__main__":
    import uvloop
    uvloop.run(main())
