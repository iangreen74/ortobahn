"""Tenant subscription and billing routes."""

from __future__ import annotations

import stripe
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse

from ortobahn.auth import AuthClient

router = APIRouter(prefix="/my")


@router.get("/subscribe")
async def tenant_subscribe(request: Request, client: AuthClient):
    """Create a Stripe Checkout session and redirect the user to it."""
    settings = request.app.state.settings
    db = request.app.state.db

    if not settings.stripe_secret_key or not settings.stripe_price_id:
        raise HTTPException(status_code=503, detail="Payments not configured")

    stripe.api_key = settings.stripe_secret_key

    # Get or create Stripe customer
    customer_id = client.get("stripe_customer_id")
    if not customer_id:
        customer = stripe.Customer.create(
            name=client["name"],
            email=client.get("email", ""),
            metadata={"ortobahn_client_id": client["id"]},
        )
        customer_id = customer.id
        db.update_subscription(client["id"], stripe_customer_id=customer_id)

    session = stripe.checkout.Session.create(
        customer=customer_id,
        payment_method_types=["card"],
        line_items=[{"price": settings.stripe_price_id, "quantity": 1}],
        mode="subscription",
        success_url=str(request.url_for("tenant_dashboard").replace(query="payment=success")),
        cancel_url=str(request.url_for("tenant_dashboard").replace(query="payment=cancelled")),
        metadata={"ortobahn_client_id": client["id"]},
    )

    return RedirectResponse(str(session.url or "/my/dashboard"), status_code=303)


@router.post("/billing")
async def tenant_billing_portal(request: Request, client: AuthClient):
    """Redirect to Stripe Customer Portal for subscription management."""
    settings = request.app.state.settings

    if not settings.stripe_secret_key:
        raise HTTPException(status_code=503, detail="Payments not configured")

    stripe.api_key = settings.stripe_secret_key
    customer_id = client.get("stripe_customer_id")
    if not customer_id:
        raise HTTPException(status_code=400, detail="No billing account found")

    portal_session = stripe.billing_portal.Session.create(
        customer=customer_id,
        return_url=str(request.url_for("tenant_settings")),
    )

    return RedirectResponse(portal_session.url, status_code=303)
