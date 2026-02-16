"""Validate all links in the landing page."""

from __future__ import annotations

import re
import socket
from pathlib import Path
from urllib.parse import urlparse

import pytest

pytestmark = pytest.mark.network

LANDING_HTML = Path(__file__).parent.parent / "ortobahn" / "landing" / "index.html"


def _extract_hrefs(html: str) -> list[str]:
    """Extract all href values from HTML."""
    return re.findall(r'href=["\']([^"\']+)["\']', html)


def _extract_anchors(html: str) -> set[str]:
    """Extract all id attributes (valid anchor targets) from HTML."""
    return set(re.findall(r'id=["\']([^"\']+)["\']', html))


@pytest.fixture(scope="module")
def landing_html() -> str:
    return LANDING_HTML.read_text()


@pytest.fixture(scope="module")
def all_hrefs(landing_html: str) -> list[str]:
    return _extract_hrefs(landing_html)


@pytest.fixture(scope="module")
def anchors(landing_html: str) -> set[str]:
    return _extract_anchors(landing_html)


class TestLandingPageAnchors:
    """Verify internal #anchor links point to existing IDs."""

    def test_anchor_links_resolve(self, all_hrefs: list[str], anchors: set[str]) -> None:
        anchor_links = [h for h in all_hrefs if h.startswith("#")]
        broken = [link for link in anchor_links if link[1:] not in anchors]
        assert not broken, f"Broken anchor links: {broken}"


class TestLandingPageExternalLinks:
    """Verify external URLs have valid DNS."""

    def test_external_hosts_resolve(self, all_hrefs: list[str]) -> None:
        external = [h for h in all_hrefs if h.startswith("http")]
        hosts = {h for url in external if (h := urlparse(url).hostname) is not None}

        broken: list[tuple[str, str]] = []
        for host in sorted(hosts):
            try:
                socket.getaddrinfo(host, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
            except socket.gaierror as exc:
                broken.append((host, str(exc)))

        assert not broken, "External links with DNS failures:\n" + "\n".join(f"  {host}: {err}" for host, err in broken)

    def test_no_localhost_links(self, all_hrefs: list[str]) -> None:
        localhost = [h for h in all_hrefs if "localhost" in h or "127.0.0.1" in h]
        assert not localhost, f"Landing page contains localhost links: {localhost}"

    def test_all_external_links_use_https(self, all_hrefs: list[str]) -> None:
        http_links = [h for h in all_hrefs if h.startswith("http://")]
        assert not http_links, f"Non-HTTPS links found: {http_links}"
