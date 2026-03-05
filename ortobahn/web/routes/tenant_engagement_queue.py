"""Tenant engagement queue — pending replies, outcomes, analytics."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

from ortobahn.auth import AuthClient

logger = logging.getLogger("ortobahn.web.tenant")
router = APIRouter(prefix="/my")


@router.get("/engagement")
async def tenant_engagement(request: Request, client: AuthClient):
    """Engagement queue — queued conversations, recent replies, outcomes."""
    db = request.app.state.db
    templates = request.app.state.templates
    client_id = client["id"]

    # Queued conversations awaiting engagement
    queued = db.fetchall(
        """SELECT * FROM discovered_conversations
           WHERE client_id=? AND status='queued'
           ORDER BY relevance_score DESC, engagement_score DESC LIMIT 30""",
        (client_id,),
    )

    # Recent engagement replies
    replies = db.fetchall(
        """SELECT * FROM engagement_replies
           WHERE client_id=? ORDER BY created_at DESC LIMIT 30""",
        (client_id,),
    )

    # Recent outcomes
    outcomes = db.fetchall(
        """SELECT * FROM engagement_outcomes
           WHERE client_id=? ORDER BY created_at DESC LIMIT 20""",
        (client_id,),
    )

    # Stats
    stats = {
        "queued_count": len(queued),
        "total_replies": db.fetchone(
            "SELECT COUNT(*) as cnt FROM engagement_replies WHERE client_id=?",
            (client_id,),
        )["cnt"],
        "proactive_replies": db.fetchone(
            "SELECT COUNT(*) as cnt FROM engagement_replies WHERE client_id=? AND engagement_type='proactive'",
            (client_id,),
        )["cnt"],
        "avg_confidence": 0.0,
        "avg_outcome_score": 0.0,
    }

    # Average confidence
    conf_row = db.fetchone(
        "SELECT AVG(confidence) as avg_conf FROM engagement_replies WHERE client_id=? AND status='posted'",
        (client_id,),
    )
    if conf_row and conf_row["avg_conf"]:
        stats["avg_confidence"] = round(conf_row["avg_conf"], 2)

    # Average outcome score
    outcome_row = db.fetchone(
        "SELECT AVG(outcome_score) as avg_score FROM engagement_outcomes WHERE client_id=?",
        (client_id,),
    )
    if outcome_row and outcome_row["avg_score"]:
        stats["avg_outcome_score"] = round(outcome_row["avg_score"], 2)

    return templates.TemplateResponse(
        "tenant_engagement_queue.html",
        {
            "request": request,
            "client": client,
            "queued": queued,
            "replies": replies,
            "outcomes": outcomes,
            "stats": stats,
        },
    )


@router.post("/engagement/{conv_id}/approve")
async def tenant_approve_engagement(request: Request, conv_id: str, client: AuthClient):
    """Approve a queued conversation for engagement."""
    db = request.app.state.db
    db.execute(
        "UPDATE discovered_conversations SET status='queued' WHERE id=? AND client_id=?",
        (conv_id, client["id"]),
        commit=True,
    )
    return RedirectResponse("/my/engagement?msg=approved", status_code=303)


@router.post("/engagement/{conv_id}/skip")
async def tenant_skip_engagement(request: Request, conv_id: str, client: AuthClient):
    """Skip a queued conversation."""
    db = request.app.state.db
    db.execute(
        "UPDATE discovered_conversations SET status='skipped' WHERE id=? AND client_id=?",
        (conv_id, client["id"]),
        commit=True,
    )
    return RedirectResponse("/my/engagement?msg=skipped", status_code=303)
