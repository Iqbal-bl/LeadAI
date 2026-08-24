"""
FastAPI entry point — exposes Facebook/Instagram post/reply/lead actions
as URLs a CRM (or anything else) can call. Every action still goes
through the LLM agent (LangGraph + CRAG for replies) — this is a thin
HTTP wrapper around agent/orchestrator.py, not a bypass of it.

    uvicorn api.main:app --reload --port 8080

Auth: every endpoint (except /health) requires an X-API-Key header —
see api/auth.py and .env's API_KEYS.
"""

from fastapi import FastAPI

from api.db import init_db
from api.routers import content, direct_post, facebook, instagram, leads, monitoring, multi_platform, webhook, multi_platform_direct, post_drafts
from fastapi.middleware.cors import CORSMiddleware


app = FastAPI(title="Agentic Browser API", version="1.0")


@app.on_event("startup")
def on_startup():
    init_db()


@app.get("/health")
async def health():
    return {"status": "ok"}

app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:4200"
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

app.include_router(facebook.router)
app.include_router(instagram.router)
app.include_router(content.router)
app.include_router(leads.router)
app.include_router(monitoring.router)
app.include_router(webhook.router)
app.include_router(direct_post.router)
app.include_router(multi_platform.router) 
app.include_router(multi_platform_direct.router)
app.include_router(post_drafts.router)
