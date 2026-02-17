"""Rich-based terminal dashboard."""

from __future__ import annotations

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from ortobahn.db import Database


def show_dashboard(db: Database):
    console = Console()

    console.print()
    console.print(
        Panel.fit(
            "[bold cyan]ORTOBAHN[/bold cyan] - Autonomous Marketing Engine\n[dim]A Vaultscaler subsidiary[/dim]",
            border_style="cyan",
        )
    )

    # --- Active Strategy ---
    strategy = db.get_active_strategy()
    if strategy:
        strat_text = Text()
        strat_text.append("Themes: ", style="bold")
        strat_text.append(", ".join(strategy["themes"]))
        strat_text.append("\nTone: ", style="bold")
        strat_text.append(strategy["tone"])
        strat_text.append("\nGoals: ", style="bold")
        strat_text.append(", ".join(strategy["goals"]))
        strat_text.append("\nValid until: ", style="bold")
        strat_text.append(strategy["valid_until"])
        console.print(Panel(strat_text, title="Current Strategy", border_style="green"))
    else:
        console.print(Panel("[yellow]No active strategy - run a pipeline cycle first[/yellow]", title="Strategy"))

    # --- Recent Posts ---
    posts = db.get_recent_posts_with_metrics(limit=10)
    if posts:
        table = Table(title="Recent Posts", show_lines=True)
        table.add_column("Post", max_width=50)
        table.add_column("Status", width=10)
        table.add_column("Likes", width=6, justify="right")
        table.add_column("Reposts", width=8, justify="right")
        table.add_column("Replies", width=8, justify="right")
        table.add_column("Published", width=20)

        for p in posts:
            status_style = "green" if p["status"] == "published" else "yellow"
            table.add_row(
                p["text"][:50] + ("..." if len(p["text"]) > 50 else ""),
                f"[{status_style}]{p['status']}[/{status_style}]",
                str(p.get("like_count") or 0),
                str(p.get("repost_count") or 0),
                str(p.get("reply_count") or 0),
                str(p.get("published_at") or "—"),
            )
        console.print(table)
    else:
        console.print(Panel("[yellow]No posts yet[/yellow]", title="Posts"))

    # --- Pipeline Runs ---
    runs = db.get_recent_runs(limit=5)
    if runs:
        table = Table(title="Pipeline Runs")
        table.add_column("Run ID", width=10)
        table.add_column("Status", width=12)
        table.add_column("Posts", width=6, justify="right")
        table.add_column("Tokens (in/out)", width=16)
        table.add_column("Started", width=20)

        for r in runs:
            status_style = "green" if r["status"] == "completed" else "red"
            table.add_row(
                r["id"][:8],
                f"[{status_style}]{r['status']}[/{status_style}]",
                str(r["posts_published"]),
                f"{r.get('total_input_tokens', 0)}/{r.get('total_output_tokens', 0)}",
                str(r["started_at"]),
            )
        console.print(table)

    # --- Agent Logs ---
    logs = db.get_recent_agent_logs(limit=10)
    if logs:
        table = Table(title="Agent Activity Log")
        table.add_column("Agent", width=12)
        table.add_column("Action", max_width=60)
        table.add_column("Tokens", width=12)
        table.add_column("Time", width=20)

        for log in logs:
            table.add_row(
                f"[cyan]{log['agent_name']}[/cyan]",
                log.get("output_summary", "")[:60],
                f"{log.get('input_tokens', 0)}/{log.get('output_tokens', 0)}",
                str(log["created_at"]),
            )
        console.print(table)

    console.print()
    db.close()
