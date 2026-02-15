"""SRE system health dashboard routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from ortobahn.auth import get_admin_client

router = APIRouter(dependencies=[Depends(get_admin_client)])


@router.get("/")
async def sre_dashboard(request: Request):
    db = request.app.state.db
    templates = request.app.state.templates

    # Get recent pipeline runs for health overview
    recent_runs = db.get_recent_runs(limit=20)
    total_runs = len(recent_runs)
    failed_runs = sum(1 for r in recent_runs if r.get("status") == "failed")
    success_rate = ((total_runs - failed_runs) / total_runs * 100) if total_runs else 0

    # Token usage
    total_input = sum(r.get("total_input_tokens") or 0 for r in recent_runs)
    total_output = sum(r.get("total_output_tokens") or 0 for r in recent_runs)
    est_cost = (total_input / 1_000_000 * 3) + (total_output / 1_000_000 * 15)

    # Platform health
    posts = db.get_all_posts(limit=50)
    platform_health = {}
    for platform in ["bluesky", "twitter", "linkedin"]:
        p_posts = [p for p in posts if p.get("platform") == platform and p.get("status") in ("published", "failed")]
        if p_posts:
            platform_health[platform] = "healthy" if p_posts[0]["status"] == "published" else "failing"
        else:
            platform_health[platform] = "no_data"

    # Agent logs
    agent_logs = db.get_recent_agent_logs(limit=10)

    # Overall health
    if total_runs == 0:
        health = "unknown"
    elif success_rate >= 80:
        health = "healthy"
    elif success_rate >= 50:
        health = "degraded"
    else:
        health = "critical"

    return templates.TemplateResponse(
        "sre.html",
        {
            "request": request,
            "health": health,
            "success_rate": round(success_rate, 1),
            "total_runs": total_runs,
            "failed_runs": failed_runs,
            "total_input_tokens": total_input,
            "total_output_tokens": total_output,
            "estimated_cost": round(est_cost, 4),
            "platform_health": platform_health,
            "recent_runs": recent_runs[:10],
            "agent_logs": agent_logs,
        },
    )
