"""Unit tests for YouTube About parser (ScraperAPI replacement)."""
from __future__ import annotations

import pytest

from app.influencers.yt_about import parse_about_html


pytestmark = pytest.mark.unit


def test_parses_email_or_website(fixture_text):
    html = fixture_text("yt_about_mrbeast.html")
    result = parse_about_html(html)
    assert set(result.keys()) == {"email", "websiteUrl"}
    assert result["email"] is not None or result["websiteUrl"] is not None


def test_ignores_platform_emails():
    html = """
    <html><body>
    <a href="mailto:youtube-press@google.com">contact</a>
    <a href="mailto:real@creator.com">biz</a>
    </body></html>
    """
    result = parse_about_html(html)
    assert result["email"] == "real@creator.com"


def test_extracts_redirect_website():
    html = '<a href="/redirect?event=channel_description&amp;q=https%3A%2F%2Fcreator-site.com%2Fhome">site</a>'
    result = parse_about_html(html)
    assert result["websiteUrl"] == "https://creator-site.com/home"


def test_returns_nulls_on_empty():
    assert parse_about_html("") == {"email": None, "websiteUrl": None}
    assert parse_about_html("<html></html>") == {"email": None, "websiteUrl": None}
