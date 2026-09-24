"""
Orchestrator / Planner node for blog generation workflow.
Produces a detailed outline with sections, word targets, bullets, and strategic structure.
"""
import os
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from ...config import settings
from .schemas import Plan
from .state import State

ORCH_SYSTEM = """
You are a principal content strategist, executive editor, and lead technical writer.

Your task is to produce a structured, high-value, and engaging outline for a blog post tailored to the user's requested audience, tone, word count target, and keywords.

Requirements:
- Plan the number of sections (tasks) and target words per section so their sum closely matches the TOTAL requested word count target.
  * For ~500-800 words: 3 to 4 concise sections (150-250 words each).
  * For ~1000-1500 words: 4 to 6 structured sections (200-300 words each).
  * For ~1500-2500 words: 6 to 8 in-depth sections (250-400 words each).
- Adapt style and tone strictly to the requested tone (e.g. professional, thought leadership, conversational, persuasive).
- Naturally integrate the focus keywords across section goals and bullet outlines.
- Each task must include:
  1) id (sequential integer starting at 1)
  2) title (catchy, informative section heading)
  3) goal (1 clear sentence stating the section's purpose)
  4) 3 to 5 concrete, actionable bullets
  5) target word count for the section
- Ensure the outline includes:
  * Engaging hook & strategic context in Introduction
  * Actionable frameworks, workflows, step-by-step best practices
  * Practical examples or real-world takeaways
  * Compelling conclusion tying into the requested Call to Action (CTA)

Output must strictly match the Plan schema.
"""


def _get_llm():
    api_key = settings.openai_api_key or os.getenv("OPENAI_API_KEY")
    return ChatOpenAI(
        model=settings.openai_model or "gpt-4o-mini",
        api_key=api_key,
        temperature=0.3,
    )


def orchestrator_node(state: State) -> dict:
    """Create structured blog outline from topic, tone, audience, and evidence."""
    llm = _get_llm()
    planner = llm.with_structured_output(Plan)

    evidence = state.get("evidence", []) or []
    mode = state.get("mode", "closed_book")
    tone = state.get("tone") or "professional"
    audience = state.get("target_audience") or "Business leaders, practitioners, and modern professionals"
    keywords = state.get("keywords") or []
    target_words = state.get("target_words") or 1000
    target_length = state.get("target_length") or f"~{target_words} words"
    language = state.get("language") or "English"
    cta_text = state.get("cta_text")
    cta_url = state.get("cta_url")
    forced_kind = state.get("forced_kind")
    cta_part = f"Call to Action (CTA): {cta_text} ({cta_url})\n" if cta_text else ""
    forced_part = "Force blog_kind=news_roundup\n" if forced_kind else ""
    evidence_list = [e.model_dump() for e in evidence[:12]]

    prompt = (
        f"Topic: {state['topic']}\n"
        f"Target Audience: {audience}\n"
        f"Tone: {tone}\n"
        f"Language: {language}\n"
        f"Total Target Word Count: {target_words} words ({target_length})\n"
        f"Focus Keywords: {', '.join(keywords) if keywords else 'None specified'}\n"
        f"{cta_part}"
        f"Mode: {mode}\n"
        f"As-of Date: {state.get('as_of', '2026-09-01')}\n"
        f"{forced_part}\n"
        f"Research Evidence:\n"
        f"{evidence_list}\n"
    )


    plan: Plan = planner.invoke(
        [
            SystemMessage(content=ORCH_SYSTEM),
            HumanMessage(content=prompt),
        ]
    )

    plan.tone = tone
    plan.audience = audience
    if forced_kind:
        plan.blog_kind = "news_roundup"

    return {"plan": plan}
