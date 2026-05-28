"""多平台红人采集 · 替代 Apify actor

支持平台：
- instagram (profile / posts)
- tiktok (profile / posts)
- youtube (channel / videos)
- twitter (profile / tweets)

统一 schema 定义见 models.py
平台实现在 instagram.py / tiktok.py / youtube.py / twitter.py
统一调度入口：fetch_profile(platform, username) / fetch_posts(platform, username)
"""
from __future__ import annotations

from .models import InfluencerProfile, RecentPost

PLATFORMS = ("instagram", "tiktok", "youtube", "twitter")


def fetch_profile(platform: str, username: str) -> InfluencerProfile:
    if platform == "instagram":
        from .instagram import fetch_profile as f
    elif platform == "tiktok":
        from .tiktok import fetch_profile as f
    elif platform == "youtube":
        from .youtube import fetch_profile as f
    elif platform in ("twitter", "x"):
        from .twitter import fetch_profile as f
    else:
        raise ValueError(f"未知平台: {platform}")
    return f(username)


def fetch_posts(platform: str, username: str, limit: int = 20) -> list[RecentPost]:
    if platform == "instagram":
        from .instagram import fetch_posts as f
    elif platform == "tiktok":
        from .tiktok import fetch_posts as f
    elif platform == "youtube":
        from .youtube import fetch_posts as f
    elif platform in ("twitter", "x"):
        from .twitter import fetch_posts as f
    else:
        raise ValueError(f"未知平台: {platform}")
    return f(username, limit=limit)


from .discover import dispatch as discover  # noqa: E402
from .discover_models import CreatorRecord  # noqa: E402

__all__ = ["fetch_profile", "fetch_posts", "PLATFORMS",
           "InfluencerProfile", "RecentPost",
           "discover", "CreatorRecord"]
