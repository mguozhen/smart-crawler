#!/usr/bin/env python3
"""HTTP-level deployment smoke test for smart-crawler."""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any


BASE_URL = os.environ.get("SMARTCRAWLER_BASE_URL", "http://127.0.0.1:8077").rstrip("/")
API_KEY = os.environ.get("SMARTCRAWLER_API_KEY") or os.environ.get("API_KEY") or ""
ADMIN_USERNAME = os.environ.get("SMARTCRAWLER_ADMIN_USERNAME") or os.environ.get("ADMIN_USERNAME") or ""
ADMIN_PASSWORD = os.environ.get("SMARTCRAWLER_ADMIN_PASSWORD") or os.environ.get("ADMIN_PASSWORD") or ""


def parse_response_body(raw: str) -> Any:
    if not raw:
        return None
    candidate = raw.strip()
    if "data:" in candidate:
        for line in candidate.splitlines():
            line = line.strip()
            if line.startswith("data:"):
                candidate = line.split("data:", 1)[1].strip()
                break
    return json.loads(candidate)


@dataclass
class Result:
    name: str
    ok: bool
    detail: str


def request(method: str, path: str, *, token: str = "", api_key: str = "",
            body: dict[str, Any] | None = None, workspace_id: int | None = None,
            extra_headers: dict[str, str] | None = None,
            timeout: int = 12) -> tuple[int, Any, dict[str, str]]:
    data = None
    headers: dict[str, str] = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
        headers["X-API-Key"] = api_key
    if workspace_id:
        headers["X-Workspace-ID"] = str(workspace_id)
    if extra_headers:
        headers.update(extra_headers)
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(BASE_URL + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode()
            parsed = parse_response_body(raw)
            return resp.status, parsed, {k.lower(): v for k, v in resp.headers.items()}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode()
        try:
            parsed = json.loads(raw)
        except Exception:
            parsed = raw
        return exc.code, parsed, {k.lower(): v for k, v in exc.headers.items()}


def wait_health() -> Result:
    last = ""
    for _ in range(30):
        try:
            status, body, _ = request("GET", "/health", timeout=3)
            if status == 200 and body and body.get("status") == "ok":
                return Result("health", True, "/health ok")
            last = f"{status} {body}"
        except Exception as exc:
            last = str(exc)
        time.sleep(1)
    return Result("health", False, last)


def login() -> tuple[Result, str]:
    if not ADMIN_USERNAME or not ADMIN_PASSWORD:
        return Result("admin_login", False, "SMARTCRAWLER_ADMIN_USERNAME/PASSWORD not set"), ""
    status, body, _ = request(
        "POST", "/api/auth/login",
        body={"identifier": ADMIN_USERNAME, "password": ADMIN_PASSWORD},
    )
    if status == 200 and body and body.get("token"):
        return Result("admin_login", True, f"logged in as {ADMIN_USERNAME}"), body["token"]
    return Result("admin_login", False, f"{status} {body}"), ""


def verify_admin_surface(token: str) -> list[Result]:
    results: list[Result] = []
    status, me, _ = request("GET", "/api/me", token=token)
    if status != 200:
        return [Result("api_me", False, f"{status} {me}")]
    workspace_id = me.get("current_workspace_id")
    results.append(Result(
        "api_me", bool(me.get("workspaces") and workspace_id),
        f"user={me.get('username')} workspaces={len(me.get('workspaces') or [])} current={workspace_id}",
    ))
    status, workspaces, _ = request("GET", "/api/workspaces", token=token)
    results.append(Result(
        "workspaces", status == 200 and isinstance(workspaces, list) and len(workspaces) >= 1,
        f"{status} count={len(workspaces) if isinstance(workspaces, list) else 'n/a'}",
    ))
    status, sites, _ = request("GET", "/api/sites", token=token, workspace_id=workspace_id)
    results.append(Result(
        "workspace_sites", status == 200 and isinstance(sites, list),
        f"{status} count={len(sites) if isinstance(sites, list) else 'n/a'}",
    ))
    status, keys, _ = request("GET", "/api/keys", token=token, workspace_id=workspace_id)
    results.append(Result(
        "workspace_keys", status == 200 and isinstance(keys, list),
        f"{status} count={len(keys) if isinstance(keys, list) else 'n/a'}",
    ))
    if isinstance(sites, list) and sites:
        site = sites[0]["site"]
        status, overview, _ = request("GET", f"/api/sites/{site}/overview", token=token, workspace_id=workspace_id)
        results.append(Result("site_overview", status == 200 and isinstance(overview, dict), f"{status} site={site}"))
    return results


def verify_api_key_surface() -> list[Result]:
    if not API_KEY:
        return [Result("api_key", False, "SMARTCRAWLER_API_KEY/API_KEY not set")]
    results: list[Result] = []
    status, sources, _ = request("GET", "/api/v2/sources", api_key=API_KEY)
    results.append(Result(
        "api_v2_sources", status == 200 and isinstance(sources, dict),
        f"{status}",
    ))
    status, sites, _ = request("GET", "/api/sites", api_key=API_KEY)
    results.append(Result(
        "api_key_workspace_sites", status == 200 and isinstance(sites, list),
        f"{status} count={len(sites) if isinstance(sites, list) else 'n/a'}",
    ))
    results.append(verify_mcp_tools())
    return results


def verify_mcp_tools() -> Result:
    init_body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "smart-crawler-deploy-verify", "version": "1.0"},
        },
    }
    status, body, headers = request(
        "POST", "/mcp/", api_key=API_KEY,
        extra_headers={"Accept": "application/json, text/event-stream"},
        body=init_body,
        timeout=15,
    )
    session_id = headers.get("mcp-session-id")
    if status != 200 or not session_id:
        return Result("mcp_initialize", False, f"{status} session={bool(session_id)} body={body}")
    request(
        "POST", "/mcp/", api_key=API_KEY,
        extra_headers={
            "Mcp-Session-Id": session_id,
            "Accept": "application/json, text/event-stream",
        },
        body={"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
        timeout=8,
    )
    data = json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}).encode()
    req = urllib.request.Request(
        BASE_URL + "/mcp/",
        data=data,
        headers={
            "Authorization": f"Bearer {API_KEY}",
            "X-API-Key": API_KEY,
            "Mcp-Session-Id": session_id,
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read().decode()
    except Exception as exc:
        return Result("mcp_tools", False, str(exc))
    payload = raw
    if "data:" in raw:
        payload = raw.split("data:", 1)[1].strip()
    try:
        parsed = json.loads(payload)
        names = [t["name"] for t in parsed.get("result", {}).get("tools", [])]
    except Exception:
        return Result("mcp_tools", False, raw[:300])
    expected = {"query_warehouse", "scrape_url", "crawl_site"}
    missing = sorted(expected.difference(names))
    return Result("mcp_tools", not missing, f"tools={len(names)} missing={missing}")


def main() -> int:
    results: list[Result] = [wait_health()]
    token = ""
    login_result, token = login()
    results.append(login_result)
    if token:
        results.extend(verify_admin_surface(token))
    results.extend(verify_api_key_surface())
    print(f"smart-crawler post-deploy verification: {BASE_URL}")
    for r in results:
        mark = "OK" if r.ok else "FAIL"
        print(f"[{mark}] {r.name}: {r.detail}")
    return 0 if all(r.ok for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
