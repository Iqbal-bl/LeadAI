"""
Interactive entrypoint: type a task, watch the agent do it.

    python main.py
"""

import asyncio

from agent.orchestrator import run_prompt


async def main():
    task = input("What should the agent do?\n> ")
    result = await run_prompt(task, headless=False, verbose=True)
    print("\n" + "=" * 50)
    print(result)


if __name__ == "__main__":
    asyncio.run(main())
