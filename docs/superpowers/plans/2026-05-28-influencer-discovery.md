# Influencer Discovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace Apify (TikTok/Instagram/Facebook hashtag→creators) + ScraperAPI (YouTube About enrichment) with native smart-crawler endpoints. Internal Node caller switches base URL with no other change. Ship today.

**Architecture:** Add `backend/app/influencers/{tt,ig,fb}_discover.py` + `yt_about.py` adapters using `curl_cffi` and existing proxy/antiban infrastructure. Orchestrator (`discover.py`) dispatches by platform, dedupes, maps to a unified `CreatorRecord` schema. FastAPI router (`api/influencer_discover.py`) exposes Apify-compatible run/dataset lifecycle backed by an in-memory registry. Cookies for IG/FB loaded from disk via env paths.

**Tech Stack:** Python 3.11 · FastAPI · curl_cffi (chrome131 impersonation) · pytest · Docker (NAS)

**Spec:** [`docs/superpowers/specs/2026-05-28-influencer-discovery-design.md`](../specs/2026-05-28-influencer-discovery-design.md)

---

## File map

**Create:**
- `backend/app/influencers/discover_models.py` — `CreatorRecord` dataclass + per-platform raw→record mappers
- `backend/app/influencers/cookie_jar.py` — load cookies from env path, cache, invalidate, redact for logs
- `backend/app/influencers/run_registry.py` — in-memory RUNS dict, RLock, TTL GC
- `backend/app/influencers/yt_about.py` — YouTube About parser (replaces ScraperAPI)
- `backend/app/influencers/tt_discover.py` — TikTok hashtag→creators
- `backend/app/influencers/ig_discover.py` — Instagram hashtag→creators
- `backend/app/influencers/fb_discover.py` — Facebook pages search→pages
- `backend/app/influencers/discover.py` — orchestrator (dispatch, dedupe, deadline)
- `backend/app/api/influencer_discover.py` — FastAPI router (`/discover/runs`, `/discover/datasets`)
- `backend/app/influencers/README.md` — adapter docs + cookie runbook
- `backend/pytest.ini` — register `unit` + `smoke` markers
- `backend/tests/influencers/__init__.py`
- `backend/tests/influencers/conftest.py` — fixture loader helper
- `backend/tests/influencers/fixtures/*.{html,json}` — captured network samples (committed)
- `backend/tests/influencers/test_*.py` — one test file per adapter + orchestrator + http contract

**Modify:**
- `backend/app/main.py` — register influencer_discover router
- `backend/app/influencers/__init__.py` — re-export `CreatorRecord`, `discover`
- `.gitignore` — add `backend/data/cookies/` (NOT fixtures)

---

## Task 0: Bootstrap — pytest markers, test scaffold, fixtures dir

**Files:**
- Create: `backend/pytest.ini`
- Create: `backend/tests/influencers/__init__.py`
- Create: `backend/tests/influencers/conftest.py`
- Create: `backend/tests/influencers/fixtures/.gitkeep`
- Modify: `.gitignore`

- [ ] **Step 1: Add pytest config with markers**

Create `backend/pytest.ini`:

```ini
[pytest]
testpaths = tests
markers =
    unit: pure unit test, no network, no I/O beyond fixture files
    smoke: integration smoke; hits real network or running server; skip when prerequisites missing
addopts = -ra --strict-markers
```

- [ ] **Step 2: Create test package + fixture dir**

```bash
mkdir -p /Users/guozhen/MailOutbound/smart-crawler/backend/tests/influencers/fixtures
touch /Users/guozhen/MailOutbound/smart-crawler/backend/tests/influencers/__init__.py
touch /Users/guozhen/MailOutbound/smart-crawler/backend/tests/influencers/fixtures/.gitkeep
```

- [ ] **Step 3: Add fixture loader helper**

Create `backend/tests/influencers/conftest.py`:

```python
"""Fixture loader for influencer adapter tests.

Fixture files live in tests/influencers/fixtures/ and are committed to the repo
so unit tests stay deterministic without network.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


def load_text(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def load_json(name: str) -> dict:
    return json.loads(load_text(name))


@pytest.fixture
def fixture_text():
    return load_text


@pytest.fixture
def fixture_json():
    return load_json
```

- [ ] **Step 4: Update .gitignore**

Append to `/Users/guozhen/MailOutbound/smart-crawler/.gitignore`:

```
# Influencer adapter cookies (loaded by env paths)
backend/data/cookies/
```

- [ ] **Step 5: Verify pytest still collects existing tests**

Run from `/Users/guozhen/MailOutbound/smart-crawler/backend`:

```bash
cd /Users/guozhen/MailOutbound/smart-crawler/backend && python -m pytest --collect-only -q
```

Expected: shows existing `test_routes_smoke.py` discoverable, no errors about unknown markers.

- [ ] **Step 6: Commit**

```bash
cd /Users/guozhen/MailOutbound/smart-crawler
git add backend/pytest.ini backend/tests/influencers/__init__.py backend/tests/influencers/conftest.py backend/tests/influencers/fixtures/.gitkeep .gitignore
git commit -m "test: bootstrap influencers test package + pytest markers"
```

---

## Task 1: CreatorRecord schema + per-platform mappers

**Files:**
- Create: `backend/app/influencers/discover_models.py`
- Test: `backend/tests/influencers/test_creator_record.py`

- [ ] **Step 1: Write failing tests**

Create `backend/tests/influencers/test_creator_record.py`:

```python
"""Unit tests for CreatorRecord schema + raw→record mappers."""
from __future__ import annotations

import pytest

from app.influencers.discover_models import (
    CreatorRecord,
    map_facebook,
    map_instagram,
    map_tiktok,
    map_youtube_about,
)


pytestmark = pytest.mark.unit


def test_tiktok_mapper_full():
    raw = {
        "authorMeta": {
            "uniqueId": "sellerjoe",
            "nickName": "Seller Joe",
            "fans": 12345,
            "signature": "Contact: hello@sellerjoe.com",
            "bioLink": "https://sellerjoe.com",
        },
    }
    r = map_tiktok(raw)
    assert r.channelId == "@sellerjoe"
    assert r.handle == "sellerjoe"
    assert r.name == "Seller Joe"
    assert r.platform == "TikTok"
    assert r.profileUrl == "https://www.tiktok.com/@sellerjoe"
    assert r.followerCount == 12345
    assert r.email == "hello@sellerjoe.com"
    assert r.websiteUrl == "https://sellerjoe.com"


def test_tiktok_mapper_fallback_follower_keys():
    raw = {"authorMeta": {"uniqueId": "x", "followerCount": 99}}
    r = map_tiktok(raw)
    assert r.followerCount == 99


def test_tiktok_mapper_missing_required_returns_none():
    assert map_tiktok({"authorMeta": {}}) is None
    assert map_tiktok({}) is None


def test_instagram_mapper_full():
    raw = {
        "ownerUsername": "sellerjoe",
        "ownerFullName": "Seller Joe",
        "ownerFollowersCount": 12345,
        "ownerBiography": "email me at hi@sellerjoe.com",
        "ownerExternalUrl": "https://sellerjoe.com",
        "publicEmail": "direct@sellerjoe.com",
    }
    r = map_instagram(raw)
    assert r.channelId == "ig:sellerjoe"
    assert r.platform == "Instagram"
    assert r.profileUrl == "https://www.instagram.com/sellerjoe/"
    assert r.followerCount == 12345
    # publicEmail wins over biography parse
    assert r.email == "direct@sellerjoe.com"
    assert r.websiteUrl == "https://sellerjoe.com"


def test_instagram_mapper_falls_back_to_bio_email():
    raw = {"ownerUsername": "x", "ownerBiography": "Reach: foo@bar.com"}
    r = map_instagram(raw)
    assert r.email == "foo@bar.com"


def test_facebook_mapper_full():
    raw = {
        "username": "sellerjoe",
        "url": "https://www.facebook.com/sellerjoe",
        "followers": 12345,
        "name": "Seller Joe",
        "email": "hi@sellerjoe.com",
        "website": "https://sellerjoe.com",
    }
    r = map_facebook(raw)
    assert r.channelId == "fb:sellerjoe"
    assert r.platform == "Facebook"
    assert r.profileUrl == "https://www.facebook.com/sellerjoe"
    assert r.followerCount == 12345


def test_facebook_mapper_uses_pageId_when_no_username():
    raw = {"pageId": "9988", "url": "https://www.facebook.com/9988", "name": "X"}
    r = map_facebook(raw)
    assert r.channelId == "fb:9988"
    assert r.handle == "9988"


def test_youtube_about_mapper():
    r = map_youtube_about(
        "https://www.youtube.com/@MrBeast/about",
        {"email": "biz@mrbeast.com", "websiteUrl": "https://mrbeast.com"},
    )
    assert r == {"email": "biz@mrbeast.com", "websiteUrl": "https://mrbeast.com"}


def test_youtube_about_mapper_missing():
    r = map_youtube_about("https://www.youtube.com/@x/about", {})
    assert r == {"email": None, "websiteUrl": None}
```

- [ ] **Step 2: Run tests, confirm fail**

```bash
cd /Users/guozhen/MailOutbound/smart-crawler/backend && python -m pytest tests/influencers/test_creator_record.py -v
```

Expected: `ModuleNotFoundError: No module named 'app.influencers.discover_models'`.

- [ ] **Step 3: Implement `discover_models.py`**

Create `backend/app/influencers/discover_models.py`:

```python
"""Apify-compatible output schema + per-platform raw→CreatorRecord mappers.

Discovery adapters return Apify-shaped raw dicts (matching the contracts the
internal Node caller already speaks). This module turns those into the unified
CreatorRecord the HTTP API returns.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass

_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")


@dataclass
class CreatorRecord:
    channelId: str
    name: str | None
    platform: str
    profileUrl: str
    handle: str | None
    followerCount: int | None
    email: str | None
    websiteUrl: str | None

    def to_dict(self) -> dict:
        return asdict(self)


def _first_email(*texts: str | None) -> str | None:
    for t in texts:
        if not t:
            continue
        m = _EMAIL_RE.search(t)
        if m:
            return m.group(0)
    return None


def _first_nonempty(*vals):
    for v in vals:
        if v not in (None, "", 0):
            return v
    # accept 0 only if no None/missing: re-scan for 0 if everything else was None
    for v in vals:
        if v == 0:
            return 0
    return None


def map_tiktok(raw: dict) -> CreatorRecord | None:
    a = (raw or {}).get("authorMeta") or {}
    uid = a.get("uniqueId") or a.get("name")
    if not uid:
        return None
    return CreatorRecord(
        channelId=f"@{uid}",
        name=a.get("nickName") or a.get("name"),
        platform="TikTok",
        profileUrl=f"https://www.tiktok.com/@{uid}",
        handle=uid,
        followerCount=_first_nonempty(
            a.get("fans"), a.get("followers"), a.get("followerCount"),
        ),
        email=_first_email(a.get("signature"), a.get("bioLink")),
        websiteUrl=a.get("bioLink"),
    )


def map_instagram(raw: dict) -> CreatorRecord | None:
    uid = raw.get("ownerUsername") or raw.get("username")
    if not uid:
        return None
    public_email = raw.get("publicEmail")
    bio = raw.get("ownerBiography") or raw.get("biography")
    return CreatorRecord(
        channelId=f"ig:{uid}",
        name=raw.get("ownerFullName") or raw.get("fullName"),
        platform="Instagram",
        profileUrl=f"https://www.instagram.com/{uid}/",
        handle=uid,
        followerCount=_first_nonempty(
            raw.get("ownerFollowersCount"), raw.get("followersCount"),
        ),
        email=public_email or _first_email(bio),
        websiteUrl=raw.get("ownerExternalUrl") or raw.get("externalUrl"),
    )


def map_facebook(raw: dict) -> CreatorRecord | None:
    uid = raw.get("username") or raw.get("pageId")
    if not uid:
        return None
    url = raw.get("url") or f"https://www.facebook.com/{uid}"
    return CreatorRecord(
        channelId=f"fb:{uid}",
        name=raw.get("title") or raw.get("name"),
        platform="Facebook",
        profileUrl=url,
        handle=str(uid),
        followerCount=_first_nonempty(
            raw.get("followers"), raw.get("followersCount"),
            raw.get("likes"), raw.get("fanCount"),
        ),
        email=raw.get("email") or _first_email(raw.get("about"), raw.get("description")),
        websiteUrl=raw.get("website"),
    )


def map_youtube_about(_url: str, parsed: dict) -> dict:
    return {
        "email": parsed.get("email"),
        "websiteUrl": parsed.get("websiteUrl"),
    }
```

- [ ] **Step 4: Run tests, confirm pass**

```bash
cd /Users/guozhen/MailOutbound/smart-crawler/backend && python -m pytest tests/influencers/test_creator_record.py -v
```

Expected: all 9 tests pass.

- [ ] **Step 5: Commit**

```bash
cd /Users/guozhen/MailOutbound/smart-crawler
git add backend/app/influencers/discover_models.py backend/tests/influencers/test_creator_record.py
git commit -m "feat(influencers): CreatorRecord schema + Apify-compatible mappers"
```

---

## Task 2: Cookie jar (load · cache · invalidate · redact)

**Files:**
- Create: `backend/app/influencers/cookie_jar.py`
- Test: `backend/tests/influencers/test_cookie_jar.py`

- [ ] **Step 1: Write failing tests**

Create `backend/tests/influencers/test_cookie_jar.py`:

```python
"""Unit tests for cookie jar."""
from __future__ import annotations

import json

import pytest

from app.influencers.cookie_jar import (
    CookieExpiredError,
    invalidate,
    load_cookies,
    redact,
)


pytestmark = pytest.mark.unit


def _write_jar(tmp_path, name="ig.json"):
    path = tmp_path / name
    path.write_text(json.dumps([
        {"name": "sessionid", "value": "ABCDEFG123", "domain": ".instagram.com", "path": "/"},
        {"name": "csrftoken", "value": "xyz", "domain": ".instagram.com", "path": "/"},
    ]))
    return str(path)


def test_load_cookies_returns_dict_from_env(monkeypatch, tmp_path):
    path = _write_jar(tmp_path)
    monkeypatch.setenv("IG_COOKIES_PATH", path)
    invalidate("instagram")  # clear any cache from prior tests
    jar = load_cookies("instagram")
    assert jar["sessionid"] == "ABCDEFG123"
    assert jar["csrftoken"] == "xyz"


def test_load_cookies_caches(monkeypatch, tmp_path):
    path = _write_jar(tmp_path)
    monkeypatch.setenv("IG_COOKIES_PATH", path)
    invalidate("instagram")
    jar1 = load_cookies("instagram")
    # mutate file; cache should ignore until invalidate()
    (tmp_path / "ig.json").write_text(json.dumps([
        {"name": "sessionid", "value": "CHANGED", "domain": ".instagram.com", "path": "/"},
    ]))
    jar2 = load_cookies("instagram")
    assert jar2["sessionid"] == "ABCDEFG123"
    invalidate("instagram")
    jar3 = load_cookies("instagram")
    assert jar3["sessionid"] == "CHANGED"


def test_load_cookies_missing_env_raises(monkeypatch):
    monkeypatch.delenv("IG_COOKIES_PATH", raising=False)
    invalidate("instagram")
    with pytest.raises(CookieExpiredError) as ei:
        load_cookies("instagram")
    assert "instagram" in str(ei.value)


def test_load_cookies_missing_file_raises(monkeypatch, tmp_path):
    monkeypatch.setenv("FB_COOKIES_PATH", str(tmp_path / "nope.json"))
    invalidate("facebook")
    with pytest.raises(CookieExpiredError):
        load_cookies("facebook")


def test_redact_removes_cookie_values():
    s = "Cookie: sessionid=SECRETVAL; csrftoken=xyz"
    out = redact(s, {"sessionid": "SECRETVAL", "csrftoken": "xyz"})
    assert "SECRETVAL" not in out
    assert "xyz" not in out
    assert "[REDACTED]" in out
```

- [ ] **Step 2: Run, confirm fail**

```bash
cd /Users/guozhen/MailOutbound/smart-crawler/backend && python -m pytest tests/influencers/test_cookie_jar.py -v
```

Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement cookie_jar.py**

Create `backend/app/influencers/cookie_jar.py`:

```python
"""Cookie loader for IG / FB adapters.

Cookies are JSON arrays of {name, value, domain, path} entries (Playwright
context.cookies() format). File paths come from env vars; the loader caches
parsed cookies until invalidate() is called (e.g. after a 401).

Never log cookie values — use redact() before emitting any string that may
contain them.
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path

_ENV_KEY = {
    "instagram": "IG_COOKIES_PATH",
    "facebook": "FB_COOKIES_PATH",
}

_cache: dict[str, dict[str, str]] = {}
_lock = threading.RLock()


class CookieExpiredError(RuntimeError):
    """Raised when a cookie jar is missing, unreadable, or rejected by the platform."""

    def __init__(self, platform: str, detail: str = ""):
        self.platform = platform
        msg = f"cookies_expired_{platform}"
        if detail:
            msg += f": {detail}"
        super().__init__(msg)


def load_cookies(platform: str) -> dict[str, str]:
    """Return {name: value} for the platform. Raises CookieExpiredError if missing."""
    with _lock:
        cached = _cache.get(platform)
        if cached is not None:
            return cached
        env_key = _ENV_KEY.get(platform)
        if not env_key:
            raise CookieExpiredError(platform, f"no env key for {platform}")
        path = os.environ.get(env_key)
        if not path:
            raise CookieExpiredError(platform, f"env {env_key} unset")
        p = Path(path)
        if not p.is_file():
            raise CookieExpiredError(platform, f"file {path} not found")
        try:
            entries = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            raise CookieExpiredError(platform, f"parse error: {e}") from e
        if not isinstance(entries, list):
            raise CookieExpiredError(platform, "expected JSON array")
        jar = {str(e["name"]): str(e["value"]) for e in entries if "name" in e and "value" in e}
        if not jar:
            raise CookieExpiredError(platform, "empty jar")
        _cache[platform] = jar
        return jar


def invalidate(platform: str) -> None:
    """Drop the cached jar so the next load_cookies() re-reads the file."""
    with _lock:
        _cache.pop(platform, None)


def redact(text: str, jar: dict[str, str]) -> str:
    """Replace every cookie value in `text` with [REDACTED]. For log scrubbing."""
    out = text
    for v in jar.values():
        if v:
            out = out.replace(v, "[REDACTED]")
    return out
```

- [ ] **Step 4: Run, confirm pass**

```bash
cd /Users/guozhen/MailOutbound/smart-crawler/backend && python -m pytest tests/influencers/test_cookie_jar.py -v
```

Expected: 5 tests pass.

- [ ] **Step 5: Commit**

```bash
cd /Users/guozhen/MailOutbound/smart-crawler
git add backend/app/influencers/cookie_jar.py backend/tests/influencers/test_cookie_jar.py
git commit -m "feat(influencers): cookie jar with env-path loading + log redaction"
```

---

## Task 3: YouTube About scraper (replaces ScraperAPI)

**Files:**
- Create: `backend/app/influencers/yt_about.py`
- Create: `backend/tests/influencers/fixtures/yt_about_mrbeast.html` (captured)
- Test: `backend/tests/influencers/test_yt_about_parser.py`

- [ ] **Step 1: Capture a real fixture**

```bash
mkdir -p /Users/guozhen/MailOutbound/smart-crawler/backend/tests/influencers/fixtures
curl -sL \
  -H 'User-Agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36' \
  -H 'Accept-Language: en-US,en;q=0.9' \
  'https://www.youtube.com/@MrBeast/about' \
  > /Users/guozhen/MailOutbound/smart-crawler/backend/tests/influencers/fixtures/yt_about_mrbeast.html

# Sanity check: file should be > 50KB and contain ytInitialData
wc -c /Users/guozhen/MailOutbound/smart-crawler/backend/tests/influencers/fixtures/yt_about_mrbeast.html
grep -c "ytInitialData" /Users/guozhen/MailOutbound/smart-crawler/backend/tests/influencers/fixtures/yt_about_mrbeast.html
```

Expected: file ≥ 50000 bytes, ≥1 match for `ytInitialData`. If empty or 0, change the handle to any known channel and retry.

- [ ] **Step 2: Write failing tests**

Create `backend/tests/influencers/test_yt_about_parser.py`:

```python
"""Unit tests for YouTube About parser (ScraperAPI replacement)."""
from __future__ import annotations

import pytest

from app.influencers.yt_about import parse_about_html


pytestmark = pytest.mark.unit


def test_parses_email_or_website(fixture_text):
    html = fixture_text("yt_about_mrbeast.html")
    result = parse_about_html(html)
    assert set(result.keys()) == {"email", "websiteUrl"}
    # MrBeast has business links; at least one of the two should be present.
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
```

- [ ] **Step 3: Run, confirm fail**

```bash
cd /Users/guozhen/MailOutbound/smart-crawler/backend && python -m pytest tests/influencers/test_yt_about_parser.py -v
```

Expected: `ModuleNotFoundError`.

- [ ] **Step 4: Implement yt_about.py**

Create `backend/app/influencers/yt_about.py`:

```python
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
from .cookie_jar import CookieExpiredError  # noqa: F401  (re-used semantics)

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
    raw = html.replace("\\u0026", "&")

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
    """Fetch the About page for a YouTube channel URL and parse email/website.

    profile_url accepts https://www.youtube.com/@handle, /@handle/about, or
    /channel/UCxxx — we normalize to /about.
    """
    url = profile_url.rstrip("/")
    if not url.endswith("/about"):
        url = url + "/about"
    s = http()
    s.headers["Accept-Language"] = "en-US,en;q=0.9"
    r = s.get(url, timeout=timeout)
    if r.status_code != 200:
        return {"email": None, "websiteUrl": None}
    return parse_about_html(r.text)
```

- [ ] **Step 5: Run, confirm pass**

```bash
cd /Users/guozhen/MailOutbound/smart-crawler/backend && python -m pytest tests/influencers/test_yt_about_parser.py -v
```

Expected: 4 tests pass. If the captured fixture had neither email nor website (test 1 fails), re-capture using a different known-good handle and rerun.

- [ ] **Step 6: Commit**

```bash
cd /Users/guozhen/MailOutbound/smart-crawler
git add backend/app/influencers/yt_about.py backend/tests/influencers/test_yt_about_parser.py backend/tests/influencers/fixtures/yt_about_mrbeast.html
git commit -m "feat(influencers): native YouTube About parser (replaces ScraperAPI)"
```

---

## Task 4: Run registry (in-memory RUNS/DATASETS dict + RLock + GC)

**Files:**
- Create: `backend/app/influencers/run_registry.py`
- Test: `backend/tests/influencers/test_run_registry.py`

- [ ] **Step 1: Write failing tests**

Create `backend/tests/influencers/test_run_registry.py`:

```python
"""Unit tests for in-memory run registry."""
from __future__ import annotations

import time

import pytest

from app.influencers.run_registry import (
    RunRegistry,
    RunStatus,
)


pytestmark = pytest.mark.unit


def test_create_run_returns_pending():
    reg = RunRegistry()
    rid = reg.create_run()
    run = reg.get_run(rid)
    assert run["status"] == RunStatus.PENDING
    assert run["itemCount"] == 0
    assert run["error"] is None
    assert run["startedAt"] is not None


def test_mark_running_succeeded_with_items():
    reg = RunRegistry()
    rid = reg.create_run()
    reg.mark_running(rid)
    assert reg.get_run(rid)["status"] == RunStatus.RUNNING
    reg.mark_succeeded(rid, items=[{"a": 1}, {"a": 2}])
    run = reg.get_run(rid)
    assert run["status"] == RunStatus.SUCCEEDED
    assert run["itemCount"] == 2
    assert run["finishedAt"] is not None
    assert reg.get_items(rid) == [{"a": 1}, {"a": 2}]


def test_mark_failed_preserves_partial_items():
    reg = RunRegistry()
    rid = reg.create_run()
    reg.mark_failed(rid, error="cookies_expired_instagram", partial_items=[{"x": 1}])
    run = reg.get_run(rid)
    assert run["status"] == RunStatus.FAILED
    assert run["error"] == "cookies_expired_instagram"
    assert reg.get_items(rid) == [{"x": 1}]


def test_get_items_supports_pagination():
    reg = RunRegistry()
    rid = reg.create_run()
    reg.mark_succeeded(rid, items=[{"i": i} for i in range(10)])
    assert reg.get_items(rid, limit=3, offset=0) == [{"i": 0}, {"i": 1}, {"i": 2}]
    assert reg.get_items(rid, limit=3, offset=7) == [{"i": 7}, {"i": 8}, {"i": 9}]


def test_get_run_unknown_returns_none():
    reg = RunRegistry()
    assert reg.get_run("nope") is None
    assert reg.get_items("nope") == []


def test_gc_drops_runs_older_than_ttl():
    reg = RunRegistry(ttl_seconds=0.05)
    rid = reg.create_run()
    reg.mark_succeeded(rid, items=[])
    time.sleep(0.1)
    n = reg.gc()
    assert n == 1
    assert reg.get_run(rid) is None
```

- [ ] **Step 2: Run, confirm fail**

```bash
cd /Users/guozhen/MailOutbound/smart-crawler/backend && python -m pytest tests/influencers/test_run_registry.py -v
```

Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement run_registry.py**

Create `backend/app/influencers/run_registry.py`:

```python
"""In-memory run/dataset registry for the discover HTTP API.

A `run` is one POST /discover/runs invocation. Its dataset is the list of items
produced. State lives in process memory; the FastAPI worker losing data on
restart is acceptable (clients retry).
"""
from __future__ import annotations

import threading
import time
import uuid
from datetime import datetime, timezone


class RunStatus:
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class RunRegistry:
    def __init__(self, ttl_seconds: float = 3600.0):
        self._runs: dict[str, dict] = {}
        self._items: dict[str, list[dict]] = {}
        self._lock = threading.RLock()
        self._ttl = ttl_seconds

    def create_run(self) -> str:
        rid = uuid.uuid4().hex
        with self._lock:
            self._runs[rid] = {
                "status": RunStatus.PENDING,
                "itemCount": 0,
                "error": None,
                "startedAt": _now(),
                "finishedAt": None,
                "_t": time.monotonic(),
            }
            self._items[rid] = []
        return rid

    def mark_running(self, rid: str) -> None:
        with self._lock:
            r = self._runs.get(rid)
            if r is not None:
                r["status"] = RunStatus.RUNNING

    def mark_succeeded(self, rid: str, items: list[dict]) -> None:
        with self._lock:
            r = self._runs.get(rid)
            if r is None:
                return
            self._items[rid] = list(items)
            r["status"] = RunStatus.SUCCEEDED
            r["itemCount"] = len(items)
            r["finishedAt"] = _now()
            r["_t"] = time.monotonic()

    def mark_failed(self, rid: str, error: str, partial_items: list[dict] | None = None) -> None:
        with self._lock:
            r = self._runs.get(rid)
            if r is None:
                return
            if partial_items:
                self._items[rid] = list(partial_items)
            r["status"] = RunStatus.FAILED
            r["error"] = error
            r["itemCount"] = len(self._items[rid])
            r["finishedAt"] = _now()
            r["_t"] = time.monotonic()

    def get_run(self, rid: str) -> dict | None:
        with self._lock:
            r = self._runs.get(rid)
            if r is None:
                return None
            return {k: v for k, v in r.items() if not k.startswith("_")}

    def get_items(self, rid: str, limit: int | None = None, offset: int = 0) -> list[dict]:
        with self._lock:
            items = self._items.get(rid, [])
            if limit is None:
                return items[offset:]
            return items[offset : offset + limit]

    def gc(self) -> int:
        """Drop runs older than TTL. Returns count dropped."""
        cutoff = time.monotonic() - self._ttl
        dropped = 0
        with self._lock:
            for rid in list(self._runs):
                if self._runs[rid].get("_t", 0) < cutoff:
                    self._runs.pop(rid, None)
                    self._items.pop(rid, None)
                    dropped += 1
        return dropped


REGISTRY = RunRegistry()
```

- [ ] **Step 4: Run, confirm pass**

```bash
cd /Users/guozhen/MailOutbound/smart-crawler/backend && python -m pytest tests/influencers/test_run_registry.py -v
```

Expected: 6 tests pass.

- [ ] **Step 5: Commit**

```bash
cd /Users/guozhen/MailOutbound/smart-crawler
git add backend/app/influencers/run_registry.py backend/tests/influencers/test_run_registry.py
git commit -m "feat(influencers): in-memory run registry with TTL GC"
```

---

## Task 5: Orchestrator + dispatch (with only YouTube wired)

**Files:**
- Create: `backend/app/influencers/discover.py`
- Test: `backend/tests/influencers/test_discover_orchestrator.py`

- [ ] **Step 1: Write failing tests**

Create `backend/tests/influencers/test_discover_orchestrator.py`:

```python
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
```

- [ ] **Step 2: Run, confirm fail**

```bash
cd /Users/guozhen/MailOutbound/smart-crawler/backend && python -m pytest tests/influencers/test_discover_orchestrator.py -v
```

Expected: `ImportError`.

- [ ] **Step 3: Implement orchestrator (YouTube only for now)**

Create `backend/app/influencers/discover.py`:

```python
"""Discover orchestrator — dispatches to per-platform adapters, dedupes results.

Each adapter exposes `run(params, limit) -> list[dict]` returning raw items
shaped for the per-platform mapper in discover_models.py. Adapters added
incrementally (Task 6+): tt_discover, ig_discover, fb_discover.
"""
from __future__ import annotations

from . import yt_about
from .discover_models import (
    CreatorRecord,
    map_facebook,
    map_instagram,
    map_tiktok,
    map_youtube_about,
)


def dedupe(items: list[dict]) -> list[dict]:
    """Drop dups keyed by (platform, handle)."""
    seen: set[tuple[str, str]] = set()
    out: list[dict] = []
    for it in items:
        key = (it.get("platform", ""), it.get("handle") or it.get("channelId", ""))
        if key in seen:
            continue
        seen.add(key)
        out.append(it)
    return out


def _yt_about_run(params: dict, limit: int) -> list[dict]:
    urls = list(params.get("urls") or [])[:limit]
    out = []
    for url in urls:
        parsed = yt_about.fetch_about(url)
        out.append(map_youtube_about(url, parsed))
    return out


_ADAPTERS = {
    "youtube_about": _yt_about_run,
}


def _to_dicts(records: list[CreatorRecord | dict | None]) -> list[dict]:
    out = []
    for r in records:
        if r is None:
            continue
        if isinstance(r, CreatorRecord):
            out.append(r.to_dict())
        else:
            out.append(r)
    return out


def dispatch(platform: str, params: dict, limit: int) -> list[dict]:
    """Run the adapter for `platform`. Raises ValueError for unknown platforms."""
    fn = _ADAPTERS.get(platform)
    if fn is None:
        raise ValueError(f"unknown platform: {platform}")
    raw = fn(params, limit)
    # Hashtag platforms produce CreatorRecord; youtube_about produces dicts.
    items = _to_dicts(raw)
    if platform in ("tiktok", "instagram", "facebook"):
        items = dedupe(items)
    return items


__all__ = ["dispatch", "dedupe", "map_tiktok", "map_instagram", "map_facebook"]
```

- [ ] **Step 4: Run, confirm pass**

```bash
cd /Users/guozhen/MailOutbound/smart-crawler/backend && python -m pytest tests/influencers/test_discover_orchestrator.py -v
```

Expected: 3 tests pass.

- [ ] **Step 5: Commit**

```bash
cd /Users/guozhen/MailOutbound/smart-crawler
git add backend/app/influencers/discover.py backend/tests/influencers/test_discover_orchestrator.py
git commit -m "feat(influencers): orchestrator with dedupe + youtube_about dispatch"
```

---

## Task 6: FastAPI router + HTTP contract tests (end-to-end with YT only)

**Files:**
- Create: `backend/app/api/influencer_discover.py`
- Modify: `backend/app/main.py`
- Test: `backend/tests/influencers/test_http_contract.py`

- [ ] **Step 1: Write failing tests**

Create `backend/tests/influencers/test_http_contract.py`:

```python
"""HTTP contract tests for /discover/* — uses FastAPI TestClient + monkeypatched adapters."""
from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from app.main import app


pytestmark = pytest.mark.unit


@pytest.fixture
def client(monkeypatch):
    # Make youtube_about deterministic and instant.
    monkeypatch.setattr(
        "app.influencers.yt_about.fetch_about",
        lambda url, timeout=20: {"email": "x@x.com", "websiteUrl": "https://x.com"},
    )
    return TestClient(app)


def _await_terminal(client, rid, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = client.get(f"/discover/runs/{rid}")
        assert r.status_code == 200
        status = r.json()["status"]
        if status in ("SUCCEEDED", "FAILED"):
            return r.json()
        time.sleep(0.05)
    raise AssertionError("run did not reach terminal state in time")


def test_yt_about_run_end_to_end(client):
    r = client.post("/discover/runs", json={
        "platform": "youtube_about",
        "urls": ["https://www.youtube.com/@a/about"],
    })
    assert r.status_code == 200, r.text
    body = r.json()
    rid = body["runId"]
    assert body["status"] == "PENDING"
    assert body["datasetId"] == rid

    final = _await_terminal(client, rid)
    assert final["status"] == "SUCCEEDED"
    assert final["itemCount"] == 1

    items = client.get(f"/discover/datasets/{rid}/items").json()
    assert items == [{"email": "x@x.com", "websiteUrl": "https://x.com"}]


def test_unknown_platform_returns_400(client):
    r = client.post("/discover/runs", json={"platform": "myspace", "urls": []})
    assert r.status_code == 400
    assert "unknown platform" in r.json()["detail"]


def test_get_unknown_run_returns_404(client):
    r = client.get("/discover/runs/does-not-exist")
    assert r.status_code == 404


def test_items_pagination(client, monkeypatch):
    monkeypatch.setattr(
        "app.influencers.yt_about.fetch_about",
        lambda url, timeout=20: {"email": url[-5:], "websiteUrl": None},
    )
    urls = [f"https://www.youtube.com/@a{i}/about" for i in range(5)]
    rid = client.post("/discover/runs", json={
        "platform": "youtube_about", "urls": urls,
    }).json()["runId"]
    _await_terminal(client, rid)
    items = client.get(f"/discover/datasets/{rid}/items?limit=2&offset=1").json()
    assert len(items) == 2
```

- [ ] **Step 2: Run, confirm fail**

```bash
cd /Users/guozhen/MailOutbound/smart-crawler/backend && python -m pytest tests/influencers/test_http_contract.py -v
```

Expected: 404 on `/discover/runs` (route not registered).

- [ ] **Step 3: Implement the router**

Create `backend/app/api/influencer_discover.py`:

```python
"""HTTP API for influencer discovery — Apify-compatible run/dataset lifecycle.

Endpoints:
  POST /discover/runs                    create run (returns runId + datasetId)
  GET  /discover/runs/{runId}            run status
  GET  /discover/datasets/{datasetId}/items   dataset items (paginated)

Runs execute in a FastAPI BackgroundTask; state lives in RunRegistry. Caller
should poll runs/{id} until status is SUCCEEDED or FAILED, then GET items.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query
from pydantic import BaseModel, Field

from ..influencers.discover import dispatch
from ..influencers.run_registry import REGISTRY, RunStatus

log = logging.getLogger(__name__)

router = APIRouter(prefix="/discover", tags=["influencer-discover"])

_SUPPORTED = {"tiktok", "instagram", "facebook", "youtube_about"}


class RunRequest(BaseModel):
    platform: str
    hashtags: list[str] | None = None
    urls: list[str] | None = None
    limit: int = Field(default=38, ge=1, le=200)


class RunCreated(BaseModel):
    runId: str
    datasetId: str
    status: str


class RunStatusResponse(BaseModel):
    status: str
    itemCount: int
    error: str | None
    startedAt: str
    finishedAt: str | None


def _execute_run(rid: str, platform: str, params: dict, limit: int) -> None:
    REGISTRY.mark_running(rid)
    try:
        items = dispatch(platform, params, limit)
        REGISTRY.mark_succeeded(rid, items=items)
    except ValueError as e:
        REGISTRY.mark_failed(rid, error=str(e))
    except Exception as e:  # pylint: disable=broad-except
        log.exception("discover run %s failed", rid)
        REGISTRY.mark_failed(rid, error=f"{type(e).__name__}: {e}")


@router.post("/runs", response_model=RunCreated)
def create_run(req: RunRequest, background: BackgroundTasks) -> RunCreated:
    if req.platform not in _SUPPORTED:
        raise HTTPException(
            status_code=400,
            detail=f"unknown platform: {req.platform}. Supported: {sorted(_SUPPORTED)}",
        )
    params = {
        "hashtags": req.hashtags or [],
        "urls": req.urls or [],
    }
    rid = REGISTRY.create_run()
    background.add_task(_execute_run, rid, req.platform, params, req.limit)
    return RunCreated(runId=rid, datasetId=rid, status=RunStatus.PENDING)


@router.get("/runs/{run_id}", response_model=RunStatusResponse)
def get_run(run_id: str) -> RunStatusResponse:
    r = REGISTRY.get_run(run_id)
    if r is None:
        raise HTTPException(status_code=404, detail=f"run not found: {run_id}")
    return RunStatusResponse(**r)


@router.get("/datasets/{dataset_id}/items")
def get_items(
    dataset_id: str,
    limit: int = Query(default=1000, ge=1, le=10000),
    offset: int = Query(default=0, ge=0),
):
    if REGISTRY.get_run(dataset_id) is None:
        raise HTTPException(status_code=404, detail=f"dataset not found: {dataset_id}")
    return REGISTRY.get_items(dataset_id, limit=limit, offset=offset)
```

- [ ] **Step 4: Register the router in main.py**

Edit `backend/app/main.py`. Add the import next to the other api imports (around line 15):

```python
from .api.influencer_discover import router as influencer_discover_router
```

Add the `include_router` call next to the other `include_router` calls (after `discovery_router`, around line 100):

```python
app.include_router(influencer_discover_router)  # /discover/runs · /discover/datasets
```

- [ ] **Step 5: Run, confirm pass**

```bash
cd /Users/guozhen/MailOutbound/smart-crawler/backend && python -m pytest tests/influencers/test_http_contract.py -v
```

Expected: 4 tests pass. If TestClient import fails, install: `pip install httpx` (FastAPI TestClient needs it).

- [ ] **Step 6: Run the full influencer suite to confirm nothing else broke**

```bash
cd /Users/guozhen/MailOutbound/smart-crawler/backend && python -m pytest tests/influencers/ -v
```

Expected: all tests so far pass (creator_record + cookie_jar + yt_about + run_registry + orchestrator + http_contract).

- [ ] **Step 7: Commit**

```bash
cd /Users/guozhen/MailOutbound/smart-crawler
git add backend/app/api/influencer_discover.py backend/app/main.py backend/tests/influencers/test_http_contract.py
git commit -m "feat(influencers): FastAPI router for /discover lifecycle"
```

At this point: end-to-end YouTube About replacement is shippable. The next three tasks add TikTok/IG/FB incrementally.

---

## Task 7: TikTok hashtag → creators

**Files:**
- Create: `backend/app/influencers/tt_discover.py`
- Create: `backend/tests/influencers/fixtures/tt_tag_amazonfba.html` (captured)
- Test: `backend/tests/influencers/test_tt_discover.py`

- [ ] **Step 1: Capture a real fixture**

```bash
curl -sL --compressed \
  -H 'User-Agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36' \
  -H 'Accept-Language: en-US,en;q=0.9' \
  -H 'Referer: https://www.tiktok.com/' \
  'https://www.tiktok.com/tag/amazonfba' \
  > /Users/guozhen/MailOutbound/smart-crawler/backend/tests/influencers/fixtures/tt_tag_amazonfba.html

wc -c /Users/guozhen/MailOutbound/smart-crawler/backend/tests/influencers/fixtures/tt_tag_amazonfba.html
grep -c "__UNIVERSAL_DATA_FOR_REHYDRATION__\|authorMeta\|uniqueId" \
  /Users/guozhen/MailOutbound/smart-crawler/backend/tests/influencers/fixtures/tt_tag_amazonfba.html
```

Expected: file ≥ 20000 bytes; ≥1 match. If 0, the IP is challenged — repeat through a residential proxy:

```bash
PROXY=$(head -1 /Users/guozhen/MailOutbound/smart-crawler/backend/proxies.txt)
curl -sL --compressed --proxy "$PROXY" \
  -H 'User-Agent: Mozilla/5.0 ... Chrome/131.0.0.0 Safari/537.36' \
  'https://www.tiktok.com/tag/amazonfba' \
  > /Users/guozhen/MailOutbound/smart-crawler/backend/tests/influencers/fixtures/tt_tag_amazonfba.html
```

- [ ] **Step 2: Write failing tests**

Create `backend/tests/influencers/test_tt_discover.py`:

```python
"""Unit tests for TikTok tag parser."""
from __future__ import annotations

import pytest

from app.influencers.tt_discover import extract_creators_from_tag_html


pytestmark = pytest.mark.unit


def test_extracts_creators_from_real_tag_page(fixture_text):
    html = fixture_text("tt_tag_amazonfba.html")
    creators = extract_creators_from_tag_html(html)
    assert len(creators) >= 1
    sample = creators[0]
    # Must yield an authorMeta-shaped dict the mapper can consume.
    assert "authorMeta" in sample
    assert sample["authorMeta"].get("uniqueId")


def test_empty_html_returns_empty_list():
    assert extract_creators_from_tag_html("") == []
    assert extract_creators_from_tag_html("<html></html>") == []
```

- [ ] **Step 3: Run, confirm fail**

```bash
cd /Users/guozhen/MailOutbound/smart-crawler/backend && python -m pytest tests/influencers/test_tt_discover.py -v
```

Expected: `ImportError`.

- [ ] **Step 4: Implement tt_discover.py**

Create `backend/app/influencers/tt_discover.py`:

```python
"""TikTok hashtag → creators adapter — replaces clockworks/tiktok-scraper.

Strategy: fetch https://www.tiktok.com/tag/{hashtag}, extract embedded JSON
(__UNIVERSAL_DATA_FOR_REHYDRATION__), walk to ItemList/ItemModule for the page's
videos, and harvest authorMeta from each. Each authorMeta dict is shaped to
match what the Apify TikTok actor produces, so discover_models.map_tiktok works
unchanged.
"""
from __future__ import annotations

import json
import logging
import re

from ..antiban import check_blocked, humanized_sleep, ip_record, rate_delay
from ..proxy import get_proxy
from ._common import http

log = logging.getLogger(__name__)

_TT_BASE = "https://www.tiktok.com"
_UNIVERSAL_RE = re.compile(
    r'<script[^>]*id="__UNIVERSAL_DATA_FOR_REHYDRATION__"[^>]*>(.+?)</script>',
    re.S,
)
_SIGI_RE = re.compile(r'<script[^>]*id="SIGI_STATE"[^>]*>(.+?)</script>', re.S)


def _parse_state(html: str) -> dict:
    m = _UNIVERSAL_RE.search(html)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    m = _SIGI_RE.search(html)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    return {}


def _walk_for_items(node, found: list[dict]) -> None:
    """DFS the state tree, collect any dict that looks like a video item."""
    if isinstance(node, dict):
        if isinstance(node.get("author"), dict) and node["author"].get("uniqueId"):
            found.append(node)
            return
        for v in node.values():
            _walk_for_items(v, found)
    elif isinstance(node, list):
        for v in node:
            _walk_for_items(v, found)


def _to_author_meta(item: dict) -> dict | None:
    author = item.get("author") or {}
    stats = item.get("authorStats") or {}
    uid = author.get("uniqueId")
    if not uid:
        return None
    return {
        "authorMeta": {
            "uniqueId": uid,
            "nickName": author.get("nickname") or author.get("nickName"),
            "fans": stats.get("followerCount") or stats.get("fans"),
            "followers": stats.get("followerCount"),
            "followerCount": stats.get("followerCount"),
            "signature": author.get("signature"),
            "bioLink": ((author.get("bioLink") or {}).get("link")
                        if isinstance(author.get("bioLink"), dict)
                        else author.get("bioLink")),
        },
    }


def extract_creators_from_tag_html(html: str) -> list[dict]:
    """Return a list of authorMeta-shaped dicts, deduped by uniqueId."""
    if not html:
        return []
    state = _parse_state(html)
    if not state:
        return []
    items: list[dict] = []
    _walk_for_items(state, items)
    out: list[dict] = []
    seen: set[str] = set()
    for it in items:
        meta = _to_author_meta(it)
        if not meta:
            continue
        uid = meta["authorMeta"]["uniqueId"]
        if uid in seen:
            continue
        seen.add(uid)
        out.append(meta)
    return out


def fetch_hashtag(hashtag: str, limit: int) -> list[dict]:
    """Fetch one hashtag page, return up to `limit` raw items (authorMeta dicts)."""
    tag = hashtag.lstrip("#")
    url = f"{_TT_BASE}/tag/{tag}"
    proxy = get_proxy("residential", site="tiktok")
    s = http()
    s.headers["Referer"] = f"{_TT_BASE}/"
    proxies = {"http": proxy, "https": proxy} if proxy else None
    r = s.get(url, timeout=20, proxies=proxies)
    ip_record(proxy or "direct")
    check_blocked(r.status_code, f"tiktok:tag:{tag}")
    if r.status_code != 200:
        return []
    creators = extract_creators_from_tag_html(r.text)
    humanized_sleep(rate_delay("tiktok", 3.0))
    return creators[:limit]


def run(params: dict, limit: int) -> list[dict]:
    """Adapter entry-point used by discover.dispatch."""
    from .discover_models import map_tiktok

    hashtags = list(params.get("hashtags") or [])
    out: list[dict] = []
    per_tag = max(1, limit // max(1, len(hashtags))) if hashtags else 0
    for tag in hashtags:
        for raw in fetch_hashtag(tag, per_tag):
            rec = map_tiktok(raw)
            if rec:
                out.append(rec.to_dict())
            if len(out) >= limit:
                return out
    return out
```

- [ ] **Step 5: Wire into orchestrator**

Edit `backend/app/influencers/discover.py`. At the top, add:

```python
from . import tt_discover
```

In the `_ADAPTERS` dict, add the entry:

```python
_ADAPTERS = {
    "youtube_about": _yt_about_run,
    "tiktok": tt_discover.run,
}
```

- [ ] **Step 6: Run unit tests**

```bash
cd /Users/guozhen/MailOutbound/smart-crawler/backend && python -m pytest tests/influencers/test_tt_discover.py tests/influencers/test_discover_orchestrator.py -v
```

Expected: pass.

- [ ] **Step 7: Smoke test (real network)**

Mark as smoke; add to the bottom of `test_tt_discover.py`:

```python
@pytest.mark.smoke
def test_smoke_tiktok_hashtag_returns_real_creators():
    from app.influencers.tt_discover import fetch_hashtag
    creators = fetch_hashtag("amazonfba", limit=5)
    assert len(creators) >= 1
    assert creators[0]["authorMeta"].get("uniqueId")
```

Run:

```bash
cd /Users/guozhen/MailOutbound/smart-crawler/backend && python -m pytest tests/influencers/test_tt_discover.py -m smoke -v
```

Expected: ≥1 creator. If 0, TikTok is challenging the IP — verify `backend/proxies.txt` is populated and re-run; if still failing, document the blocker and continue to other platforms.

- [ ] **Step 8: Commit**

```bash
cd /Users/guozhen/MailOutbound/smart-crawler
git add backend/app/influencers/tt_discover.py backend/app/influencers/discover.py backend/tests/influencers/test_tt_discover.py backend/tests/influencers/fixtures/tt_tag_amazonfba.html
git commit -m "feat(influencers): TikTok hashtag→creators adapter (replaces Apify)"
```

---

## Task 8: Instagram hashtag → creators

**Files:**
- Create: `backend/app/influencers/ig_discover.py`
- Create: `backend/tests/influencers/fixtures/ig_tag_amazonfba.json` (captured with cookie)
- Test: `backend/tests/influencers/test_ig_discover.py`

**Prerequisite:** Place a valid IG cookie jar at `backend/data/cookies/ig.json` (Playwright export from a logged-in IG session). The smoke test reads `IG_COOKIES_PATH`; for local dev:

```bash
export IG_COOKIES_PATH=/Users/guozhen/MailOutbound/smart-crawler/backend/data/cookies/ig.json
```

- [ ] **Step 1: Capture a fixture (one-shot manual)**

Run this Python snippet from `backend/` after exporting IG_COOKIES_PATH:

```bash
cd /Users/guozhen/MailOutbound/smart-crawler/backend && python -c "
import json, os
from app.influencers.cookie_jar import load_cookies
from app.influencers._common import http

jar = load_cookies('instagram')
s = http()
s.headers.update({
    'x-ig-app-id': '936619743392459',
    'x-asbd-id': '129477',
    'Referer': 'https://www.instagram.com/explore/tags/amazonfba/',
})
r = s.get('https://www.instagram.com/api/v1/tags/web_info/?tag_name=amazonfba',
          cookies=jar, timeout=20)
print('status:', r.status_code, 'bytes:', len(r.content))
open('tests/influencers/fixtures/ig_tag_amazonfba.json','wb').write(r.content)
"

ls -la /Users/guozhen/MailOutbound/smart-crawler/backend/tests/influencers/fixtures/ig_tag_amazonfba.json
```

Expected: status 200, bytes > 1000. If 401/302 → cookie is bad (re-export from a browser session and retry).

- [ ] **Step 2: Write failing tests**

Create `backend/tests/influencers/test_ig_discover.py`:

```python
"""Unit tests for Instagram tag JSON parser."""
from __future__ import annotations

import pytest

from app.influencers.ig_discover import extract_creators_from_tag_json


pytestmark = pytest.mark.unit


def test_extracts_creators_from_real_tag_json(fixture_json):
    data = fixture_json("ig_tag_amazonfba.json")
    creators = extract_creators_from_tag_json(data)
    assert len(creators) >= 1
    sample = creators[0]
    # Shape expected by discover_models.map_instagram
    assert sample.get("ownerUsername")


def test_empty_json_returns_empty():
    assert extract_creators_from_tag_json({}) == []
    assert extract_creators_from_tag_json({"data": {}}) == []
```

- [ ] **Step 3: Run, confirm fail**

```bash
cd /Users/guozhen/MailOutbound/smart-crawler/backend && python -m pytest tests/influencers/test_ig_discover.py -v
```

Expected: `ImportError`.

- [ ] **Step 4: Implement ig_discover.py**

Create `backend/app/influencers/ig_discover.py`:

```python
"""Instagram hashtag → creators adapter — replaces apify/instagram-scraper.

Uses the public-ish web JSON endpoint /api/v1/tags/web_info/?tag_name=... with
a logged-in cookie jar. Output dicts match the Apify Instagram actor's shape
so discover_models.map_instagram works unchanged.
"""
from __future__ import annotations

import logging

from ..antiban import check_blocked, humanized_sleep, ip_record, rate_delay
from ..proxy import get_proxy
from ._common import http
from .cookie_jar import CookieExpiredError, invalidate, load_cookies

log = logging.getLogger(__name__)

_IG_BASE = "https://www.instagram.com"
_TAG_API = _IG_BASE + "/api/v1/tags/web_info/?tag_name={tag}"


def _iter_users(node) -> list[dict]:
    """Walk the tag_info JSON and collect every user-shaped dict."""
    found: list[dict] = []

    def walk(n):
        if isinstance(n, dict):
            u = n.get("user")
            if isinstance(u, dict) and u.get("username"):
                found.append(u)
            for v in n.values():
                walk(v)
        elif isinstance(n, list):
            for v in n:
                walk(v)

    walk(node)
    return found


def extract_creators_from_tag_json(data: dict) -> list[dict]:
    if not data:
        return []
    users = _iter_users(data)
    out: list[dict] = []
    seen: set[str] = set()
    for u in users:
        uid = u.get("username")
        if not uid or uid in seen:
            continue
        seen.add(uid)
        out.append({
            "ownerUsername": uid,
            "ownerFullName": u.get("full_name"),
            "ownerFollowersCount": (u.get("follower_count")
                                    or u.get("followers_count")
                                    or u.get("edge_followed_by", {}).get("count")
                                    if isinstance(u.get("edge_followed_by"), dict) else None),
            "ownerBiography": u.get("biography"),
            "ownerExternalUrl": u.get("external_url"),
            "publicEmail": u.get("public_email"),
        })
    return out


def fetch_hashtag(hashtag: str, limit: int) -> list[dict]:
    tag = hashtag.lstrip("#")
    try:
        jar = load_cookies("instagram")
    except CookieExpiredError:
        raise
    s = http()
    s.headers.update({
        "x-ig-app-id": "936619743392459",
        "x-asbd-id": "129477",
        "Referer": f"{_IG_BASE}/explore/tags/{tag}/",
    })
    proxy = get_proxy("residential", site="instagram")
    proxies = {"http": proxy, "https": proxy} if proxy else None
    r = s.get(_TAG_API.format(tag=tag), cookies=jar, timeout=20,
              proxies=proxies, allow_redirects=False)
    ip_record(proxy or "direct")
    if r.status_code in (301, 302) or r.status_code in (401, 403):
        loc = r.headers.get("Location", "")
        if r.status_code in (401, 403) or "/accounts/login" in loc:
            invalidate("instagram")
            raise CookieExpiredError("instagram", f"status={r.status_code}")
    check_blocked(r.status_code, f"instagram:tag:{tag}")
    if r.status_code != 200:
        return []
    try:
        data = r.json()
    except ValueError:
        return []
    creators = extract_creators_from_tag_json(data)
    humanized_sleep(rate_delay("instagram", 4.0))
    return creators[:limit]


def run(params: dict, limit: int) -> list[dict]:
    from .discover_models import map_instagram

    hashtags = list(params.get("hashtags") or [])
    out: list[dict] = []
    per_tag = max(1, limit // max(1, len(hashtags))) if hashtags else 0
    for tag in hashtags:
        for raw in fetch_hashtag(tag, per_tag):
            rec = map_instagram(raw)
            if rec:
                out.append(rec.to_dict())
            if len(out) >= limit:
                return out
    return out
```

- [ ] **Step 5: Wire into orchestrator**

Edit `backend/app/influencers/discover.py`:

```python
from . import ig_discover, tt_discover, yt_about
```

Update `_ADAPTERS`:

```python
_ADAPTERS = {
    "youtube_about": _yt_about_run,
    "tiktok": tt_discover.run,
    "instagram": ig_discover.run,
}
```

- [ ] **Step 6: Run unit tests**

```bash
cd /Users/guozhen/MailOutbound/smart-crawler/backend && python -m pytest tests/influencers/test_ig_discover.py -v
```

Expected: 2 unit tests pass.

- [ ] **Step 7: Smoke test (real network, requires IG_COOKIES_PATH)**

Append to `test_ig_discover.py`:

```python
import os

@pytest.mark.smoke
@pytest.mark.skipif(not os.environ.get("IG_COOKIES_PATH"),
                    reason="IG_COOKIES_PATH not set")
def test_smoke_instagram_hashtag_returns_real_creators():
    from app.influencers.ig_discover import fetch_hashtag
    creators = fetch_hashtag("amazonfba", limit=5)
    assert len(creators) >= 1
    assert creators[0]["ownerUsername"]
```

Run:

```bash
cd /Users/guozhen/MailOutbound/smart-crawler/backend && python -m pytest tests/influencers/test_ig_discover.py -m smoke -v
```

Expected: pass. CookieExpiredError → re-export cookies and retry.

- [ ] **Step 8: Commit**

```bash
cd /Users/guozhen/MailOutbound/smart-crawler
git add backend/app/influencers/ig_discover.py backend/app/influencers/discover.py backend/tests/influencers/test_ig_discover.py backend/tests/influencers/fixtures/ig_tag_amazonfba.json
git commit -m "feat(influencers): Instagram hashtag→creators adapter (replaces Apify)"
```

---

## Task 9: Facebook pages search → pages

**Files:**
- Create: `backend/app/influencers/fb_discover.py`
- Create: `backend/tests/influencers/fixtures/fb_search_amazonfba.html` (captured with cookie)
- Test: `backend/tests/influencers/test_fb_discover.py`

**Prerequisite:** Place a valid FB cookie jar at `backend/data/cookies/fb.json`; export `FB_COOKIES_PATH`.

- [ ] **Step 1: Capture a fixture**

```bash
cd /Users/guozhen/MailOutbound/smart-crawler/backend && python -c "
from app.influencers.cookie_jar import load_cookies
from app.influencers._common import http
jar = load_cookies('facebook')
s = http()
s.headers['Referer'] = 'https://www.facebook.com/'
r = s.get('https://www.facebook.com/search/pages/?q=amazon+fba',
         cookies=jar, timeout=20, allow_redirects=False)
print('status:', r.status_code, 'bytes:', len(r.content))
open('tests/influencers/fixtures/fb_search_amazonfba.html','wb').write(r.content)
"

wc -c /Users/guozhen/MailOutbound/smart-crawler/backend/tests/influencers/fixtures/fb_search_amazonfba.html
```

Expected: status 200, ≥ 50000 bytes. Status 302→/login means cookie is bad.

- [ ] **Step 2: Write failing tests**

Create `backend/tests/influencers/test_fb_discover.py`:

```python
"""Unit tests for Facebook search/pages parser."""
from __future__ import annotations

import pytest

from app.influencers.fb_discover import extract_pages_from_search_html


pytestmark = pytest.mark.unit


def test_extracts_pages_from_real_search_html(fixture_text):
    html = fixture_text("fb_search_amazonfba.html")
    pages = extract_pages_from_search_html(html)
    assert len(pages) >= 1
    sample = pages[0]
    # Must yield dict shaped for discover_models.map_facebook
    assert sample.get("username") or sample.get("pageId")
    assert sample.get("url", "").startswith("https://www.facebook.com/")


def test_empty_html_returns_empty():
    assert extract_pages_from_search_html("") == []
```

- [ ] **Step 3: Run, confirm fail**

```bash
cd /Users/guozhen/MailOutbound/smart-crawler/backend && python -m pytest tests/influencers/test_fb_discover.py -v
```

Expected: `ImportError`.

- [ ] **Step 4: Implement fb_discover.py**

Create `backend/app/influencers/fb_discover.py`:

```python
"""Facebook pages search → pages adapter — replaces apify/facebook-pages-scraper.

Facebook search response embeds page URLs in inline scripts as
"https://www.facebook.com/<handle>" or "/profile.php?id=<pageId>". Parse those,
dedupe, optionally enrich (followers / website / email) with a second request
to the page's about_overview tab.
"""
from __future__ import annotations

import logging
import re
from urllib.parse import quote_plus

from ..antiban import check_blocked, humanized_sleep, ip_record, rate_delay
from ..proxy import get_proxy
from ._common import http
from .cookie_jar import CookieExpiredError, invalidate, load_cookies

log = logging.getLogger(__name__)

_FB_BASE = "https://www.facebook.com"
_SEARCH = _FB_BASE + "/search/pages/?q={q}"

_PAGE_HANDLE_RE = re.compile(
    r'"(?:url|page_url)"\s*:\s*"https://www\.facebook\.com/([A-Za-z0-9.\-]+)/?"'
)


def extract_pages_from_search_html(html: str) -> list[dict]:
    if not html:
        return []
    raw = html.replace("\\/", "/")

    handles: list[str] = []
    seen: set[str] = set()
    for m in _PAGE_HANDLE_RE.finditer(raw):
        h = m.group(1)
        if h.lower() in ("search", "pages", "watch", "home", "marketplace"):
            continue
        if h in seen:
            continue
        seen.add(h)
        handles.append(h)

    out: list[dict] = []
    for h in handles:
        out.append({
            "username": h,
            "url": f"{_FB_BASE}/{h}",
            "name": None,
            "followers": None,
            "website": None,
            "email": None,
        })
    return out


def _fetch_search(query: str) -> str:
    try:
        jar = load_cookies("facebook")
    except CookieExpiredError:
        raise
    s = http()
    s.headers["Referer"] = f"{_FB_BASE}/"
    proxy = get_proxy("residential", site="facebook")
    proxies = {"http": proxy, "https": proxy} if proxy else None
    r = s.get(_SEARCH.format(q=quote_plus(query)), cookies=jar, timeout=20,
              proxies=proxies, allow_redirects=False)
    ip_record(proxy or "direct")
    loc = r.headers.get("Location", "")
    if r.status_code in (301, 302) and ("/login" in loc or "/checkpoint/" in loc):
        invalidate("facebook")
        raise CookieExpiredError("facebook", f"redirect to {loc[:64]}")
    check_blocked(r.status_code, f"facebook:search:{query[:32]}")
    if r.status_code != 200:
        return ""
    humanized_sleep(rate_delay("facebook", 4.0))
    return r.text


def fetch_query(query: str, limit: int) -> list[dict]:
    html = _fetch_search(query)
    pages = extract_pages_from_search_html(html)
    return pages[:limit]


def run(params: dict, limit: int) -> list[dict]:
    from .discover_models import map_facebook

    queries = list(params.get("hashtags") or [])  # FB uses hashtags slot as queries
    out: list[dict] = []
    per_q = max(1, limit // max(1, len(queries))) if queries else 0
    for q in queries:
        for raw in fetch_query(q, per_q):
            rec = map_facebook(raw)
            if rec:
                out.append(rec.to_dict())
            if len(out) >= limit:
                return out
    return out
```

- [ ] **Step 5: Wire into orchestrator**

Edit `backend/app/influencers/discover.py`:

```python
from . import fb_discover, ig_discover, tt_discover, yt_about
```

Update `_ADAPTERS`:

```python
_ADAPTERS = {
    "youtube_about": _yt_about_run,
    "tiktok": tt_discover.run,
    "instagram": ig_discover.run,
    "facebook": fb_discover.run,
}
```

- [ ] **Step 6: Run unit tests**

```bash
cd /Users/guozhen/MailOutbound/smart-crawler/backend && python -m pytest tests/influencers/test_fb_discover.py -v
```

Expected: 2 tests pass.

- [ ] **Step 7: Smoke test**

Append to `test_fb_discover.py`:

```python
import os

@pytest.mark.smoke
@pytest.mark.skipif(not os.environ.get("FB_COOKIES_PATH"),
                    reason="FB_COOKIES_PATH not set")
def test_smoke_facebook_search_returns_real_pages():
    from app.influencers.fb_discover import fetch_query
    pages = fetch_query("amazon fba", limit=5)
    assert len(pages) >= 1
    assert pages[0]["username"]
```

Run:

```bash
cd /Users/guozhen/MailOutbound/smart-crawler/backend && python -m pytest tests/influencers/test_fb_discover.py -m smoke -v
```

Expected: ≥1 page.

- [ ] **Step 8: Commit**

```bash
cd /Users/guozhen/MailOutbound/smart-crawler
git add backend/app/influencers/fb_discover.py backend/app/influencers/discover.py backend/tests/influencers/test_fb_discover.py backend/tests/influencers/fixtures/fb_search_amazonfba.html
git commit -m "feat(influencers): Facebook pages search adapter (replaces Apify)"
```

---

## Task 10: __init__.py re-exports + README + runbook

**Files:**
- Modify: `backend/app/influencers/__init__.py`
- Create: `backend/app/influencers/README.md`

- [ ] **Step 1: Re-export new public symbols**

Open `backend/app/influencers/__init__.py`. After the existing imports/exports, append:

```python
from .discover import dispatch as discover  # noqa: E402
from .discover_models import CreatorRecord  # noqa: E402

__all__ = list(__all__) + ["discover", "CreatorRecord"]
```

- [ ] **Step 2: Write README**

Create `backend/app/influencers/README.md`:

```markdown
# Influencers — native discover adapters

Native replacement for Apify + ScraperAPI. Exposed via HTTP:

- `POST /discover/runs`                  create run
- `GET  /discover/runs/{runId}`          poll status
- `GET  /discover/datasets/{id}/items`   fetch items

## Supported platforms

| platform string | input slot      | output                          |
|-----------------|-----------------|---------------------------------|
| `tiktok`        | `hashtags[]`    | CreatorRecord[]                 |
| `instagram`     | `hashtags[]`    | CreatorRecord[]                 |
| `facebook`      | `hashtags[]` (used as search queries) | CreatorRecord[] |
| `youtube_about` | `urls[]`        | `[{email, websiteUrl}, ...]`    |

## Cookie runbook (IG / FB)

Adapters read cookies from JSON files pointed to by env vars:

```
IG_COOKIES_PATH=/app/data/cookies/ig.json    # in-container
FB_COOKIES_PATH=/app/data/cookies/fb.json
```

NAS host path: `/volume1/docker/smart-crawler/app/data/cookies/`.

### How to refresh a cookie jar

**Preferred path — TGE 指纹浏览器（免费额度够用）：**

1. 在 TGE 里新建一个干净 profile（指纹独立、IP 走住宅代理），登录 instagram.com / facebook.com 完成所有人机校验。
2. 用 TGE 的 "导出 cookies" 功能，导出为 JSON 数组（Playwright 兼容格式）。如果 TGE 只能导出 cookies.txt，用下面 Python 一行转 JSON：
   ```python
   import json, http.cookiejar as cj
   jar = cj.MozillaCookieJar(); jar.load("cookies.txt", ignore_discard=True)
   open("ig.json","w").write(json.dumps([
       {"name":c.name,"value":c.value,"domain":c.domain,"path":c.path}
       for c in jar
   ]))
   ```
3. `scp ig.json solvea@192.168.1.80:/volume1/docker/smart-crawler/app/data/cookies/`
4. `chmod 600` the file. No container restart needed — adapters reload on next 401/403.

**Fallback — local Playwright（只在 TGE 不可用时用）：**

```python
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b = p.chromium.launch(headless=False)
    ctx = b.new_context()
    page = ctx.new_page()
    page.goto("https://www.instagram.com/")
    input("log in, press Enter...")
    import json
    open("ig.json","w").write(json.dumps(ctx.cookies()))
```

注意：本机直连 Playwright 容易触发 IG/FB 风控；TGE 指纹+代理组合存活率显著更高。

## Example calls

```bash
# TikTok hashtag discovery
curl -X POST http://localhost:8077/discover/runs \
  -H 'Content-Type: application/json' \
  -d '{"platform":"tiktok","hashtags":["amazonfba","amazonseller"],"limit":38}'

# YouTube About enrichment
curl -X POST http://localhost:8077/discover/runs \
  -H 'Content-Type: application/json' \
  -d '{"platform":"youtube_about","urls":["https://www.youtube.com/@MrBeast/about"]}'
```
```

- [ ] **Step 3: Commit**

```bash
cd /Users/guozhen/MailOutbound/smart-crawler
git add backend/app/influencers/__init__.py backend/app/influencers/README.md
git commit -m "docs(influencers): README + cookie refresh runbook"
```

---

## Task 11: Deploy to NAS + manual smoke verification

**Files:** (deployment artifacts only)
- Create on NAS: `/volume1/docker/smart-crawler/app/data/cookies/{ig,fb}.json`
- Create on NAS: `/volume1/docker/smart-crawler/app/deliverables/discover_smoke_2026-05-28.json`

- [ ] **Step 1: Full local test suite green**

```bash
cd /Users/guozhen/MailOutbound/smart-crawler/backend && python -m pytest tests/influencers/ -v
```

Expected: every unit test passes. Smoke tests may be skipped locally if cookie env not set.

- [ ] **Step 2: Sync code to NAS**

```bash
scp /Users/guozhen/MailOutbound/smart-crawler/backend/app/influencers/discover_models.py \
    /Users/guozhen/MailOutbound/smart-crawler/backend/app/influencers/cookie_jar.py \
    /Users/guozhen/MailOutbound/smart-crawler/backend/app/influencers/run_registry.py \
    /Users/guozhen/MailOutbound/smart-crawler/backend/app/influencers/yt_about.py \
    /Users/guozhen/MailOutbound/smart-crawler/backend/app/influencers/tt_discover.py \
    /Users/guozhen/MailOutbound/smart-crawler/backend/app/influencers/ig_discover.py \
    /Users/guozhen/MailOutbound/smart-crawler/backend/app/influencers/fb_discover.py \
    /Users/guozhen/MailOutbound/smart-crawler/backend/app/influencers/discover.py \
    /Users/guozhen/MailOutbound/smart-crawler/backend/app/influencers/__init__.py \
    /Users/guozhen/MailOutbound/smart-crawler/backend/app/influencers/README.md \
    solvea@192.168.1.80:/volume1/docker/smart-crawler/app/backend/app/influencers/

scp /Users/guozhen/MailOutbound/smart-crawler/backend/app/api/influencer_discover.py \
    solvea@192.168.1.80:/volume1/docker/smart-crawler/app/backend/app/api/

scp /Users/guozhen/MailOutbound/smart-crawler/backend/app/main.py \
    solvea@192.168.1.80:/volume1/docker/smart-crawler/app/backend/app/

scp -r /Users/guozhen/MailOutbound/smart-crawler/backend/tests/influencers \
    solvea@192.168.1.80:/volume1/docker/smart-crawler/app/backend/tests/

scp /Users/guozhen/MailOutbound/smart-crawler/backend/pytest.ini \
    solvea@192.168.1.80:/volume1/docker/smart-crawler/app/backend/
```

- [ ] **Step 3: Place cookies on NAS**

```bash
ssh solvea@192.168.1.80 'mkdir -p /volume1/docker/smart-crawler/app/data/cookies'
scp /local/path/to/ig.json /local/path/to/fb.json \
    solvea@192.168.1.80:/volume1/docker/smart-crawler/app/data/cookies/
ssh solvea@192.168.1.80 'chmod 600 /volume1/docker/smart-crawler/app/data/cookies/*.json'
```

- [ ] **Step 4: Set cookie env vars + restart container**

Edit `/volume1/docker/smart-crawler/docker-compose.yml` to add under the `api` service `environment:`:

```yaml
      - IG_COOKIES_PATH=/app/data/cookies/ig.json
      - FB_COOKIES_PATH=/app/data/cookies/fb.json
```

Restart:

```bash
ssh solvea@192.168.1.80 'cd /volume1/docker/smart-crawler && docker compose restart api'
```

Wait 5s, then verify the container is healthy:

```bash
ssh solvea@192.168.1.80 'docker compose -f /volume1/docker/smart-crawler/docker-compose.yml ps api'
```

Expected: `Up` and no restart loop.

- [ ] **Step 5: Run smoke from inside the container**

```bash
ssh solvea@192.168.1.80 'cd /volume1/docker/smart-crawler && docker compose exec -T api python -m pytest backend/tests/influencers/ -v'
```

Expected: all unit tests pass; IG/FB/TT smoke marked tests pass when cookies are in place; YT About smoke passes unconditionally.

- [ ] **Step 6: Run the curl validation suite + archive output**

```bash
OUT=/tmp/discover_smoke_2026-05-28.json
echo "[" > "$OUT"
for p in tiktok instagram facebook; do
  echo "{\"platform\":\"$p\",\"resp\":" >> "$OUT"
  R=$(curl -s -X POST http://192.168.1.80:8077/discover/runs \
    -H 'Content-Type: application/json' \
    -d "{\"platform\":\"$p\",\"hashtags\":[\"amazonfba\"],\"limit\":10}")
  echo "$R," >> "$OUT"
  RID=$(echo "$R" | python -c 'import json,sys;print(json.load(sys.stdin)["runId"])')
  sleep 15
  echo "\"final\":" >> "$OUT"
  curl -s "http://192.168.1.80:8077/discover/runs/$RID" >> "$OUT"
  echo "," >> "$OUT"
  echo "\"items\":" >> "$OUT"
  curl -s "http://192.168.1.80:8077/discover/datasets/$RID/items" >> "$OUT"
  echo "}," >> "$OUT"
done
echo "{\"platform\":\"youtube_about\",\"resp\":" >> "$OUT"
R=$(curl -s -X POST http://192.168.1.80:8077/discover/runs \
  -H 'Content-Type: application/json' \
  -d '{"platform":"youtube_about","urls":["https://www.youtube.com/@MrBeast/about"]}')
RID=$(echo "$R" | python -c 'import json,sys;print(json.load(sys.stdin)["runId"])')
sleep 5
echo "$R," >> "$OUT"
echo "\"items\":" >> "$OUT"
curl -s "http://192.168.1.80:8077/discover/datasets/$RID/items" >> "$OUT"
echo "}]" >> "$OUT"

cat "$OUT" | python -m json.tool > /dev/null && echo OK || echo "JSON INVALID"

scp "$OUT" solvea@192.168.1.80:/volume1/docker/smart-crawler/app/deliverables/
```

Expected: all 4 platforms return `SUCCEEDED` with `itemCount >= 1`. Save the archived JSON.

- [ ] **Step 7: Tell the Node caller to switch base URL**

DingTalk message: `python3 /Users/guozhen/MailOutbound/send_group.py "红人发现接口已迁移到 smart-crawler：把 Apify base URL 切到 http://192.168.1.80:8077/discover/，schema 见 backend/app/influencers/README.md"`

- [ ] **Step 8: Final commit (deployment notes if any)**

```bash
cd /Users/guozhen/MailOutbound/smart-crawler
git status -s  # verify nothing dirty unintentionally
```

If the compose file got edited locally:

```bash
git add docker-compose.yml
git commit -m "ops(influencers): wire IG/FB cookie env paths in api service"
```

---

## Open risks / known gaps

- **TikTok hashtag without proxy** may return DataDome challenge. Mitigation already in code: residential proxy via existing `proxies.txt`.
- **IG `/api/v1/tags/web_info/`** can return a stripped payload depending on session quality; if `extract_creators_from_tag_json` finds 0 users on a real fixture, fall back to scraping `/explore/tags/{tag}/` HTML and walking `xdt_api__v1__tags__tag_name__sections` in the inline JSON. Document this as a Phase 2 fallback.
- **FB followers / website / email enrichment** is currently null on first request — the parser only yields handles. If the caller depends on followerCount, add a per-page enrichment hop in Phase 2 (`GET /{handle}/about`).
- **Twitter / X** is intentionally out of scope; existing `influencers/twitter.py` is untouched.
