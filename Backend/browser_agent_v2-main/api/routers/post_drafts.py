"""Text-only social-post draft generation for the frontend editor.

This router deliberately generates a draft and returns it to the caller; it
does not enqueue, upload, or publish anything. The frontend owns the next
step, including asking for another version or sending an approved draft to a
separate publishing endpoint.
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, model_validator

from api.auth import require_api_key
from api.request_logging import Timer, log_call
from models import get_text_model

router = APIRouter(prefix="/content/drafts", tags=["content-drafts"])


class DraftRequest(BaseModel):
    """Single free-form field: topic, raw content, and/or instructions —
    all mixed together in one block of text, exactly as the frontend
    captures it from the user."""

    content: str = Field(..., min_length=1, max_length=12_000)
    version: int = Field(default=1, ge=1, le=100)

    @model_validator(mode="after")
    def has_source_material(self):
        if not self.content.strip():
            raise ValueError("content must not be empty.")
        return self


def _text_from_content(content) -> str:
    """Normalize text responses from the supported LangChain providers."""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") in {"text", "output_text"}:
                parts.append(block.get("text", ""))
        return "".join(parts).strip()
    return str(content).strip()


def _prompt(body: DraftRequest) -> str:
    alternate_note = ""
    if body.version > 1:
        alternate_note = (
            "This is an alternate version. Use a noticeably different hook, structure, "
            "and phrasing from a typical first draft while preserving the source material."
        )

    return f"""You are a professional social-media copywriter.
Create one polished, creative, publication-ready text post from the material below.

The material may contain a topic, raw content, specific instructions, or a mix of all
three, all together in a single block of text — read it carefully and work out what's
source material to draw from versus what's an instruction to follow (e.g. tone,
length, format, things to include or avoid).

Material:
{body.content.strip()}

Requirements:
- Preserve factual claims from the supplied material. Do not invent company facts, results, prices, features, or announcements.
- Follow any instructions embedded in the material when they do not conflict with the source content.
- Use a clear, engaging professional tone and a natural structure suitable for a social post.
- Include a concise call to action only when it fits the supplied material.
- Return only the draft text. Do not add a title, analysis, quotation marks, labels, or Markdown code fences.
- This request creates a draft only; never claim that it was posted or scheduled.
{alternate_note}
"""


@router.post("")
async def generate_post_draft(body: DraftRequest, client_id: str = Depends(require_api_key)):
    """Generate one editable text draft; no social platform is contacted."""
    with Timer() as t:
        try:
            # A moderately creative temperature makes repeated frontend requests useful
            # as genuine alternatives, without changing the shared model configuration.
            model = get_text_model(temperature=0.8, max_tokens=1_000)
            response = await model.ainvoke(_prompt(body))
            draft = _text_from_content(response.content)
            if not draft:
                raise RuntimeError("The language model returned an empty draft.")
        except Exception as exc:
            log_call(
                client_id, None, "generate_post_draft", "/content/drafts",
                {"content_length": len(body.content), "version": body.version},
                False, t.duration_ms, error_message=str(exc),
            )
            raise HTTPException(status_code=502, detail="Unable to generate a post draft.") from exc

    log_call(
        client_id, None, "generate_post_draft", "/content/drafts",
        {"content_length": len(body.content), "version": body.version},
        True, t.duration_ms, result_summary=f"draft_length={len(draft)}",
    )
    return {"draft": draft, "version": body.version}