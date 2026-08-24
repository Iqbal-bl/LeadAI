"""
Per-company dynamic scripts, and the editable prompt templates around them.

Scripts use the app's existing XML dialect, so a script authored here can drive a
real voice call through the unmodified `/media-stream` pipeline. See
services/script_engine.py for the resolution order and prompt layering.
"""
from __future__ import annotations

import os

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session

from Domain.models import Client

from .. import activity
from ..activity import A
from ..db import get_leadai_db
from ..models import LeadCompanyPrompt, LeadCompanyScript, utcnow
from ..rbac import Principal, assert_owns, require, resolve_scope
from ..schemas import (
    Ok,
    PromptOut,
    PromptUpdate,
    ScriptCreate,
    ScriptDetail,
    ScriptImportRequest,
    ScriptOut,
    ScriptUpdate,
)
from ..serializers import prompt_out, script_detail, script_out
from ..services import script_engine

router = APIRouter(prefix="/scripts", tags=["LeadAI • Scripts & prompts"])


def _company_name(db: Session, client_id: str) -> str:
    client = db.get(Client, client_id)
    if not client or client.IsDeleted:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Company not found")
    return client.Name


def _load(db: Session, script_id: str, client_id: str) -> LeadCompanyScript:
    row = db.get(LeadCompanyScript, script_id)
    if not row or row.IsDeleted:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Script not found")
    assert_owns(row.ClientId, client_id)
    return row


# ===========================================================================
# scripts
# ===========================================================================


@router.get("", response_model=list[ScriptOut], summary="List a company's scripts")
def list_scripts(
    channel: str | None = Query(default=None, pattern="^(all|chat|voice)$"),
    include_inactive: bool = Query(default=False),
    principal: Principal = Depends(require("script.read")),
    db: Session = Depends(get_leadai_db),
):
    client_id = resolve_scope(principal)
    query = db.query(LeadCompanyScript).filter(
        LeadCompanyScript.ClientId == client_id,
        LeadCompanyScript.IsDeleted == False,  # noqa: E712
    )
    if channel:
        query = query.filter(LeadCompanyScript.Channel == channel)
    if not include_inactive:
        query = query.filter(LeadCompanyScript.IsActive == True)  # noqa: E712
    rows = query.order_by(
        LeadCompanyScript.IsDefault.desc(), LeadCompanyScript.CreatedAt.desc()
    ).all()
    return [script_out(r) for r in rows]


@router.post(
    "",
    response_model=ScriptDetail,
    status_code=status.HTTP_201_CREATED,
    summary="Create a script from XML",
)
def create_script(
    payload: ScriptCreate,
    request: Request,
    principal: Principal = Depends(require("script.manage")),
    db: Session = Depends(get_leadai_db),
):
    client_id = resolve_scope(principal)
    _company_name(db, client_id)

    # Parse eagerly so an invalid script is rejected at authoring time, not at
    # 3am when a call connects and the agent has no prompt.
    try:
        sections = script_engine.parse_script_xml(payload.script_xml)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    if not sections:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "The XML parsed but produced no sections — check the template structure.",
        )

    row = LeadCompanyScript(
        ClientId=client_id,
        Name=payload.name,
        Slug=payload.name.lower().replace(" ", "-")[:160],
        Description=payload.description,
        Channel=payload.channel,
        Language=payload.language,
        Version=script_engine.next_version(db, client_id, payload.name),
        ScriptXml=payload.script_xml,
        SectionsJson=sections,
        IsActive=True,
        VoiceGender=payload.voice_gender,
        VoiceSpeaker=payload.voice_speaker,
        MultiStt=payload.multi_stt,
        CreatedBy=principal.email,
    )
    db.add(row)
    db.flush()

    if payload.is_default:
        script_engine.set_default(db, client_id, row)

    activity.log_principal(
        db,
        principal,
        action=A.SCRIPT_CREATED,
        client_id=client_id,
        entity_type="script",
        entity_id=row.Id,
        message=f"Created script '{row.Name}' v{row.Version} ({row.Channel})",
        meta={"sections": len(sections), "is_default": payload.is_default},
        request=request,
    )
    db.commit()
    db.refresh(row)
    return script_detail(row)


@router.get("/active", response_model=ScriptDetail, summary="The script currently in force")
def active_script(
    channel: str = Query(default="chat", pattern="^(all|chat|voice)$"),
    principal: Principal = Depends(require("script.read")),
    db: Session = Depends(get_leadai_db),
):
    """Resolves through the same order the live conversation path uses, so the UI
    can show 'this is what your customers are actually talking to right now'."""
    client_id = resolve_scope(principal)
    row = script_engine.resolve_script(db, client_id, channel=channel)
    if not row:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "No script configured for this company — the assistant is running on "
            "prompt templates plus the knowledge base alone.",
        )
    return script_detail(row)


@router.get("/importable", summary="Scripts available on disk to import")
def importable_scripts(
    principal: Principal = Depends(require("script.manage")),
):
    """Lists the app's existing scripts/*.xml so a working script can be adopted
    by a company instead of re-authored."""
    from multiligual_call import SCRIPTS_DIR

    try:
        files = sorted(f for f in os.listdir(SCRIPTS_DIR) if f.endswith(".xml"))
    except OSError:
        files = []
    return {"files": files, "directory": "scripts/"}


@router.post(
    "/import",
    response_model=ScriptDetail,
    status_code=status.HTTP_201_CREATED,
    summary="Import a scripts/*.xml file as a company script",
)
def import_script(
    payload: ScriptImportRequest,
    request: Request,
    principal: Principal = Depends(require("script.manage")),
    db: Session = Depends(get_leadai_db),
):
    client_id = resolve_scope(principal)
    try:
        row = script_engine.import_from_disk(db, client_id, payload.filename, principal.email)
    except FileNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc

    activity.log_principal(
        db,
        principal,
        action=A.SCRIPT_CREATED,
        client_id=client_id,
        entity_type="script",
        entity_id=row.Id,
        message=f"Imported script '{payload.filename}' from disk",
        request=request,
    )
    db.commit()
    db.refresh(row)
    return script_detail(row)


@router.get("/{script_id}", response_model=ScriptDetail, summary="Get one script")
def get_script(
    script_id: str,
    principal: Principal = Depends(require("script.read")),
    db: Session = Depends(get_leadai_db),
):
    client_id = resolve_scope(principal)
    return script_detail(_load(db, script_id, client_id))


@router.patch("/{script_id}", response_model=ScriptDetail, summary="Update a script")
def update_script(
    script_id: str,
    payload: ScriptUpdate,
    request: Request,
    principal: Principal = Depends(require("script.manage")),
    db: Session = Depends(get_leadai_db),
):
    client_id = resolve_scope(principal)
    row = _load(db, script_id, client_id)
    changed: list[str] = []

    if payload.script_xml is not None:
        try:
            sections = script_engine.parse_script_xml(payload.script_xml)
        except ValueError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
        row.ScriptXml = payload.script_xml
        row.SectionsJson = sections
        # Editing the content bumps the version: a company needs to be able to
        # say which wording its agent used on a given date.
        row.Version = row.Version + 1
        changed += ["script_xml", "version"]

    for field, column in (
        ("name", "Name"),
        ("description", "Description"),
        ("channel", "Channel"),
        ("language", "Language"),
        ("is_active", "IsActive"),
        ("voice_gender", "VoiceGender"),
        ("voice_speaker", "VoiceSpeaker"),
        ("multi_stt", "MultiStt"),
    ):
        value = getattr(payload, field)
        if value is not None:
            setattr(row, column, value)
            changed.append(field)

    if payload.is_default is True:
        script_engine.set_default(db, client_id, row)
        changed.append("is_default")
    elif payload.is_default is False:
        row.IsDefault = False
        changed.append("is_default")

    row.UpdatedBy = principal.email
    row.UpdatedAt = utcnow()

    activity.log_principal(
        db,
        principal,
        action=A.SCRIPT_UPDATED,
        client_id=client_id,
        entity_type="script",
        entity_id=row.Id,
        message=f"Updated script '{row.Name}' -> v{row.Version}",
        meta={"changed_fields": changed},
        request=request,
    )
    db.commit()
    db.refresh(row)
    return script_detail(row)


@router.post(
    "/{script_id}/set-default",
    response_model=ScriptDetail,
    summary="Make this the company's default script for its channel",
)
def set_default_script(
    script_id: str,
    request: Request,
    principal: Principal = Depends(require("script.manage")),
    db: Session = Depends(get_leadai_db),
):
    client_id = resolve_scope(principal)
    row = _load(db, script_id, client_id)
    script_engine.set_default(db, client_id, row)
    row.IsActive = True
    row.UpdatedBy = principal.email
    row.UpdatedAt = utcnow()

    activity.log_principal(
        db,
        principal,
        action=A.SCRIPT_ACTIVATED,
        client_id=client_id,
        entity_type="script",
        entity_id=row.Id,
        message=f"Set '{row.Name}' v{row.Version} as default for channel '{row.Channel}'",
        request=request,
    )
    db.commit()
    db.refresh(row)
    return script_detail(row)


@router.post(
    "/{script_id}/preview",
    summary="Render the exact system prompt this script produces",
)
def preview_script(
    script_id: str,
    channel: str = Query(default="chat", pattern="^(all|chat|voice)$"),
    principal: Principal = Depends(require("script.read")),
    db: Session = Depends(get_leadai_db),
):
    """Shows the fully-layered prompt (channel template + script sections) that
    the model will receive. The single most useful debugging endpoint when a
    company says 'the bot isn't behaving like the script says'."""
    client_id = resolve_scope(principal)
    company_name = _company_name(db, client_id)
    row = _load(db, script_id, client_id)

    prompt, _ = script_engine.build_system_prompt(
        db, client_id, company_name, channel=channel, script=row
    )
    return {
        "script_id": row.Id,
        "script_name": row.Name,
        "channel": channel,
        "system_prompt": prompt,
        "sections": script_engine.sections_of(row),
        "character_count": len(prompt),
    }


@router.delete("/{script_id}", response_model=Ok, summary="Delete a script")
def delete_script(
    script_id: str,
    request: Request,
    principal: Principal = Depends(require("script.manage")),
    db: Session = Depends(get_leadai_db),
):
    client_id = resolve_scope(principal)
    row = _load(db, script_id, client_id)
    row.IsDeleted = True
    row.IsActive = False
    row.IsDefault = False
    row.UpdatedBy = principal.email
    row.UpdatedAt = utcnow()

    activity.log_principal(
        db,
        principal,
        action=A.SCRIPT_DELETED,
        client_id=client_id,
        entity_type="script",
        entity_id=row.Id,
        message=f"Deleted script '{row.Name}' v{row.Version}",
        log_type="Security",
        request=request,
    )
    db.commit()
    return Ok(message=f"Deleted script '{row.Name}'")


# ===========================================================================
# prompt templates
# ===========================================================================
prompts_router = APIRouter(prefix="/prompts", tags=["LeadAI • Scripts & prompts"])


@prompts_router.get("", response_model=list[PromptOut], summary="List prompt templates")
def list_prompts(
    principal: Principal = Depends(require("prompt.read")),
    db: Session = Depends(get_leadai_db),
):
    client_id = resolve_scope(principal)
    company_name = _company_name(db, client_id)
    script_engine.seed_prompts(db, client_id, created_by=principal.email)
    db.commit()

    rows = {
        r.PromptKey: r
        for r in db.query(LeadCompanyPrompt)
        .filter(
            LeadCompanyPrompt.ClientId == client_id,
            LeadCompanyPrompt.IsDeleted == False,  # noqa: E712
        )
        .all()
    }
    out = []
    for key, default in script_engine.DEFAULT_PROMPTS.items():
        row = rows.get(key)
        content = (row.Content if row else default).replace("{company}", company_name)
        out.append(
            prompt_out(
                key,
                content,
                is_customised=bool(row and row.Content != default),
                updated_at=row.UpdatedAt if row else None,
            )
        )
    return out


@prompts_router.put("/{key}", response_model=PromptOut, summary="Update a prompt template")
def update_prompt(
    key: str,
    payload: PromptUpdate,
    request: Request,
    principal: Principal = Depends(require("prompt.manage")),
    db: Session = Depends(get_leadai_db),
):
    client_id = resolve_scope(principal)
    company_name = _company_name(db, client_id)
    if key not in script_engine.VALID_PROMPT_KEYS:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"Unknown prompt key. Valid keys: {', '.join(script_engine.VALID_PROMPT_KEYS)}",
        )

    row = (
        db.query(LeadCompanyPrompt)
        .filter(LeadCompanyPrompt.ClientId == client_id, LeadCompanyPrompt.PromptKey == key)
        .one_or_none()
    )
    if row is None:
        row = LeadCompanyPrompt(
            ClientId=client_id, PromptKey=key, Content=payload.content, CreatedBy=principal.email
        )
        db.add(row)
    else:
        row.Content = payload.content
        row.UpdatedBy = principal.email
        row.UpdatedAt = utcnow()

    activity.log_principal(
        db,
        principal,
        action=A.PROMPT_UPDATED,
        client_id=client_id,
        entity_type="prompt",
        entity_id=key,
        message=f"Updated '{key}' prompt",
        meta={"length": len(payload.content)},
        request=request,
    )
    db.commit()
    db.refresh(row)
    return prompt_out(
        key,
        row.Content.replace("{company}", company_name),
        is_customised=True,
        updated_at=row.UpdatedAt,
    )


@prompts_router.post("/{key}/reset", response_model=PromptOut, summary="Reset a prompt to default")
def reset_prompt(
    key: str,
    request: Request,
    principal: Principal = Depends(require("prompt.manage")),
    db: Session = Depends(get_leadai_db),
):
    client_id = resolve_scope(principal)
    company_name = _company_name(db, client_id)
    if key not in script_engine.VALID_PROMPT_KEYS:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Unknown prompt key: {key}")

    default = script_engine.DEFAULT_PROMPTS[key]
    row = (
        db.query(LeadCompanyPrompt)
        .filter(LeadCompanyPrompt.ClientId == client_id, LeadCompanyPrompt.PromptKey == key)
        .one_or_none()
    )
    if row is None:
        row = LeadCompanyPrompt(
            ClientId=client_id, PromptKey=key, Content=default, CreatedBy=principal.email
        )
        db.add(row)
    else:
        row.Content = default
        row.UpdatedBy = principal.email
        row.UpdatedAt = utcnow()

    activity.log_principal(
        db,
        principal,
        action=A.PROMPT_RESET,
        client_id=client_id,
        entity_type="prompt",
        entity_id=key,
        message=f"Reset '{key}' prompt to default",
        request=request,
    )
    db.commit()
    return prompt_out(key, default.replace("{company}", company_name), is_customised=False)
