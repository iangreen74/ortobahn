"""Two-tier content guardrails — platform ToS + per-tenant custom rules.

Tier 1: Global platform terms-of-service rules (always enforced).
Tier 2: Per-tenant custom guardrails (max 1000 chars, user-defined).

Enforcement:
- Pre-publish: LLM evaluates each draft against all applicable rules.
- Violations flag the post for human review with annotations.
- Re-check on publish if post was edited after last guardrail check.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from ortobahn.db import Database
from ortobahn.llm import call_llm, parse_json_response
from ortobahn.models import DraftPost

logger = logging.getLogger("ortobahn.guardrails")

# ---------------------------------------------------------------------------
# Tier 1: Global platform ToS rules
# ---------------------------------------------------------------------------

GLOBAL_RULES: list[dict[str, str]] = [
    {
        "id": "no-hate-speech",
        "rule": "Must not contain hate speech, slurs, or content targeting protected groups.",
        "severity": "block",
    },
    {
        "id": "no-violence",
        "rule": "Must not promote or glorify violence, self-harm, or dangerous activities.",
        "severity": "block",
    },
    {
        "id": "no-misleading-health",
        "rule": "Must not make unverified health or medical claims.",
        "severity": "block",
    },
    {
        "id": "no-spam-manipulation",
        "rule": "Must not use engagement bait, clickbait, or manipulation tactics (fake urgency, misleading claims).",
        "severity": "warn",
    },
    {
        "id": "no-impersonation",
        "rule": "Must not impersonate real people, organizations, or official accounts.",
        "severity": "block",
    },
    {
        "id": "no-illegal-content",
        "rule": "Must not promote illegal activities, fraud, or scams.",
        "severity": "block",
    },
    {
        "id": "disclosure-ai",
        "rule": "Must not falsely claim human authorship when required by platform policies.",
        "severity": "warn",
    },
    {
        "id": "no-adult-content",
        "rule": "Must not contain sexually explicit or pornographic content.",
        "severity": "block",
    },
]


def get_global_rules() -> list[dict[str, str]]:
    """Return the global ToS rules."""
    return GLOBAL_RULES


# ---------------------------------------------------------------------------
# Tier 2: Per-tenant custom guardrails
# ---------------------------------------------------------------------------


def get_custom_guardrails(db: Database, client_id: str) -> str:
    """Return the tenant's custom guardrails text (may be empty)."""
    row = db.fetchone("SELECT custom_guardrails FROM clients WHERE id = ?", (client_id,))
    return (row["custom_guardrails"] or "") if row else ""


def save_custom_guardrails(db: Database, client_id: str, text: str) -> None:
    """Save custom guardrails (max 1000 chars)."""
    db.execute(
        "UPDATE clients SET custom_guardrails = ? WHERE id = ?",
        (text[:1000], client_id),
        commit=True,
    )


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------

_EVAL_SYSTEM = """You are a content safety evaluator. Given a draft social media post and a set of rules,
check whether the post violates any rule. Be strict but fair — only flag genuine violations, not edge cases.

Return JSON:
{
  "violations": [
    {"rule_id": "...", "severity": "block|warn", "explanation": "brief reason"}
  ],
  "clean": true|false
}

If no violations, return {"violations": [], "clean": true}."""


class GuardrailResult:
    """Result of guardrail evaluation for a single draft."""

    __slots__ = ("violations", "clean")

    def __init__(self, violations: list[dict], clean: bool):
        self.violations = violations
        self.clean = clean

    @property
    def has_blocks(self) -> bool:
        return any(v.get("severity") == "block" for v in self.violations)

    @property
    def has_warnings(self) -> bool:
        return any(v.get("severity") == "warn" for v in self.violations)

    def to_json(self) -> str:
        return json.dumps({"violations": self.violations, "clean": self.clean})

    @classmethod
    def from_json(cls, s: str) -> GuardrailResult:
        data = json.loads(s)
        return cls(violations=data.get("violations", []), clean=data.get("clean", True))


def evaluate_draft(
    draft: DraftPost,
    custom_guardrails: str = "",
) -> GuardrailResult:
    """Evaluate a single draft against all guardrails using one LLM call.

    Returns GuardrailResult with any violations found.
    """
    rules_text = _build_rules_text(custom_guardrails)

    user_msg = (
        f"## Post to evaluate\n"
        f"Platform: {draft.platform.value}\n"
        f"Text: {draft.text}\n\n"
        f"## Rules\n{rules_text}"
    )

    try:
        response = call_llm(user_msg, system_prompt=_EVAL_SYSTEM, max_tokens=500)
        from pydantic import BaseModel

        class _EvalResponse(BaseModel):
            violations: list[dict] = []
            clean: bool = True

        parsed = parse_json_response(response.text, _EvalResponse)
        return GuardrailResult(violations=parsed.violations, clean=parsed.clean)
    except Exception as e:
        logger.warning(f"Guardrail evaluation failed: {e}")
        # On failure, don't block — return clean with a warning
        return GuardrailResult(violations=[], clean=True)


def evaluate_drafts(
    drafts: list[DraftPost],
    custom_guardrails: str = "",
) -> list[GuardrailResult]:
    """Evaluate multiple drafts. Batches up to 5 per LLM call for efficiency."""
    if not drafts:
        return []

    rules_text = _build_rules_text(custom_guardrails)
    results: list[GuardrailResult] = []

    # Process in batches of 5
    for i in range(0, len(drafts), 5):
        batch = drafts[i : i + 5]
        batch_results = _evaluate_batch(batch, rules_text)
        results.extend(batch_results)

    return results


def _build_rules_text(custom_guardrails: str = "") -> str:
    """Build the full rules text from global + custom."""
    lines = ["### Global Platform Rules (Tier 1)"]
    for rule in GLOBAL_RULES:
        lines.append(f"- [{rule['id']}] ({rule['severity']}): {rule['rule']}")

    if custom_guardrails.strip():
        lines.append("\n### Custom Client Rules (Tier 2)")
        for i, line in enumerate(custom_guardrails.strip().splitlines(), 1):
            line = line.strip()
            if line:
                lines.append(f"- [custom-{i}] (warn): {line}")

    return "\n".join(lines)


_BATCH_SYSTEM = """You are a content safety evaluator. Given multiple draft posts and a set of rules,
check whether each post violates any rule. Be strict but fair — only flag genuine violations.

Return JSON array (one entry per post, in order):
[
  {"violations": [{"rule_id": "...", "severity": "block|warn", "explanation": "brief"}], "clean": true|false},
  ...
]"""


def _evaluate_batch(
    drafts: list[DraftPost],
    rules_text: str,
) -> list[GuardrailResult]:
    """Evaluate a batch of drafts in one LLM call."""
    parts = ["## Posts to evaluate"]
    for i, draft in enumerate(drafts, 1):
        parts.append(f"\n### Post {i}")
        parts.append(f"Platform: {draft.platform.value}")
        parts.append(f"Text: {draft.text}")

    parts.append(f"\n## Rules\n{rules_text}")
    user_msg = "\n".join(parts)

    try:
        response = call_llm(user_msg, system_prompt=_BATCH_SYSTEM, max_tokens=1000)
        # Parse the JSON array
        text = response.text.strip()
        # Handle markdown code fences
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text[3:]
            if text.endswith("```"):
                text = text[:-3]
            text = text.strip()
        parsed = json.loads(text)
        if not isinstance(parsed, list):
            parsed = [parsed]

        results = []
        for j, entry in enumerate(parsed):
            if j >= len(drafts):
                break
            results.append(
                GuardrailResult(
                    violations=entry.get("violations", []),
                    clean=entry.get("clean", True),
                )
            )
        # Pad if LLM returned fewer results than drafts
        while len(results) < len(drafts):
            results.append(GuardrailResult(violations=[], clean=True))
        return results
    except Exception as e:
        logger.warning(f"Batch guardrail evaluation failed: {e}")
        return [GuardrailResult(violations=[], clean=True) for _ in drafts]


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------


def save_guardrail_result(db: Database, post_id: str, result: GuardrailResult) -> None:
    """Persist guardrail check result on a post."""
    now = datetime.now(timezone.utc).isoformat()
    db.execute(
        "UPDATE posts SET guardrail_violations = ?, guardrail_checked_at = ? WHERE id = ?",
        (result.to_json() if result.violations else None, now, post_id),
        commit=True,
    )


def needs_recheck(post: dict) -> bool:
    """Check if a post was edited after its last guardrail check."""
    checked_at = post.get("guardrail_checked_at")
    edited_at = post.get("edited_at")
    if not checked_at or not edited_at:
        return not checked_at  # Never checked = needs check
    return str(edited_at) > str(checked_at)
