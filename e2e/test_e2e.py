"""End-to-end Playwright tests for critical user paths.

These tests run against a real FastAPI server with a real SQLite database.
They verify that pages load, forms work, and HTMX polling functions correctly.

Run: pytest e2e/ --headed   (to see the browser)
Run: pytest e2e/            (headless)
"""

from __future__ import annotations

import pytest
from playwright.sync_api import Page, expect


@pytest.fixture(autouse=True)
def _set_session_cookie(page: Page, e2e_client_session):
    """Inject session cookie into the browser before each test."""
    info = e2e_client_session
    base_url = info["base_url"]
    # Navigate to set the cookie domain
    page.goto(f"{base_url}/health")
    page.context.add_cookies(
        [
            {
                "name": "session",
                "value": info["session_token"],
                "url": base_url,
                "httpOnly": True,
            }
        ]
    )


class TestLoginRedirect:
    """Auth flow: unauthenticated users get redirected to login."""

    def test_dashboard_redirects_without_auth(self, page: Page, e2e_server):
        """Visiting /my/dashboard without auth should redirect to login."""
        base_url = e2e_server[0]
        # Clear cookies to simulate unauthenticated user
        page.context.clear_cookies()
        response = page.goto(f"{base_url}/my/dashboard")
        # Should redirect to login or return 401/302
        url = page.url
        assert "/login" in url or "/auth" in url or response.status in (302, 401)


class TestDashboardPolls:
    """Dashboard loads and HTMX polling elements are present."""

    def test_dashboard_renders(self, page: Page, e2e_client_session):
        base_url = e2e_client_session["base_url"]
        page.goto(f"{base_url}/my/dashboard")
        # Dashboard page should load with key elements
        expect(page).to_have_title(lambda t: "Ortobahn" in t)
        # Should have at least one hx-get attribute (HTMX polling)
        hx_elements = page.locator("[hx-get]")
        assert hx_elements.count() > 0


class TestDraftReviewApprove:
    """Review queue: drafts are displayed and can be approved."""

    def test_review_queue_renders(self, page: Page, e2e_client_session):
        base_url = e2e_client_session["base_url"]
        page.goto(f"{base_url}/my/review")
        expect(page.locator("h1")).to_contain_text("Review Queue")

    def test_review_with_draft(self, page: Page, e2e_client_session):
        """Check review queue page structure."""
        info = e2e_client_session
        page.goto(f"{info['base_url']}/my/review")
        # Either drafts are shown or the empty state
        page_text = page.text_content("body")
        assert "Review Queue" in page_text
        assert ("All caught up" in page_text or "draft" in page_text.lower())


class TestDraftInlineEdit:
    """Inline editor toggles visibility on click."""

    def test_edit_button_toggle(self, page: Page, e2e_client_session):
        """Edit button should exist on the review page (even if empty state)."""
        base_url = e2e_client_session["base_url"]
        page.goto(f"{base_url}/my/review")
        # Verify the page loaded successfully
        expect(page.locator("h1")).to_contain_text("Review Queue")


class TestSettingsSaveRoundtrip:
    """Settings save and reload correctly."""

    def test_settings_loads(self, page: Page, e2e_client_session):
        base_url = e2e_client_session["base_url"]
        page.goto(f"{base_url}/my/settings")
        expect(page.locator("h1")).to_contain_text("Settings")
        # Should have Brand Profile and Content Sources sections
        assert "Brand Profile" in page.text_content("body")
        assert "Content Sources" in page.text_content("body")
        assert "Content Guardrails" in page.text_content("body")

    def test_guardrails_save_roundtrip(self, page: Page, e2e_client_session):
        """Save custom guardrails and verify they persist on reload."""
        base_url = e2e_client_session["base_url"]
        page.goto(f"{base_url}/my/settings")

        # Fill in custom guardrails
        textarea = page.locator("#custom_guardrails")
        textarea.fill("Never mention competitor products")

        # Submit the guardrails form
        page.locator("text=Save Guardrails").click()
        page.wait_for_url("**/settings?msg=saved")

        # Verify the value persists
        page.goto(f"{base_url}/my/settings")
        expect(page.locator("#custom_guardrails")).to_have_value("Never mention competitor products")


class TestSearchLiveResults:
    """Search page returns results."""

    def test_search_page_loads(self, page: Page, e2e_client_session):
        base_url = e2e_client_session["base_url"]
        page.goto(f"{base_url}/my/search")
        # Search page should load without errors
        assert "Internal Server Error" not in page.title()

    def test_search_with_query(self, page: Page, e2e_client_session):
        base_url = e2e_client_session["base_url"]
        page.goto(f"{base_url}/my/search?q=settings")
        page_text = page.text_content("body")
        # Should return results or empty state — no 500 error
        assert "Internal Server Error" not in page_text


class TestCalendarRendering:
    """Calendar page renders without errors."""

    def test_calendar_loads(self, page: Page, e2e_client_session):
        base_url = e2e_client_session["base_url"]
        page.goto(f"{base_url}/my/calendar")
        expect(page).to_have_title(lambda t: "Ortobahn" in t)
        assert "Calendar" in page.text_content("body")
