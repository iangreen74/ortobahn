"""Unified authorization middleware and rate limiting for Ortobahn web application."""

import os
from datetime import datetime, timedelta
from functools import wraps
from typing import Any, Callable, Optional

import jwt
from fastapi import FastAPI, HTTPException, Request, Security, status
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address


def get_user_identifier(request: Request) -> str:
    """Get user identifier for rate limiting (IP or user ID)."""
    user_id = getattr(request.state, "user_id", None)
    if user_id:
        return f"user:{user_id}"
    return get_remote_address(request)


limiter = Limiter(key_func=get_user_identifier)
app = FastAPI(title="Ortobahn API")
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Security schemes
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)
bearer_scheme = HTTPBearer(auto_error=False)

# Configuration
API_KEYS = set(os.getenv("ORTOBAHN_API_KEYS", "").split(","))
JWT_SECRET = os.getenv("JWT_SECRET", "dev-secret-change-in-production")
JWT_ALGORITHM = "HS256"
COGNITO_REGION = os.getenv("COGNITO_REGION", "us-east-1")
COGNITO_USER_POOL_ID = os.getenv("COGNITO_USER_POOL_ID", "")


class AuthContext:
    """Authentication context containing user information."""

    def __init__(
        self,
        user_id: str,
        auth_type: str,
        roles: list[str] | None = None,
        permissions: list[str] | None = None,
    ):
        self.user_id = user_id
        self.auth_type = auth_type
        self.roles = roles or []
        self.permissions = permissions or []

    def has_role(self, role: str) -> bool:
        return role in self.roles

    def has_permission(self, permission: str) -> bool:
        return permission in self.permissions


async def verify_api_key(api_key: Optional[str] = Security(api_key_header)) -> Optional[AuthContext]:
    """Verify API key authentication."""
    if api_key and api_key in API_KEYS:
        return AuthContext(user_id=f"apikey:{api_key[:8]}", auth_type="api_key", roles=["api_user"])
    return None


async def verify_jwt(credentials: Optional[HTTPAuthorizationCredentials] = Security(bearer_scheme)) -> Optional[AuthContext]:
    """Verify JWT token authentication."""
    if not credentials:
        return None
    try:
        token = credentials.credentials
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        return AuthContext(
            user_id=payload.get("sub", "unknown"),
            auth_type="jwt",
            roles=payload.get("roles", []),
            permissions=payload.get("permissions", []),
        )
    except jwt.InvalidTokenError:
        return None


async def verify_cognito(credentials: Optional[HTTPAuthorizationCredentials] = Security(bearer_scheme)) -> Optional[AuthContext]:
    """Verify AWS Cognito token authentication."""
    if not credentials or not COGNITO_USER_POOL_ID:
        return None
    try:
        # In production, verify against Cognito public keys
        # For now, decode without verification (placeholder)
        token = credentials.credentials
        payload = jwt.decode(token, options={"verify_signature": False})
        if payload.get("iss", "").endswith(COGNITO_USER_POOL_ID):
            return AuthContext(
                user_id=payload.get("sub", "unknown"),
                auth_type="cognito",
                roles=payload.get("cognito:groups", []),
                permissions=payload.get("permissions", []),
            )
    except jwt.InvalidTokenError:
        pass
    return None


async def unified_auth(request: Request, api_key: Optional[str] = Security(api_key_header), credentials: Optional[HTTPAuthorizationCredentials] = Security(bearer_scheme)) -> AuthContext:
    """Unified authentication middleware trying all mechanisms."""
    auth_context = await verify_api_key(api_key) or await verify_jwt(credentials) or await verify_cognito(credentials)
    
    if not auth_context:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing authentication credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    # Store in request state for rate limiting
    request.state.user_id = auth_context.user_id
    return auth_context


def require_permission(*required_permissions: str) -> Callable:
    """Decorator for role-based access control."""
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(*args: Any, auth: AuthContext = Security(unified_auth), **kwargs: Any) -> Any:
            if not any(auth.has_permission(perm) for perm in required_permissions):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"Missing required permissions: {', '.join(required_permissions)}",
                )
            return await func(*args, auth=auth, **kwargs)
        return wrapper
    return decorator


def require_role(*required_roles: str) -> Callable:
    """Decorator requiring specific roles."""
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(*args: Any, auth: AuthContext = Security(unified_auth), **kwargs: Any) -> Any:
            if not any(auth.has_role(role) for role in required_roles):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"Missing required roles: {', '.join(required_roles)}",
                )
            return await func(*args, auth=auth, **kwargs)
        return wrapper
    return decorator
