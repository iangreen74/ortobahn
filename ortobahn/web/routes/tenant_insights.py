"""Tenant AI insights engine routes."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from ortobahn.auth import AuthClient
from ortobahn.web.utils import escape as _escape

logger = logging.getLogger("ortobahn.web.tenant")

router = APIRouter(prefix="/my")


def _generate_insights(db, client_id: str) -> list[dict]:
    """Analyze client post data and generate actionable insights.

    Each insight is a dict with keys: icon, title, detail, category.
    Queries the database directly -- no LLM call needed for speed.
    """
    insights: list[dict] = []

    # Metrics live in a separate table; join to get engagement data
    _MJ = (
        " LEFT JOIN metrics m ON p.id = m.post_id"
        " AND m.id = (SELECT m2.id FROM metrics m2 WHERE m2.post_id = p.id ORDER BY m2.measured_at DESC LIMIT 1)"
    )

    # ------------------------------------------------------------------
    # 1. Best posting day analysis
    # ------------------------------------------------------------------
    try:
        day_stats = db.fetchall(
            "SELECT CASE CAST(strftime('%%w', p.published_at) AS INTEGER)"
            "  WHEN 0 THEN 'Sunday' WHEN 1 THEN 'Monday' WHEN 2 THEN 'Tuesday'"
            "  WHEN 3 THEN 'Wednesday' WHEN 4 THEN 'Thursday'"
            "  WHEN 5 THEN 'Friday' WHEN 6 THEN 'Saturday' END as day_name,"
            " COUNT(*) as cnt,"
            " AVG(COALESCE(m.like_count,0)+COALESCE(m.repost_count,0)+COALESCE(m.reply_count,0)) as avg_eng"
            " FROM posts p" + _MJ + " WHERE p.status='published' AND p.client_id=? AND p.published_at IS NOT NULL"
            " GROUP BY strftime('%%w', p.published_at)"
            " HAVING cnt >= 2"
            " ORDER BY avg_eng DESC",
            (client_id,),
        )
        if len(day_stats) >= 2:
            best_day = day_stats[0]
            worst_day = day_stats[-1]
            best_avg = best_day["avg_eng"] or 0
            worst_avg = worst_day["avg_eng"] or 0
            if worst_avg > 0:
                ratio = best_avg / worst_avg
                if ratio >= 1.5:
                    insights.append(
                        {
                            "icon": "calendar",
                            "title": f"Your posts perform {ratio:.1f}x better on {best_day['day_name']}s",
                            "detail": (
                                f"Average engagement on {best_day['day_name']}: {best_avg:.0f} vs "
                                f"{worst_day['day_name']}: {worst_avg:.0f}. "
                                f"Consider scheduling more content for {best_day['day_name']}."
                            ),
                            "category": "timing",
                        }
                    )
    except Exception:
        pass  # strftime not available on PostgreSQL

    # ------------------------------------------------------------------
    # 2. Best posting hour analysis
    # ------------------------------------------------------------------
    try:
        hour_stats = db.fetchall(
            "SELECT CAST(strftime('%%H', p.published_at) AS INTEGER) as hour,"
            " COUNT(*) as cnt,"
            " AVG(COALESCE(m.like_count,0)+COALESCE(m.repost_count,0)+COALESCE(m.reply_count,0)) as avg_eng"
            " FROM posts p" + _MJ + " WHERE p.status='published' AND p.client_id=? AND p.published_at IS NOT NULL"
            " GROUP BY strftime('%%H', p.published_at)"
            " HAVING cnt >= 2"
            " ORDER BY avg_eng DESC",
            (client_id,),
        )
        if hour_stats:
            top_hours = hour_stats[:3]
            hours_str = ", ".join(f"{h['hour']}:00" for h in top_hours)
            if len(top_hours) >= 2:
                insights.append(
                    {
                        "icon": "clock",
                        "title": f"Your audience is most active around {top_hours[0]['hour']}:00",
                        "detail": (
                            f"Top engagement hours: {hours_str}. "
                            f"Posts at {top_hours[0]['hour']}:00 average "
                            f"{top_hours[0]['avg_eng']:.0f} engagement."
                        ),
                        "category": "timing",
                    }
                )
    except Exception:
        pass  # strftime not available on PostgreSQL

    # ------------------------------------------------------------------
    # 3. Content type / platform comparison
    # ------------------------------------------------------------------
    platform_stats = db.fetchall(
        "SELECT p.platform, COUNT(*) as cnt,"
        " AVG(COALESCE(m.like_count,0)+COALESCE(m.repost_count,0)+COALESCE(m.reply_count,0)) as avg_eng"
        " FROM posts p" + _MJ + " WHERE p.status='published' AND p.client_id=?"
        " GROUP BY p.platform HAVING cnt >= 2"
        " ORDER BY avg_eng DESC",
        (client_id,),
    )
    if len(platform_stats) >= 2:
        top_plat = platform_stats[0]
        bottom_plat = platform_stats[-1]
        top_avg = top_plat["avg_eng"] or 0
        bottom_avg = bottom_plat["avg_eng"] or 0
        if bottom_avg > 0:
            plat_ratio = top_avg / bottom_avg
            if plat_ratio >= 1.3:
                insights.append(
                    {
                        "icon": "target",
                        "title": (
                            f"{(top_plat['platform'] or 'generic').capitalize()} "
                            f"outperforms {(bottom_plat['platform'] or 'generic').capitalize()} "
                            f"by {plat_ratio:.1f}x"
                        ),
                        "detail": (
                            f"Average engagement: {top_plat['platform'] or 'generic'} = {top_avg:.0f}, "
                            f"{bottom_plat['platform'] or 'generic'} = {bottom_avg:.0f}. "
                            "Consider focusing more effort on your stronger platform."
                        ),
                        "category": "platform",
                    }
                )

    # ------------------------------------------------------------------
    # 4. Content length insight (short vs long posts)
    # ------------------------------------------------------------------
    short_stats = db.fetchone(
        "SELECT COUNT(*) as cnt,"
        " AVG(COALESCE(m.like_count,0)+COALESCE(m.repost_count,0)+COALESCE(m.reply_count,0)) as avg_eng"
        " FROM posts p" + _MJ + " WHERE p.status='published' AND p.client_id=? AND LENGTH(p.text) < 150",
        (client_id,),
    )
    long_stats = db.fetchone(
        "SELECT COUNT(*) as cnt,"
        " AVG(COALESCE(m.like_count,0)+COALESCE(m.repost_count,0)+COALESCE(m.reply_count,0)) as avg_eng"
        " FROM posts p" + _MJ + " WHERE p.status='published' AND p.client_id=? AND LENGTH(p.text) >= 150",
        (client_id,),
    )
    if short_stats and long_stats and (short_stats["cnt"] or 0) >= 2 and (long_stats["cnt"] or 0) >= 2:
        short_avg = short_stats["avg_eng"] or 0
        long_avg = long_stats["avg_eng"] or 0
        if short_avg > 0 and long_avg > 0:
            if short_avg > long_avg * 1.3:
                insights.append(
                    {
                        "icon": "zap",
                        "title": "Shorter posts are winning",
                        "detail": (
                            f"Posts under 150 chars average {short_avg:.0f} engagement vs "
                            f"{long_avg:.0f} for longer posts. Keep it punchy."
                        ),
                        "category": "content",
                    }
                )
            elif long_avg > short_avg * 1.3:
                insights.append(
                    {
                        "icon": "file-text",
                        "title": "Longer posts get more engagement",
                        "detail": (
                            f"Posts over 150 chars average {long_avg:.0f} engagement vs "
                            f"{short_avg:.0f} for shorter posts. Your audience appreciates depth."
                        ),
                        "category": "content",
                    }
                )

    # ------------------------------------------------------------------
    # 5. Posting cadence health
    # ------------------------------------------------------------------
    recent_count_row = db.fetchone(
        "SELECT COUNT(*) as cnt FROM posts WHERE status='published' AND client_id=?"
        " AND published_at >= datetime('now', '-7 days')",
        (client_id,),
    )
    prev_count_row = db.fetchone(
        "SELECT COUNT(*) as cnt FROM posts WHERE status='published' AND client_id=?"
        " AND published_at >= datetime('now', '-14 days')"
        " AND published_at < datetime('now', '-7 days')",
        (client_id,),
    )
    recent_cnt = recent_count_row["cnt"] if recent_count_row else 0
    prev_cnt = prev_count_row["cnt"] if prev_count_row else 0

    if prev_cnt > 0 and recent_cnt > 0:
        change_pct = ((recent_cnt - prev_cnt) / prev_cnt) * 100
        if change_pct >= 30:
            insights.append(
                {
                    "icon": "trending-up",
                    "title": f"Posting volume up {change_pct:.0f}% this week",
                    "detail": (
                        f"{recent_cnt} posts this week vs {prev_cnt} last week. Great momentum -- keep it going!"
                    ),
                    "category": "cadence",
                }
            )
        elif change_pct <= -30:
            insights.append(
                {
                    "icon": "trending-down",
                    "title": f"Posting volume dropped {abs(change_pct):.0f}%",
                    "detail": (
                        f"{recent_cnt} posts this week vs {prev_cnt} last week. "
                        "Consistent posting builds audience growth."
                    ),
                    "category": "cadence",
                }
            )
    elif recent_cnt == 0 and prev_cnt > 0:
        insights.append(
            {
                "icon": "alert-triangle",
                "title": "No posts published this week",
                "detail": (
                    f"You published {prev_cnt} posts last week but none this week. "
                    "Run the pipeline to keep your audience engaged."
                ),
                "category": "cadence",
            }
        )

    # ------------------------------------------------------------------
    # 6. Engagement growth trend
    # ------------------------------------------------------------------
    recent_eng_row = db.fetchone(
        "SELECT AVG(COALESCE(m.like_count,0)+COALESCE(m.repost_count,0)+COALESCE(m.reply_count,0)) as avg_eng"
        " FROM posts p" + _MJ + " WHERE p.status='published' AND p.client_id=?"
        " AND p.published_at >= datetime('now', '-7 days')",
        (client_id,),
    )
    prev_eng_row = db.fetchone(
        "SELECT AVG(COALESCE(m.like_count,0)+COALESCE(m.repost_count,0)+COALESCE(m.reply_count,0)) as avg_eng"
        " FROM posts p" + _MJ + " WHERE p.status='published' AND p.client_id=?"
        " AND p.published_at >= datetime('now', '-14 days')"
        " AND p.published_at < datetime('now', '-7 days')",
        (client_id,),
    )
    recent_eng = (recent_eng_row["avg_eng"] or 0) if recent_eng_row else 0
    prev_eng = (prev_eng_row["avg_eng"] or 0) if prev_eng_row else 0

    if prev_eng > 0 and recent_eng > 0:
        eng_change = ((recent_eng - prev_eng) / prev_eng) * 100
        if eng_change >= 20:
            insights.append(
                {
                    "icon": "award",
                    "title": f"Engagement up {eng_change:.0f}% week-over-week",
                    "detail": (
                        f"Average engagement rose from {prev_eng:.1f} to {recent_eng:.1f}. "
                        "Your content strategy is resonating."
                    ),
                    "category": "growth",
                }
            )
        elif eng_change <= -20:
            insights.append(
                {
                    "icon": "bar-chart",
                    "title": f"Engagement dipped {abs(eng_change):.0f}% this week",
                    "detail": (
                        f"Average engagement dropped from {prev_eng:.1f} to {recent_eng:.1f}. "
                        "Try varying your content format or posting times."
                    ),
                    "category": "growth",
                }
            )

    # ------------------------------------------------------------------
    # 7. Failure rate warning
    # ------------------------------------------------------------------
    failed_count, total_attempts = db.get_post_failure_rate(hours=168, client_id=client_id)
    if total_attempts >= 3 and failed_count > 0:
        fail_pct = (failed_count / total_attempts) * 100
        if fail_pct >= 25:
            insights.append(
                {
                    "icon": "alert-circle",
                    "title": f"{fail_pct:.0f}% of posts failed to publish this week",
                    "detail": (
                        f"{failed_count} out of {total_attempts} posts failed. "
                        "Check your platform credentials in Settings."
                    ),
                    "category": "health",
                }
            )

    # ------------------------------------------------------------------
    # Fallback when there is not enough data
    # ------------------------------------------------------------------
    if not insights:
        total_row = db.fetchone(
            "SELECT COUNT(*) as cnt FROM posts WHERE status='published' AND client_id=?",
            (client_id,),
        )
        total = total_row["cnt"] if total_row else 0
        if total == 0:
            insights.append(
                {
                    "icon": "rocket",
                    "title": "Publish your first post to unlock insights",
                    "detail": (
                        "Insights are generated from your publishing history. "
                        "Run the pipeline above to generate and publish content, "
                        "then come back for data-driven recommendations."
                    ),
                    "category": "onboarding",
                }
            )
        elif total < 5:
            insights.append(
                {
                    "icon": "bar-chart",
                    "title": f"{total} posts published -- keep going!",
                    "detail": (
                        "We need at least 5 published posts to generate meaningful insights. "
                        f"You're {5 - total} post(s) away from unlocking trend analysis."
                    ),
                    "category": "onboarding",
                }
            )

    return insights[:5]


_INSIGHT_ICONS = {
    "calendar": '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="18" rx="2" ry="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/></svg>',
    "clock": '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>',
    "target": '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="6"/><circle cx="12" cy="12" r="2"/></svg>',
    "zap": '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/></svg>',
    "file-text": '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/></svg>',
    "trending-up": '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="23 6 13.5 15.5 8.5 10.5 1 18"/><polyline points="17 6 23 6 23 12"/></svg>',
    "trending-down": '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="23 18 13.5 8.5 8.5 13.5 1 6"/><polyline points="17 18 23 18 23 12"/></svg>',
    "alert-triangle": '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>',
    "alert-circle": '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>',
    "award": '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="8" r="7"/><polyline points="8.21 13.89 7 23 12 20 17 23 15.79 13.88"/></svg>',
    "bar-chart": '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="20" x2="12" y2="10"/><line x1="18" y1="20" x2="18" y2="4"/><line x1="6" y1="20" x2="6" y2="16"/></svg>',
    "rocket": '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4.5 16.5c-1.5 1.26-2 5-2 5s3.74-.5 5-2c.71-.84.7-2.13-.09-2.91a2.18 2.18 0 0 0-2.91-.09z"/><path d="M12 15l-3-3a22 22 0 0 1 2-3.95A12.88 12.88 0 0 1 22 2c0 2.72-.78 7.5-6 11a22.35 22.35 0 0 1-4 2z"/><path d="M9 12H4s.55-3.03 2-4c1.62-1.08 5 0 5 0"/><path d="M12 15v5s3.03-.55 4-2c1.08-1.62 0-5 0-5"/></svg>',
}

_CATEGORY_COLORS = {
    "timing": "#6366f1",
    "platform": "#00d4aa",
    "content": "#f59e0b",
    "cadence": "#3b82f6",
    "growth": "#10b981",
    "health": "#ef4444",
    "onboarding": "#8b5cf6",
}


@router.get("/api/partials/insights", response_class=HTMLResponse)
async def tenant_insights_partial(request: Request, client: AuthClient):
    """Return AI-generated insights as an HTML fragment."""
    db = request.app.state.db
    try:
        insights = _generate_insights(db, client["id"])
    except Exception:
        insights = []

    if not insights:
        return HTMLResponse('<p style="opacity: 0.6; text-align: center;">Not enough data for insights yet.</p>')

    parts = []
    for insight in insights:
        icon_svg = _INSIGHT_ICONS.get(insight["icon"], _INSIGHT_ICONS["bar-chart"])
        color = _CATEGORY_COLORS.get(insight["category"], "#6366f1")
        parts.append(
            f'<div class="insight-item" style="border-left: 3px solid {color};">'
            f'<div class="insight-header">'
            f'<span class="insight-icon" style="color: {color};">{icon_svg}</span>'
            f"<strong>{_escape(insight['title'])}</strong>"
            f"</div>"
            f'<p class="insight-detail">{_escape(insight["detail"])}</p>'
            f"</div>"
        )

    return HTMLResponse("".join(parts))


@router.get("/api/partials/post-insights", response_class=HTMLResponse)
async def post_insights_partial(request: Request, client: AuthClient):
    """Return recent 'Why This Worked' insights as an HTML fragment."""
    db = request.app.state.db
    client_id = client["id"]

    rows = db.fetchall(
        "SELECT pi.insight_text, pi.factors, pi.confidence, pi.created_at,"
        " p.text as post_text, p.platform"
        " FROM post_insights pi"
        " JOIN posts p ON pi.post_id = p.id"
        " WHERE pi.client_id=?"
        " ORDER BY pi.created_at DESC LIMIT 5",
        (client_id,),
    )

    if not rows:
        return HTMLResponse(
            '<p style="opacity: 0.6; text-align: center;">No post insights yet. '
            "Insights are generated when posts significantly outperform your average.</p>"
        )

    import json

    parts = ['<div class="post-insights-list">']
    for row in rows:
        post_preview = _escape((row.get("post_text") or "")[:80])
        platform = _escape(row.get("platform") or "")
        insight = _escape(row.get("insight_text") or "")
        conf = int((row.get("confidence") or 0) * 100)

        try:
            factors = json.loads(row.get("factors") or "[]")
        except (json.JSONDecodeError, TypeError):
            factors = []

        factor_badges = " ".join(
            f'<span class="badge draft" style="font-size: 0.7rem;">{_escape(f)}</span>'
            for f in factors[:4]
        )

        parts.append(
            f'<div class="insight-item" style="border-left: 3px solid #00d4aa;">'
            f'<div class="insight-header">'
            f'<span class="insight-icon" style="color: #00d4aa;">&#x1F4A1;</span>'
            f"<strong>Why This Worked</strong>"
            f'<span class="badge completed" style="margin-left: auto; font-size: 0.7rem;">{platform}</span>'
            f"</div>"
            f'<p class="insight-detail" style="margin: 0.25rem 0;">'
            f'<em>"{post_preview}..."</em></p>'
            f'<p class="insight-detail">{insight}</p>'
            f'<div style="margin-top: 0.25rem;">{factor_badges}</div>'
            f"</div>"
        )
    parts.append("</div>")
    return HTMLResponse("".join(parts))
