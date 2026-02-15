"""Tests for Stripe payment integration."""

from __future__ import annotations

import pytest


class TestSubscriptionDb:
    def test_update_subscription(self, test_db):
        test_db.create_client({"id": "pay1", "name": "Payer"})
        test_db.update_subscription(
            "pay1",
            stripe_customer_id="cus_123",
            stripe_subscription_id="sub_456",
            subscription_status="active",
            subscription_plan="price_789",
        )
        client = test_db.get_client("pay1")
        assert client["stripe_customer_id"] == "cus_123"
        assert client["stripe_subscription_id"] == "sub_456"
        assert client["subscription_status"] == "active"
        assert client["subscription_plan"] == "price_789"

    def test_get_client_by_stripe_customer(self, test_db):
        test_db.create_client({"id": "pay2", "name": "Payer2"})
        test_db.update_subscription("pay2", stripe_customer_id="cus_abc")
        result = test_db.get_client_by_stripe_customer("cus_abc")
        assert result is not None
        assert result["id"] == "pay2"

    def test_get_client_by_stripe_customer_not_found(self, test_db):
        result = test_db.get_client_by_stripe_customer("cus_nonexistent")
        assert result is None

    def test_record_stripe_event_idempotent(self, test_db):
        assert test_db.record_stripe_event("evt_1", "customer.subscription.created") is True
        assert test_db.record_stripe_event("evt_1", "customer.subscription.created") is False

    def test_record_different_events(self, test_db):
        assert test_db.record_stripe_event("evt_a", "type_a") is True
        assert test_db.record_stripe_event("evt_b", "type_b") is True
