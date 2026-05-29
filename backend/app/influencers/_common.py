"""通用工具：HTTP 客户端 / 联系方式抽取 / 时间戳."""
from __future__ import annotations

import re
from datetime import datetime, timezone

from curl_cffi import requests as creq

_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


def http() -> creq.Session:
    s = creq.Session(impersonate="chrome131")
    s.headers.update({
        "User-Agent": _UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    })
    return s


_EMAIL_RE = re.compile(
    r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")
_WHATSAPP_RE = re.compile(
    r"(?:wa\.me/|whatsapp(?:\.com)?[:/]+)([0-9+]{6,})", re.I)
_LINKTREE_RE = re.compile(
    r"(?:linktr\.ee|beacons\.ai|allmylinks|linkin\.bio|lnk\.bio|link\.tree|bio\.link|tap\.bio|carrd\.co)/[A-Za-z0-9_\-./]+",
    re.I)


def extract_contacts(text: str | None) -> dict[str, str | None]:
    if not text:
        return {"email": None, "whatsapp": None, "linktree": None}
    m_email = _EMAIL_RE.search(text)
    m_wa = _WHATSAPP_RE.search(text)
    m_lt = _LINKTREE_RE.search(text)
    return {
        "email": m_email.group(0) if m_email else None,
        "whatsapp": m_wa.group(1) if m_wa else None,
        "linktree": m_lt.group(0) if m_lt else None,
    }


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_count(s: str | None) -> int | None:
    """'1.2M' / '34.5K' / '1,234' → int"""
    if not s:
        return None
    s = s.strip().replace(",", "")
    mult = 1
    if s.lower().endswith("m"):
        mult = 1_000_000
        s = s[:-1]
    elif s.lower().endswith("k"):
        mult = 1_000
        s = s[:-1]
    elif s.lower().endswith("b"):
        mult = 1_000_000_000
        s = s[:-1]
    try:
        return int(float(s) * mult)
    except (ValueError, TypeError):
        return None
