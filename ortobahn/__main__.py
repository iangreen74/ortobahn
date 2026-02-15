"""CLI entry point: python -m ortobahn [command]"""

from __future__ import annotations

import argparse
import logging
import signal
import sys
import time

from rich.console import Console
from rich.logging import RichHandler
from rich.table import Table

console = Console()


def setup_logging(level: str = "INFO"):
    logging.basicConfig(
        level=level,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(console=console, rich_tracebacks=True)],
    )


def cmd_run(args):
    """Run a single pipeline cycle."""
    from ortobahn.config import load_settings
    from ortobahn.orchestrator import Pipeline

    settings = load_settings()
    setup_logging(settings.log_level)

    dry_run = args.dry_run

    # Validate config
    errors = settings.validate(require_bluesky=not dry_run and not args.generate_only)
    if errors:
        for err in errors:
            console.print(f"[red]Config error: {err}[/red]")
        console.print("\n[dim]Copy .env.example to .env and configure your API keys, or use --dry-run.[/dim]")
        sys.exit(1)

    if dry_run:
        console.print("[yellow]DRY RUN mode - posts will NOT be published[/yellow]")

    platforms = None
    if args.platforms:
        from ortobahn.models import Platform

        platforms = [Platform(p.strip()) for p in args.platforms.split(",")]

    pipeline = Pipeline(settings, dry_run=dry_run)
    try:
        console.print("\n[bold cyan]ORTOBAHN[/bold cyan] - Autonomous Marketing Engine")
        console.print("━" * 50)
        result = pipeline.run_cycle(
            client_id=args.client or settings.default_client_id,
            target_platforms=platforms,
            generate_only=True if args.generate_only else None,
        )
        console.print("━" * 50)
        console.print("[green]Cycle complete![/green]")
        console.print(f"  Posts published: {result['posts_published']}/{result['total_drafts']}")
        console.print(f"  Tokens used: {result['input_tokens']} in / {result['output_tokens']} out")
        if result["errors"]:
            console.print(f"  [yellow]Errors: {result['errors']}[/yellow]")
    finally:
        pipeline.close()


def cmd_generate(args):
    """Generate content for a client (no publishing)."""
    from ortobahn.config import load_settings
    from ortobahn.models import Platform
    from ortobahn.orchestrator import Pipeline

    settings = load_settings()
    setup_logging(settings.log_level)

    errors = settings.validate(require_bluesky=False)
    if errors:
        for err in errors:
            console.print(f"[red]Config error: {err}[/red]")
        sys.exit(1)

    client_id = args.client or settings.default_client_id
    platforms = None
    if args.platforms:
        platforms = [Platform(p.strip()) for p in args.platforms.split(",")]

    pipeline = Pipeline(settings, dry_run=True)
    try:
        console.print(f"\n[bold cyan]ORTOBAHN[/bold cyan] - Generating content for [green]{client_id}[/green]")
        if platforms:
            console.print(f"  Platforms: {', '.join(p.value for p in platforms)}")
        console.print("━" * 50)
        result = pipeline.run_cycle(
            client_id=client_id,
            target_platforms=platforms,
            generate_only=True,
        )
        console.print("━" * 50)
        console.print(f"[green]Generated {result['total_drafts']} drafts for review[/green]")
        console.print(f"  Tokens used: {result['input_tokens']} in / {result['output_tokens']} out")
    finally:
        pipeline.close()


def cmd_schedule(args):
    """Run pipeline on a schedule."""
    from ortobahn.config import load_settings
    from ortobahn.orchestrator import Pipeline

    settings = load_settings()
    setup_logging(settings.log_level)

    interval_hours = args.interval or settings.pipeline_interval_hours
    interval_seconds = interval_hours * 3600
    dry_run = args.dry_run

    console.print(f"\n[bold cyan]ORTOBAHN[/bold cyan] - Scheduled Mode (every {interval_hours}h)")
    if dry_run:
        console.print("[yellow]DRY RUN mode[/yellow]")

    # Graceful shutdown
    running = True

    def handle_signal(sig, frame):
        nonlocal running
        console.print("\n[yellow]Shutting down gracefully...[/yellow]")
        running = False

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    pipeline = Pipeline(settings, dry_run=dry_run)
    cycle_num = 0

    try:
        while running:
            cycle_num += 1
            console.print(f"\n[cyan]--- Cycle {cycle_num} ---[/cyan]")
            try:
                if args.client:
                    # Single-client mode (explicit --client flag)
                    clients_to_run = [{"id": args.client, "name": args.client}]
                else:
                    # All active, non-paused clients
                    rows = pipeline.db.conn.execute(
                        "SELECT id, name FROM clients WHERE active=1 AND status != 'paused' ORDER BY name"
                    ).fetchall()
                    clients_to_run = [dict(r) for r in rows] if rows else [{"id": settings.default_client_id, "name": settings.default_client_id}]

                total_published = 0
                for client in clients_to_run:
                    cid = client["id"]
                    console.print(f"  [dim]Running for client: {client.get('name', cid)}[/dim]")
                    try:
                        result = pipeline.run_cycle(client_id=cid)
                        total_published += result["posts_published"]
                        console.print(f"    Published {result['posts_published']} posts for {client.get('name', cid)}")
                    except Exception as e:
                        console.print(f"    [red]Failed for {client.get('name', cid)}: {e}[/red]")

                console.print(f"[green]Cycle complete: {total_published} total posts published[/green]")
            except Exception as e:
                console.print(f"[red]Cycle failed: {e}[/red]")

            if running:
                console.print(f"Next cycle in {interval_hours}h...")
                # Sleep in small increments so we can respond to signals
                for _ in range(int(interval_seconds)):
                    if not running:
                        break
                    time.sleep(1)
    finally:
        pipeline.close()


def cmd_dashboard(args):
    """Show the terminal dashboard."""
    from ortobahn.config import load_settings
    from ortobahn.dashboard.terminal import show_dashboard

    settings = load_settings()
    show_dashboard(settings.db_path)


def cmd_healthcheck(args):
    """Run health checks on all external dependencies."""
    from ortobahn.config import load_settings
    from ortobahn.healthcheck import run_all_checks

    settings = load_settings()
    setup_logging(settings.log_level)

    console.print("\n[bold cyan]ORTOBAHN HEALTH CHECK[/bold cyan]")
    console.print("━" * 40)

    results = run_all_checks(settings)
    all_ok = True

    for result in results:
        icon = "[green]PASS[/green]" if result.ok else "[red]FAIL[/red]"
        console.print(f"  {icon} {result.name}: {result.message}")
        if not result.ok:
            all_ok = False

    console.print("━" * 40)
    if all_ok:
        console.print("[green]All checks passed![/green]")
    else:
        console.print("[red]Some checks failed. Fix the issues above.[/red]")
        sys.exit(1)


def cmd_status(args):
    """Quick status check."""
    from ortobahn.config import load_settings
    from ortobahn.db import Database

    settings = load_settings()
    db = Database(settings.db_path)

    runs = db.get_recent_runs(limit=5)
    strategy = db.get_active_strategy()

    console.print("\n[bold cyan]ORTOBAHN STATUS[/bold cyan]")
    console.print("━" * 40)

    # Clients
    clients = db.get_all_clients()
    console.print(f"Clients: {', '.join(c['name'] for c in clients)}")

    if strategy:
        console.print(f"\n[green]Active strategy:[/green] {', '.join(strategy['themes'])}")
        console.print(f"  Valid until: {strategy['valid_until']}")
    else:
        console.print("\n[yellow]No active strategy[/yellow]")

    if runs:
        last = runs[0]
        console.print(f"\nLast run: {last['status']} ({last['started_at']})")
        console.print(f"  Posts published: {last['posts_published']}")
    else:
        console.print("\n[yellow]No pipeline runs yet[/yellow]")

    # Pending drafts
    drafts = db.get_drafts_for_review()
    if drafts:
        console.print(f"\n[yellow]{len(drafts)} drafts pending review[/yellow]")

    db.close()


def cmd_client_add(args):
    """Add a new client."""
    from ortobahn.config import load_settings
    from ortobahn.db import Database

    settings = load_settings()
    db = Database(settings.db_path)

    client_data = {
        "name": args.name,
        "description": args.description or "",
        "industry": args.industry or "",
        "target_audience": args.audience or "",
        "brand_voice": args.voice or "",
        "website": args.website or "",
    }
    if args.id:
        client_data["id"] = args.id

    cid = db.create_client(client_data)
    console.print(f"[green]Client created: {args.name} (id={cid})[/green]")
    db.close()


def cmd_client_list(args):
    """List all clients."""
    from ortobahn.config import load_settings
    from ortobahn.db import Database

    settings = load_settings()
    db = Database(settings.db_path)

    clients = db.get_all_clients()
    table = Table(title="Clients")
    table.add_column("ID")
    table.add_column("Name")
    table.add_column("Industry")
    table.add_column("Website")

    for c in clients:
        table.add_row(c["id"], c["name"], c["industry"], c["website"])

    console.print(table)
    db.close()


def cmd_seed(args):
    """Seed all known clients (Vaultscaler, Ortobahn)."""
    from ortobahn.config import load_settings
    from ortobahn.db import Database
    from ortobahn.seed import seed_all

    settings = load_settings()
    db = Database(settings.db_path)
    client_ids = seed_all(db, settings=settings)
    for cid in client_ids:
        client = db.get_client(cid)
        internal = client.get("internal") if client else False
        label = " (internal)" if internal else ""
        console.print(f"[green]Client ready (id={cid}){label}[/green]")
    db.close()


def cmd_review(args):
    """Review pending drafts."""
    from ortobahn.config import load_settings
    from ortobahn.db import Database

    settings = load_settings()
    db = Database(settings.db_path)

    drafts = db.get_drafts_for_review(
        client_id=args.client or None,
        platform=args.platform or None,
    )

    if not drafts:
        console.print("[yellow]No pending drafts[/yellow]")
        db.close()
        return

    table = Table(title=f"Pending Drafts ({len(drafts)})")
    table.add_column("ID", max_width=8)
    table.add_column("Platform")
    table.add_column("Type")
    table.add_column("Text", max_width=60)
    table.add_column("Confidence")

    for d in drafts:
        table.add_row(
            d["id"][:8],
            d.get("platform", "?"),
            d.get("content_type", "?"),
            d["text"][:60] + ("..." if len(d["text"]) > 60 else ""),
            f"{d['confidence']:.2f}" if d.get("confidence") else "?",
        )

    console.print(table)
    console.print("\n[dim]Approve: python -m ortobahn approve <post-id>[/dim]")
    db.close()


def cmd_approve(args):
    """Approve a draft post."""
    from ortobahn.config import load_settings
    from ortobahn.db import Database

    settings = load_settings()
    db = Database(settings.db_path)

    # Find post by prefix
    all_drafts = db.get_drafts_for_review()
    match = None
    for d in all_drafts:
        if d["id"].startswith(args.post_id):
            match = d
            break

    if not match:
        console.print(f"[red]No draft found matching '{args.post_id}'[/red]")
        db.close()
        sys.exit(1)

    db.approve_post(match["id"])
    console.print(f"[green]Approved: {match['text'][:60]}...[/green]")
    db.close()


def cmd_reject(args):
    """Reject a draft post."""
    from ortobahn.config import load_settings
    from ortobahn.db import Database

    settings = load_settings()
    db = Database(settings.db_path)

    all_drafts = db.get_drafts_for_review()
    match = None
    for d in all_drafts:
        if d["id"].startswith(args.post_id):
            match = d
            break

    if not match:
        console.print(f"[red]No draft found matching '{args.post_id}'[/red]")
        db.close()
        sys.exit(1)

    db.reject_post(match["id"])
    console.print(f"[yellow]Rejected: {match['text'][:60]}...[/yellow]")
    db.close()


def cmd_credentials(args):
    """Set platform credentials for a client."""
    from ortobahn.config import load_settings
    from ortobahn.credentials import save_platform_credentials
    from ortobahn.db import Database

    settings = load_settings()
    if not settings.secret_key:
        console.print("[red]ORTOBAHN_SECRET_KEY must be set for credential encryption[/red]")
        sys.exit(1)

    db = Database(settings.db_path)
    client = db.get_client(args.client)
    if not client:
        console.print(f"[red]Client '{args.client}' not found[/red]")
        db.close()
        sys.exit(1)

    creds = {}
    if args.platform == "bluesky":
        if not args.handle or not args.password:
            console.print("[red]Bluesky requires --handle and --password[/red]")
            db.close()
            sys.exit(1)
        creds = {"handle": args.handle, "app_password": args.password}
    elif args.platform == "twitter":
        if not all([args.api_key, args.api_secret, args.access_token, args.access_token_secret]):
            console.print("[red]Twitter requires --api-key, --api-secret, --access-token, --access-token-secret[/red]")
            db.close()
            sys.exit(1)
        creds = {
            "api_key": args.api_key,
            "api_secret": args.api_secret,
            "access_token": args.access_token,
            "access_token_secret": args.access_token_secret,
        }
    elif args.platform == "linkedin":
        if not args.access_token or not args.person_urn:
            console.print("[red]LinkedIn requires --access-token and --person-urn[/red]")
            db.close()
            sys.exit(1)
        creds = {"access_token": args.access_token, "person_urn": args.person_urn}

    save_platform_credentials(db, args.client, args.platform, creds, settings.secret_key)
    console.print(f"[green]Credentials saved for {args.client}/{args.platform}[/green]")
    db.close()


def cmd_api_key(args):
    """Create or list API keys."""
    from ortobahn.auth import generate_api_key, hash_api_key, key_prefix
    from ortobahn.config import load_settings
    from ortobahn.db import Database

    settings = load_settings()
    db = Database(settings.db_path)

    if args.apikey_action == "create":
        client = db.get_client(args.client)
        if not client:
            console.print(f"[red]Client '{args.client}' not found[/red]")
            db.close()
            sys.exit(1)
        raw_key = generate_api_key()
        hashed = hash_api_key(raw_key)
        prefix = key_prefix(raw_key)
        db.create_api_key(args.client, hashed, prefix, args.name)
        console.print(f"[green]API key created for {args.client}:[/green]")
        console.print(f"  [bold]{raw_key}[/bold]")
        console.print("[yellow]Save this key now — it cannot be retrieved again.[/yellow]")

    elif args.apikey_action == "list":
        keys = db.get_api_keys_for_client(args.client)
        if not keys:
            console.print(f"[yellow]No API keys for {args.client}[/yellow]")
        else:
            from rich.table import Table

            table = Table(title=f"API Keys for {args.client}")
            table.add_column("Prefix")
            table.add_column("Name")
            table.add_column("Created")
            table.add_column("Last Used")
            table.add_column("Active")
            for k in keys:
                table.add_row(
                    k["key_prefix"], k["name"],
                    k["created_at"] or "", k["last_used_at"] or "never",
                    "yes" if k["active"] else "no",
                )
            console.print(table)
    else:
        console.print("[red]Usage: ortobahn api-key create|list --client X[/red]")

    db.close()


def cmd_web(args):
    """Start the web dashboard."""
    from ortobahn.config import load_settings

    settings = load_settings()
    setup_logging(settings.log_level)

    try:
        import uvicorn

        from ortobahn.web.app import create_app  # noqa: F401
    except ImportError:
        console.print("[red]Web dependencies not installed. Run: pip install ortobahn[web][/red]")
        sys.exit(1)

    host = args.host or settings.web_host
    port = args.port or settings.web_port
    console.print(f"\n[bold cyan]ORTOBAHN[/bold cyan] - Web Dashboard at http://{host}:{port}")
    uvicorn.run("ortobahn.web.app:create_app", factory=True, host=host, port=port, reload=False)


def main():
    parser = argparse.ArgumentParser(
        prog="ortobahn",
        description="Ortobahn - Autonomous AI Marketing Engine by Vaultscaler",
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # run
    run_parser = subparsers.add_parser("run", help="Run a single pipeline cycle")
    run_parser.add_argument("--dry-run", action="store_true", help="Don't publish to platforms")
    run_parser.add_argument("--client", type=str, help="Client ID (default: from config)")
    run_parser.add_argument("--generate-only", action="store_true", help="Generate content only, no publishing")
    run_parser.add_argument("--platforms", type=str, help="Comma-separated platforms (bluesky,twitter,linkedin)")
    run_parser.set_defaults(func=cmd_run)

    # generate
    gen_parser = subparsers.add_parser("generate", help="Generate content for a client (no publishing)")
    gen_parser.add_argument("--client", type=str, help="Client ID (default: from config)")
    gen_parser.add_argument("--platforms", type=str, help="Comma-separated platforms (twitter,linkedin,google_ads)")
    gen_parser.set_defaults(func=cmd_generate)

    # schedule
    sched_parser = subparsers.add_parser("schedule", help="Run pipeline on a schedule")
    sched_parser.add_argument("--interval", type=float, help="Hours between cycles (default: 6)")
    sched_parser.add_argument("--dry-run", action="store_true", help="Don't publish to platforms")
    sched_parser.add_argument("--client", type=str, help="Client ID (default: from config)")
    sched_parser.set_defaults(func=cmd_schedule)

    # dashboard
    dash_parser = subparsers.add_parser("dashboard", help="Show terminal dashboard")
    dash_parser.set_defaults(func=cmd_dashboard)

    # healthcheck
    health_parser = subparsers.add_parser("healthcheck", help="Run health checks on all dependencies")
    health_parser.set_defaults(func=cmd_healthcheck)

    # status
    status_parser = subparsers.add_parser("status", help="Quick status check")
    status_parser.set_defaults(func=cmd_status)

    # client add
    client_add_parser = subparsers.add_parser("client-add", help="Add a new client")
    client_add_parser.add_argument("name", help="Client name")
    client_add_parser.add_argument("--id", type=str, help="Custom client ID")
    client_add_parser.add_argument("--description", type=str, help="Company description")
    client_add_parser.add_argument("--industry", type=str, help="Industry")
    client_add_parser.add_argument("--audience", type=str, help="Target audience")
    client_add_parser.add_argument("--voice", type=str, help="Brand voice")
    client_add_parser.add_argument("--website", type=str, help="Website URL")
    client_add_parser.set_defaults(func=cmd_client_add)

    # client list
    client_list_parser = subparsers.add_parser("client-list", help="List all clients")
    client_list_parser.set_defaults(func=cmd_client_list)

    # seed
    seed_parser = subparsers.add_parser("seed", help="Seed the Vaultscaler client")
    seed_parser.set_defaults(func=cmd_seed)

    # review
    review_parser = subparsers.add_parser("review", help="Review pending drafts")
    review_parser.add_argument("--client", type=str, help="Filter by client ID")
    review_parser.add_argument("--platform", type=str, help="Filter by platform")
    review_parser.set_defaults(func=cmd_review)

    # approve
    approve_parser = subparsers.add_parser("approve", help="Approve a draft post")
    approve_parser.add_argument("post_id", help="Post ID (or prefix)")
    approve_parser.set_defaults(func=cmd_approve)

    # reject
    reject_parser = subparsers.add_parser("reject", help="Reject a draft post")
    reject_parser.add_argument("post_id", help="Post ID (or prefix)")
    reject_parser.set_defaults(func=cmd_reject)

    # credentials
    creds_parser = subparsers.add_parser("credentials", help="Manage per-tenant platform credentials")
    creds_sub = creds_parser.add_subparsers(dest="creds_action")
    set_parser = creds_sub.add_parser("set", help="Set credentials for a client+platform")
    set_parser.add_argument("--client", required=True, help="Client ID")
    set_parser.add_argument("--platform", required=True, choices=["bluesky", "twitter", "linkedin"])
    set_parser.add_argument("--handle", help="Bluesky handle")
    set_parser.add_argument("--password", help="Bluesky app password")
    set_parser.add_argument("--api-key", help="Twitter API key")
    set_parser.add_argument("--api-secret", help="Twitter API secret")
    set_parser.add_argument("--access-token", help="Access token (Twitter or LinkedIn)")
    set_parser.add_argument("--access-token-secret", help="Twitter access token secret")
    set_parser.add_argument("--person-urn", help="LinkedIn person URN")
    creds_parser.set_defaults(func=cmd_credentials)

    # api-key
    apikey_parser = subparsers.add_parser("api-key", help="Manage API keys")
    apikey_sub = apikey_parser.add_subparsers(dest="apikey_action")
    ak_create = apikey_sub.add_parser("create", help="Create API key for a client")
    ak_create.add_argument("--client", required=True, help="Client ID")
    ak_create.add_argument("--name", default="default", help="Key name/label")
    ak_list = apikey_sub.add_parser("list", help="List API keys for a client")
    ak_list.add_argument("--client", required=True, help="Client ID")
    apikey_parser.set_defaults(func=cmd_api_key)

    # web
    web_parser = subparsers.add_parser("web", help="Start the web dashboard")
    web_parser.add_argument("--host", type=str, help="Host to bind to")
    web_parser.add_argument("--port", type=int, help="Port to bind to")
    web_parser.set_defaults(func=cmd_web)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    args.func(args)


if __name__ == "__main__":
    main()
