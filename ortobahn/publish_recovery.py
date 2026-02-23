"""Publisher Error Classification and Recovery — resilient publishing with auto-retry.

Classifies publishing errors into categories and applies appropriate recovery
strategies. Zero LLM calls — pure pattern matching and retry logic.
"""

from __future__ import annotations

import enum
import logging
import re
import time

from ortobahn.db import Database
from ortobahn.memory import MemoryStore
from ortobahn.models import AgentMemory, MemoryCategory, MemoryType

logger = logging.getLogger("ortobahn.publish_recovery")


class ErrorCategory(enum.Enum):
    TRANSIENT = "transient"
    AUTH = "auth"
    CONTENT_VIOLATION = "content_violation"
    QUOTA = "quota"
    UNKNOWN = "unknown"


class PublishErrorClassifier:
    """Classify publishing errors by pattern matching on exception messages."""

    _TRANSIENT_PATTERNS = [
        r"timeout",
        r"timed?\s*out",
        r"connection",
        r"connect",
        r"5\d{2}",
        r"429",
        r"rate\s*limit",
        r"retry",
        r"temporary",
        r"unavailable",
        r"service\s*unavailable",
        r"bad\s*gateway",
        r"gateway\s*timeout",
    ]
    _AUTH_PATTERNS = [
        r"401",
        r"403",
        r"unauthorized",
        r"forbidden",
        r"expired",
        r"invalid\s*token",
        r"auth",
        r"credential",
    ]
    _CONTENT_VIOLATION_PATTERNS = [
        r"violation",
        r"rejected",
        r"policy",
        r"blocked",
        r"spam",
        r"inappropriate",
        r"banned",
        r"content\s*filter",
    ]
    _QUOTA_PATTERNS = [
        r"quota",
        r"limit\s*exceeded",
        r"daily\s*limit",
        r"too\s*many",
        r"max\s*posts",
        r"allowance",
    ]

    @classmethod
    def classify_error(cls, exception: Exception, platform: str = "") -> ErrorCategory:
        """Classify an error based on its string representation."""
        error_str = str(exception).lower()

        for pattern in cls._TRANSIENT_PATTERNS:
            if re.search(pattern, error_str):
                return ErrorCategory.TRANSIENT
        for pattern in cls._AUTH_PATTERNS:
            if re.search(pattern, error_str):
                return ErrorCategory.AUTH
        for pattern in cls._CONTENT_VIOLATION_PATTERNS:
            if re.search(pattern, error_str):
                return ErrorCategory.CONTENT_VIOLATION
        for pattern in cls._QUOTA_PATTERNS:
            if re.search(pattern, error_str):
                return ErrorCategory.QUOTA

        return ErrorCategory.UNKNOWN


class PublishRecoveryManager:
    """Apply recovery strategies based on error category."""

    def __init__(self, db: Database, memory_store: MemoryStore | None = None, max_retries: int = 2):
        self.db = db
        self.memory = memory_store
        self.max_retries = max_retries

    def attempt_recovery(
        self,
        post_id: str,
        draft,
        error_category: ErrorCategory,
        platform_client,
        client_id: str = "default",
        run_id: str = "",
    ) -> dict:
        """Try to recover from a publishing failure.

        Returns {"recovered": bool, "action": str, "should_skip_remaining": bool}
        """
        if error_category == ErrorCategory.TRANSIENT:
            return self._handle_transient(post_id, draft, platform_client, client_id, run_id)
        elif error_category == ErrorCategory.AUTH:
            return self._handle_auth(post_id, client_id, run_id)
        elif error_category == ErrorCategory.CONTENT_VIOLATION:
            return self._handle_content_violation(post_id, draft, client_id, run_id)
        elif error_category == ErrorCategory.QUOTA:
            return self._handle_quota(post_id, client_id, run_id)
        else:
            return self._handle_unknown(post_id, client_id, run_id)

    def _handle_transient(self, post_id, draft, platform_client, client_id, run_id) -> dict:
        """Retry with exponential backoff."""
        backoff_delays = [30, 60]  # seconds
        for attempt in range(self.max_retries):
            delay = backoff_delays[attempt] if attempt < len(backoff_delays) else 60
            logger.info(f"Transient error retry {attempt + 1}/{self.max_retries} after {delay}s for post {post_id[:8]}")
            time.sleep(delay)
            try:
                uri, platform_id = platform_client.post(draft.text)
                self.db.update_post_published(post_id, uri, platform_id)
                logger.info(f"Retry succeeded for post {post_id[:8]}")
                return {
                    "recovered": True,
                    "action": f"retry_success_attempt_{attempt + 1}",
                    "should_skip_remaining": False,
                }
            except Exception as e:
                logger.warning(f"Retry {attempt + 1} failed: {e}")

        self.db.update_post_failed_with_category(
            post_id, "Transient error: retries exhausted", ErrorCategory.TRANSIENT.value
        )
        return {"recovered": False, "action": "retries_exhausted", "should_skip_remaining": False}

    def _handle_auth(self, post_id, client_id, run_id) -> dict:
        """Auth errors: mark client credential issue, skip remaining posts."""
        self.db.update_post_failed_with_category(post_id, "Authentication error", ErrorCategory.AUTH.value)
        # Set client status to credential_issue so next cycle skips
        self.db.execute(
            "UPDATE clients SET status='credential_issue' WHERE id=?",
            (client_id,),
            commit=True,
        )
        logger.warning(f"Auth error for client {client_id} — marking credential_issue, skipping remaining posts")
        return {"recovered": False, "action": "credential_issue_flagged", "should_skip_remaining": True}

    def _handle_content_violation(self, post_id, draft, client_id, run_id) -> dict:
        """Content violation: create memory for creator, mark failed."""
        self.db.update_post_failed_with_category(
            post_id, "Content policy violation", ErrorCategory.CONTENT_VIOLATION.value
        )

        if self.memory:
            text_preview = (draft.text or "")[:80]
            self.memory.remember(
                AgentMemory(
                    agent_name="creator",
                    client_id=client_id,
                    memory_type=MemoryType.OBSERVATION,
                    category=MemoryCategory.CONTENT_PATTERN,
                    content={
                        "summary": f"Content rejected by platform policy: {text_preview}",
                        "signal": "content_violation",
                    },
                    confidence=0.8,
                    source_run_id=run_id,
                )
            )

        logger.warning(f"Content violation for post {post_id[:8]}")
        return {"recovered": False, "action": "content_violation_recorded", "should_skip_remaining": False}

    def _handle_quota(self, post_id, client_id, run_id) -> dict:
        """Quota exceeded: mark skipped, skip remaining posts."""
        self.db.execute(
            "UPDATE posts SET status='skipped', error_message=?, failure_category=? WHERE id=?",
            ("Platform quota exceeded", ErrorCategory.QUOTA.value, post_id),
            commit=True,
        )
        logger.info(f"Quota exceeded for client {client_id} — skipping remaining posts")
        return {"recovered": False, "action": "quota_skip", "should_skip_remaining": True}

    def _handle_unknown(self, post_id, client_id, run_id) -> dict:
        """Unknown error: mark failed, continue."""
        self.db.update_post_failed_with_category(post_id, "Unknown publishing error", ErrorCategory.UNKNOWN.value)
        return {"recovered": False, "action": "unknown_marked_failed", "should_skip_remaining": False}
