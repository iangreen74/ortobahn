"""Weekly Digest — generates and sends performance summary emails."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone

from ortobahn.db import Database

logger = logging.getLogger("ortobahn.digest")

# Metrics JOIN pattern (same as dashboard)
_METRICS_JOIN = (
    " LEFT JOIN metrics m ON p.id = m.post_id"
    " AND m.id = (SELECT m2.id FROM metrics m2 WHERE m2.post_id = p.id ORDER BY m2.measured_at DESC LIMIT 1)"
)


class WeeklyDigest:
    """Generate and send weekly performance digest emails."""

    def __init__(self, db: Database) -> None:
        self.db = db

    def get_clients_due_for_digest(self, now: datetime | None = None) -> list[dict]:
        """Find clients who are due for their weekly digest.

        A client is due if:
        - digest_enabled = 1
        - digest_email is not empty
        - Current day of week matches digest_day (0=Monday)
        - Current hour matches digest_hour
        - No digest was sent in the last 23 hours (prevent double-sends)
        """
        now = now or datetime.now(timezone.utc)
        current_day = now.weekday()  # 0=Monday
        current_hour = now.hour
        cutoff = (now - timedelta(hours=23)).isoformat()

        clients = self.db.fetchall(
            "SELECT id, name, digest_email, digest_day, digest_hour"
            " FROM clients"
            " WHERE active=1 AND digest_enabled=1 AND digest_email != ''"
            " AND digest_day=? AND digest_hour=?",
            (current_day, current_hour),
        )

        due = []
        for c in clients:
            # Check no recent digest
            recent = self.db.fetchone(
                "SELECT id FROM digest_history WHERE client_id=? AND sent_at > ? AND status='sent'",
                (c["id"], cutoff),
            )
            if not recent:
                due.append(dict(c))
        return due

    def generate_digest(self, client_id: str, days: int = 7) -> dict:
        """Generate digest data for a client covering the last N days.

        Returns a dict with:
        - posts_published: int
        - total_engagement: int
        - avg_engagement: float
        - top_post: dict | None
        - engagement_change_pct: float
        - platform_breakdown: list[dict]
        - period_start: str
        - period_end: str
        """
        now = datetime.now(timezone.utc)
        period_end = now.isoformat()
        period_start = (now - timedelta(days=days)).isoformat()

        # Posts published this period
        count_row = self.db.fetchone(
            "SELECT COUNT(*) as c FROM posts WHERE status='published' AND client_id=? AND published_at >= ?",
            (client_id, period_start),
        )
        posts_published = count_row["c"] if count_row else 0

        # Total and average engagement
        eng_row = self.db.fetchone(
            "SELECT SUM(COALESCE(m.like_count,0)+COALESCE(m.repost_count,0)+COALESCE(m.reply_count,0)) as total,"
            " AVG(COALESCE(m.like_count,0)+COALESCE(m.repost_count,0)+COALESCE(m.reply_count,0)) as avg_eng"
            " FROM posts p" + _METRICS_JOIN + " WHERE p.status='published' AND p.client_id=? AND p.published_at >= ?",
            (client_id, period_start),
        )
        total_engagement = int(eng_row["total"] or 0) if eng_row else 0
        avg_engagement = float(eng_row["avg_eng"] or 0) if eng_row else 0

        # Top post
        top_post = self.db.fetchone(
            "SELECT p.id, p.text, p.platform,"
            " COALESCE(m.like_count,0) as like_count,"
            " COALESCE(m.repost_count,0) as repost_count,"
            " COALESCE(m.reply_count,0) as reply_count"
            " FROM posts p" + _METRICS_JOIN + " WHERE p.status='published' AND p.client_id=? AND p.published_at >= ?"
            " ORDER BY (COALESCE(m.like_count,0)+COALESCE(m.repost_count,0)+COALESCE(m.reply_count,0)) DESC"
            " LIMIT 1",
            (client_id, period_start),
        )
        top_post_dict = dict(top_post) if top_post else None

        # Engagement change vs prior period
        prior_start = (now - timedelta(days=days * 2)).isoformat()
        prior_eng = self.db.fetchone(
            "SELECT AVG(COALESCE(m.like_count,0)+COALESCE(m.repost_count,0)+COALESCE(m.reply_count,0)) as avg_eng"
            " FROM posts p"
            + _METRICS_JOIN
            + " WHERE p.status='published' AND p.client_id=? AND p.published_at >= ? AND p.published_at < ?",
            (client_id, prior_start, period_start),
        )
        prior_avg = float(prior_eng["avg_eng"] or 0) if prior_eng else 0
        if prior_avg > 0:
            engagement_change_pct = round(((avg_engagement - prior_avg) / prior_avg) * 100)
        else:
            engagement_change_pct = 0

        # Platform breakdown
        platform_rows = self.db.fetchall(
            "SELECT p.platform, COUNT(*) as count,"
            " SUM(COALESCE(m.like_count,0)+COALESCE(m.repost_count,0)+COALESCE(m.reply_count,0)) as engagement"
            " FROM posts p" + _METRICS_JOIN + " WHERE p.status='published' AND p.client_id=? AND p.published_at >= ?"
            " GROUP BY p.platform",
            (client_id, period_start),
        )

        return {
            "posts_published": posts_published,
            "total_engagement": total_engagement,
            "avg_engagement": round(avg_engagement, 1),
            "top_post": top_post_dict,
            "engagement_change_pct": engagement_change_pct,
            "platform_breakdown": [dict(r) for r in platform_rows],
            "period_start": period_start,
            "period_end": period_end,
        }

    def render_email(self, client_name: str, digest_data: dict) -> str:
        """Render digest data as an HTML email."""
        posts = digest_data["posts_published"]
        total_eng = digest_data["total_engagement"]
        change = digest_data["engagement_change_pct"]
        top = digest_data.get("top_post")
        platforms = digest_data.get("platform_breakdown", [])

        change_color = "#4caf50" if change >= 0 else "#ef5350"
        change_arrow = "+" if change >= 0 else ""

        top_section = ""
        if top:
            top_text = (top.get("text") or "")[:200]
            top_eng = (top.get("like_count") or 0) + (top.get("repost_count") or 0) + (top.get("reply_count") or 0)
            top_section = f"""
            <div style="background:#f8f9ff;border-radius:8px;padding:16px;margin-top:20px;">
                <h3 style="margin:0 0 8px;font-size:14px;color:#6366f1;">Top Performing Post</h3>
                <p style="margin:0 0 8px;font-size:14px;line-height:1.5;color:#333;">{_esc(top_text)}</p>
                <span style="font-size:13px;color:#666;">{top_eng} total interactions on {_esc(top.get("platform", ""))}</span>
            </div>"""

        platform_rows_html = ""
        for p in platforms:
            platform_rows_html += (
                f'<tr><td style="padding:8px 12px;border-bottom:1px solid #eee;">{_esc(p.get("platform", ""))}</td>'
                f'<td style="padding:8px 12px;border-bottom:1px solid #eee;text-align:center;">{p.get("count", 0)}</td>'
                f'<td style="padding:8px 12px;border-bottom:1px solid #eee;text-align:center;">{p.get("engagement", 0)}</td></tr>'
            )

        return f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0"></head>
<body style="margin:0;padding:0;background:#f5f5f5;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;">
<div style="max-width:560px;margin:0 auto;padding:20px;">
    <div style="background:linear-gradient(135deg,#6366f1,#764ba2);border-radius:12px 12px 0 0;padding:24px 32px;color:white;">
        <h1 style="margin:0;font-size:22px;">Weekly Performance Digest</h1>
        <p style="margin:4px 0 0;opacity:0.85;font-size:14px;">{_esc(client_name)}</p>
    </div>
    <div style="background:white;padding:24px 32px;border-radius:0 0 12px 12px;">
        <div style="display:flex;gap:20px;margin-bottom:20px;flex-wrap:wrap;">
            <div style="flex:1;min-width:120px;text-align:center;padding:16px;background:#f8f9ff;border-radius:8px;">
                <div style="font-size:28px;font-weight:700;color:#6366f1;">{posts}</div>
                <div style="font-size:12px;color:#666;margin-top:4px;">Posts Published</div>
            </div>
            <div style="flex:1;min-width:120px;text-align:center;padding:16px;background:#f8f9ff;border-radius:8px;">
                <div style="font-size:28px;font-weight:700;color:#6366f1;">{total_eng}</div>
                <div style="font-size:12px;color:#666;margin-top:4px;">Total Engagement</div>
            </div>
            <div style="flex:1;min-width:120px;text-align:center;padding:16px;background:#f8f9ff;border-radius:8px;">
                <div style="font-size:28px;font-weight:700;color:{change_color};">{change_arrow}{change}%</div>
                <div style="font-size:12px;color:#666;margin-top:4px;">vs Last Week</div>
            </div>
        </div>
        {top_section}
        {
            ""
            if not platform_rows_html
            else f'''
        <h3 style="margin:20px 0 12px;font-size:14px;color:#333;">Platform Breakdown</h3>
        <table style="width:100%;border-collapse:collapse;font-size:14px;">
            <tr style="background:#f8f9ff;">
                <th style="padding:8px 12px;text-align:left;">Platform</th>
                <th style="padding:8px 12px;text-align:center;">Posts</th>
                <th style="padding:8px 12px;text-align:center;">Engagement</th>
            </tr>
            {platform_rows_html}
        </table>'''
        }
        <div style="margin-top:24px;text-align:center;">
            <a href="https://app.ortobahn.com/my/analytics"
               style="display:inline-block;padding:12px 32px;background:linear-gradient(135deg,#6366f1,#764ba2);color:white;text-decoration:none;border-radius:8px;font-weight:600;font-size:14px;">
                View Full Analytics
            </a>
        </div>
        <p style="margin-top:20px;font-size:12px;color:#999;text-align:center;">
            You're receiving this because digest emails are enabled for your Ortobahn account.
            <a href="https://app.ortobahn.com/my/settings" style="color:#6366f1;">Manage preferences</a>
        </p>
    </div>
</div>
</body></html>"""

    def send_digest(
        self,
        client_id: str,
        client_name: str,
        to_email: str,
        ses_client,
    ) -> bool:
        """Generate and send a weekly digest email. Returns True on success."""
        digest_data = self.generate_digest(client_id)
        html = self.render_email(client_name, digest_data)

        digest_id = str(uuid.uuid4())

        message_id = ses_client.send_html_email(
            to_email=to_email,
            subject=f"Your Weekly Marketing Digest — {client_name}",
            html_body=html,
            text_body=f"Weekly digest for {client_name}: {digest_data['posts_published']} posts, "
            f"{digest_data['total_engagement']} total engagement",
        )

        if message_id:
            self.db.execute(
                "INSERT INTO digest_history (id, client_id, sent_at, period_start, period_end,"
                " posts_published, total_engagement, top_post_id, status)"
                " VALUES (?, ?, CURRENT_TIMESTAMP, ?, ?, ?, ?, ?, 'sent')",
                (
                    digest_id,
                    client_id,
                    digest_data["period_start"],
                    digest_data["period_end"],
                    digest_data["posts_published"],
                    digest_data["total_engagement"],
                    digest_data["top_post"]["id"] if digest_data.get("top_post") else None,
                ),
                commit=True,
            )
            logger.info("Sent weekly digest to %s for client %s", to_email, client_id)
            return True
        else:
            self.db.execute(
                "INSERT INTO digest_history (id, client_id, sent_at, period_start, period_end,"
                " posts_published, total_engagement, top_post_id, status, error)"
                " VALUES (?, ?, CURRENT_TIMESTAMP, ?, ?, ?, ?, ?, 'failed', 'SES send failed')",
                (
                    digest_id,
                    client_id,
                    digest_data["period_start"],
                    digest_data["period_end"],
                    digest_data["posts_published"],
                    digest_data["total_engagement"],
                    digest_data["top_post"]["id"] if digest_data.get("top_post") else None,
                ),
                commit=True,
            )
            logger.error("Failed to send weekly digest for client %s", client_id)
            return False


def _esc(text: str) -> str:
    """Minimal HTML escaping for email templates."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
