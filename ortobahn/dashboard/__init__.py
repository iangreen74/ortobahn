"""Dashboard module for Ortobahn.

Provides WebSocket endpoints and static file serving for the real-time dashboard.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel


class AgentStatus(BaseModel):
    """Agent status data model."""

    agent_id: str
    status: str
    current_task: str | None = None
    tokens_used: int = 0
    requests_count: int = 0


class CampaignMetrics(BaseModel):
    """Campaign metrics data model."""

    campaign_id: str
    total_agents: int
    active_agents: int
    completed_tasks: int
    total_tasks: int
    success_rate: float


class MemoryVisualization(BaseModel):
    """Memory visualization data model."""

    agent_id: str
    memory_type: str
    content: dict[str, Any]
    timestamp: str


class DashboardUpdate(BaseModel):
    """Dashboard update message."""

    update_type: str
    data: AgentStatus | CampaignMetrics | MemoryVisualization


class ConnectionManager:
    """Manages WebSocket connections for real-time updates."""

    def __init__(self) -> None:
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket) -> None:
        """Accept and register a WebSocket connection."""
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket) -> None:
        """Remove a WebSocket connection."""
        self.active_connections.remove(websocket)

    async def broadcast(self, message: dict[str, Any]) -> None:
        """Broadcast message to all connected clients."""
        dead_connections = []
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except Exception:
                dead_connections.append(connection)

        for conn in dead_connections:
            self.active_connections.remove(conn)


manager = ConnectionManager()
router = APIRouter(prefix="/dashboard", tags=["dashboard"])

# Static files directory
STATIC_DIR = Path(__file__).parent / "static"
STATIC_DIR.mkdir(exist_ok=True)


@router.get("/", response_class=HTMLResponse)
async def dashboard_root() -> HTMLResponse:
    """Serve the dashboard HTML page."""
    html_file = STATIC_DIR / "index.html"
    if html_file.exists():
        return HTMLResponse(content=html_file.read_text())
    # Fallback minimal HTML
    return HTMLResponse(
        content="""
        <!DOCTYPE html>
        <html><head><title>Ortobahn Dashboard</title></head>
        <body><div id="app">Loading dashboard...</div>
        <script>const ws = new WebSocket('ws://localhost:8000/dashboard/ws');
        ws.onmessage = (e) => console.log('Update:', JSON.parse(e.data));</script>
        </body></html>
        """
    )


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    """WebSocket endpoint for real-time dashboard updates."""
    await manager.connect(websocket)
    try:
        while True:
            # Keep connection alive and handle incoming messages
            data = await websocket.receive_text()
            # Echo back for heartbeat
            await websocket.send_json({"type": "pong", "data": data})
    except WebSocketDisconnect:
        manager.disconnect(websocket)


async def send_agent_status_update(agent_status: AgentStatus) -> None:
    """Send agent status update to all connected clients."""
    update = DashboardUpdate(update_type="agent_status", data=agent_status)
    await manager.broadcast(update.model_dump())


async def send_campaign_metrics_update(metrics: CampaignMetrics) -> None:
    """Send campaign metrics update to all connected clients."""
    update = DashboardUpdate(update_type="campaign_metrics", data=metrics)
    await manager.broadcast(update.model_dump())


async def send_memory_visualization_update(memory: MemoryVisualization) -> None:
    """Send memory visualization update to all connected clients."""
    update = DashboardUpdate(update_type="memory_visualization", data=memory)
    await manager.broadcast(update.model_dump())
