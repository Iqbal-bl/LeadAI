"""
Worker node for generating individual blog sections in parallel.
"""
import os
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from ...config import settings
from .schemas import EvidenceItem, Plan, Task
from .state import State

WORKER_SYSTEM = """
You are a senior technical writer and content specialist.
Write ONE section of a blog post in clean, well-formatted Markdown.

Constraints:
- Follow the provided Goal and cover ALL bullets in order.
- Do not skip or merge bullets.
- Stay close to the Target words (+-15%).
- Output ONLY the section content in Markdown.
- Start with a '## <Section Title>' heading.
- Do NOT include the overarching blog title H1.
- If citations are required and evidence URLs are provided, link sources naturally using Markdown links: ([Source](URL)).
- Use short, readable paragraphs, bullet points where helpful, and bold key concepts for scannability.
- If code snippets are relevant, use proper code fences with syntax highlighting.
- Avoid generic fluff. Make every sentence impactful and informative.
"""


def _get_llm():
    api_key = settings.openai_api_key or os.getenv("OPENAI_API_KEY")
    return ChatOpenAI(
        model=settings.openai_model or "gpt-4o-mini",
        api_key=api_key,
        temperature=0.4,
    )


def worker_node(payload: dict) -> dict:
    """Generate one blog section for one planned task."""
    task = Task(**payload["task"])
    plan = Plan(**payload["plan"])
    evidence = [EvidenceItem(**item) for item in payload.get("evidence", [])]
    topic = payload["topic"]
    mode = payload.get("mode", "closed_book")
    as_of = payload.get("as_of", "2026-09-01")
    recency_days = payload.get("recency_days", 3650)

    bullets_text = "\n- " + "\n- ".join(task.bullets)
    evidence_text = ""
    if evidence:
        evidence_text = "\n".join(
            f"- {item.title} | {item.url} | {item.published_at or 'date:unknown'}"
            for item in evidence[:15]
        )

    citation_section = f"Evidence to Cite:\n{evidence_text}" if evidence_text else ""

    llm = _get_llm()
    section_md = llm.invoke(
        [
            SystemMessage(content=WORKER_SYSTEM),
            HumanMessage(
                content=(
                    f"Blog Title: {plan.blog_title}\n"
                    f"Audience: {plan.audience}\n"
                    f"Tone: {plan.tone}\n"
                    f"Topic: {topic}\n"
                    f"Mode: {mode}\n"
                    f"As-of Date: {as_of}\n\n"
                    f"Section Title: {task.title}\n"
                    f"Goal: {task.goal}\n"
                    f"Target words: {task.target_words}\n"
                    f"Bullets to Cover:\n{bullets_text}\n\n"
                    f"{citation_section}"
                )
            ),
        ]
    ).content.strip()


    return {
        "sections": [(task.id, section_md)]
    }
