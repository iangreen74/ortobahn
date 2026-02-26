"""Tenant Posts page — all social posts with filtering."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request

from ortobahn.auth import AuthClient

logger = logging.getLogger("ortobahn.web.tenant")

router = APIRouter(prefix="/my")


@router.get("/posts")
async def tenant_posts(request: Request, client: AuthClient):
    """Full Posts page — all social posts, filterable by platform/status."""
    db = request.app.state.db
    templates = request.app.state.templates
    client_id = client["id"]

    # Get filter params
    platform_filter = request.query_params.get("platform", "")
    status_filter = request.query_params.get("status", "")

    # Build query with optional filters
    conditions = ["p.client_id=?"]
    params: list = [client_id]

    if platform_filter:
        conditions.append("p.platform=?")
        params.append(platform_filter)

    if status_filter:
        conditions.append("p.status=?")
        params.append(status_filter)

    where = " AND ".join(conditions)

    _MJ = (
        " LEFT JOIN metrics m ON p.id = m.post_id"
        " AND m.id = (SELECT m2.id FROM metrics m2 WHERE m2.post_id = p.id ORDER BY m2.measured_at DESC LIMIT 1)"
    )

    posts = db.fetchall(
        "SELECT p.id, p.text, p.platform, p.status, p.created_at, p.published_at,"
        " COALESCE(m.like_count,0) as like_count,"
        " COALESCE(m.repost_count,0) as repost_count,"
        " COALESCE(m.reply_count,0) as reply_count"
        f" FROM posts p{_MJ} WHERE {where}"
        " ORDER BY COALESCE(p.published_at, p.created_at) DESC"
        " LIMIT 100",
        tuple(params),
    )

    # Get list of connected platforms for filter dropdown
    platforms = db.fetchall(
        "SELECT DISTINCT platform FROM posts WHERE client_id=? ORDER BY platform",
        (client_id,),
    )
    available_platforms = [r["platform"] for r in platforms if r["platform"]]

    return templates.TemplateResponse(
        "tenant_posts.html",
        {
            "request": request,
            "client": client,
            "posts": posts,
            "available_platforms": available_platforms,
            "platform_filter": platform_filter,
            "status_filter": status_filter,
        },
    )
