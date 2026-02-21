"""Chat routes -- AI support chatbot for tenant dashboard."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse

from ortobahn.auth import AuthClient
from ortobahn.llm import call_llm

logger = logging.getLogger("ortobahn.web.chat")

router = APIRouter(prefix="/my/chat")


def _escape(text: str) -> str:
    """Minimal HTML escaping."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _build_system_prompt(db, client_id: str) -> str:
    """Build a context-rich system prompt for the support chatbot."""
    client = db.get_client(client_id)
    if not client:
        return "You are a helpful support assistant for Ortobahn, an AI marketing platform."

    # Recent posts summary
    recent_posts = db.get_recent_posts_with_metrics(limit=5, client_id=client_id)
    posts_lines = []
    for p in recent_posts:
        posts_lines.append(
            f"- [{p['status']}] {p['text'][:80]}... "
            f"(likes: {p.get('like_count', 0)}, reposts: {p.get('repost_count', 0)})"
        )
    posts_summary = "\n".join(posts_lines) if posts_lines else "No recent posts"

    # Pipeline status
    last_run = db.fetchone(
        "SELECT status, completed_at, posts_published FROM pipeline_runs"
        " WHERE client_id=? ORDER BY started_at DESC LIMIT 1",
        (client_id,),
    )
    if last_run:
        pipeline_status = (
            f"Last run: {last_run['status']}, "
            f"published {last_run.get('posts_published', 0)} posts, "
            f"completed at {last_run.get('completed_at', 'unknown')}"
        )
    else:
        pipeline_status = "No runs yet"

    # Active strategy
    strategy = db.get_active_strategy(client_id=client_id)
    if strategy:
        themes = strategy.get("themes", [])
        if isinstance(themes, str):
            themes = [themes]
        strategy_summary = (
            f"Themes: {', '.join(themes)}, "
            f"Tone: {strategy.get('tone', 'unknown')}, "
            f"Frequency: {strategy.get('posting_frequency', 'unknown')}"
        )
    else:
        strategy_summary = "No active strategy"

    # Connected platforms
    platforms = []
    for platform in ("bluesky", "twitter", "linkedin"):
        row = db.fetchone(
            "SELECT id FROM platform_credentials WHERE client_id=? AND platform=?",
            (client_id, platform),
        )
        if row:
            platforms.append(platform)

    return f"""You are a helpful support assistant for Ortobahn, an autonomous AI marketing platform.
You are chatting with the team at {client.get("name", "this company")}.

## Client Context
- Company: {client.get("name", "Unknown")}
- Industry: {client.get("industry", "Not set")}
- Target audience: {client.get("target_audience", "Not set")}
- Brand voice: {client.get("brand_voice", "Not set")}
- Subscription: {client.get("subscription_status", "none")}
- Auto-publish: {"enabled" if client.get("auto_publish") else "disabled"}
- Connected platforms: {", ".join(platforms) if platforms else "None"}

## Current Strategy
{strategy_summary}

## Pipeline Status
{pipeline_status}

## Recent Posts
{posts_summary}

## Your Role
- Answer questions about their account, content performance, and how Ortobahn works.
- Help troubleshoot issues (missing credentials, failed pipelines, content quality).
- Suggest improvements to their profile, strategy, or platform connections.
- Be concise and helpful. Use the client context above to personalize answers.
- If you don't know something specific about their account, say so honestly.
- Do NOT make up data. Only reference information from the context above.
- Keep responses under 200 words."""


def _render_message(role: str, content: str) -> str:
    """Render a single chat message as HTML."""
    css_class = "chat-msg-user" if role == "user" else "chat-msg-assistant"
    label = "You" if role == "user" else "Ortobahn"
    return (
        f'<div class="chat-msg {css_class}">'
        f'<div class="chat-msg-label">{label}</div>'
        f'<div class="chat-msg-content">{_escape(content)}</div>'
        f"</div>"
    )


@router.get("/history", response_class=HTMLResponse)
async def chat_history(request: Request, client: AuthClient):
    """Load chat history for the widget."""
    db = request.app.state.db
    messages = db.get_chat_history(client["id"], limit=20)

    if not messages:
        return HTMLResponse(
            '<div class="chat-msg chat-msg-assistant">'
            '<div class="chat-msg-label">Ortobahn</div>'
            '<div class="chat-msg-content">Hi! I\'m your Ortobahn support assistant. '
            "How can I help you today?</div></div>"
        )

    html_parts = [_render_message(m["role"], m["content"]) for m in messages]
    return HTMLResponse("".join(html_parts))


@router.post("/send", response_class=HTMLResponse)
async def chat_send(request: Request, client: AuthClient, message: str = Form(...)):
    """Send a message and get AI response. Returns full updated history."""
    db = request.app.state.db
    settings = request.app.state.settings

    if not message.strip():
        return HTMLResponse("")

    # Save user message
    db.save_chat_message(client["id"], "user", message.strip())

    # Build conversation context (last 10 messages for LLM)
    history = db.get_chat_history(client["id"], limit=10)
    conversation = "\n\n".join(f"{'User' if m['role'] == 'user' else 'Assistant'}: {m['content']}" for m in history)

    # Build system prompt with client context
    system_prompt = _build_system_prompt(db, client["id"])

    # Call LLM
    try:
        llm_response = call_llm(
            system_prompt=system_prompt,
            user_message=conversation,
            model=settings.claude_model,
            max_tokens=1024,
            api_key=settings.anthropic_api_key,
            use_bedrock=settings.use_bedrock,
            bedrock_region=settings.bedrock_region,
        )
        assistant_content = llm_response.text
    except Exception as e:
        logger.error(f"Chat LLM error for {client['id']}: {e}")
        assistant_content = "I'm sorry, I encountered an error. Please try again in a moment."

    # Save assistant response
    db.save_chat_message(client["id"], "assistant", assistant_content)

    # Return full updated history
    all_messages = db.get_chat_history(client["id"], limit=20)
    html_parts = [_render_message(m["role"], m["content"]) for m in all_messages]
    return HTMLResponse("".join(html_parts))
