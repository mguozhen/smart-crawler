"""LLM 统一接口 —— 封装 Anthropic SDK 调用 flatkey.ai 网关。

环境变量：
  OPENAI_API_KEY       flatkey.ai 的 sk- 密钥（兼容旧字段名）
  ANTHROPIC_API_KEY    优先生效；上面那个是 fallback
  LLM_BASE_URL         默认 https://api.flatkey.ai
  LLM_MODEL            默认 claude-haiku-4-5（最便宜）

替换原 openai.chat.completions.create() 模式。返回值仍是 .choices[0].message.content 兼容形态。
"""
from __future__ import annotations

import json
import os
from typing import Any

# 默认配置
DEFAULT_BASE = os.environ.get("LLM_BASE_URL", "https://api.flatkey.ai")
DEFAULT_MODEL = os.environ.get("LLM_MODEL", "claude-haiku-4-5")
DEFAULT_MAX_TOKENS = int(os.environ.get("LLM_MAX_TOKENS", "4096"))


def _get_api_key() -> str:
    key = (os.environ.get("ANTHROPIC_API_KEY")
           or os.environ.get("OPENAI_API_KEY"))
    if not key:
        raise RuntimeError("未配置 ANTHROPIC_API_KEY / OPENAI_API_KEY")
    return key


def _client():
    from anthropic import Anthropic
    return Anthropic(api_key=_get_api_key(), base_url=DEFAULT_BASE)


def _split_system(messages: list[dict]) -> tuple[str | None, list[dict]]:
    """Anthropic 把 system 单独传，不能在 messages 数组里。"""
    sys, others = None, []
    for m in messages:
        if m.get("role") == "system":
            sys = (sys + "\n\n" + m["content"]) if sys else m["content"]
        else:
            others.append({"role": m["role"], "content": m["content"]})
    return sys, others


class _Message:
    """模拟 OpenAI SDK 的 message 对象。"""
    def __init__(self, content: str):
        self.content = content


class _Choice:
    def __init__(self, content: str):
        self.message = _Message(content)


class _Response:
    """模拟 OpenAI SDK 的 response 对象，让旧代码不用改 resp.choices[0].message.content。"""
    def __init__(self, content: str):
        self.choices = [_Choice(content)]


def chat_completions_create(messages: list[dict],
                            model: str | None = None,
                            response_format: dict | None = None,
                            max_tokens: int | None = None,
                            **kwargs) -> _Response:
    """OpenAI-style chat.completions.create() 适配 Anthropic Messages API。

    - response_format={"type": "json_object"}: 把 system prompt 加 JSON 强约束
    - 返回的 _Response 对象暴露 .choices[0].message.content
    """
    sys_prompt, msgs = _split_system(messages)

    # JSON 模式：append JSON 强约束到 system
    if response_format and response_format.get("type") == "json_object":
        json_hint = ("\n\nIMPORTANT: Reply with ONLY valid JSON, no markdown, "
                     "no code fences, no commentary. Just the JSON object.")
        sys_prompt = (sys_prompt + json_hint) if sys_prompt else json_hint.strip()

    api_kwargs: dict[str, Any] = {
        "model": model or DEFAULT_MODEL,
        "max_tokens": max_tokens or DEFAULT_MAX_TOKENS,
        "messages": msgs,
    }
    if sys_prompt:
        api_kwargs["system"] = sys_prompt

    resp = _client().messages.create(**api_kwargs)
    # 提取文本内容（content 是 list of blocks）
    text = ""
    for blk in resp.content:
        if hasattr(blk, "text"):
            text += blk.text
    return _Response(text)


class OpenAICompat:
    """OpenAI SDK 形式的接口：cli = OpenAICompat(); cli.chat.completions.create(...)"""
    class _Chat:
        class _Completions:
            @staticmethod
            def create(**kw):
                return chat_completions_create(
                    messages=kw.get("messages", []),
                    model=kw.get("model"),
                    response_format=kw.get("response_format"),
                    max_tokens=kw.get("max_tokens"),
                )
        completions = _Completions()
    chat = _Chat()


def get_client():
    """返回 OpenAI 兼容的客户端对象（实际走 Anthropic API）。"""
    return OpenAICompat()
