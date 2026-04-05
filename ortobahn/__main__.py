from __future__ import annotations

import sys
from pathlib import Path

import click


@click.group()
@click.version_option()
def cli() -> None:
    """Ortobahn - AI-powered workflow automation platform."""
    pass


@cli.command()
@click.option(
    "--host",
    default="127.0.0.1",
    help="Host to bind the MCP server to.",
    show_default=True,
)
@click.option(
    "--port",
    default=8000,
    type=int,
    help="Port to bind the MCP server to.",
    show_default=True,
)
@click.option(
    "--log-level",
    default="INFO",
    type=click.Choice(["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"], case_sensitive=False),
    help="Logging level.",
    show_default=True,
)
def mcp_server(host: str, port: int, log_level: str) -> None:
    """Start the MCP (Model Context Protocol) server."""
    click.echo(f"Starting MCP server on {host}:{port} with log level {log_level}")
    try:
        from ortobahn.mcp.server import start_server
        start_server(host=host, port=port, log_level=log_level)
    except ImportError:
        click.echo("Error: MCP server module not found. Please implement ortobahn/mcp/server.py", err=True)
        sys.exit(1)


@cli.command()
@click.option(
    "--host",
    default="0.0.0.0",
    help="Host to bind the web application to.",
    show_default=True,
)
@click.option(
    "--port",
    default=8080,
    type=int,
    help="Port to bind the web application to.",
    show_default=True,
)
@click.option(
    "--reload",
    is_flag=True,
    default=False,
    help="Enable auto-reload for development.",
)
def web(host: str, port: int, reload: bool) -> None:
    """Start the web application."""
    click.echo(f"Starting web application on {host}:{port}")
    try:
        from ortobahn.web.app import start_app
        start_app(host=host, port=port, reload=reload)
    except ImportError:
        click.echo("Error: Web application module not found. Please implement ortobahn/web/app.py", err=True)
        sys.exit(1)


@cli.command()
@click.argument("agent_name", required=False)
@click.option(
    "--config",
    type=click.Path(exists=True, path_type=Path),
    help="Path to agent configuration file.",
)
@click.option(
    "--list",
    "list_agents",
    is_flag=True,
    default=False,
    help="List available agents.",
)
def agent(agent_name: str | None, config: Path | None, list_agents: bool) -> None:
    """Run an AI agent."""
    if list_agents:
        click.echo("Available agents:")
        try:
            from ortobahn.agents import list_available_agents
            for name in list_available_agents():
                click.echo(f"  - {name}")
        except ImportError:
            click.echo("Error: Agents module not found. Please implement ortobahn/agents/__init__.py", err=True)
            sys.exit(1)
        return

    if not agent_name:
        click.echo("Error: agent_name is required unless --list is specified.", err=True)
        sys.exit(1)

    click.echo(f"Running agent: {agent_name}")
    if config:
        click.echo(f"Using configuration from: {config}")

    try:
        from ortobahn.agents import run_agent
        run_agent(agent_name=agent_name, config_path=config)
    except ImportError:
        click.echo("Error: Agents module not found. Please implement ortobahn/agents/__init__.py", err=True)
        sys.exit(1)


@cli.command()
def dashboard() -> None:
    """Start the monitoring dashboard."""
    click.echo("Starting monitoring dashboard...")
    try:
        from ortobahn.dashboard.app import start_dashboard
        start_dashboard()
    except ImportError:
        click.echo("Error: Dashboard module not found. Please implement ortobahn/dashboard/app.py", err=True)
        sys.exit(1)


if __name__ == "__main__":
    cli()
