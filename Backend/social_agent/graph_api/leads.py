"""
Reads leads captured through Facebook/Instagram Lead Ads forms. Requires
leads_retrieval granted to the token. This only READS submissions from a
Lead Ad you've already built in Ads Manager — it doesn't create the ad
itself (that's the separate Marketing API, ads_management permission,
not built here yet).
"""

from .client import graph_get


async def list_lead_forms(page_id: str) -> dict:
    """Lists the Lead Ad forms that exist on a Page, so you can find the
    form_id needed by get_leads()."""
    return await graph_get(f"{page_id}/leadgen_forms", params={"fields": "id,name,status"})


async def get_leads(form_id: str, limit: int = 50) -> dict:
    """Fetches submissions for one Lead Ad form. Each entry's "field_data"
    holds the actual name/email/phone/etc. the person submitted."""
    return await graph_get(
        f"{form_id}/leads",
        params={"limit": limit, "fields": "id,created_time,field_data"},
    )
