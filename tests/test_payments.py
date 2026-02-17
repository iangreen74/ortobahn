"""Tests for Stripe payment integration."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone


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


class TestTrialExpiry:
    def test_trial_active_not_expired(self, test_db):
        test_db.create_client({"id": "trial1", "name": "TrialCo"})
        future = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
        test_db.execute(
            "UPDATE clients SET subscription_status='trialing', trial_ends_at=? WHERE id=?",
            (future, "trial1"),
            commit=True,
        )
        status = test_db.check_and_expire_trial("trial1")
        assert status == "trialing"

    def test_trial_expired_flips_to_expired(self, test_db):
        test_db.create_client({"id": "trial2", "name": "ExpiredCo"})
        past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        test_db.execute(
            "UPDATE clients SET subscription_status='trialing', trial_ends_at=? WHERE id=?",
            (past, "trial2"),
            commit=True,
        )
        status = test_db.check_and_expire_trial("trial2")
        assert status == "expired"
        client = test_db.get_client("trial2")
        assert client["subscription_status"] == "expired"

    def test_active_subscription_not_affected(self, test_db):
        test_db.create_client({"id": "trial3", "name": "ActiveCo"})
        test_db.update_subscription("trial3", subscription_status="active")
        status = test_db.check_and_expire_trial("trial3")
        assert status == "active"

    def test_nonexistent_client_returns_none(self, test_db):
        status = test_db.check_and_expire_trial("nonexistent")
        assert status == "none"

    def test_trial_no_end_date_stays_trialing(self, test_db):
        test_db.create_client({"id": "trial4", "name": "NoDateCo"})
        test_db.execute(
            "UPDATE clients SET subscription_status='trialing' WHERE id=?",
            ("trial4",),
            commit=True,
        )
        status = test_db.check_and_expire_trial("trial4")
        assert status == "trialing"
