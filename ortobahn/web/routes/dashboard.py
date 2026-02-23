"""Dashboard route - main overview page with HTMX partial endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from ortobahn.auth import get_admin_client

router = APIRouter(dependencies=[Depends(get_admin_client)])


@router.get("/")
async def index(request: Request):
    db = request.app.state.db
    templates = request.app.state.templates

    clients = db.get_all_clients()
    recent_runs = db.get_recent_runs(limit=5)
    pending_drafts = db.get_drafts_for_review()
    strategy = db.get_active_strategy()

    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "clients": clients,
            "recent_runs": recent_runs,
            "pending_drafts_count": len(pending_drafts),
            "strategy": strategy,
        },
    )


# ---------------------------------------------------------------------------
# HTMX partial endpoints for auto-refresh
# ---------------------------------------------------------------------------


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _badge(status: str) -> str:
    return f'<span class="badge {status}">{status}</span>'


@router.get("/api/admin/partials/kpi", response_class=HTMLResponse)
async def admin_kpi_partial(request: Request):
    """Return the KPI cards HTML fragment for the admin dashboard."""
    db = request.app.state.db
    clients = db.get_all_clients()
    pending_drafts = db.get_drafts_for_review()
    strategy = db.get_active_strategy()

    # Clients card
    client_items = "".join(
        f'<li><a href="/clients/{c["id"]}">{_escape(c.get("name", ""))}</a> - {_escape(c.get("industry", ""))}</li>'
        for c in clients
    )
    clients_html = (
        "<article>"
        "<header>Clients</header>"
        f"<p><strong>{len(clients)}</strong> active clients</p>"
        f"<ul>{client_items}</ul>"
        '<footer><a href="/clients/">Manage clients</a></footer>'
        "</article>"
    )

    # Pending content card
    count = len(pending_drafts)
    review_btn = '<a href="/content/?status=draft" role="button">Review Drafts</a>' if count > 0 else ""
    pending_html = (
        "<article>"
        "<header>Pending Content</header>"
        f"<p><strong>{count}</strong> drafts awaiting review</p>"
        f"{review_btn}"
        "</article>"
    )

    # Strategy card
    if strategy:
        themes = strategy.get("themes", [])
        if isinstance(themes, list):
            themes_str = ", ".join(themes)
        else:
            themes_str = str(themes)
        strategy_html = (
            "<article>"
            "<header>Active Strategy</header>"
            f"<p><strong>Themes:</strong> {_escape(themes_str)}</p>"
            f"<p><strong>Tone:</strong> {_escape(strategy.get('tone', ''))}</p>"
            f"<p><small>Valid until: {_escape(str(strategy.get('valid_until', '')))}</small></p>"
            "</article>"
        )
    else:
        strategy_html = (
            "<article>"
            "<header>Active Strategy</header>"
            "<p>No active strategy. Run the pipeline to generate one.</p>"
            "</article>"
        )

    return HTMLResponse(clients_html + pending_html + strategy_html)


@router.get("/api/admin/partials/pipeline", response_class=HTMLResponse)
async def admin_pipeline_partial(request: Request):
    """Return live pipeline status as an HTML fragment."""
    db = request.app.state.db

    running = db.fetchone(
        "SELECT id, started_at, client_id FROM pipeline_runs WHERE status='running' ORDER BY started_at DESC LIMIT 1",
    )

    if running:
        latest_agent = db.fetchone(
            "SELECT agent_name FROM agent_logs WHERE run_id=? ORDER BY created_at DESC LIMIT 1",
            (running["id"],),
        )
        step_name = latest_agent["agent_name"] if latest_agent else "initializing"
        client_id = running.get("client_id", "unknown")
        html = (
            '<div class="glass-status-card">'
            '<span class="glass-pulse running"></span>'
            f" <strong>Pipeline RUNNING</strong> for {_escape(client_id)}"
            f" &mdash; current step: {_escape(step_name)}"
            "</div>"
        )
    else:
        last = db.fetchone(
            "SELECT status, completed_at, posts_published, client_id FROM pipeline_runs"
            " WHERE status IN ('completed','failed') ORDER BY completed_at DESC LIMIT 1",
        )
        if last and last["status"] == "failed":
            html = (
                '<div class="glass-status-card">'
                '<span class="glass-pulse failed"></span>'
                f" <strong>Last run FAILED</strong> ({_escape(str(last.get('client_id', '')))})"
                f" &mdash; {_escape(str(last.get('completed_at', 'unknown')))}"
                "</div>"
            )
        elif last:
            published = last.get("posts_published") or 0
            html = (
                '<div class="glass-status-card">'
                '<span class="glass-pulse idle"></span>'
                f" <strong>Pipeline IDLE</strong> &mdash; last run published {published} post(s)"
                f" for {_escape(str(last.get('client_id', '')))}"
                "</div>"
            )
        else:
            html = (
                '<div class="glass-status-card">'
                '<span class="glass-pulse idle"></span>'
                " <strong>Pipeline IDLE</strong> &mdash; awaiting first run"
                "</div>"
            )

    return HTMLResponse(html)


@router.get("/api/admin/partials/runs", response_class=HTMLResponse)
async def admin_runs_partial(request: Request):
    """Return recent pipeline runs table as an HTML fragment."""
    db = request.app.state.db
    recent_runs = db.get_recent_runs(limit=5)

    if not recent_runs:
        return HTMLResponse('<p style="opacity: 0.6;">No pipeline runs yet.</p>')

    rows = []
    for run in recent_runs:
        status = run.get("status", "unknown")
        badge = _badge(status)
        rows.append(
            "<tr>"
            f"<td><code>{_escape(run['id'][:8])}</code></td>"
            f"<td>{badge}</td>"
            f"<td>{run.get('posts_published', 0)}</td>"
            f"<td>{_escape(str(run.get('started_at', '')))}</td>"
            "</tr>"
        )

    html = (
        "<table><thead><tr>"
        "<th>Run ID</th><th>Status</th><th>Posts</th><th>Started</th>"
        "</tr></thead><tbody>" + "".join(rows) + "</tbody></table>"
    )
    return HTMLResponse(html)
