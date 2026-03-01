"""Client CRUD operations — create, read, update, subscriptions, API keys."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from ortobahn.db.core import to_datetime

# TTL for cached client profiles (seconds).
_CLIENT_CACHE_TTL: float = 300.0  # 5 minutes


class ClientsMixin:
    """Mixed into Database to provide client-related methods."""

    # --- Clients ---

    def create_client(self, client_data: dict, start_trial: bool = True) -> str:
        cid = client_data.get("id") or str(uuid.uuid4())
        if start_trial:
            sub_status = "trialing"
            trial_ends_at = (datetime.now(timezone.utc) + timedelta(days=14)).isoformat()
        else:
            sub_status = "none"
            trial_ends_at = None
        self.execute(
            """INSERT INTO clients (id, name, description, industry, target_audience, brand_voice,
               website, email, status, products, competitive_positioning, key_messages,
               content_pillars, company_story, subscription_status, trial_ends_at, auto_publish)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                cid,
                client_data["name"],
                client_data.get("description", ""),
                client_data.get("industry", ""),
                client_data.get("target_audience", ""),
                client_data.get("brand_voice", ""),
                client_data.get("website", ""),
                client_data.get("email", ""),
                client_data.get("status", "active"),
                client_data.get("products", ""),
                client_data.get("competitive_positioning", ""),
                client_data.get("key_messages", ""),
                client_data.get("content_pillars", ""),
                client_data.get("company_story", ""),
                sub_status,
                trial_ends_at,
                client_data.get("auto_publish", 0),
            ),
            commit=True,
        )
        # Invalidate caches
        self._cache_invalidate(f"client:{cid}")
        self._cache_invalidate_prefix("all_clients")
        return cid

    def get_client(self, client_id: str) -> dict | None:
        cache_key = f"client:{client_id}"
        cached = self._cache_get(cache_key, _CLIENT_CACHE_TTL)
        if cached is not None:
            return cached
        result = self.fetchone("SELECT * FROM clients WHERE id=?", (client_id,))
        if result is not None:
            self._cache_set(cache_key, result)
        return result

    def get_client_by_email(self, email: str) -> dict | None:
        return self.fetchone("SELECT * FROM clients WHERE email=?", (email,))

    def get_client_by_cognito_sub(self, cognito_sub: str) -> dict | None:
        return self.fetchone("SELECT * FROM clients WHERE cognito_sub=?", (cognito_sub,))

    def get_all_clients(self) -> list[dict]:
        return self.fetchall("SELECT * FROM clients WHERE active=1 ORDER BY name")

    def update_client(self, client_id: str, data: dict) -> None:
        allowed = {
            "name",
            "description",
            "industry",
            "target_audience",
            "brand_voice",
            "website",
            "active",
            "status",
            "products",
            "competitive_positioning",
            "key_messages",
            "content_pillars",
            "company_story",
            "monthly_budget",
            "internal",
            "subscription_status",
            "subscription_plan",
            "cognito_sub",
            "news_category",
            "news_keywords",
            "rss_feeds",
            "posting_interval_hours",
            "timezone",
            "preferred_posting_hours",
            "article_enabled",
            "article_frequency",
            "article_voice",
            "article_platforms",
            "article_topics",
            "article_length",
            "last_article_at",
            "auto_publish_articles",
            "voice_confidence",
            "article_schedule",
            "adaptive_threshold",
            "auto_graduation_status",
        }
        updates = {k: v for k, v in data.items() if k in allowed}
        if not updates:
            return
        set_clause = ", ".join(f"{k}=?" for k in updates)
        values = list(updates.values()) + [client_id]
        self.execute(f"UPDATE clients SET {set_clause} WHERE id=?", values, commit=True)
        # Invalidate caches
        self._cache_invalidate(f"client:{client_id}")
        self._cache_invalidate_prefix("all_clients")

    def pause_client(self, client_id: str) -> None:
        """Set client status to paused (budget exceeded)."""
        self.execute("UPDATE clients SET status='paused' WHERE id=?", (client_id,), commit=True)
        self._cache_invalidate(f"client:{client_id}")

    # --- Subscriptions ---

    def update_subscription(
        self,
        client_id: str,
        stripe_customer_id: str = "",
        stripe_subscription_id: str = "",
        subscription_status: str = "none",
        subscription_plan: str = "",
    ) -> None:
        self.execute(
            """UPDATE clients SET stripe_customer_id=?, stripe_subscription_id=?,
               subscription_status=?, subscription_plan=? WHERE id=?""",
            (stripe_customer_id, stripe_subscription_id, subscription_status, subscription_plan, client_id),
            commit=True,
        )
        self._cache_invalidate(f"client:{client_id}")

    def get_client_by_stripe_customer(self, stripe_customer_id: str) -> dict | None:
        return self.fetchone("SELECT * FROM clients WHERE stripe_customer_id=?", (stripe_customer_id,))

    def record_stripe_event(self, event_id: str, event_type: str) -> bool:
        """Record a Stripe event. Returns False if already processed."""
        existing = self.fetchone("SELECT id FROM stripe_events WHERE id=?", (event_id,))
        if existing:
            return False
        self.execute(
            "INSERT INTO stripe_events (id, event_type) VALUES (?, ?)",
            (event_id, event_type),
            commit=True,
        )
        return True

    def check_and_expire_trial(self, client_id: str) -> str:
        """If client is trialing and trial has ended, flip to 'expired'. Returns current status."""
        row = self.fetchone(
            "SELECT subscription_status, trial_ends_at FROM clients WHERE id=?",
            (client_id,),
        )
        if not row:
            return "none"
        status = row["subscription_status"]
        if status == "trialing" and row["trial_ends_at"]:
            try:
                trial_end = to_datetime(row["trial_ends_at"])
                if trial_end.tzinfo is None:
                    trial_end = trial_end.replace(tzinfo=timezone.utc)
            except (ValueError, TypeError):
                return status
            if datetime.now(timezone.utc) > trial_end:
                self.execute(
                    "UPDATE clients SET subscription_status='expired' WHERE id=?",
                    (client_id,),
                    commit=True,
                )
                self._cache_invalidate(f"client:{client_id}")
                return "expired"
        return status

    # --- API Keys ---

    def create_api_key(self, client_id: str, key_hash: str, key_prefix: str, name: str = "default") -> str:
        kid = str(uuid.uuid4())
        self.execute(
            "INSERT INTO api_keys (id, client_id, key_hash, key_prefix, name) VALUES (?, ?, ?, ?, ?)",
            (kid, client_id, key_hash, key_prefix, name),
            commit=True,
        )
        return kid

    def get_api_keys_for_client(self, client_id: str) -> list[dict]:
        return self.fetchall(
            "SELECT id, key_prefix, name, created_at, last_used_at, active FROM api_keys WHERE client_id=?",
            (client_id,),
        )

    def revoke_api_key(self, key_id: str) -> None:
        self.execute("UPDATE api_keys SET active=0 WHERE id=?", (key_id,), commit=True)
