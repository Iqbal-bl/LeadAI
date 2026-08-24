"""
Social publishing for LeadAI.

Turns the standalone single-tenant browser/Graph agent into a multi-tenant
feature: every operation runs against the Facebook Page or Instagram account
that the calling company registered under /channels, resolved per request.

  credentials.py  LeadChannelAccount -> agent credential context
  service.py      publish orchestration + per-platform fan-out
  agent_bridge.py lazy, tenant-aware entry to the LangGraph content agent
  schemas.py      request/response models
"""
