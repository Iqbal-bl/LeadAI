# --------------------------------------------------
# agentic_browser_v2 / crag\grader.py
# --------------------------------------------------
"""
Relevance grading: for each retrieved chunk, ask the LLM a focused yes/no
question — "is this chunk actually relevant to the question?" — using
structured output so the result is a reliable boolean, not free text to
parse. This is the "Corrective" part of CRAG: it's what lets the pipeline
notice bad retrieval instead of blindly trusting whatever came back.
"""

from pydantic import BaseModel, Field

from models import get_text_model


class RelevanceGrade(BaseModel):
    relevant: bool = Field(description="True if the document is relevant to the question, False otherwise.")


def build_grader():
    model = get_text_model(temperature=0)
    return model.with_structured_output(RelevanceGrade)


async def grade_document(grader, question: str, document_text: str) -> bool:
    prompt = (
        f"Question: {question}\n\n"
        f"Retrieved document:\n{document_text}\n\n"
        "Does this document contain information relevant to answering the "
        "question? Be strict — only say relevant if it actually helps "
        "answer this specific question, not just if it's on a related topic."
    )
    result = await grader.ainvoke(prompt)
    return result.relevant
