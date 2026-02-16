"""Authentication routes: login, confirmation, password reset, API key management."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, EmailStr

from ortobahn.auth import (
    AdminClient,
    AuthClient,
    create_session_token,
    generate_api_key,
    hash_api_key,
    key_prefix,
)
from ortobahn.cognito import CognitoError

router = APIRouter()


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------


@router.get("/login")
async def login_page(request: Request, next: str = "/my/dashboard"):
    """Render the login page."""
    templates = request.app.state.templates
    return templates.TemplateResponse("login.html", {"request": request, "next_url": next})


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


@router.post("/login")
async def login(request: Request, body: LoginRequest):
    """Authenticate with email and password via Cognito, then issue a session JWT."""
    db = request.app.state.db
    cognito = request.app.state.cognito
    secret_key = request.app.state.settings.secret_key

    try:
        cognito.login(body.email, body.password)
    except CognitoError as exc:
        if exc.code == "UserNotConfirmedException":
            return JSONResponse(
                status_code=403,
                content={
                    "detail": exc.message,
                    "needs_confirmation": True,
                    "email": body.email,
                },
            )
        raise HTTPException(status_code=401, detail=exc.message) from None

    client = db.get_client_by_email(body.email)
    if not client:
        raise HTTPException(status_code=401, detail="Client not found")

    token = create_session_token(client["id"], secret_key)
    response = JSONResponse({"token": token, "client_id": client["id"], "client_name": client["name"]})
    response.set_cookie(
        key="session",
        value=token,
        httponly=True,
        samesite="lax",
        max_age=86400,
    )
    return response


# ---------------------------------------------------------------------------
# Logout
# ---------------------------------------------------------------------------


@router.post("/logout")
async def logout():
    """Clear session cookie."""
    response = JSONResponse({"message": "Logged out"})
    response.delete_cookie("session")
    return response


# ---------------------------------------------------------------------------
# Email confirmation
# ---------------------------------------------------------------------------


@router.get("/confirm")
async def confirm_page(request: Request, email: str = ""):
    """Render the email confirmation page."""
    templates = request.app.state.templates
    return templates.TemplateResponse("confirm.html", {"request": request, "email": email})


class ConfirmRequest(BaseModel):
    email: EmailStr
    code: str


@router.post("/confirm")
async def confirm(request: Request, body: ConfirmRequest):
    """Confirm a user's email with the verification code."""
    cognito = request.app.state.cognito

    try:
        cognito.confirm_sign_up(body.email, body.code)
    except CognitoError as exc:
        raise HTTPException(status_code=400, detail=exc.message) from None

    return {"message": "Email confirmed. You may now log in."}


# ---------------------------------------------------------------------------
# Forgot password
# ---------------------------------------------------------------------------


@router.get("/forgot-password")
async def forgot_password_page(request: Request):
    """Render the forgot-password page."""
    templates = request.app.state.templates
    return templates.TemplateResponse("forgot_password.html", {"request": request})


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


@router.post("/forgot-password")
async def forgot_password(request: Request, body: ForgotPasswordRequest):
    """Initiate the forgot-password flow (sends a code to the user's email)."""
    cognito = request.app.state.cognito

    try:
        cognito.forgot_password(body.email)
    except CognitoError as exc:
        raise HTTPException(status_code=400, detail=exc.message) from None

    return {"message": "If that email is registered, a reset code has been sent."}


# ---------------------------------------------------------------------------
# Reset password
# ---------------------------------------------------------------------------


@router.get("/reset-password")
async def reset_password_page(request: Request, email: str = ""):
    """Render the reset-password page."""
    templates = request.app.state.templates
    return templates.TemplateResponse("reset_password.html", {"request": request, "email": email})


class ResetPasswordRequest(BaseModel):
    email: EmailStr
    code: str
    new_password: str


@router.post("/reset-password")
async def reset_password(request: Request, body: ResetPasswordRequest):
    """Complete the password reset with a verification code and new password."""
    cognito = request.app.state.cognito

    try:
        cognito.confirm_forgot_password(body.email, body.code, body.new_password)
    except CognitoError as exc:
        raise HTTPException(status_code=400, detail=exc.message) from None

    return {"message": "Password has been reset. You may now log in."}


# ---------------------------------------------------------------------------
# API key management
# ---------------------------------------------------------------------------


class CreateApiKeyRequest(BaseModel):
    client_id: str
    name: str = "default"


@router.post("/keys")
async def create_api_key_route(request: Request, body: CreateApiKeyRequest, admin: AdminClient):
    """Create a new API key for a client. Admin only."""
    db = request.app.state.db

    client = db.get_client(body.client_id)
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")

    raw_key = generate_api_key()
    hashed = hash_api_key(raw_key)
    prefix = key_prefix(raw_key)

    db.create_api_key(body.client_id, hashed, prefix, body.name)

    return {"api_key": raw_key, "prefix": prefix, "client_id": body.client_id}


@router.get("/keys")
async def list_api_keys(request: Request, client: AuthClient):
    """List API keys for the authenticated client (shows prefix only)."""
    db = request.app.state.db
    keys = db.get_api_keys_for_client(client["id"])
    return {"keys": keys}
