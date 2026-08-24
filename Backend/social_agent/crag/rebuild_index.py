"""
Rebuilds the CRAG vector index from whatever's currently in
knowledge/company_docs/ (.md, .txt, .pdf). Run this any time you add,
remove, or edit files there — CragEngine won't pick up changes on its own
otherwise, since it just loads whatever index already exists on disk.

    python -m crag.rebuild_index
"""

from dotenv import load_dotenv

from social_agent.crag.engine import CragEngine

load_dotenv()


def main():
    print("Rebuilding CRAG index from knowledge/company_docs/ ...")
    engine = CragEngine(knowledge_dir="knowledge/company_docs", force_rebuild=True)
    print("Done. Testing with a sample question...")
    import asyncio
    answer = asyncio.run(engine.answer("What does this company do?"))
    print("\nSample answer:\n" + answer)


if __name__ == "__main__":
    main()
