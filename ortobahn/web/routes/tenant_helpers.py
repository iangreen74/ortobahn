"""Shared helpers for tenant route modules."""

from __future__ import annotations

import logging

from ortobahn.models import Platform

logger = logging.getLogger("ortobahn.web.tenant")

# Common metrics JOIN: posts LEFT JOIN latest metrics snapshot (exactly one row per post)
_METRICS_JOIN = (
    " LEFT JOIN metrics m ON p.id = m.post_id"
    " AND m.id = (SELECT m2.id FROM metrics m2 WHERE m2.post_id = p.id ORDER BY m2.measured_at DESC LIMIT 1)"
)


def _run_tenant_pipeline(
    settings, client_id: str, platforms: list[Platform], publish: bool = False
):
    """Run pipeline in background for a tenant."""
    from ortobahn.orchestrator import Pipeline

    pipeline = Pipeline(settings, dry_run=not publish)
    try:
        result = pipeline.run_cycle(
            client_id=client_id,
            target_platforms=platforms,
            generate_only=not publish,
        )
        logger.info(f"Tenant pipeline complete for {client_id}: {result['posts_published']} published")
    except Exception as e:
        logger.error(f"Tenant pipeline failed for {client_id}: {e}")
    finally:
        pipeline.close()
