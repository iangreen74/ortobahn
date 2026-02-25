"""Tenant article CRUD and publishing routes."""

from __future__ import annotations

import logging

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from fastapi.responses import RedirectResponse

from ortobahn.auth import AuthClient

logger = logging.getLogger("ortobahn.web.tenant")

router = APIRouter(prefix="/my")


@router.get("/articles")
async def tenant_articles(request: Request, client: AuthClient):
    """List articles with status badges and publication errors."""
    db = request.app.state.db
    templates = request.app.state.templates
    articles = db.get_recent_articles(client["id"], limit=50)
    pubs_by_article: dict = {}
    has_generating = False
    for a in articles:
        pubs_by_article[a["id"]] = db.get_article_publications(a["id"])
        if a.get("status") == "generating":
            has_generating = True
    return templates.TemplateResponse(
        "tenant_articles.html",
        {
            "request": request,
            "client": client,
            "articles": articles,
            "pubs_by_article": pubs_by_article,
            "has_generating": has_generating,
        },
    )


@router.get("/articles/{article_id}")
async def tenant_article_detail(request: Request, article_id: str, client: AuthClient):
    """View full article content before publishing."""
    db = request.app.state.db
    templates = request.app.state.templates
    article = db.get_article(article_id)
    if not article or article.get("client_id") != client["id"]:
        raise HTTPException(status_code=404, detail="Article not found")
    pubs = db.get_article_publications(article_id)
    return templates.TemplateResponse(
        "tenant_article_detail.html",
        {"request": request, "client": client, "article": article, "pubs": pubs},
    )


@router.post("/articles/{article_id}/approve")
async def tenant_approve_article(request: Request, article_id: str, client: AuthClient):
    db = request.app.state.db
    article = db.get_article(article_id)
    if not article or article.get("client_id") != client["id"]:
        raise HTTPException(status_code=404, detail="Article not found")
    db.approve_article(article_id)
    return RedirectResponse("/my/articles", status_code=303)


@router.post("/articles/{article_id}/reject")
async def tenant_reject_article(request: Request, article_id: str, client: AuthClient):
    db = request.app.state.db
    article = db.get_article(article_id)
    if not article or article.get("client_id") != client["id"]:
        raise HTTPException(status_code=404, detail="Article not found")
    db.reject_article(article_id)
    return RedirectResponse("/my/articles", status_code=303)


@router.post("/articles/{article_id}/edit")
async def tenant_edit_article(request: Request, article_id: str, client: AuthClient):
    db = request.app.state.db
    article = db.get_article(article_id)
    if not article or article.get("client_id") != client["id"]:
        raise HTTPException(status_code=404, detail="Article not found")
    form = await request.form()
    db.update_article_body(
        article_id,
        title=form.get("title", article["title"]),
        subtitle=form.get("subtitle", article.get("subtitle", "")),
        body_markdown=form.get("body_markdown", article["body_markdown"]),
    )
    return RedirectResponse("/my/articles", status_code=303)


@router.post("/articles/{article_id}/publish")
async def tenant_publish_article(
    request: Request, article_id: str, background_tasks: BackgroundTasks, client: AuthClient
):
    """Approve and publish an article to configured platforms."""
    db = request.app.state.db
    settings = request.app.state.settings
    article = db.get_article(article_id)
    if not article or article.get("client_id") != client["id"]:
        raise HTTPException(status_code=404, detail="Article not found")

    db.approve_article(article_id)

    def _do_publish():
        from ortobahn.orchestrator import Pipeline

        pipeline = Pipeline(settings)
        try:
            pub_results = pipeline._publish_article(article_id, client["id"])
            if not pub_results:
                logger.warning(f"Article {article_id}: no platforms configured, marking as failed")
                pipeline.db.execute(
                    "UPDATE articles SET status='failed', updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (article_id,),
                    commit=True,
                )
            elif any(r["status"] == "published" for r in pub_results):
                pipeline.db.execute(
                    "UPDATE articles SET status='published', updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (article_id,),
                    commit=True,
                )
            else:
                logger.warning(f"Article {article_id}: all platforms skipped/failed, marking as failed")
                pipeline.db.execute(
                    "UPDATE articles SET status='failed', updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (article_id,),
                    commit=True,
                )
        except Exception as e:
            logger.error(f"Article publish failed: {e}")
        finally:
            pipeline.close()

    background_tasks.add_task(_do_publish)
    return RedirectResponse("/my/articles", status_code=303)


@router.post("/generate-article")
async def tenant_generate_article(request: Request, background_tasks: BackgroundTasks, client: AuthClient):
    """Trigger one-shot article generation."""
    settings = request.app.state.settings
    db = request.app.state.db

    # Pre-check: ensure articles are enabled for this client
    if not client.get("article_enabled"):
        db.execute("UPDATE clients SET article_enabled=1 WHERE id=?", (client["id"],), commit=True)
        client = db.get_client(client["id"]) or client

    if not settings.anthropic_api_key:
        return RedirectResponse("/my/articles?msg=error&detail=no_api_key", status_code=303)

    # Ensure subscription/internal status allows article generation
    if not client.get("internal") and client.get("subscription_status") not in ("active", "trialing"):
        return RedirectResponse("/my/articles?msg=error&detail=no_subscription", status_code=303)

    def _do_generate():
        from ortobahn.orchestrator import Pipeline

        pipeline = Pipeline(settings)
        try:
            result = pipeline.run_article_cycle(client_id=client["id"])
            status = result.get("status", "unknown")
            if status == "success":
                logger.info(f"Article generated for {client['id']}: {result.get('title', '')}")
            elif status in ("skipped", "error"):
                reason = result.get("error", "unknown")
                logger.warning(f"Article generation {status} for {client['id']}: {reason}")
            else:
                logger.info(f"Article generation for {client['id']}: {status}")
        except Exception as e:
            logger.error(f"Article generation failed for {client['id']}: {e}", exc_info=True)
        finally:
            pipeline.close()

    background_tasks.add_task(_do_generate)
    return RedirectResponse("/my/articles?msg=generating", status_code=303)
