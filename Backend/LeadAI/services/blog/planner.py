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

CRITICAL WORD COUNT REQUIREMENTS:
- The sum of target_words across all tasks MUST closely match (or slightly undershoot) the requested Total Target Word Count.
- Scale the number of sections (tasks) strictly according to the word count:
  * For 400-750 words: Exactly 3 sections (e.g. 150-200 words each). Provide only 2 to 3 concise bullets per task.
  * For 750-1200 words: Exactly 4 sections (e.g. 200-280 words each). Provide 3 bullets per task.
  * For 1200-1800 words: 4 to 5 sections (e.g. 250-350 words each).
  * For 1800-3000 words: 6 to 7 sections (e.g. 300-450 words each).
- Adapt style and tone strictly to the requested tone (e.g. professional, thought leadership, conversational, persuasive).
- Naturally integrate the focus keywords across section goals and bullet outlines.
- Each task must include:
  1) id (sequential integer starting at 1)
  2) title (catchy, informative section heading)
  3) goal (1 clear sentence stating the section's purpose)
  4) bullets (2 to 4 concrete, actionable bullets)
  5) target_words (strict section word budget)
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
    target_length = f"~{target_words} words"
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
        f"Total Target Word Count: STRICTLY {target_words} words ({target_length})\n"
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

    # Enforce task limits and word budget scaling
    if plan.tasks:
        if target_words <= 750 and len(plan.tasks) > 3:
            # Consolidate into 3 focused sections: Intro, Core Body, Conclusion
            plan.tasks = [plan.tasks[0], plan.tasks[1], plan.tasks[-1]]
            for idx, t in enumerate(plan.tasks, start=1):
                t.id = idx

        total_budget = sum(t.target_words for t in plan.tasks)
        if total_budget > target_words:
            scale = target_words / float(total_budget)
            for t in plan.tasks:
                t.target_words = max(80, int(t.target_words * scale))

    return {"plan": plan}
