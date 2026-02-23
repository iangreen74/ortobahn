"""SRE system health dashboard routes with HTMX auto-refresh partials."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from ortobahn.auth import get_admin_client

router = APIRouter(dependencies=[Depends(get_admin_client)])


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _badge(status: str) -> str:
    return f'<span class="badge {status}">{status}</span>'


def _fmt_tokens(n: int | None) -> str:
    if not n:
        return "0"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}k"
    return str(n)


def _compute_sre_data(db):
    """Shared data computation for both the full page and partial endpoints."""
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

    # Overall health
    if total_runs == 0:
        health = "unknown"
    elif success_rate >= 80:
        health = "healthy"
    elif success_rate >= 50:
        health = "degraded"
    else:
        health = "critical"

    return {
        "health": health,
        "success_rate": round(success_rate, 1),
        "total_runs": total_runs,
        "failed_runs": failed_runs,
        "total_input_tokens": total_input,
        "total_output_tokens": total_output,
        "estimated_cost": round(est_cost, 4),
        "platform_health": platform_health,
        "recent_runs": recent_runs[:10],
    }


@router.get("/")
async def sre_dashboard(request: Request):
    db = request.app.state.db
    templates = request.app.state.templates

    data = _compute_sre_data(db)
    agent_logs = db.get_recent_agent_logs(limit=10)

    return templates.TemplateResponse(
        "sre.html",
        {
            "request": request,
            **data,
            "agent_logs": agent_logs,
        },
    )


# ---------------------------------------------------------------------------
# HTMX partial endpoints for SRE auto-refresh
# ---------------------------------------------------------------------------


@router.get("/partials/overview", response_class=HTMLResponse)
async def sre_overview_partial(request: Request):
    """Return the SRE overview panel as an HTML fragment."""
    db = request.app.state.db
    data = _compute_sre_data(db)

    # Health indicator
    health = data["health"]
    if health == "healthy":
        health_html = '<span class="sre-indicator sre-green"></span><span class="badge completed">HEALTHY</span>'
    elif health == "degraded":
        health_html = '<span class="sre-indicator sre-yellow"></span><span class="badge running">DEGRADED</span>'
    elif health == "critical":
        health_html = '<span class="sre-indicator sre-red"></span><span class="badge failed">CRITICAL</span>'
    else:
        health_html = '<span class="sre-indicator sre-gray"></span><span class="badge">UNKNOWN</span>'

    # Platform health items
    plat_items = []
    for platform, status in data["platform_health"].items():
        if status == "healthy":
            ind = '<span class="sre-indicator sre-green"></span>'
            badge = '<span class="badge completed">healthy</span>'
        elif status == "failing":
            ind = '<span class="sre-indicator sre-red"></span>'
            badge = '<span class="badge failed">failing</span>'
        else:
            ind = '<span class="sre-indicator sre-gray"></span>'
            badge = '<span class="badge">no data</span>'
        plat_items.append(
            f'<div style="display: flex; align-items: center; gap: 0.5rem; margin-bottom: 0.5rem;">'
            f"{ind}<strong>{platform.capitalize()}</strong>{badge}</div>"
        )

    sr = data["success_rate"]
    if sr >= 80:
        gauge_bg = "linear-gradient(90deg, #10b981, #4caf50)"
    elif sr >= 50:
        gauge_bg = "linear-gradient(90deg, #f59e0b, #ffb74d)"
    else:
        gauge_bg = "linear-gradient(90deg, #ef4444, #ef5350)"

    html = (
        '<div class="grid">'
        # Health card
        "<article><header>Overall Health</header>"
        f'<p style="text-align: center; font-size: 2rem;">{health_html}</p>'
        f"<p>Pipeline success rate: <strong>{data['success_rate']}%</strong></p>"
        f"<p><small>{data['total_runs']} runs, {data['failed_runs']} failed</small></p>"
        "</article>"
        # Token usage card
        "<article><header>Token Usage</header>"
        f"<p>Input: <strong>{data['total_input_tokens']:,}</strong></p>"
        f"<p>Output: <strong>{data['total_output_tokens']:,}</strong></p>"
        f"<p>Estimated cost: <strong>${data['estimated_cost']}</strong></p>"
        "</article>"
        # Platform health card
        "<article><header>Platform Health</header>" + "".join(plat_items) + "</article></div>"
        # Gauge
        "<h2>Pipeline Success Rate</h2><article>"
        '<div class="sre-gauge-container">'
        '<div class="sre-gauge">'
        f'<div class="sre-gauge-fill" style="width: {sr}%; background: {gauge_bg};"></div>'
        "</div>"
        '<div style="display: flex; justify-content: space-between; margin-top: 0.5rem;">'
        '<small style="opacity: 0.5;">0%</small>'
        f"<strong>{sr}%</strong>"
        '<small style="opacity: 0.5;">100%</small>'
        "</div></div></article>"
    )
    return HTMLResponse(html)


@router.get("/partials/token-trend", response_class=HTMLResponse)
async def sre_token_trend_partial(request: Request):
    """Return a 7-day token usage bar chart as an HTML fragment."""
    db = request.app.state.db

    # Get daily token usage for the last 7 days
    cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    day_rows = db.fetchall(
        "SELECT DATE(started_at) as day,"
        " SUM(COALESCE(total_input_tokens,0) + COALESCE(total_output_tokens,0)) as total_tokens,"
        " COUNT(*) as runs"
        " FROM pipeline_runs WHERE started_at >= ?"
        " GROUP BY DATE(started_at) ORDER BY day",
        (cutoff,),
    )

    if not day_rows:
        return HTMLResponse('<p style="opacity: 0.6; text-align: center;">No pipeline runs in the last 7 days.</p>')

    max_tokens = max((r["total_tokens"] or 0 for r in day_rows), default=0)

    bars = []
    for r in day_rows:
        tokens = r["total_tokens"] or 0
        pct = round((tokens / max_tokens) * 100) if max_tokens > 0 else 0
        day_label = str(r["day"] or "")[-5:]  # MM-DD
        bars.append(
            '<div style="flex: 1; display: flex; flex-direction: column; align-items: center; height: 100%;">'
            '<div style="flex: 1; display: flex; align-items: flex-end; width: 100%;">'
            f'<div class="bar" style="height: {pct}%; width: 100%;" '
            f'title="{day_label}: {_fmt_tokens(tokens)} tokens from {r["runs"]} run(s)"></div>'
            "</div>"
            f'<div class="bar-label">{day_label}</div>'
            "</div>"
        )

    labels = []
    for r in day_rows:
        tokens = r["total_tokens"] or 0
        labels.append(
            f'<small style="flex: 1; text-align: center; opacity: 0.5; font-size: 0.7rem;">'
            f"{_fmt_tokens(tokens)}</small>"
        )

    html = (
        '<div class="bar-chart">' + "".join(bars) + "</div>"
        '<div style="display: flex; justify-content: space-between; padding: 0 0.25rem;">' + "".join(labels) + "</div>"
    )
    return HTMLResponse(html)


@router.get("/partials/errors", response_class=HTMLResponse)
async def sre_errors_partial(request: Request):
    """Return recent errors as an HTML fragment."""
    db = request.app.state.db

    # Get failed posts (last 10)
    failed_posts = db.fetchall(
        "SELECT text, platform, error_message, failure_category, created_at"
        " FROM posts WHERE status='failed'"
        " ORDER BY created_at DESC LIMIT 10",
    )

    # Get failed pipeline runs (last 5)
    failed_runs = db.fetchall(
        "SELECT id, errors, started_at, client_id FROM pipeline_runs"
        " WHERE status='failed' ORDER BY started_at DESC LIMIT 5",
    )

    if not failed_posts and not failed_runs:
        return HTMLResponse(
            '<p style="opacity: 0.6; text-align: center;">No errors in recent history. All systems operational.</p>'
        )

    parts = []

    if failed_runs:
        parts.append("<h4>Failed Pipeline Runs</h4>")
        for run in failed_runs:
            errors_str = ""
            if run.get("errors"):
                try:
                    err_list = json.loads(run["errors"]) if isinstance(run["errors"], str) else run["errors"]
                    if isinstance(err_list, list) and err_list:
                        errors_str = _escape(str(err_list[0])[:200])
                except (json.JSONDecodeError, TypeError):
                    errors_str = _escape(str(run["errors"])[:200])

            parts.append(
                '<div class="sre-error-item">'
                f'<span class="badge failed">FAILED</span> '
                f"<code>{_escape(run['id'][:8])}</code> "
                f"<small>({_escape(str(run.get('client_id', '')))})</small>"
            )
            if errors_str:
                parts.append(f'<p style="margin: 0.25rem 0; font-size: 0.85rem;">{errors_str}</p>')
            parts.append(f'<small style="opacity: 0.5;">{_escape(str(run.get("started_at", "")))}</small></div>')

    if failed_posts:
        parts.append("<h4>Failed Posts</h4>")
        for post in failed_posts:
            text = _escape(post["text"][:80]) + ("..." if len(post["text"]) > 80 else "")
            error = _escape(post.get("error_message") or "Unknown error")[:200]
            category = post.get("failure_category") or ""
            platform = post.get("platform") or "generic"
            cat_badge = f' <span class="badge">{_escape(category)}</span>' if category else ""

            parts.append(
                '<div class="sre-error-item">'
                f'<span class="badge {platform}">{_escape(platform)}</span>{cat_badge} '
                f"{text}"
                f'<p style="margin: 0.25rem 0; font-size: 0.85rem; color: #ef5350;">{error}</p>'
                f'<small style="opacity: 0.5;">{_escape(str(post.get("created_at", "")))}</small>'
                "</div>"
            )

    return HTMLResponse("".join(parts))
