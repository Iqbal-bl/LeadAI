"""Lead Ads retrieval."""

from fastapi import APIRouter, Depends

from api.auth import require_api_key
from api.request_logging import Timer, log_call
from graph_api import leads as graph_leads

router = APIRouter(prefix="/leads", tags=["leads"])


@router.get("/forms")
async def list_forms(page_id: str, client_id: str = Depends(require_api_key)):
    with Timer() as t:
        result = await graph_leads.list_lead_forms(page_id)
    log_call(client_id, "facebook", "list_lead_forms", "/leads/forms", {"page_id": page_id},
              True, t.duration_ms, str(result))
    return result


@router.get("/{form_id}")
async def get_leads(form_id: str, client_id: str = Depends(require_api_key)):
    with Timer() as t:
        result = await graph_leads.get_leads(form_id)
    log_call(client_id, "facebook", "get_leads", f"/leads/{form_id}", {"form_id": form_id},
              True, t.duration_ms, str(result))
    return result
