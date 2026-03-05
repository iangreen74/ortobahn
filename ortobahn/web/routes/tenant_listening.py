"""Tenant listening dashboard — conversations, rules, tracked accounts."""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

from ortobahn.auth import AuthClient

logger = logging.getLogger("ortobahn.web.tenant")
router = APIRouter(prefix="/my")


@router.get("/listening")
async def tenant_listening(request: Request, client: AuthClient):
    """Listening dashboard — discovered conversations, rules, tracked accounts."""
    db = request.app.state.db
    templates = request.app.state.templates
    client_id = client["id"]

    # Recent discovered conversations
    conversations = db.fetchall(
        """SELECT * FROM discovered_conversations
           WHERE client_id=? ORDER BY discovered_at DESC LIMIT 50""",
        (client_id,),
    )

    # Active listening rules
    rules = db.fetchall(
        "SELECT * FROM listening_rules WHERE client_id=? AND active=1 ORDER BY priority ASC",
        (client_id,),
    )

    # Tracked accounts
    accounts = db.fetchall(
        "SELECT * FROM tracked_accounts WHERE client_id=? AND active=1 ORDER BY created_at DESC LIMIT 30",
        (client_id,),
    )

    # Stats
    stats = {
        "total_discovered": db.fetchone(
            "SELECT COUNT(*) as cnt FROM discovered_conversations WHERE client_id=?",
            (client_id,),
        )["cnt"],
        "queued": db.fetchone(
            "SELECT COUNT(*) as cnt FROM discovered_conversations WHERE client_id=? AND status='queued'",
            (client_id,),
        )["cnt"],
        "replied": db.fetchone(
            "SELECT COUNT(*) as cnt FROM discovered_conversations WHERE client_id=? AND status='replied'",
            (client_id,),
        )["cnt"],
        "rules_count": len(rules),
        "accounts_count": len(accounts),
    }

    return templates.TemplateResponse(
        "tenant_listening.html",
        {
            "request": request,
            "client": client,
            "conversations": conversations,
            "rules": rules,
            "accounts": accounts,
            "stats": stats,
        },
    )


@router.post("/listening/rules")
async def tenant_add_rule(request: Request, client: AuthClient):
    """Add a listening rule."""
    db = request.app.state.db
    form = await request.form()

    platform = str(form.get("platform", "bluesky"))
    rule_type = str(form.get("rule_type", "keyword"))
    value = str(form.get("value", "")).strip()
    priority = int(str(form.get("priority", "3")))

    if not value:
        return RedirectResponse("/my/listening?error=empty_value", status_code=303)

    db.execute(
        """INSERT INTO listening_rules (id, client_id, platform, rule_type, value, priority, active)
           VALUES (?, ?, ?, ?, ?, ?, 1)""",
        (str(uuid.uuid4()), client["id"], platform, rule_type, value, priority),
        commit=True,
    )
    return RedirectResponse("/my/listening?msg=rule_added", status_code=303)


@router.post("/listening/rules/{rule_id}/delete")
async def tenant_delete_rule(request: Request, rule_id: str, client: AuthClient):
    """Delete a listening rule."""
    db = request.app.state.db
    db.execute(
        "UPDATE listening_rules SET active=0 WHERE id=? AND client_id=?",
        (rule_id, client["id"]),
        commit=True,
    )
    return RedirectResponse("/my/listening?msg=rule_deleted", status_code=303)


@router.post("/listening/accounts")
async def tenant_add_account(request: Request, client: AuthClient):
    """Add a tracked account."""
    db = request.app.state.db
    form = await request.form()

    platform = str(form.get("platform", "bluesky"))
    handle = str(form.get("handle", "")).strip()
    account_type = str(form.get("account_type", "influencer"))

    if not handle:
        return RedirectResponse("/my/listening?error=empty_handle", status_code=303)

    # Check for duplicate
    existing = db.fetchone(
        "SELECT id FROM tracked_accounts WHERE client_id=? AND platform=? AND account_handle=?",
        (client["id"], platform, handle),
    )
    if existing:
        return RedirectResponse("/my/listening?error=duplicate_account", status_code=303)

    db.execute(
        """INSERT INTO tracked_accounts (id, client_id, platform, account_handle, account_type, active)
           VALUES (?, ?, ?, ?, ?, 1)""",
        (str(uuid.uuid4()), client["id"], platform, handle, account_type),
        commit=True,
    )
    return RedirectResponse("/my/listening?msg=account_added", status_code=303)


@router.post("/listening/accounts/{account_id}/delete")
async def tenant_delete_account(request: Request, account_id: str, client: AuthClient):
    """Remove a tracked account."""
    db = request.app.state.db
    db.execute(
        "UPDATE tracked_accounts SET active=0 WHERE id=? AND client_id=?",
        (account_id, client["id"]),
        commit=True,
    )
    return RedirectResponse("/my/listening?msg=account_removed", status_code=303)
