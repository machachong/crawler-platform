import json

import httpx
import pytest

from app.ai.extractor import (
    AIConfigError,
    chunk_text,
    extract_structured,
    keyword_hit,
    parse_model_output,
)


def test_chunk_text_overlap():
    text = "a" * 2500
    chunks = chunk_text(text, size=1000, overlap=100)
    assert len(chunks) == 3
    assert chunks[0][-100:] == chunks[1][:100]
    assert chunk_text("short", size=1000) == ["short"]


def test_parse_model_output_strips_fence():
    raw = '```json\n{"text": "x", "author": "y"}\n```'
    assert parse_model_output(raw) == {"text": "x", "author": "y"}
    assert parse_model_output("plain summary") == {"text": "plain summary"}


def test_missing_api_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(AIConfigError) as caught:
        extract_structured("hello world", "提取标题")
    assert "OPENAI_API_KEY" in str(caught.value)


def test_map_reduce_merges_long_page(monkeypatch):
    calls = []

    class DummyResponse:
        status_code = 200

        def __init__(self, content):
            self._content = content

        def json(self):
            return {
                "choices": [{"message": {"content": self._content}}],
                "usage": {"total_tokens": 5},
            }

        @property
        def text(self):
            return self._content

    def fake_post(self, url, headers=None, json=None):
        content = json["messages"][-1]["content"]
        calls.append(content)
        if "分段结果" in content:
            return DummyResponse('{"items": [1, 2]}')
        return DummyResponse(json_lib({"part": len(calls)}))

    json_lib = json.dumps
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr("app.ai.extractor.AI_CHUNK_CHARS", 1000)
    monkeypatch.setattr("app.ai.extractor.AI_CHUNK_OVERLAP", 0)
    monkeypatch.setattr(httpx.Client, "post", fake_post)

    result = extract_structured("Z" * 2500, "列出条目")
    assert result["token_usage"] == 20
    assert result["data"] == {"items": [1, 2]}
    assert len(calls) == 4
    assert "分段结果" in calls[-1]


def test_keyword_hit():
    assert keyword_hit("We sell fruit", []) is True
    assert keyword_hit("We sell fruit", ["Fruit"]) is True
    assert keyword_hit("Apple costs 3", ["fruit"]) is False
