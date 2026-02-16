"""Stripe payment routes: checkout, webhooks, subscription status."""

from __future__ import annotations

import logging

import stripe
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ortobahn.auth import AuthClient

logger = logging.getLogger("ortobahn.payments")
router = APIRouter()


class CheckoutRequest(BaseModel):
    client_id: str
    success_url: str = "https://ortobahn.com/dashboard?payment=success"
    cancel_url: str = "https://ortobahn.com/pricing?payment=cancelled"


@router.post("/checkout")
async def create_checkout_session(request: Request, body: CheckoutRequest, client: AuthClient):
    """Create a Stripe Checkout session for subscription sign-up."""
    settings = request.app.state.settings
    db = request.app.state.db

    if not settings.stripe_secret_key:
        raise HTTPException(status_code=503, detail="Payments not configured")

    stripe.api_key = settings.stripe_secret_key

    if client["id"] != body.client_id and not client.get("internal"):
        raise HTTPException(status_code=403, detail="Cannot create checkout for another client")

    target_client = db.get_client(body.client_id)
    if not target_client:
        raise HTTPException(status_code=404, detail="Client not found")

    customer_id = target_client.get("stripe_customer_id")
    if not customer_id:
        customer = stripe.Customer.create(
            name=target_client["name"],
            email=target_client.get("email", ""),
            metadata={"ortobahn_client_id": body.client_id},
        )
        customer_id = customer.id
        db.update_subscription(body.client_id, stripe_customer_id=customer_id)

    session = stripe.checkout.Session.create(
        customer=customer_id,
        payment_method_types=["card"],
        line_items=[{"price": settings.stripe_price_id, "quantity": 1}],
        mode="subscription",
        success_url=body.success_url,
        cancel_url=body.cancel_url,
        metadata={"ortobahn_client_id": body.client_id},
    )

    return {"checkout_url": session.url, "session_id": session.id}


@router.post("/webhook")
async def stripe_webhook(request: Request):
    """Handle Stripe webhook events. No auth required (verified by signature)."""
    settings = request.app.state.settings
    db = request.app.state.db

    if not settings.stripe_secret_key or not settings.stripe_webhook_secret:
        raise HTTPException(status_code=503, detail="Payments not configured")

    stripe.api_key = settings.stripe_secret_key
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature", "")

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, settings.stripe_webhook_secret)
    except (ValueError, stripe.SignatureVerificationError) as e:
        raise HTTPException(status_code=400, detail="Invalid signature") from e

    if not db.record_stripe_event(event["id"], event["type"]):
        return {"status": "already_processed"}

    event_type = event["type"]
    data = event["data"]["object"]

    if event_type == "customer.subscription.created":
        _handle_subscription_change(db, data, "active")
    elif event_type == "customer.subscription.updated":
        status = data.get("status", "active")
        _handle_subscription_change(db, data, status)
    elif event_type == "customer.subscription.deleted":
        _handle_subscription_change(db, data, "cancelled")
    elif event_type == "invoice.payment_failed":
        customer_id = data.get("customer")
        client = db.get_client_by_stripe_customer(customer_id)
        if client:
            db.update_subscription(
                client["id"],
                stripe_customer_id=customer_id,
                stripe_subscription_id=client.get("stripe_subscription_id", ""),
                subscription_status="past_due",
            )
            logger.warning(f"Payment failed for client {client['id']}")

    return {"status": "processed"}


def _handle_subscription_change(db, subscription_data: dict, status: str):
    customer_id = subscription_data.get("customer")
    subscription_id = subscription_data.get("id")
    plan = ""
    items = subscription_data.get("items", {})
    if isinstance(items, dict) and items.get("data"):
        plan = items["data"][0].get("price", {}).get("id", "")

    client = db.get_client_by_stripe_customer(customer_id)
    if client:
        db.update_subscription(
            client["id"],
            stripe_customer_id=customer_id,
            stripe_subscription_id=subscription_id,
            subscription_status=status,
            subscription_plan=plan,
        )
        logger.info(f"Subscription {status} for client {client['id']}")


@router.get("/status")
async def subscription_status(request: Request, client: AuthClient):
    """Get the authenticated client's subscription status."""
    return {
        "client_id": client["id"],
        "subscription_status": client.get("subscription_status", "none"),
        "subscription_plan": client.get("subscription_plan", ""),
        "internal": bool(client.get("internal")),
    }
