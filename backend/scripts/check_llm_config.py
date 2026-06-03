#!/usr/bin/env python3
"""Check optional LLM configuration without printing secrets."""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


def main() -> int:
    parser = argparse.ArgumentParser(description="Check smart-crawler LLM config")
    parser.add_argument("--live", action="store_true",
                        help="perform a tiny live request to the configured LLM gateway")
    args = parser.parse_args()

    has_key = bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("OPENAI_API_KEY"))
    if not has_key:
        print("LLM key: missing")
        return 2

    from app.llm import DEFAULT_BASE, DEFAULT_MODEL, _get_api_key
    _get_api_key()
    print(f"LLM key: configured; base={DEFAULT_BASE}; model={DEFAULT_MODEL}")

    if not args.live:
        return 0

    from app.llm import chat_completions_create
    try:
        resp = chat_completions_create(
            messages=[
                {"role": "system", "content": "Reply with exactly OK."},
                {"role": "user", "content": "health check"},
            ],
            max_tokens=8,
        )
    except Exception as exc:
        print(f"LLM live check failed: {type(exc).__name__}: {str(exc)[:300]}")
        return 1

    text = resp.choices[0].message.content.strip()
    if not text:
        print("LLM live check failed: empty response")
        return 1
    print(f"LLM live check: ok; response_len={len(text)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
