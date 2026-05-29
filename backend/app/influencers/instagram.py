"""Instagram 红人采集
- 公共 endpoint：i.instagram.com/api/v1/users/web_profile_info/?username=<u>
- 需要 X-IG-App-ID: 936619743392459（Web 标准 ID）
- profile 含 follower/following/posts/bio/avatar/verified + edge_owner_to_timeline_media 最近 12 条
"""
from __future__ import annotations

import json
import re

from ._common import extract_contacts, http, now_iso
from .models import Contact, InfluencerProfile, RecentPost

_BASE_API = "https://i.instagram.com/api/v1/users/web_profile_info/"
_BASE_WEB = "https://www.instagram.com"
_APP_ID = "936619743392459"


def _api(s, username: str) -> dict | None:
    headers = {
        "X-IG-App-ID": _APP_ID,
        "Accept": "application/json, text/plain, */*",
        "Sec-Fetch-Site": "same-origin",
        "X-Requested-With": "XMLHttpRequest",
        "Referer": f"{_BASE_WEB}/{username}/",
    }
    r = s.get(_BASE_API, params={"username": username},
              headers=headers, timeout=20)
    if r.status_code != 200:
        return None
    try:
        d = r.json()
    except Exception:
        return None
    return ((d.get("data") or {}).get("user") or {})


def _scrape_page(s, username: str) -> dict | None:
    """fallback：抓 /username/ 页 og:meta + 内嵌 JSON"""
    r = s.get(f"{_BASE_WEB}/{username}/", timeout=20)
    if r.status_code != 200:
        return None
    html = r.text
    out = {"username": username}
    m = re.search(r'<meta property="og:title" content="([^"]+)"', html)
    if m:
        out["full_name"] = m.group(1)
    m = re.search(r'<meta property="og:description" content="([^"]+)"', html)
    if m:
        out["_og_desc"] = m.group(1)  # "1.2M Followers, 100 Following, 50 Posts - See..."
    m = re.search(r'<meta property="og:image" content="([^"]+)"', html)
    if m:
        out["profile_pic_url"] = m.group(1)
    # 解析 og:description 拿到数字
    desc = out.get("_og_desc") or ""
    m = re.search(r"([\d,.]+\s*[KMB]?)\s*Followers", desc, re.I)
    if m:
        from ._common import parse_count
        out["edge_followed_by"] = {"count": parse_count(m.group(1))}
    m = re.search(r"([\d,.]+\s*[KMB]?)\s*Following", desc, re.I)
    if m:
        from ._common import parse_count
        out["edge_follow"] = {"count": parse_count(m.group(1))}
    m = re.search(r"([\d,.]+\s*[KMB]?)\s*Posts", desc, re.I)
    if m:
        from ._common import parse_count
        out["edge_owner_to_timeline_media"] = {"count": parse_count(m.group(1))}
    return out


def fetch_profile(username: str) -> InfluencerProfile:
    username = username.lstrip("@").lower()
    s = http()
    user = _api(s, username) or _scrape_page(s, username) or {}
    bio = user.get("biography") or ""

    bio_links = []
    for bl in (user.get("bio_links") or []):
        if isinstance(bl, dict):
            url = bl.get("url") or bl.get("link_url")
            if url:
                bio_links.append(url)
    ext = user.get("external_url") or (bio_links[0] if bio_links else None)
    contact_pieces = " ".join([bio] + bio_links + ([ext] if ext else []))
    contacts = extract_contacts(contact_pieces)

    return InfluencerProfile(
        platform="instagram",
        username=username,
        user_id=str(user.get("id") or user.get("pk") or "") or None,
        display_name=user.get("full_name"),
        bio=bio or None,
        avatar_url=user.get("profile_pic_url_hd") or user.get("profile_pic_url"),
        is_verified=bool(user.get("is_verified")),
        is_business=bool(user.get("is_business_account")
                         or user.get("is_professional_account")),
        category=user.get("category_name") or user.get("category"),
        followers=(user.get("edge_followed_by") or {}).get("count")
                  or user.get("follower_count"),
        following=(user.get("edge_follow") or {}).get("count")
                  or user.get("following_count"),
        posts_count=(user.get("edge_owner_to_timeline_media") or {}).get("count")
                    or user.get("media_count"),
        contact=Contact(**contacts),
        external_url=ext,
        raw_url=f"{_BASE_WEB}/{username}/",
        fetched_at=now_iso(),
        fetched_via="web_profile_info+og_meta",
    )


def fetch_posts(username: str, limit: int = 20) -> list[RecentPost]:
    username = username.lstrip("@").lower()
    s = http()
    user = _api(s, username) or {}
    timeline = ((user.get("edge_owner_to_timeline_media") or {}).get("edges") or [])
    out = []
    for ed in timeline[:limit]:
        node = ed.get("node") or {}
        cap_edges = (node.get("edge_media_to_caption") or {}).get("edges") or []
        cap = (cap_edges[0].get("node") or {}).get("text") if cap_edges else None
        media_type = "image"
        if node.get("is_video"):
            media_type = "video"
        elif (node.get("edge_sidecar_to_children") or {}).get("edges"):
            media_type = "carousel"
        shortcode = node.get("shortcode")
        out.append(RecentPost(
            platform="instagram",
            post_id=shortcode or node.get("id") or "",
            post_url=f"{_BASE_WEB}/p/{shortcode}/" if shortcode else "",
            posted_at=str(node.get("taken_at_timestamp")) if node.get("taken_at_timestamp") else None,
            caption=cap,
            media_type=media_type,
            media_url=node.get("video_url") if node.get("is_video") else node.get("display_url"),
            thumbnail_url=node.get("thumbnail_src") or node.get("display_url"),
            likes=(node.get("edge_liked_by") or {}).get("count")
                  or (node.get("edge_media_preview_like") or {}).get("count"),
            comments=(node.get("edge_media_to_comment") or {}).get("count"),
            views=node.get("video_view_count"),
            duration_sec=int(node.get("video_duration") or 0) or None,
        ))
    return out
