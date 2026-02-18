"""Glass Company — public live dashboard showing Ortobahn's inner workings."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

router = APIRouter()

# Only show data for internal clients.
INTERNAL_IDS = ("default", "vaultscaler", "ortobahn")
_IN_CLAUSE = ",".join("?" for _ in INTERNAL_IDS)

PIPELINE_STEPS = [
    "sre", "cifix", "analytics", "reflection", "trends",
    "ceo", "strategist", "creator", "publisher", "support",
    "marketing", "learning",
]

_CACHE_HEADERS = {"Cache-Control": "public, max-age=15"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _cost(input_tok: int, output_tok: int, cache_create: int = 0, cache_read: int = 0) -> float:
    """Sonnet pricing — mirrors db.get_current_month_spend."""
    uncached = max(0, input_tok - cache_create - cache_read)
    return (
        uncached / 1_000_000 * 3
        + cache_create / 1_000_000 * 3.75
        + cache_read / 1_000_000 * 0.30
        + output_tok / 1_000_000 * 15
    )


def _cost_query(db, since: str | None = None) -> float:
    query = (
        "SELECT COALESCE(SUM(total_input_tokens),0) as input_tok,"
        " COALESCE(SUM(total_output_tokens),0) as output_tok,"
        " COALESCE(SUM(total_cache_creation_tokens),0) as cache_create,"
        " COALESCE(SUM(total_cache_read_tokens),0) as cache_read"
        f" FROM pipeline_runs WHERE client_id IN ({_IN_CLAUSE})"
    )
    params: list = list(INTERNAL_IDS)
    if since:
        query += " AND started_at >= ?"
        params.append(since)
    row = db.fetchone(query, params)
    if not row:
        return 0.0
    return _cost(row["input_tok"], row["output_tok"], row["cache_create"], row["cache_read"])


def _trunc(text: str | None, length: int = 200) -> str:
    if not text:
        return ""
    return text[:length] + ("..." if len(text) > length else "")


def _fmt_duration(seconds: float | None) -> str:
    if not seconds:
        return "-"
    if seconds < 60:
        return f"{seconds:.1f}s"
    return f"{seconds / 60:.1f}m"


def _fmt_tokens(n: int | None) -> str:
    if not n:
        return "0"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}k"
    return str(n)


def _badge(status: str) -> str:
    return f'<span class="badge {status}">{status}</span>'


def _step_index(agent_name: str) -> int:
    """Map agent name to pipeline step number (1-based)."""
    name = agent_name.lower().replace("_agent", "").replace("agent", "").strip()
    for i, step in enumerate(PIPELINE_STEPS):
        if step in name or name in step:
            return i + 1
    return 0


def _escape(text: str) -> str:
    """Minimal HTML escaping."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


# ---------------------------------------------------------------------------
# Full page
# ---------------------------------------------------------------------------

@router.get("/glass")
async def glass_dashboard(request: Request):
    templates = request.app.state.templates
    return templates.TemplateResponse("glass.html", {"request": request})


# ---------------------------------------------------------------------------
# HTMX partials
# ---------------------------------------------------------------------------

@router.get("/glass/api/status", response_class=HTMLResponse)
async def glass_status(request: Request):
    db = request.app.state.db

    running = db.fetchone(
        "SELECT id, started_at, client_id FROM pipeline_runs"
        f" WHERE status='running' AND client_id IN ({_IN_CLAUSE})"
        " ORDER BY started_at DESC LIMIT 1",
        INTERNAL_IDS,
    )

    last = db.fetchone(
        "SELECT id, completed_at, status, posts_published FROM pipeline_runs"
        f" WHERE status IN ('completed','failed') AND client_id IN ({_IN_CLAUSE})"
        " ORDER BY completed_at DESC LIMIT 1",
        INTERNAL_IDS,
    )

    if running:
        latest_agent = db.fetchone(
            "SELECT agent_name FROM agent_logs WHERE run_id=? ORDER BY created_at DESC LIMIT 1",
            (running["id"],),
        )
        step_name = latest_agent["agent_name"] if latest_agent else "initializing"
        step_num = _step_index(step_name) if latest_agent else 0
        html = (
            '<div class="glass-status-card">'
            '<span class="glass-pulse running"></span>'
            f' <strong>Pipeline RUNNING</strong> &mdash; step {step_num}/12: {_escape(step_name)}'
            '</div>'
        )
    elif last and last["status"] == "failed":
        html = (
            '<div class="glass-status-card">'
            '<span class="glass-pulse failed"></span>'
            f' <strong>Last run FAILED</strong> &mdash; {last["completed_at"] or "unknown"}'
            '</div>'
        )
    elif last:
        html = (
            '<div class="glass-status-card">'
            '<span class="glass-pulse idle"></span>'
            f' <strong>Pipeline IDLE</strong> &mdash; last run published {last["posts_published"] or 0} post(s)'
            '</div>'
        )
    else:
        html = (
            '<div class="glass-status-card">'
            '<span class="glass-pulse idle"></span>'
            ' <strong>Pipeline IDLE</strong> &mdash; awaiting first run'
            '</div>'
        )

    return HTMLResponse(html, headers=_CACHE_HEADERS)


@router.get("/glass/api/costs", response_class=HTMLResponse)
async def glass_costs(request: Request):
    db = request.app.state.db
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()

    cost_today = _cost_query(db, today_start)
    cost_month = _cost_query(db, month_start)
    cost_all = _cost_query(db)

    html = (
        '<h3>Costs</h3>'
        '<div class="grid">'
        f'<div class="glass-stat"><div class="value">${cost_today:.4f}</div><div class="label">Today</div></div>'
        f'<div class="glass-stat"><div class="value">${cost_month:.2f}</div><div class="label">This month</div></div>'
        f'<div class="glass-stat"><div class="value">${cost_all:.2f}</div><div class="label">All time</div></div>'
        '</div>'
    )
    return HTMLResponse(html, headers=_CACHE_HEADERS)


@router.get("/glass/api/health", response_class=HTMLResponse)
async def glass_health(request: Request):
    db = request.app.state.db

    total_runs = db.fetchone(
        f"SELECT COUNT(*) as c FROM pipeline_runs WHERE client_id IN ({_IN_CLAUSE})",
        INTERNAL_IDS,
    )
    completed_runs = db.fetchone(
        f"SELECT COUNT(*) as c FROM pipeline_runs WHERE status='completed' AND client_id IN ({_IN_CLAUSE})",
        INTERNAL_IDS,
    )
    total_posts = db.fetchone(
        f"SELECT COUNT(*) as c FROM posts WHERE status='published' AND client_id IN ({_IN_CLAUSE})",
        INTERNAL_IDS,
    )
    platforms = db.fetchone(
        f"SELECT COUNT(DISTINCT platform) as c FROM posts WHERE status='published' AND client_id IN ({_IN_CLAUSE})",
        INTERNAL_IDS,
    )

    total = total_runs["c"] if total_runs else 0
    completed = completed_runs["c"] if completed_runs else 0
    success_rate = f"{completed / total * 100:.0f}%" if total > 0 else "N/A"
    posts_count = total_posts["c"] if total_posts else 0
    platform_count = platforms["c"] if platforms else 0

    html = (
        '<h3>System Health</h3>'
        '<div class="grid">'
        f'<div class="glass-stat"><div class="value">{success_rate}</div><div class="label">Pipeline success rate</div></div>'
        f'<div class="glass-stat"><div class="value">{posts_count}</div><div class="label">Posts published</div></div>'
        f'<div class="glass-stat"><div class="value">{platform_count}</div><div class="label">Active platforms</div></div>'
        '</div>'
    )
    return HTMLResponse(html, headers=_CACHE_HEADERS)


@router.get("/glass/api/agents", response_class=HTMLResponse)
async def glass_agents(request: Request):
    db = request.app.state.db

    logs = db.fetchall(
        "SELECT al.agent_name, al.output_summary, al.reasoning, al.input_tokens,"
        " al.output_tokens, al.duration_seconds, al.created_at"
        " FROM agent_logs al"
        f" INNER JOIN pipeline_runs pr ON al.run_id = pr.id AND pr.client_id IN ({_IN_CLAUSE})"
        " ORDER BY al.created_at DESC LIMIT 20",
        INTERNAL_IDS,
    )

    if not logs:
        return HTMLResponse(
            '<h3>Agent Activity</h3><p class="glass-empty">No agent activity yet.</p>',
            headers=_CACHE_HEADERS,
        )

    cards = []
    for log in logs:
        reasoning = _trunc(log.get("reasoning") or log.get("output_summary") or "", 200)
        tokens = (log.get("input_tokens") or 0) + (log.get("output_tokens") or 0)
        cards.append(
            '<div class="glass-agent-card">'
            f'<strong>{_escape(log["agent_name"])}</strong>'
            f' <small>{_fmt_duration(log.get("duration_seconds"))} &middot; {_fmt_tokens(tokens)} tokens</small>'
            f'<p>{_escape(reasoning)}</p>'
            f'<small class="glass-ts">{log.get("created_at", "")}</small>'
            '</div>'
        )

    html = '<h3>Agent Activity</h3><div class="glass-feed">' + "".join(cards) + '</div>'
    return HTMLResponse(html, headers=_CACHE_HEADERS)


@router.get("/glass/api/runs", response_class=HTMLResponse)
async def glass_runs(request: Request):
    db = request.app.state.db

    runs = db.fetchall(
        "SELECT id, status, started_at, completed_at, posts_published,"
        " total_input_tokens, total_output_tokens,"
        " total_cache_creation_tokens, total_cache_read_tokens, errors"
        f" FROM pipeline_runs WHERE client_id IN ({_IN_CLAUSE})"
        " ORDER BY started_at DESC LIMIT 15",
        INTERNAL_IDS,
    )

    if not runs:
        return HTMLResponse(
            '<h3>Pipeline Runs</h3><p class="glass-empty">No pipeline runs yet.</p>',
            headers=_CACHE_HEADERS,
        )

    rows = []
    for r in runs:
        run_cost = _cost(
            r.get("total_input_tokens") or 0,
            r.get("total_output_tokens") or 0,
            r.get("total_cache_creation_tokens") or 0,
            r.get("total_cache_read_tokens") or 0,
        )
        duration = ""
        if r.get("started_at") and r.get("completed_at"):
            try:
                started = datetime.fromisoformat(str(r["started_at"]))
                completed = datetime.fromisoformat(str(r["completed_at"]))
                duration = _fmt_duration((completed - started).total_seconds())
            except (ValueError, TypeError):
                pass
        # Truncate error to first line
        errors = ""
        if r.get("errors"):
            err_text = str(r["errors"])
            first_line = err_text.split("\n")[0][:100]
            errors = f' <small title="{_escape(first_line)}">(error)</small>'

        total_tok = (r.get("total_input_tokens") or 0) + (r.get("total_output_tokens") or 0)
        rows.append(
            '<tr>'
            f'<td><code>{r["id"][:8]}</code></td>'
            f'<td>{_badge(r["status"])}{errors}</td>'
            f'<td>{r.get("posts_published") or 0}</td>'
            f'<td>{_fmt_tokens(total_tok)}</td>'
            f'<td>${run_cost:.4f}</td>'
            f'<td>{duration or "-"}</td>'
            f'<td><small>{r.get("started_at", "")}</small></td>'
            '</tr>'
        )

    html = (
        '<h3>Pipeline Runs</h3>'
        '<div style="overflow-x:auto">'
        '<table><thead><tr>'
        '<th>Run</th><th>Status</th><th>Posts</th><th>Tokens</th><th>Cost</th><th>Duration</th><th>Started</th>'
        '</tr></thead><tbody>'
        + "".join(rows)
        + '</tbody></table></div>'
    )
    return HTMLResponse(html, headers=_CACHE_HEADERS)


@router.get("/glass/api/posts", response_class=HTMLResponse)
async def glass_posts(request: Request):
    db = request.app.state.db

    posts = db.fetchall(
        "SELECT text, confidence, status, platform, published_at, bluesky_uri"
        f" FROM posts WHERE status='published' AND client_id IN ({_IN_CLAUSE})"
        " ORDER BY published_at DESC LIMIT 15",
        INTERNAL_IDS,
    )

    if not posts:
        return HTMLResponse(
            '<h3>Published Posts</h3><p class="glass-empty">No published posts yet.</p>',
            headers=_CACHE_HEADERS,
        )

    cards = []
    for p in posts:
        conf = p.get("confidence") or 0
        conf_pct = int(conf * 100)
        conf_color = "#4caf50" if conf >= 0.7 else "#ffb74d" if conf >= 0.4 else "#ef5350"
        platform = p.get("platform") or "generic"

        # Build link to the actual post
        link = ""
        if p.get("bluesky_uri"):
            link = f' <a href="https://bsky.app/profile/{_escape(p["bluesky_uri"])}" target="_blank" rel="noopener">view &rarr;</a>'

        cards.append(
            '<div class="glass-post-card">'
            f'<div class="glass-post-meta">'
            f'<span class="badge {platform}">{_escape(platform)}</span>'
            f' <span class="confidence-bar"><span class="fill" style="width:{conf_pct}%;background:{conf_color}"></span></span>'
            f' <small>{conf_pct}% confidence</small>'
            f'{link}'
            '</div>'
            f'<p>{_escape(_trunc(p["text"], 280))}</p>'
            f'<small class="glass-ts">{p.get("published_at", "")}</small>'
            '</div>'
        )

    html = '<h3>Published Posts</h3><div class="glass-feed">' + "".join(cards) + '</div>'
    return HTMLResponse(html, headers=_CACHE_HEADERS)
