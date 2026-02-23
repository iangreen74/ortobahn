"""Tests for the Legal web routes (/legal/terms, /legal/privacy)."""

from __future__ import annotations

from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from ortobahn.config import Settings
from ortobahn.db import Database
from ortobahn.web.routes.legal import _markdown_to_html

# ---------------------------------------------------------------------------
# Helper: create test app (legal routes are public — no auth needed)
# ---------------------------------------------------------------------------


def _create_test_app(tmp_path):
    from ortobahn.web.app import create_app

    settings = Settings(
        anthropic_api_key="sk-ant-test",
        db_path=tmp_path / "legal_test.db",
        secret_key="test-secret-key-for-legal-tests!",
    )
    app = create_app.__wrapped__() if hasattr(create_app, "__wrapped__") else create_app()
    db = Database(settings.db_path)
    app.state.db = db
    app.state.settings = settings
    app.state.cognito = MagicMock()
    return app


# ---------------------------------------------------------------------------
# TestMarkdownToHTML — unit tests for the converter
# ---------------------------------------------------------------------------


class TestMarkdownToHTML:
    """Test the _markdown_to_html helper function."""

    def test_heading_h1(self):
        result = _markdown_to_html("# Title")
        assert "<h1>Title</h1>" in result

    def test_heading_h2(self):
        result = _markdown_to_html("## Subtitle")
        assert "<h2>Subtitle</h2>" in result

    def test_heading_h3(self):
        result = _markdown_to_html("### Section")
        assert "<h3>Section</h3>" in result

    def test_paragraph(self):
        result = _markdown_to_html("Just a plain paragraph.")
        assert "<p>Just a plain paragraph.</p>" in result

    def test_bold_text(self):
        result = _markdown_to_html("This is **bold** text.")
        assert "<strong>bold</strong>" in result

    def test_italic_text(self):
        result = _markdown_to_html("This is *italic* text.")
        assert "<em>italic</em>" in result

    def test_unordered_list_dash(self):
        result = _markdown_to_html("- Item 1\n- Item 2")
        assert "<ul>" in result
        assert "<li>Item 1</li>" in result
        assert "<li>Item 2</li>" in result
        assert "</ul>" in result

    def test_unordered_list_asterisk(self):
        result = _markdown_to_html("* Item A\n* Item B")
        assert "<li>Item A</li>" in result
        assert "<li>Item B</li>" in result

    def test_list_closes_on_empty_line(self):
        result = _markdown_to_html("- Item\n\nParagraph after list")
        assert "</ul>" in result
        assert "<p>Paragraph after list</p>" in result

    def test_list_closes_on_heading(self):
        result = _markdown_to_html("- Item\n## Heading")
        assert "</ul>" in result
        assert "<h2>Heading</h2>" in result

    def test_list_closes_at_end(self):
        result = _markdown_to_html("- Item 1\n- Item 2")
        assert result.count("<ul>") == result.count("</ul>")

    def test_bold_in_list_item(self):
        result = _markdown_to_html("- **Bold** item")
        assert "<strong>Bold</strong>" in result

    def test_empty_input(self):
        result = _markdown_to_html("")
        assert result == ""

    def test_complex_document(self):
        md = """# Terms of Service

## 1. Acceptance

By using Ortobahn, you agree to these terms.

## 2. Services

- **Content generation** via AI
- *Analytics* and reporting
- Platform integrations

## 3. Privacy

We respect your privacy. See our Privacy Policy for details."""

        result = _markdown_to_html(md)
        assert "<h1>Terms of Service</h1>" in result
        assert "<h2>1. Acceptance</h2>" in result
        assert "<strong>Content generation</strong>" in result
        assert "<em>Analytics</em>" in result
        assert "<li>" in result


# ---------------------------------------------------------------------------
# TestLegalTermsRoute
# ---------------------------------------------------------------------------


class TestLegalTermsRoute:
    """Test the /legal/terms endpoint."""

    def test_terms_fallback_when_no_document(self, tmp_path):
        app = _create_test_app(tmp_path)
        client = TestClient(app)
        resp = client.get("/legal/terms")
        assert resp.status_code == 200
        assert "Terms of Service" in resp.text
        assert "being generated" in resp.text

    def test_terms_renders_existing_document(self, tmp_path):
        app = _create_test_app(tmp_path)
        db = app.state.db

        db.save_legal_document(
            {
                "client_id": "default",
                "document_type": "terms_of_service",
                "title": "Terms of Service",
                "content": "# Terms\n\nYou agree to these terms.",
                "version": "1.0",
                "effective_date": "2026-01-01",
            }
        )

        client = TestClient(app)
        resp = client.get("/legal/terms")
        assert resp.status_code == 200
        assert "Terms of Service" in resp.text
        assert "You agree to these terms" in resp.text
        assert "1.0" in resp.text

    def test_terms_renders_html_from_markdown(self, tmp_path):
        app = _create_test_app(tmp_path)
        db = app.state.db

        db.save_legal_document(
            {
                "client_id": "default",
                "document_type": "terms_of_service",
                "title": "ToS",
                "content": "## Section 1\n\n- Rule one\n- Rule two",
                "version": "2.0",
            }
        )

        client = TestClient(app)
        resp = client.get("/legal/terms")
        assert resp.status_code == 200
        assert "<h2>Section 1</h2>" in resp.text
        assert "<li>Rule one</li>" in resp.text


# ---------------------------------------------------------------------------
# TestLegalPrivacyRoute
# ---------------------------------------------------------------------------


class TestLegalPrivacyRoute:
    """Test the /legal/privacy endpoint."""

    def test_privacy_fallback_when_no_document(self, tmp_path):
        app = _create_test_app(tmp_path)
        client = TestClient(app)
        resp = client.get("/legal/privacy")
        assert resp.status_code == 200
        assert "Privacy Policy" in resp.text
        assert "being generated" in resp.text

    def test_privacy_renders_existing_document(self, tmp_path):
        app = _create_test_app(tmp_path)
        db = app.state.db

        db.save_legal_document(
            {
                "client_id": "default",
                "document_type": "privacy_policy",
                "title": "Privacy Policy",
                "content": "# Privacy\n\nWe collect minimal data.",
                "version": "1.0",
                "effective_date": "2026-01-01",
            }
        )

        client = TestClient(app)
        resp = client.get("/legal/privacy")
        assert resp.status_code == 200
        assert "Privacy Policy" in resp.text
        assert "minimal data" in resp.text

    def test_privacy_no_auth_required(self, tmp_path):
        """Legal pages are public — no API key or session needed."""
        app = _create_test_app(tmp_path)
        client = TestClient(app)
        resp = client.get("/legal/privacy")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# TestLegalEdgeCases
# ---------------------------------------------------------------------------


class TestLegalEdgeCases:
    """Test edge cases for legal routes."""

    def test_both_documents_exist(self, tmp_path):
        app = _create_test_app(tmp_path)
        db = app.state.db

        db.save_legal_document(
            {
                "client_id": "default",
                "document_type": "terms_of_service",
                "title": "Terms",
                "content": "Terms content here.",
            }
        )
        db.save_legal_document(
            {
                "client_id": "default",
                "document_type": "privacy_policy",
                "title": "Privacy",
                "content": "Privacy content here.",
            }
        )

        client = TestClient(app)
        terms_resp = client.get("/legal/terms")
        privacy_resp = client.get("/legal/privacy")

        assert "Terms content here" in terms_resp.text
        assert "Privacy content here" in privacy_resp.text

    def test_updated_document_shows_latest(self, tmp_path):
        app = _create_test_app(tmp_path)
        db = app.state.db

        db.save_legal_document(
            {
                "client_id": "default",
                "document_type": "terms_of_service",
                "title": "Old Terms",
                "content": "Old content",
                "version": "1.0",
            }
        )
        # Upsert the same document type
        db.save_legal_document(
            {
                "client_id": "default",
                "document_type": "terms_of_service",
                "title": "New Terms",
                "content": "Updated content with **bold**.",
                "version": "2.0",
            }
        )

        client = TestClient(app)
        resp = client.get("/legal/terms")
        assert "New Terms" in resp.text
        assert "Updated content" in resp.text
        assert "<strong>bold</strong>" in resp.text

    def test_nonexistent_legal_route_404(self, tmp_path):
        app = _create_test_app(tmp_path)
        client = TestClient(app)
        resp = client.get("/legal/nonexistent")
        assert resp.status_code in (404, 405)

    def test_version_and_date_shown(self, tmp_path):
        app = _create_test_app(tmp_path)
        db = app.state.db

        db.save_legal_document(
            {
                "client_id": "default",
                "document_type": "terms_of_service",
                "title": "Terms",
                "content": "Content here.",
                "version": "3.2",
                "effective_date": "2026-03-01",
            }
        )

        client = TestClient(app)
        resp = client.get("/legal/terms")
        assert resp.status_code == 200
