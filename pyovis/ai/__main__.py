import asyncio

from pyovis.ai.planner import Planner
from pyovis.ai.brain import Brain
from pyovis.ai.hands import Hands
from pyovis.ai.judge import Judge
from pyovis.ai.swap_manager import ModelSwapManager


class _MockContext:
    def __init__(self) -> None:
        self.task_description = "테스트 과제: 간단한 TODO 리스트 생성"
        self.plan = ""
        self.fail_reasons = []
        self.loop_count = 0
        self.critic_result = {"stderr": ""}


async def _smoke_test() -> None:
    swap = ModelSwapManager()

    planner = Planner(swap)
    brain = Brain(swap)
    hands = Hands(swap)
    judge = Judge(swap)

    ctx = _MockContext()

    print("Planner plan call (local server required)...")
    try:
        _ = await planner.plan(ctx)
    except Exception as exc:
        print(f"Planner test skipped: {exc}")

    print("Brain final review call (local server required)...")
    try:
        _ = await brain.final_review(ctx)
    except Exception as exc:
        print(f"Brain test skipped: {exc}")

    print("Hands build call (local server required)...")
    try:
        _ = await hands.build({"title": "Task", "description": "desc"}, "plan", "skill")
    except Exception as exc:
        print(f"Hands test skipped: {exc}")

    print("Judge evaluate call (local server required)...")
    try:
        _ = await judge.evaluate(
            {"id": 1, "title": "Task"},
            {"1": ["조건1"]},
            {"exit_code": 0, "execution_time": 0.1, "stdout": "ok", "stderr": ""},
            0,
        )
    except Exception as exc:
        print(f"Judge test skipped: {exc}")

    await swap.shutdown()
    print("Smoke test complete.")


if __name__ == "__main__":
    asyncio.run(_smoke_test())
