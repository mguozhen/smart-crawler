"""TikTok 红人采集
- /@username 页面内嵌 __UNIVERSAL_DATA_FOR_REHYDRATION__ JSON
- 含 user info + 第一页 posts
"""
from __future__ import annotations

import json
import re

from ._common import extract_contacts, http, now_iso
from .models import Contact, InfluencerProfile, RecentPost

_TT = "https://www.tiktok.com"


def _fetch_state(s, username: str) -> tuple[dict, str]:
    username = username.lstrip("@")
    url = f"{_TT}/@{username}"
    r = s.get(url, timeout=20, headers={
        "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
    })
    if r.status_code != 200:
        return {}, ""
    html = r.text
    # Method 1: __UNIVERSAL_DATA_FOR_REHYDRATION__
    m = re.search(
        r'<script[^>]*id="__UNIVERSAL_DATA_FOR_REHYDRATION__"[^>]*>(.+?)</script>',
        html, re.S)
    if m:
        try:
            data = json.loads(m.group(1))
            return data, html
        except json.JSONDecodeError:
            pass
    # Method 2: SIGI_STATE (older)
    m = re.search(r'<script[^>]*id="SIGI_STATE"[^>]*>(.+?)</script>', html, re.S)
    if m:
        try:
            return json.loads(m.group(1)), html
        except json.JSONDecodeError:
            pass
    return {}, html


def _user_module(state: dict, username: str) -> tuple[dict, dict, list]:
    """从 state 抽 (user_info, stats, posts)"""
    ds = state.get("__DEFAULT_SCOPE__") or {}
    page = ds.get("webapp.user-detail") or ds.get("webapp.user_detail") or {}
    user_info = page.get("userInfo") or {}
    user = user_info.get("user") or {}
    stats = user_info.get("stats") or user_info.get("statsV2") or {}
    item_page = ds.get("webapp.item-list") or {}
    items = item_page.get("itemList") or []
    return user, stats, items


def fetch_profile(username: str) -> InfluencerProfile:
    username = username.lstrip("@").lower()
    s = http()
    state, html = _fetch_state(s, username)
    user, stats, _ = _user_module(state, username)

    bio = user.get("signature") or ""
    contact_text = bio + " " + (user.get("bioLink", {}) or {}).get("link", "")
    contacts = extract_contacts(contact_text)
    ext_url = (user.get("bioLink") or {}).get("link") or None

    # 兼容 og:meta fallback
    if not user and html:
        m = re.search(r'<meta property="og:title" content="([^"]+)"', html)
        if m:
            user["nickname"] = m.group(1)
        m = re.search(r'<meta property="og:image" content="([^"]+)"', html)
        if m:
            user["avatarLarger"] = m.group(1)

    def _to_int(v):
        try:
            return int(v) if v is not None else None
        except (ValueError, TypeError):
            return None

    return InfluencerProfile(
        platform="tiktok",
        username=username,
        user_id=user.get("id") or user.get("secUid"),
        display_name=user.get("nickname") or user.get("uniqueId"),
        bio=bio or None,
        avatar_url=user.get("avatarLarger") or user.get("avatarMedium"),
        is_verified=bool(user.get("verified")),
        category=user.get("category"),
        followers=_to_int(stats.get("followerCount")),
        following=_to_int(stats.get("followingCount")),
        posts_count=_to_int(stats.get("videoCount")),
        likes_total=_to_int(stats.get("heartCount") or stats.get("heart")),
        contact=Contact(**contacts),
        external_url=ext_url,
        raw_url=f"{_TT}/@{username}",
        fetched_at=now_iso(),
        fetched_via="__UNIVERSAL_DATA_FOR_REHYDRATION__",
    )


def fetch_posts(username: str, limit: int = 20) -> list[RecentPost]:
    username = username.lstrip("@").lower()
    s = http()
    state, _ = _fetch_state(s, username)
    _, _, items = _user_module(state, username)
    out = []
    for it in items[:limit]:
        vid = it.get("id") or ""
        stats = it.get("stats") or it.get("statsV2") or {}
        video = it.get("video") or {}

        def _to_int(v):
            try:
                return int(v) if v is not None else None
            except (ValueError, TypeError):
                return None

        out.append(RecentPost(
            platform="tiktok",
            post_id=vid,
            post_url=f"{_TT}/@{username}/video/{vid}",
            posted_at=str(it.get("createTime")) if it.get("createTime") else None,
            caption=it.get("desc"),
            media_type="video",
            thumbnail_url=video.get("cover") or video.get("originCover"),
            media_url=video.get("playAddr"),
            likes=_to_int(stats.get("diggCount")),
            comments=_to_int(stats.get("commentCount")),
            shares=_to_int(stats.get("shareCount")),
            views=_to_int(stats.get("playCount")),
            duration_sec=_to_int(video.get("duration")),
        ))
    return out
