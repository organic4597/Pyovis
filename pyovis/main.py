import asyncio
import signal

import importlib

import pyovis_core
from pyovis.ai import ModelSwapManager
from pyovis.memory.kg_server import start_kg_server
from pyovis.orchestration.session_manager import SessionManager
from pyovis.tracking.loop_tracker import LoopTracker


uvloop = importlib.import_module("uvloop")
asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())


async def main() -> None:
    task_queue = pyovis_core.PyPriorityQueue()
    model_swap = ModelSwapManager()

    kg_task = asyncio.create_task(start_kg_server())

    tracker = LoopTracker()
    session = SessionManager(task_queue, model_swap, tracker)

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda: asyncio.create_task(_shutdown(model_swap, kg_task)))

    try:
        await session.run()
    finally:
        await model_swap.shutdown()
        kg_task.cancel()


async def _shutdown(model_swap: ModelSwapManager, kg_task: asyncio.Task) -> None:
    await model_swap.shutdown()
    kg_task.cancel()
    asyncio.get_running_loop().stop()


if __name__ == "__main__":
    uvloop.run(main())
