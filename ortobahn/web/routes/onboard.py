"""Public onboarding API routes for the landing page."""

from __future__ import annotations

import ipaddress
import json
import logging
from urllib.parse import urlparse

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, EmailStr, Field, field_validator

from ortobahn.auth import (
    generate_api_key,
    hash_api_key,
    key_prefix,
)
from ortobahn.cognito import CognitoError

log = logging.getLogger(__name__)

router = APIRouter()

# Map common industries to news categories and keywords for trend personalization.
INDUSTRY_DEFAULTS: dict[str, dict[str, str]] = {
    "saas": {"news_category": "technology", "news_keywords": "SaaS, cloud computing, software"},
    "software": {"news_category": "technology", "news_keywords": "software, SaaS, developer tools"},
    "technology": {"news_category": "technology", "news_keywords": "technology, startups, innovation"},
    "ai": {"news_category": "technology", "news_keywords": "artificial intelligence, machine learning, AI"},
    "fintech": {"news_category": "business", "news_keywords": "fintech, payments, banking, cryptocurrency"},
    "finance": {"news_category": "business", "news_keywords": "finance, investing, markets, wealth management"},
    "healthcare": {"news_category": "health", "news_keywords": "healthcare, medtech, digital health"},
    "health": {"news_category": "health", "news_keywords": "health, wellness, medical technology"},
    "ecommerce": {"news_category": "business", "news_keywords": "ecommerce, retail, DTC, online shopping"},
    "retail": {"news_category": "business", "news_keywords": "retail, consumer goods, shopping"},
    "marketing": {"news_category": "business", "news_keywords": "marketing, advertising, brand strategy"},
    "real estate": {"news_category": "business", "news_keywords": "real estate, proptech, housing market"},
    "education": {"news_category": "science", "news_keywords": "education, edtech, online learning"},
    "cybersecurity": {"news_category": "technology", "news_keywords": "cybersecurity, infosec, data privacy"},
    "crypto": {"news_category": "business", "news_keywords": "cryptocurrency, blockchain, web3, DeFi"},
    "gaming": {"news_category": "entertainment", "news_keywords": "gaming, esports, game development"},
    "media": {"news_category": "entertainment", "news_keywords": "media, content creation, streaming"},
    "legal": {"news_category": "general", "news_keywords": "legal tech, law, compliance, regulation"},
    "consulting": {"news_category": "business", "news_keywords": "consulting, management, strategy"},
    "energy": {"news_category": "science", "news_keywords": "energy, cleantech, renewable, sustainability"},
}


def _match_industry(industry: str) -> dict[str, str]:
    """Match a user-provided industry string to trend defaults using simple keyword matching."""
    industry_lower = industry.lower().strip()
    # Exact match
    if industry_lower in INDUSTRY_DEFAULTS:
        return INDUSTRY_DEFAULTS[industry_lower]
    # Substring match
    for key, defaults in INDUSTRY_DEFAULTS.items():
        if key in industry_lower or industry_lower in key:
            return defaults
    # Default fallback
    return {"news_category": "technology", "news_keywords": ""}


class OnboardRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    company: str = Field(min_length=1, max_length=200)
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    industry: str = Field(min_length=1, max_length=100)
    website: str = Field(default="", max_length=500)
    brand_voice: str = Field(default="", max_length=500)

    @field_validator("website")
    @classmethod
    def validate_website(cls, v: str) -> str:
        if not v:
            return v
        return _validate_url(v)


def _is_internal_hostname(hostname: str) -> bool:
    """Return True if hostname resolves to localhost or a private/reserved IP range."""
    if not hostname:
        return True
    # Block obvious local hostnames
    if hostname in ("localhost", "127.0.0.1", "0.0.0.0", "::1", "[::1]"):  # noqa: S104
        return True
    try:
        addr = ipaddress.ip_address(hostname)
        return addr.is_private or addr.is_loopback or addr.is_reserved or addr.is_link_local
    except ValueError:
        # Not a raw IP — check for localhost-like domains
        return hostname.endswith(".local") or hostname.endswith(".internal")


def _validate_url(url: str) -> str:
    """Validate and normalize a URL, rejecting malformed, localhost, and internal IPs."""
    url = url.strip()
    if not url:
        return url
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    try:
        parsed = urlparse(url)
    except ValueError as exc:
        raise ValueError(f"Malformed URL: {exc}") from None
    if parsed.scheme not in ("http", "https"):
        raise ValueError("URL must use http or https scheme")
    if not parsed.hostname:
        raise ValueError("URL must include a hostname")
    if _is_internal_hostname(parsed.hostname):
        raise ValueError("URLs pointing to localhost or internal networks are not allowed")
    if "." not in parsed.hostname:
        raise ValueError("URL hostname must contain a dot (e.g. example.com)")
    return url


def _normalize_url(url: str) -> str:
    """Accept bare domains like acme.com and prepend https://."""
    url = url.strip()
    if not url:
        return url
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    return url


@router.post("/onboard")
async def onboard(request: Request, body: OnboardRequest):
    db = request.app.state.db
    body.website = _normalize_url(body.website)

    # Check for duplicate email
    existing = db.fetchone("SELECT id FROM clients WHERE email=?", (body.email,))
    if existing:
        return JSONResponse(
            {"detail": "An account with this email already exists."},
            status_code=409,
        )

    # Match industry to trend defaults
    trend_defaults = _match_industry(body.industry)

    # Create client with pending status and industry-specific trend config
    client_id = db.create_client(
        {
            "name": body.company,
            "description": f"Onboarded via landing page by {body.name}",
            "industry": body.industry,
            "brand_voice": body.brand_voice,
            "website": body.website,
            "email": body.email,
            "status": "pending",
        }
    )
    # Set trend config (after create, since create_client has a fixed column set)
    db.update_client(
        client_id,
        {
            "news_category": trend_defaults["news_category"],
            "news_keywords": trend_defaults["news_keywords"],
        },
    )

    # Register user in Cognito
    try:
        cognito_sub = request.app.state.cognito.sign_up(body.email, body.password, client_id)
    except CognitoError as exc:
        log.warning("Cognito sign-up failed for %s: %s", body.email, exc)
        # Rollback: remove the client record we just created
        db.execute("DELETE FROM clients WHERE id=?", (client_id,), commit=True)
        return JSONResponse(
            {"detail": str(exc)},
            status_code=400,
        )

    # Store the Cognito sub (trial already started by create_client)
    db.execute(
        "UPDATE clients SET cognito_sub=? WHERE id=?",
        (cognito_sub, client_id),
        commit=True,
    )

    # Generate API key for programmatic access
    raw_key = generate_api_key()
    hashed = hash_api_key(raw_key)
    prefix = key_prefix(raw_key)
    db.create_api_key(client_id, hashed, prefix, name="default")

    return JSONResponse(
        {
            "client_id": client_id,
            "api_key": raw_key,
            "needs_confirmation": True,
            "message": "Account created! Please verify your email before logging in. "
            "Save your API key -- it cannot be retrieved again.",
        }
    )


@router.get("/public/stats")
async def public_stats(request: Request):
    db = request.app.state.db
    stats = db.get_public_stats()
    return stats


# ---------------------------------------------------------------------------
# AI Brand Interview Onboarding
# ---------------------------------------------------------------------------

INTERVIEW_COOKIE = "ortobahn_interview"


def _escape_html(text: str) -> str:
    """Minimal HTML escaping."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _get_interview_state(request: Request) -> dict:
    """Read interview state from signed cookie."""
    raw = request.cookies.get(INTERVIEW_COOKIE, "")
    if not raw:
        return {"step": 1, "answers": {}, "messages": []}
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {"step": 1, "answers": {}, "messages": []}


def _render_messages(messages: list[dict], step: int) -> str:
    """Render chat messages as HTML fragment."""
    parts = []
    for m in messages:
        if m["role"] == "ai":
            parts.append(
                f'<div class="msg"><div class="msg-label">Ortobahn</div>'
                f'<div class="msg-ai">{_escape_html(m["text"])}</div></div>'
            )
        else:
            parts.append(
                f'<div class="msg"><div class="msg-label" style="text-align:right">You</div>'
                f'<div class="msg-user">{_escape_html(m["text"])}</div></div>'
            )
    # Hidden element to communicate step to JS
    parts.append(f'<div data-step="{step}" style="display:none"></div>')
    return "".join(parts)


def _get_interview_prompt(step: int) -> str:
    """Generate the system+user prompt for a given interview step."""
    import os

    prompt_path = os.path.join(os.path.dirname(__file__), "..", "..", "prompts", "brand_interview.txt")
    try:
        with open(prompt_path) as f:
            system_prompt = f.read()
    except FileNotFoundError:
        system_prompt = "You are a brand strategist conducting an onboarding interview. Ask one question at a time."
    return system_prompt


STEP_QUESTIONS = {
    1: "Welcome! I'm excited to help you set up your AI marketing engine. Let's start — what's your company name, and do you have a website URL I can look at?",
    2: "Great! Now, what industry are you in, and who's your target audience? Who are you trying to reach with your content?",
    3: "How would you describe your brand's voice and tone? For example: professional and authoritative, casual and friendly, witty and bold, or something else?",
    4: "What are your main goals for social media? Things like brand awareness, lead generation, thought leadership, community building — what matters most?",
    5: "Perfect, here's what I've got so far:\n\n{summary}\n\nDoes this look right? Type 'yes' to create your account, or tell me what to change.",
}


def _build_summary(answers: dict) -> str:
    """Build a human-readable summary from interview answers."""
    parts = []
    if answers.get("company"):
        parts.append(f"Company: {answers['company']}")
    if answers.get("website"):
        parts.append(f"Website: {answers['website']}")
    if answers.get("industry"):
        parts.append(f"Industry: {answers['industry']}")
    if answers.get("target_audience"):
        parts.append(f"Target audience: {answers['target_audience']}")
    if answers.get("brand_voice"):
        parts.append(f"Brand voice: {answers['brand_voice']}")
    if answers.get("goals"):
        parts.append(f"Goals: {answers['goals']}")
    return "\n".join(parts) if parts else "No details collected yet."


def _parse_step_answer(step: int, answer: str, answers: dict) -> dict:
    """Extract structured data from user's free-text answer for a given step."""
    answer = answer.strip()
    if step == 1:
        # Try to extract company name and website
        words = answer.split()
        website = ""
        company_parts = []
        for w in words:
            if "." in w and len(w) > 3 and not w.startswith("e.g"):
                website = w if w.startswith("http") else f"https://{w}"
            else:
                company_parts.append(w)
        answers["company"] = " ".join(company_parts).strip() or answer
        if website:
            answers["website"] = website
    elif step == 2:
        answers["industry"] = answer
        answers["target_audience"] = answer
    elif step == 3:
        answers["brand_voice"] = answer
    elif step == 4:
        answers["goals"] = answer
    return answers


@router.get("/onboard/interview")
async def interview_page(request: Request):
    """Render the interview chat page."""
    templates = request.app.state.templates
    return templates.TemplateResponse("onboard_interview.html", {"request": request})


@router.get("/onboard/interview/start", response_class=HTMLResponse)
async def interview_start(request: Request):
    """Initialize interview and return first AI message."""
    state = {"step": 1, "answers": {}, "messages": []}
    first_msg = STEP_QUESTIONS[1]
    state["messages"].append({"role": "ai", "text": first_msg})

    html = _render_messages(state["messages"], state["step"])
    response = HTMLResponse(html)
    response.set_cookie(
        INTERVIEW_COOKIE,
        json.dumps(state),
        httponly=True,
        samesite="lax",
        max_age=3600,
    )
    return response


@router.post("/onboard/interview/step", response_class=HTMLResponse)
async def interview_step(request: Request, answer: str = Form("")):
    """Process user answer and advance to next step."""
    state = _get_interview_state(request)
    step = state.get("step", 1)
    answers = state.get("answers", {})
    messages = state.get("messages", [])
    answer = answer.strip()

    if not answer:
        html = _render_messages(messages, step)
        return HTMLResponse(html)

    # Record user message
    messages.append({"role": "user", "text": answer})

    # Check if this is the confirmation step
    if step == 5:
        if answer.lower() in ("yes", "y", "yep", "looks good", "correct", "confirm"):
            # Create account — redirect to completion
            messages.append({"role": "ai", "text": "Creating your account..."})
            state["messages"] = messages
            state["step"] = 6
            state["confirmed"] = True

            html = _render_messages(messages, 6)
            html += (
                '<div class="msg"><div class="msg-ai">'
                "Your AI marketing engine is ready! "
                '<a href="/onboard/interview/complete" style="color:#667eea;font-weight:600;">'
                "Continue to set up your login &rarr;</a>"
                "</div></div>"
            )
            response = HTMLResponse(html)
            response.set_cookie(
                INTERVIEW_COOKIE,
                json.dumps(state),
                httponly=True,
                samesite="lax",
                max_age=3600,
            )
            return response
        else:
            # User wants to change something — stay on step 5
            messages.append(
                {"role": "ai", "text": "No problem! What would you like to change? Just tell me and I'll update it."}
            )
            state["messages"] = messages
            html = _render_messages(messages, step)
            response = HTMLResponse(html)
            response.set_cookie(
                INTERVIEW_COOKIE,
                json.dumps(state),
                httponly=True,
                samesite="lax",
                max_age=3600,
            )
            return response

    # Parse structured data from answer
    answers = _parse_step_answer(step, answer, answers)
    next_step = step + 1

    # Generate next AI message
    if next_step == 5:
        summary = _build_summary(answers)
        ai_msg = STEP_QUESTIONS[5].format(summary=summary)
    elif next_step in STEP_QUESTIONS:
        ai_msg = STEP_QUESTIONS[next_step]
    else:
        ai_msg = "Thanks! Let me process your information."

    messages.append({"role": "ai", "text": ai_msg})

    state["step"] = next_step
    state["answers"] = answers
    state["messages"] = messages

    html = _render_messages(messages, next_step)
    response = HTMLResponse(html)
    response.set_cookie(
        INTERVIEW_COOKIE,
        json.dumps(state),
        httponly=True,
        samesite="lax",
        max_age=3600,
    )
    return response


@router.get("/onboard/interview/complete")
async def interview_complete(request: Request):
    """Create the client from interview data and redirect to registration."""
    state = _get_interview_state(request)
    answers = state.get("answers", {})

    if not state.get("confirmed"):
        return HTMLResponse('<p>Please complete the interview first. <a href="/onboard/interview">Start over</a></p>')

    db = request.app.state.db
    company = answers.get("company", "My Company")
    website = answers.get("website", "")
    industry = answers.get("industry", "technology")
    target_audience = answers.get("target_audience", "")
    brand_voice = answers.get("brand_voice", "professional")
    goals = answers.get("goals", "")

    # Match industry for trend config
    trend_defaults = _match_industry(industry)

    # Create client with interview data
    client_id = db.create_client(
        {
            "name": company,
            "description": "Onboarded via AI brand interview",
            "industry": industry,
            "target_audience": target_audience,
            "brand_voice": brand_voice,
            "website": _normalize_url(website) if website else "",
            "status": "pending",
        }
    )

    # Set trend config and goals
    update_fields = {
        "news_category": trend_defaults["news_category"],
        "news_keywords": trend_defaults["news_keywords"],
    }
    if goals:
        update_fields["content_pillars"] = goals
    db.update_client(client_id, update_fields)

    # Redirect to login page with pre-filled context
    response = HTMLResponse(
        f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"><title>Account Created - Ortobahn</title>
<style>
body {{ font-family: -apple-system, sans-serif; background: linear-gradient(135deg, #667eea, #764ba2);
min-height: 100vh; display: flex; align-items: center; justify-content: center; }}
.card {{ background: white; padding: 40px; border-radius: 12px; max-width: 480px; text-align: center; }}
h1 {{ color: #333; margin-bottom: 12px; }}
p {{ color: #666; margin-bottom: 24px; line-height: 1.6; }}
.btn {{ display: inline-block; padding: 12px 32px; background: linear-gradient(135deg, #667eea, #764ba2);
color: white; text-decoration: none; border-radius: 8px; font-weight: 600; }}
.detail {{ background: #f8f9ff; padding: 16px; border-radius: 8px; text-align: left; margin: 20px 0; font-size: 14px; color: #444; }}
</style></head><body><div class="card">
<h1>Your brand profile is ready!</h1>
<div class="detail">
<strong>{_escape_html(company)}</strong><br>
Industry: {_escape_html(industry)}<br>
Voice: {_escape_html(brand_voice)}<br>
</div>
<p>Create your login to start generating content with your AI marketing engine.</p>
<a class="btn" href="/api/auth/login">Create Login &rarr;</a>
</div></body></html>"""
    )
    response.delete_cookie(INTERVIEW_COOKIE)
    return response
