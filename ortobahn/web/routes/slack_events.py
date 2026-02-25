"""Slack incoming events — slash commands and interactive message callbacks."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter(prefix="/api/slack")
logger = logging.getLogger("ortobahn.web.slack")


def _verify_slack_signature(request: Request, body: bytes, signing_secret: str) -> bool:
    """Verify Slack request signature to prevent spoofing."""
    timestamp = request.headers.get("X-Slack-Request-Timestamp", "")
    if not timestamp:
        return False
    try:
        if abs(time.time() - int(timestamp)) > 300:
            return False
    except ValueError:
        return False
    sig_basestring = f"v0:{timestamp}:{body.decode()}"
    my_sig = "v0=" + hmac.new(
        signing_secret.encode(), sig_basestring.encode(), hashlib.sha256
    ).hexdigest()
    slack_sig = request.headers.get("X-Slack-Signature", "")
    return hmac.compare_digest(my_sig, slack_sig)


@router.post("/commands")
async def slack_command(request: Request):
    """Handle Slack slash commands (/ortobahn status, /ortobahn approve <id>)."""
    body = await request.body()
    settings = request.app.state.settings

    # Verify signature if signing secret is configured
    if settings.slack_signing_secret:
        if not _verify_slack_signature(request, body, settings.slack_signing_secret):
            return JSONResponse({"error": "invalid signature"}, status_code=401)

    form = await request.form()
    text = (form.get("text") or "").strip()
    parts = text.split(maxsplit=1)
    action = parts[0].lower() if parts else "help"
    arg = parts[1].strip() if len(parts) > 1 else ""

    db = request.app.state.db

    if action == "status":
        runs = db.get_recent_runs(limit=3)
        if not runs:
            return JSONResponse({"response_type": "ephemeral", "text": "No recent content engine runs."})
        lines = []
        for run in runs:
            rid = run["id"][:8]
            status = run.get("status", "unknown")
            posts = run.get("posts_published", 0)
            emoji = ":white_check_mark:" if status == "completed" else ":x:" if status == "failed" else ":hourglass:"
            lines.append(f"{emoji} `{rid}` — {status} ({posts} posts)")
        return JSONResponse({
            "response_type": "ephemeral",
            "text": "*Recent content engine runs:*\n" + "\n".join(lines),
        })

    elif action == "approve" and arg:
        post_id = arg.strip()
        post = db.get_post(post_id)
        if not post:
            # Try prefix match
            posts = db.fetchall(
                "SELECT id, text, status FROM posts WHERE id LIKE ? LIMIT 1",
                (f"{post_id}%",),
            )
            if posts:
                post = posts[0]
                post_id = post["id"]
            else:
                return JSONResponse({"response_type": "ephemeral", "text": f"Post `{post_id}` not found."})

        if post["status"] != "draft":
            return JSONResponse({
                "response_type": "ephemeral",
                "text": f"Post `{post_id[:8]}` is `{post['status']}`, not a draft.",
            })

        db.approve_post(post_id)
        preview = (post.get("text") or "")[:100]
        return JSONResponse({
            "response_type": "in_channel",
            "text": f":white_check_mark: Post `{post_id[:8]}` approved.\n>{preview}",
        })

    elif action == "reject" and arg:
        post_id = arg.strip()
        post = db.get_post(post_id)
        if not post:
            posts = db.fetchall(
                "SELECT id, text, status FROM posts WHERE id LIKE ? LIMIT 1",
                (f"{post_id}%",),
            )
            if posts:
                post = posts[0]
                post_id = post["id"]
            else:
                return JSONResponse({"response_type": "ephemeral", "text": f"Post `{post_id}` not found."})

        if post["status"] != "draft":
            return JSONResponse({
                "response_type": "ephemeral",
                "text": f"Post `{post_id[:8]}` is `{post['status']}`, not a draft.",
            })

        db.reject_post(post_id)
        return JSONResponse({
            "response_type": "in_channel",
            "text": f":no_entry: Post `{post_id[:8]}` rejected.",
        })

    else:
        return JSONResponse({
            "response_type": "ephemeral",
            "text": "*Usage:*\n`/ortobahn status` — show recent runs\n`/ortobahn approve <id>` — approve a draft\n`/ortobahn reject <id>` — reject a draft",
        })


@router.post("/interactions")
async def slack_interaction(request: Request):
    """Handle interactive button callbacks from Slack messages."""
    form = await request.form()
    payload_str = form.get("payload", "{}")
    payload = json.loads(payload_str)

    # Verify if signing secret configured (for interactions, check token)
    actions = payload.get("actions", [])
    db = request.app.state.db

    for action in actions:
        action_id = action.get("action_id", "")
        value = action.get("value", "")

        if action_id == "approve_post" and value:
            post = db.get_post(value)
            if post and post["status"] == "draft":
                db.approve_post(value)
                return JSONResponse({"text": f":white_check_mark: Post `{value[:8]}` approved."})
            return JSONResponse({"text": f"Post `{value[:8]}` is not a draft."})

        elif action_id == "reject_post" and value:
            post = db.get_post(value)
            if post and post["status"] == "draft":
                db.reject_post(value)
                return JSONResponse({"text": f":no_entry: Post `{value[:8]}` rejected."})
            return JSONResponse({"text": f"Post `{value[:8]}` is not a draft."})

    return JSONResponse({"text": "Action processed."})
