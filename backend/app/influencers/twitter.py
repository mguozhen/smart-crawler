"""Twitter / X 红人采集
- syndication.twitter.com/srv/timeline-profile/screen-name/<u>（仍有部分公开数据）
- 兜底：fxtwitter.com / twstalker.com 公开镜像
"""
from __future__ import annotations

import json
import re

from ._common import extract_contacts, http, now_iso
from .models import Contact, InfluencerProfile, RecentPost

_SYND_PROFILE = (
    "https://cdn.syndication.twimg.com/timeline/profile?"
    "screen_name={u}&lang=en"
)
_TW_BASE = "https://twitter.com"


def _syndication(s, username: str) -> dict | None:
    """syndication.twimg.com timeline_profile — 不需要 auth，但 schema 不稳."""
    url = _SYND_PROFILE.format(u=username)
    r = s.get(url, timeout=20, headers={
        "Accept": "application/json",
        "Referer": "https://platform.twitter.com/",
    })
    if r.status_code != 200:
        return None
    try:
        return r.json()
    except Exception:
        return None


def _fxtwitter(s, username: str) -> dict | None:
    """fxtwitter.com 公开 JSON API（Tweet-only，但 user 也带）."""
    url = f"https://api.fxtwitter.com/{username}"
    r = s.get(url, timeout=20)
    if r.status_code != 200:
        return None
    try:
        return r.json()
    except Exception:
        return None


def fetch_profile(username: str) -> InfluencerProfile:
    username = username.lstrip("@").lower()
    s = http()

    data = _syndication(s, username) or {}
    user_data = {}
    # syndication 格式 1: headerInfo.user
    hi = data.get("headerInfo") or {}
    if isinstance(hi, dict):
        user_data = hi.get("user") or {}
    # 格式 2: globalObjects.users
    if not user_data:
        for u in (data.get("globalObjects", {}) or {}).get("users", {}).values():
            user_data = u
            break

    fx = _fxtwitter(s, username) or {}
    fx_user = (fx.get("user") or {}) if fx else {}

    bio = user_data.get("description") or fx_user.get("description") or ""
    name = user_data.get("name") or fx_user.get("name") or username
    followers = (user_data.get("followers_count")
                 or fx_user.get("followers"))
    following = (user_data.get("friends_count")
                 or fx_user.get("following"))
    posts_count = (user_data.get("statuses_count")
                   or fx_user.get("tweets"))
    avatar = (user_data.get("profile_image_url_https")
              or fx_user.get("avatar_url"))
    is_verified = bool(user_data.get("verified") or fx_user.get("verified"))
    user_id = user_data.get("id_str") or user_data.get("id") or fx_user.get("id")
    ext = user_data.get("url") or fx_user.get("url")

    via = "syndication" if user_data else ("fxtwitter" if fx_user else "failed")
    contacts = extract_contacts(bio + " " + (ext or ""))

    return InfluencerProfile(
        platform="twitter",
        username=username,
        user_id=str(user_id) if user_id else None,
        display_name=name,
        bio=bio or None,
        avatar_url=avatar,
        is_verified=is_verified,
        followers=followers,
        following=following,
        posts_count=posts_count,
        contact=Contact(**contacts),
        external_url=ext,
        raw_url=f"{_TW_BASE}/{username}",
        fetched_at=now_iso(),
        fetched_via=via,
    )


def fetch_posts(username: str, limit: int = 20) -> list[RecentPost]:
    username = username.lstrip("@").lower()
    s = http()
    data = _syndication(s, username) or {}
    tweets_dict = (data.get("globalObjects", {}) or {}).get("tweets", {}) or {}
    out = []
    for tid, t in list(tweets_dict.items())[:limit]:
        out.append(RecentPost(
            platform="twitter",
            post_id=str(tid),
            post_url=f"{_TW_BASE}/{username}/status/{tid}",
            posted_at=t.get("created_at"),
            caption=t.get("full_text") or t.get("text"),
            media_type="tweet",
            likes=t.get("favorite_count"),
            comments=t.get("reply_count"),
            shares=t.get("retweet_count"),
            views=t.get("view_count"),
        ))
    return out
