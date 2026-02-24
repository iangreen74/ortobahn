"""MCP server exposing ortobahn platform capabilities to AI assistants."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

mcp = FastMCP(
    "ortobahn",
    instructions="Ortobahn autonomous marketing engine — query analytics, manage content, trigger pipelines",
)

_db = None
_settings = None


def _get_db():
    """Lazily initialize and return the database connection."""
    global _db, _settings
    if _db is None:
        from ortobahn.config import load_settings
        from ortobahn.db import create_database

        _settings = load_settings()
        _db = create_database(_settings)
    return _db


# ═══════════════════════════════════════════════════════════════════════
# Analytics & Monitoring (5 read-only tools)
# ═══════════════════════════════════════════════════════════════════════


@mcp.tool()
def get_analytics(client_id: str | None = None) -> str:
    """Get engagement analytics for the last 7 days.

    Returns total posts, likes, reposts, replies, average engagement,
    and the best/worst performing posts.
    """
    try:
        db = _get_db()
        report = db.build_analytics_report(client_id=client_id)
        lines = [
            f"Analytics Report ({report.period})",
            f"  Total posts: {report.total_posts}",
            f"  Total likes: {report.total_likes}",
            f"  Total reposts: {report.total_reposts}",
            f"  Total replies: {report.total_replies}",
            f"  Avg engagement/post: {report.avg_engagement_per_post:.2f}",
        ]
        if report.best_post:
            lines.append(
                f"  Best post ({report.best_post.total_engagement} engagements): {report.best_post.text[:120]}..."
            )
        if report.worst_post:
            lines.append(
                f"  Worst post ({report.worst_post.total_engagement} engagements): {report.worst_post.text[:120]}..."
            )
        return "\n".join(lines)
    except Exception as e:
        return f"Error fetching analytics: {e}"


@mcp.tool()
def get_pipeline_status(limit: int = 10) -> str:
    """Get recent pipeline run statuses and detect stale runs.

    Shows the most recent pipeline runs with their status, timing,
    and post counts.  Warns about any runs stuck in 'running' state.
    """
    try:
        db = _get_db()
        stale = db.get_stale_runs(timeout_minutes=60)
        recent = db.get_recent_runs(limit=limit)

        lines = []
        if stale:
            lines.append(f"WARNING: {len(stale)} stale pipeline run(s) detected (running > 60 min):")
            for run in stale:
                lines.append(f"  - {run['id'][:8]} started {run['started_at']} (client: {run.get('client_id', '?')})")
            lines.append("")

        if recent:
            lines.append(f"Recent runs (last {limit}):")
            for run in recent:
                lines.append(
                    f"  {run['id'][:8]} | {run['status']:10s} | started {run['started_at']} | "
                    f"{run.get('posts_published', 0)} posts | client: {run.get('client_id', '?')}"
                )
        else:
            lines.append("No pipeline runs found.")
        return "\n".join(lines)
    except Exception as e:
        return f"Error fetching pipeline status: {e}"


@mcp.tool()
def get_system_health() -> str:
    """Get database and system health metrics.

    Returns table row counts, database size, connection pool stats,
    and slow query counts.
    """
    try:
        db = _get_db()
        metrics = db.get_health_metrics()
        lines = [
            "System Health Report",
            f"  Collected at: {metrics.get('collected_at', 'unknown')}",
            "",
            "  Table row counts:",
        ]
        for table, count in metrics.get("table_row_counts", {}).items():
            lines.append(f"    {table}: {count}")

        if "db_size_bytes" in metrics:
            size_mb = metrics["db_size_bytes"] / (1024 * 1024)
            lines.append(f"\n  Database size: {size_mb:.2f} MB")

        if "pool_stats" in metrics:
            pool = metrics["pool_stats"]
            lines.append(f"\n  Pool: {pool.get('checked_out', 0)} checked out / {pool.get('max_connections', 0)} max")

        lines.append(f"\n  Slow queries: {metrics.get('slow_query_count', 0)}")
        return "\n".join(lines)
    except Exception as e:
        return f"Error fetching system health: {e}"


@mcp.tool()
def get_agent_logs(limit: int = 20) -> str:
    """Get recent agent execution logs.

    Shows agent name, token usage, duration, and output summary
    for recent agent runs.
    """
    try:
        db = _get_db()
        logs = db.get_recent_agent_logs(limit=limit)
        if not logs:
            return "No agent logs found."

        lines = [f"Recent agent logs (last {limit}):"]
        for log in logs:
            lines.append(
                f"  {log.get('agent_name', '?'):15s} | run {log.get('run_id', '?')[:8]} | "
                f"in={log.get('input_tokens', 0):6d} out={log.get('output_tokens', 0):6d} | "
                f"{log.get('duration_seconds', 0):.1f}s | {log.get('created_at', '')}"
            )
            summary = log.get("output_summary", "")
            if summary:
                lines.append(f"    -> {summary[:100]}")
        return "\n".join(lines)
    except Exception as e:
        return f"Error fetching agent logs: {e}"


@mcp.tool()
def get_monthly_spend(client_id: str) -> str:
    """Get the current month's API spend for a client.

    Returns the estimated dollar cost based on Anthropic token pricing.
    """
    try:
        db = _get_db()
        spend = db.get_current_month_spend(client_id)
        return f"Current month spend for {client_id}: ${spend:.4f}"
    except Exception as e:
        return f"Error fetching monthly spend: {e}"


# ═══════════════════════════════════════════════════════════════════════
# Content Management (6 tools)
# ═══════════════════════════════════════════════════════════════════════


@mcp.tool()
def list_draft_posts(client_id: str | None = None) -> str:
    """List all draft posts awaiting review.

    Shows post ID, truncated text, confidence score, and target platform.
    """
    try:
        db = _get_db()
        drafts = db.get_drafts_for_review(client_id=client_id)
        if not drafts:
            return "No draft posts found."

        lines = [f"Draft posts ({len(drafts)}):"]
        for d in drafts:
            text_preview = d["text"][:80].replace("\n", " ")
            lines.append(
                f"  {d['id'][:8]} | {d.get('confidence', 0):.2f} | {d.get('platform', 'generic'):10s} | "
                f"{text_preview}..."
            )
        return "\n".join(lines)
    except Exception as e:
        return f"Error listing draft posts: {e}"


@mcp.tool()
def get_post(post_id: str) -> str:
    """Get full details of a specific post by ID.

    Returns all fields including text, status, confidence, reasoning,
    and publication details.
    """
    try:
        db = _get_db()
        post = db.get_post(post_id)
        if not post:
            return f"Post {post_id} not found."

        lines = [
            f"Post {post['id']}",
            f"  Status: {post.get('status', '?')}",
            f"  Platform: {post.get('platform', '?')}",
            f"  Content type: {post.get('content_type', '?')}",
            f"  Client: {post.get('client_id', '?')}",
            f"  Confidence: {post.get('confidence', 0):.2f}",
            f"  Created: {post.get('created_at', '?')}",
            f"  Text: {post['text']}",
        ]
        if post.get("reasoning"):
            lines.append(f"  Reasoning: {post['reasoning']}")
        if post.get("bluesky_uri"):
            lines.append(f"  Bluesky URI: {post['bluesky_uri']}")
        if post.get("published_at"):
            lines.append(f"  Published at: {post['published_at']}")
        return "\n".join(lines)
    except Exception as e:
        return f"Error fetching post: {e}"


@mcp.tool()
def list_recent_posts(client_id: str | None = None, limit: int = 20) -> str:
    """List recent published/failed posts with engagement metrics.

    Shows post status, engagement totals, and truncated text.
    """
    try:
        db = _get_db()
        posts = db.get_recent_posts_with_metrics(limit=limit, client_id=client_id)
        if not posts:
            return "No recent posts found."

        lines = [f"Recent posts ({len(posts)}):"]
        for p in posts:
            engagement = (
                (p.get("like_count", 0) or 0) + (p.get("repost_count", 0) or 0) + (p.get("reply_count", 0) or 0)
            )
            text_preview = p["text"][:60].replace("\n", " ")
            lines.append(
                f"  {p['id'][:8]} | {p.get('status', '?'):10s} | {p.get('platform', '?'):10s} | "
                f"eng={engagement:3d} | {text_preview}..."
            )
        return "\n".join(lines)
    except Exception as e:
        return f"Error listing recent posts: {e}"


@mcp.tool()
def approve_post(post_id: str) -> str:
    """Approve a draft post for publishing.

    The post must exist and be in 'draft' status.  Once approved,
    it will be published in the next pipeline cycle.
    """
    try:
        db = _get_db()
        post = db.get_post(post_id)
        if not post:
            return f"Post {post_id} not found."
        if post.get("status") != "draft":
            return f"Cannot approve post {post_id}: status is '{post.get('status')}', expected 'draft'."
        db.approve_post(post_id)
        return f"Post {post_id[:8]} approved successfully."
    except Exception as e:
        return f"Error approving post: {e}"


@mcp.tool()
def reject_post(post_id: str, reason: str = "") -> str:
    """Reject a draft post.

    The post must exist and be in 'draft' status.
    Optionally provide a reason for the rejection.
    """
    try:
        db = _get_db()
        post = db.get_post(post_id)
        if not post:
            return f"Post {post_id} not found."
        if post.get("status") != "draft":
            return f"Cannot reject post {post_id}: status is '{post.get('status')}', expected 'draft'."
        db.reject_post(post_id)
        msg = f"Post {post_id[:8]} rejected."
        if reason:
            msg += f" Reason: {reason}"
        return msg
    except Exception as e:
        return f"Error rejecting post: {e}"


@mcp.tool()
def list_articles(client_id: str, limit: int = 10) -> str:
    """List recent articles for a client.

    Shows article title, status, word count, and tags.
    """
    try:
        db = _get_db()
        articles = db.get_recent_articles(client_id, limit=limit)
        if not articles:
            return "No articles found."

        lines = [f"Articles for {client_id} ({len(articles)}):"]
        for a in articles:
            tags = a.get("tags", [])
            if isinstance(tags, str):
                tags = tags
            else:
                tags = ", ".join(tags) if tags else ""
            lines.append(
                f"  {a['id'][:8]} | {a.get('status', '?'):10s} | {a.get('word_count', 0):5d}w | "
                f"{a.get('title', '?')[:60]}"
            )
            if tags:
                lines.append(f"    tags: {tags}")
        return "\n".join(lines)
    except Exception as e:
        return f"Error listing articles: {e}"


# ═══════════════════════════════════════════════════════════════════════
# Client Management (3 read-only tools)
# ═══════════════════════════════════════════════════════════════════════


@mcp.tool()
def list_clients() -> str:
    """List all active clients.

    Shows client ID, name, industry, and subscription status.
    """
    try:
        db = _get_db()
        clients = db.get_all_clients()
        if not clients:
            return "No clients found."

        lines = [f"Clients ({len(clients)}):"]
        for c in clients:
            lines.append(
                f"  {c['id'][:20]:20s} | {c.get('name', '?'):30s} | "
                f"{c.get('industry', '?'):15s} | {c.get('subscription_status', '?')}"
            )
        return "\n".join(lines)
    except Exception as e:
        return f"Error listing clients: {e}"


@mcp.tool()
def get_client(client_id: str) -> str:
    """Get full details of a specific client.

    Returns all client fields including brand voice, target audience,
    products, content pillars, and subscription info.
    """
    try:
        db = _get_db()
        client = db.get_client(client_id)
        if not client:
            return f"Client {client_id} not found."

        lines = [
            f"Client: {client.get('name', '?')}",
            f"  ID: {client['id']}",
            f"  Industry: {client.get('industry', '')}",
            f"  Target audience: {client.get('target_audience', '')}",
            f"  Brand voice: {client.get('brand_voice', '')}",
            f"  Website: {client.get('website', '')}",
            f"  Active: {client.get('active', '?')}",
            f"  Subscription: {client.get('subscription_status', '?')}",
            f"  Auto-publish: {client.get('auto_publish', False)}",
        ]
        if client.get("products"):
            lines.append(f"  Products: {client['products']}")
        if client.get("content_pillars"):
            lines.append(f"  Content pillars: {client['content_pillars']}")
        return "\n".join(lines)
    except Exception as e:
        return f"Error fetching client: {e}"


@mcp.tool()
def get_client_strategy(client_id: str) -> str:
    """Get the active content strategy for a client.

    Shows themes, tone, goals, guidelines, posting frequency,
    and strategy validity period.
    """
    try:
        db = _get_db()
        strategy = db.get_active_strategy(client_id=client_id)
        if not strategy:
            return f"No active strategy for client {client_id}."

        lines = [
            f"Active strategy for {client_id}:",
            f"  Themes: {', '.join(strategy.get('themes', []))}",
            f"  Tone: {strategy.get('tone', '?')}",
            f"  Goals: {', '.join(strategy.get('goals', []))}",
            f"  Guidelines: {strategy.get('content_guidelines', '')}",
            f"  Posting frequency: {strategy.get('posting_frequency', '?')}",
            f"  Valid until: {strategy.get('valid_until', '?')}",
        ]
        return "\n".join(lines)
    except Exception as e:
        return f"Error fetching client strategy: {e}"


# ═══════════════════════════════════════════════════════════════════════
# Operations (1 write tool)
# ═══════════════════════════════════════════════════════════════════════


@mcp.tool()
def trigger_pipeline(client_id: str) -> str:
    """Trigger a full pipeline cycle for a client.

    WARNING: This consumes API tokens (Anthropic Claude calls).
    Validates the client exists and is active before running.
    Returns the number of drafts generated, posts published,
    and tokens consumed.
    """
    pipeline = None
    try:
        db = _get_db()
        client = db.get_client(client_id)
        if not client:
            return f"Client {client_id} not found."
        if not client.get("active", False):
            return f"Client {client_id} is not active."

        from ortobahn.config import load_settings
        from ortobahn.orchestrator import Pipeline

        settings = _settings or load_settings()
        pipeline = Pipeline(settings, dry_run=False)
        result = pipeline.run_cycle(client_id=client_id)
        return (
            f"Pipeline completed for {client_id}:\n"
            f"  Run ID: {result['run_id'][:8]}\n"
            f"  Drafts generated: {result.get('total_drafts', 0)}\n"
            f"  Posts published: {result.get('posts_published', 0)}\n"
            f"  Input tokens: {result.get('input_tokens', 0)}\n"
            f"  Output tokens: {result.get('output_tokens', 0)}\n"
            f"  Errors: {result.get('errors', [])}"
        )
    except Exception as e:
        return f"Error triggering pipeline: {e}"
    finally:
        if pipeline is not None:
            pipeline.close()


# ═══════════════════════════════════════════════════════════════════════
# Entry point
# ═══════════════════════════════════════════════════════════════════════


def main():
    """Entry point for the ortobahn-mcp console script."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
