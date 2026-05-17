"""API 密钥 —— 供 AI Agent / 外部系统调用数据输出 API。

密钥形如 `sck_<43 位 url-safe 随机>`，明文仅在创建时返回一次，
库中只存 SHA-256 hash。
"""
from __future__ import annotations

import hashlib
import secrets

from .auth import SECRET

PREFIX = "sck_"


def generate() -> str:
    """生成一个新明文密钥。"""
    return PREFIX + secrets.token_urlsafe(32)


def hash_key(raw: str) -> str:
    """密钥 → 存储用 hash。"""
    return hashlib.sha256((SECRET + (raw or "")).encode()).hexdigest()


def short(raw: str) -> str:
    """展示用前缀（sck_ + 前 6 位）。"""
    return raw[: len(PREFIX) + 6]
