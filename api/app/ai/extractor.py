"""OpenAI-compatible extraction with map-reduce for long pages."""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

import httpx

from ..config import AI_CHUNK_CHARS, AI_CHUNK_OVERLAP, AI_MODEL, OPENAI_API_BASE, openai_api_key
from .prompts import DIRECT_TEMPLATE, MAP_TEMPLATE, REDUCE_TEMPLATE, SYSTEM_PROMPT

logger = logging.getLogger(__name__)

MISSING_KEY_MESSAGE = (
    "未配置 OPENAI_API_KEY。抓取与清洗已完成，正文已保存，可在结果页查看；"
    "配置密钥后重新运行即可进行 AI 提取。"
)


class AIConfigError(RuntimeError):
    """Raised when extraction is requested but no API key is configured."""


def chunk_text(text: str, size: int | None = None, overlap: int | None = None) -> list[str]:
    content = text or ""
    chunk_size = max(500, int(size or AI_CHUNK_CHARS))
    chunk_overlap = max(0, min(int(overlap if overlap is not None else AI_CHUNK_OVERLAP), chunk_size // 2))
    if len(content) <= chunk_size:
        return [content]
    chunks: list[str] = []
    start = 0
    while start < len(content):
        chunks.append(content[start : start + chunk_size])
        if start + chunk_size >= len(content):
            break
        start += chunk_size - chunk_overlap
    return chunks


def parse_model_output(text: str) -> Any:
    cleaned = (text or "").strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"(\{.*\}|\[.*\])", cleaned, re.S)
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                pass
    return {"text": (text or "").strip()}


def _chat(messages: list[dict[str, str]]) -> tuple[str, int]:
    key = openai_api_key()
    if not key:
        raise AIConfigError(MISSING_KEY_MESSAGE)
    base = os.getenv("OPENAI_API_BASE", OPENAI_API_BASE).rstrip("/")
    model = os.getenv("AI_MODEL", AI_MODEL)
    with httpx.Client(timeout=90.0) as client:
        response = client.post(
            f"{base}/chat/completions",
            headers={"Authorization": f"Bearer {key}"},
            json={"model": model, "temperature": 0.1, "messages": messages},
        )
    if response.status_code != 200:
        detail = response.text[:300].replace("\n", " ")
        raise RuntimeError(f"AI 服务返回 {response.status_code}: {detail}")
    payload = response.json()
    content = payload["choices"][0]["message"]["content"] or ""
    usage = payload.get("usage") or {}
    tokens = int(usage.get("total_tokens") or 0)
    return content, tokens


def extract_structured(cleaned_content: str, user_prompt: str) -> dict[str, Any]:
    """Return ``{"data": ..., "token_usage": int}``.

    A single short page is one chat completion. Longer pages are extracted per
    chunk and merged with a second completion.
    """
    content = (cleaned_content or "").strip()
    prompt = (user_prompt or "").strip()
    if not content:
        return {"data": {"text": "", "note": "页面没有可用正文"}, "token_usage": 0}
    if not openai_api_key():
        raise AIConfigError(MISSING_KEY_MESSAGE)

    chunks = chunk_text(content)
    if len(chunks) == 1:
        text, tokens = _chat(
            [
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": DIRECT_TEMPLATE.format(user_prompt=prompt, content=chunks[0]),
                },
            ]
        )
        return {"data": parse_model_output(text), "token_usage": tokens}

    partials: list[Any] = []
    total_tokens = 0
    for index, chunk in enumerate(chunks, start=1):
        text, tokens = _chat(
            [
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": MAP_TEMPLATE.format(
                        user_prompt=prompt,
                        index=index,
                        total=len(chunks),
                        content=chunk,
                    ),
                },
            ]
        )
        total_tokens += tokens
        partials.append(parse_model_output(text))

    merged, tokens = _chat(
        [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": REDUCE_TEMPLATE.format(
                    user_prompt=prompt,
                    partials=json.dumps(partials, ensure_ascii=False),
                ),
            },
        ]
    )
    total_tokens += tokens
    return {"data": parse_model_output(merged), "token_usage": total_tokens}


def keyword_hit(text: str, keywords: list[str]) -> bool:
    cleaned = [item.strip() for item in keywords or [] if item and str(item).strip()]
    if not cleaned:
        return True
    haystack = (text or "").lower()
    return any(item.lower() in haystack for item in cleaned)


# Backwards-compatible name used by the original scaffold.
async def extract_with_ai(cleaned_content: str, user_prompt: str):
    try:
        result = extract_structured(cleaned_content, user_prompt)
        return result["data"]
    except AIConfigError as exc:
        return {"error": str(exc)}
    except Exception as exc:
        logger.exception("AI extraction failed")
        return {"error": str(exc)}
