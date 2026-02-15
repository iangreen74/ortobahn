"""Pipeline management routes."""

from __future__ import annotations

import logging

from fastapi import APIRouter, BackgroundTasks, Depends, Form, Request
from fastapi.responses import RedirectResponse

from ortobahn.auth import get_admin_client
from ortobahn.models import Platform

logger = logging.getLogger("ortobahn.web.pipeline")

router = APIRouter(dependencies=[Depends(get_admin_client)])


def _run_pipeline(settings, client_id: str, platforms: list[Platform], publish: bool = False):
    """Run pipeline in background."""
    from ortobahn.orchestrator import Pipeline

    pipeline = Pipeline(settings, dry_run=not publish)
    try:
        result = pipeline.run_cycle(
            client_id=client_id,
            target_platforms=platforms,
            generate_only=not publish,
        )
        logger.info(f"Pipeline complete: {result['total_drafts']} drafts, {result['posts_published']} published")
    except Exception as e:
        logger.error(f"Pipeline failed: {e}")
    finally:
        pipeline.close()


@router.get("/")
async def pipeline_history(request: Request):
    db = request.app.state.db
    templates = request.app.state.templates

    runs = db.get_recent_runs(limit=20)
    clients = db.get_all_clients()

    return templates.TemplateResponse(
        "pipeline.html",
        {
            "request": request,
            "runs": runs,
            "clients": clients,
        },
    )


@router.post("/run")
async def trigger_run(
    request: Request,
    background_tasks: BackgroundTasks,
    client_id: str = Form("default"),
    platforms: str = Form("twitter,linkedin"),
    publish: str = Form(""),
):
    settings = request.app.state.settings
    platform_list = [Platform(p.strip()) for p in platforms.split(",") if p.strip()]
    do_publish = publish == "true"

    background_tasks.add_task(_run_pipeline, settings, client_id, platform_list, do_publish)

    return RedirectResponse("/pipeline/", status_code=303)
