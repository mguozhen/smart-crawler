"""红人采集统一 schema · 替代 Apify actor 输出"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass
class Contact:
    email: str | None = None
    whatsapp: str | None = None
    linktree: str | None = None
    website: str | None = None


@dataclass
class InfluencerProfile:
    """红人画像 · Tier 1 字段"""
    platform: str
    username: str
    user_id: str | None = None
    display_name: str | None = None
    bio: str | None = None
    avatar_url: str | None = None
    is_verified: bool = False
    is_business: bool = False
    category: str | None = None
    followers: int | None = None
    following: int | None = None
    posts_count: int | None = None
    likes_total: int | None = None     # TikTok 特有
    contact: Contact = field(default_factory=Contact)
    external_url: str | None = None
    raw_url: str | None = None          # 红人主页 URL
    fetched_at: str | None = None       # ISO
    fetched_via: str | None = None      # web_profile_info / nitter / oembed
    notes: str | None = None

    def to_dict(self) -> dict:
        d = asdict(self)
        return d


@dataclass
class RecentPost:
    """近期帖子 · Tier 2 字段"""
    platform: str
    post_id: str
    post_url: str
    posted_at: str | None = None       # ISO
    caption: str | None = None
    media_type: str | None = None      # image / video / carousel / reel / short
    media_url: str | None = None
    thumbnail_url: str | None = None
    likes: int | None = None
    comments: int | None = None
    shares: int | None = None
    views: int | None = None
    duration_sec: int | None = None

    def to_dict(self) -> dict:
        return asdict(self)
