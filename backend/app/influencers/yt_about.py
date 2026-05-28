"""YouTube About page parser — replaces ScraperAPI dependency.

Input: HTML of https://www.youtube.com/@{handle}/about
Output: {email, websiteUrl} — both may be None.

Logic mirrors the legacy scripts/discover/lib/scraperapi.js extraction:
filter out platform-internal emails/links (youtube/google/gmail), prefer the
real-creator email, unwrap YouTube's /redirect?q= wrapper around external URLs.
"""
from __future__ import annotations

import re
from urllib.parse import unquote, urlparse

from ._common import http

_BAD_EMAIL_FRAGMENTS = ("youtube", "google", "gmail.com", "example.")
_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")
_REDIRECT_RE = re.compile(r"/redirect\?[^\"']*?[?&]q=([^\"'&]+)")
_RAW_URL_RE = re.compile(r"https?://[^\s\"'<>]+")


def _is_bad_email(addr: str) -> bool:
    a = addr.lower()
    return any(bad in a for bad in _BAD_EMAIL_FRAGMENTS)


def _is_bad_host(url: str) -> bool:
    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        return True
    return any(bad in host for bad in _BAD_EMAIL_FRAGMENTS)


def parse_about_html(html: str) -> dict[str, str | None]:
    if not html:
        return {"email": None, "websiteUrl": None}
    raw = html.replace("\\u0026", "&").replace("&amp;", "&")

    email: str | None = None
    for m in _EMAIL_RE.finditer(raw):
        candidate = m.group(0)
        if not _is_bad_email(candidate):
            email = candidate
            break

    website: str | None = None
    for m in _REDIRECT_RE.finditer(raw):
        decoded = unquote(m.group(1))
        if decoded.startswith(("http://", "https://")) and not _is_bad_host(decoded):
            website = decoded
            break
    if website is None:
        for m in _RAW_URL_RE.finditer(raw):
            cand = m.group(0).rstrip(".,;)")
            if not _is_bad_host(cand):
                website = cand
                break

    return {"email": email, "websiteUrl": website}


def fetch_about(profile_url: str, timeout: int = 20) -> dict[str, str | None]:
    """Fetch the About page for a YouTube channel URL and parse email/website."""
    url = profile_url.rstrip("/")
    if not url.endswith("/about"):
        url = url + "/about"
    s = http()
    s.headers["Accept-Language"] = "en-US,en;q=0.9"
    r = s.get(url, timeout=timeout)
    if r.status_code != 200:
        return {"email": None, "websiteUrl": None}
    return parse_about_html(r.text)
