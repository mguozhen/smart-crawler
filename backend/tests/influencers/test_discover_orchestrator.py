"""Unit tests for discover orchestrator dispatch + dedupe."""
from __future__ import annotations

import pytest

from app.influencers.discover import dispatch, dedupe


pytestmark = pytest.mark.unit


def test_dedupe_by_platform_handle():
    items = [
        {"channelId": "@a", "platform": "TikTok", "handle": "a"},
        {"channelId": "@a", "platform": "TikTok", "handle": "a"},
        {"channelId": "ig:a", "platform": "Instagram", "handle": "a"},
    ]
    out = dedupe(items)
    assert len(out) == 2


def test_dispatch_youtube_about(monkeypatch):
    calls = []

    def fake_fetch(url, timeout=20):
        calls.append(url)
        return {"email": "x@x.com", "websiteUrl": "https://x.com"}

    monkeypatch.setattr("app.influencers.yt_about.fetch_about", fake_fetch)
    items = dispatch(
        platform="youtube_about",
        params={"urls": ["https://www.youtube.com/@a/about",
                         "https://www.youtube.com/@b/about"]},
        limit=10,
    )
    assert calls == ["https://www.youtube.com/@a/about",
                     "https://www.youtube.com/@b/about"]
    assert items == [
        {"email": "x@x.com", "websiteUrl": "https://x.com"},
        {"email": "x@x.com", "websiteUrl": "https://x.com"},
    ]


def test_dispatch_unknown_platform_raises():
    with pytest.raises(ValueError, match="unknown platform"):
        dispatch(platform="myspace", params={}, limit=10)
