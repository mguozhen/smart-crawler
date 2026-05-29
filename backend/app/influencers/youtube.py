"""YouTube 红人采集 - 最简单的平台
- channel/@handle → oembed + about page parse ytInitialData
- recent videos → RSS feed videos.xml
"""
from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET

from ._common import extract_contacts, http, now_iso, parse_count
from .models import Contact, InfluencerProfile, RecentPost

_YT = "https://www.youtube.com"


def _resolve_channel_id(s, handle: str) -> tuple[str | None, str]:
    """@handle → (UCxxxx, html)
    访问 /@handle 拿到含 ytInitialData 的 HTML
    """
    handle = handle.lstrip("@")
    url = f"{_YT}/@{handle}"
    r = s.get(url, timeout=20)
    if r.status_code != 200:
        return None, ""
    html = r.text
    m = re.search(r'"channelId":"(UC[A-Za-z0-9_-]{22})"', html)
    if m:
        return m.group(1), html
    m = re.search(r'"externalId":"(UC[A-Za-z0-9_-]{22})"', html)
    if m:
        return m.group(1), html
    return None, html


def _yt_initial_data(html: str) -> dict | None:
    m = re.search(r"var ytInitialData\s*=\s*({.+?});\s*</script>", html, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError:
        return None


def fetch_profile(handle: str) -> InfluencerProfile:
    s = http()
    cid, html = _resolve_channel_id(s, handle)
    handle_clean = handle.lstrip("@")

    title = subs = videos_count = bio = avatar = banner = None
    is_verified = False
    external_url = None

    if html:
        m = re.search(r'<meta property="og:title" content="([^"]+)"', html)
        if m:
            title = m.group(1)
        m = re.search(r'<meta property="og:description" content="([^"]+)"', html)
        if m:
            bio = m.group(1)
        m = re.search(r'<meta property="og:image" content="([^"]+)"', html)
        if m:
            avatar = m.group(1)
        # subscriberCountText - 多个匹配（main channel + sidebar）取最大
        # 1) "29.7 million subscribers" 格式
        candidates = []
        for m in re.finditer(
            r'"label":"(\d+(?:[.,]\d+)?)\s*(million|thousand|billion|K|M|B|万|億)?\s*subscribers?"',
            html, re.I,
        ):
            num_s = m.group(1).replace(",", "")
            unit = (m.group(2) or "").lower()
            try:
                n = float(num_s)
            except ValueError:
                continue
            if unit in ("million", "m"):
                n *= 1_000_000
            elif unit in ("thousand", "k"):
                n *= 1_000
            elif unit in ("billion", "b"):
                n *= 1_000_000_000
            candidates.append(int(n))
        # 2) 兜底 "1.2M subscribers"
        if not candidates:
            for m in re.finditer(
                r'(\d+(?:[.,]\d+)?\s*[KMB])\s*subscribers?', html, re.I
            ):
                v = parse_count(m.group(1))
                if v:
                    candidates.append(v)
        if candidates:
            subs = max(candidates)  # main channel always largest

        # videosCountText
        candidates = []
        for m in re.finditer(r'"videosCountText":\{"runs":\[\{"text":"([\d,.]+[KMB]?)"', html):
            v = parse_count(m.group(1))
            if v:
                candidates.append(v)
        for m in re.finditer(r'"text":"(\d+(?:[.,]\d+)?\s*[KMB]?)\s*videos?"', html, re.I):
            v = parse_count(m.group(1).strip())
            if v:
                candidates.append(v)
        if candidates:
            videos_count = max(candidates)

        if '"verified":true' in html or '"verifiedBadge"' in html or "BADGE_STYLE_TYPE_VERIFIED" in html:
            is_verified = True
        # external link
        m = re.search(r'"channelExternalLinkViewModel":\{[^}]*"link":\{"content":"([^"]+)"', html)
        if m:
            external_url = m.group(1)

    bio_text = bio or ""
    contacts = extract_contacts(bio_text + " " + (external_url or ""))

    return InfluencerProfile(
        platform="youtube",
        username=handle_clean,
        user_id=cid,
        display_name=title,
        bio=bio,
        avatar_url=avatar,
        is_verified=is_verified,
        followers=subs,         # 订阅数
        following=None,
        posts_count=videos_count,
        contact=Contact(**contacts),
        external_url=external_url,
        raw_url=f"{_YT}/@{handle_clean}",
        fetched_at=now_iso(),
        fetched_via="channel_page+ytInitialData",
    )


def fetch_posts(handle: str, limit: int = 20) -> list[RecentPost]:
    s = http()
    cid, _ = _resolve_channel_id(s, handle)
    if not cid:
        return []
    rss_url = f"{_YT}/feeds/videos.xml?channel_id={cid}"
    r = s.get(rss_url, timeout=20)
    if r.status_code != 200:
        return []
    try:
        root = ET.fromstring(r.text)
    except ET.ParseError:
        return []
    ns = {"atom": "http://www.w3.org/2005/Atom",
          "yt": "http://www.youtube.com/xml/schemas/2015",
          "media": "http://search.yahoo.com/mrss/"}
    posts = []
    for entry in root.findall("atom:entry", ns)[:limit]:
        vid_el = entry.find("yt:videoId", ns)
        title_el = entry.find("atom:title", ns)
        link_el = entry.find("atom:link", ns)
        pub_el = entry.find("atom:published", ns)
        media_g = entry.find("media:group", ns)
        thumb_url = None
        views = None
        likes = None
        desc = None
        if media_g is not None:
            thumb_el = media_g.find("media:thumbnail", ns)
            if thumb_el is not None:
                thumb_url = thumb_el.get("url")
            desc_el = media_g.find("media:description", ns)
            if desc_el is not None:
                desc = desc_el.text
            stat_el = media_g.find("media:community", ns)
            if stat_el is not None:
                s_view = stat_el.find("media:statistics", ns)
                if s_view is not None:
                    try:
                        views = int(s_view.get("views") or 0)
                    except (TypeError, ValueError):
                        pass
                s_star = stat_el.find("media:starRating", ns)
                if s_star is not None:
                    try:
                        likes = int(s_star.get("count") or 0)
                    except (TypeError, ValueError):
                        pass
        posts.append(RecentPost(
            platform="youtube",
            post_id=vid_el.text if vid_el is not None else "",
            post_url=link_el.get("href") if link_el is not None else "",
            posted_at=pub_el.text if pub_el is not None else None,
            caption=(title_el.text if title_el is not None else "")
                    + ("\n\n" + desc if desc else ""),
            media_type="video",
            thumbnail_url=thumb_url,
            views=views,
            likes=likes,
        ))
    return posts
