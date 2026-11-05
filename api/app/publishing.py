"""Render saved results as self-contained HTML. All source/model text is escaped."""

import json
import re
from datetime import datetime
from html import escape
from urllib.parse import urlsplit

from fastapi import HTTPException
from fastapi.responses import HTMLResponse

LABELS = {"title": "标题", "summary": "摘要", "key_points": "要点", "text": "正文",
          "author": "作者", "content": "内容", "name": "名称", "price": "价格"}
PAGE_HEADERS = {
    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; "
    "form-action 'none'; frame-ancestors 'self'; sandbox allow-popups allow-popups-to-escape-sandbox",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Robots-Tag": "noindex, nofollow",
    "Cache-Control": "no-store",
}
CSS = """
:root{color-scheme:light}*{box-sizing:border-box}body{margin:0;background:#f5f5f2;color:#202b32;
font:16px/1.8 system-ui,-apple-system,'Segoe UI','Microsoft YaHei',sans-serif}
main{max-width:1040px;margin:auto;padding:56px 32px 72px}header{margin-bottom:36px}
.eyebrow{font-size:12px;letter-spacing:.16em;color:#4f7166}h1{font-size:clamp(26px,4vw,40px);
line-height:1.35;margin:12px 0 16px;overflow-wrap:anywhere}.meta,footer{font-size:13px;color:#65716d}
article{background:white;padding:28px 32px;margin:0 0 24px;border:1px solid #e0e5e1;border-radius:8px}
.source{font-size:13px;margin-bottom:24px;overflow-wrap:anywhere}a{color:#176b55}
h2,h3,h4,h5,h6{line-height:1.5;margin:24px 0 12px}h2{font-size:24px}h3{font-size:21px}h4{font-size:18px}
p{margin:0 0 16px;white-space:pre-wrap;overflow-wrap:anywhere}li{overflow-wrap:anywhere}
pre{padding:16px;overflow:auto;background:#f1f4f2;border-radius:4px;font-size:13px;white-space:pre-wrap;
overflow-wrap:anywhere}code{font-family:ui-monospace,monospace}.table-wrap{overflow:auto}
table{width:100%;border-collapse:collapse;font-size:14px}th,td{text-align:left;vertical-align:top;
padding:12px;border:1px solid #dfe5e1;min-width:100px;max-width:400px;overflow-wrap:anywhere;white-space:pre-wrap}
th{background:#edf2ee}.field{margin-bottom:20px}.field-label{font-size:14px;color:#4f7166;font-weight:600}
footer{margin-top:32px}@media(max-width:600px){main{padding:28px 16px}article{padding:20px 16px}}
"""


def _inline(text: str) -> str:
    text = escape(text)
    text = re.sub(r"`([^`\n]+)`", r"<code>\1</code>", text)
    return re.sub(r"\*\*([^*\n]+)\*\*", r"<strong>\1</strong>", text)


def render_text(text: str) -> str:
    """Small Markdown subset; raw HTML, images and arbitrary links stay plain text."""
    parts, paragraph, code_lines = [], [], []
    in_code = False
    list_tag = None

    def flush():
        if paragraph:
            parts.append("<p>" + _inline("\n".join(paragraph)) + "</p>")
            paragraph.clear()

    def close_list():
        nonlocal list_tag
        if list_tag:
            parts.append(f"</{list_tag}>")
            list_tag = None

    for line in str(text).splitlines():
        if line.strip().startswith("```"):
            flush()
            close_list()
            if in_code:
                parts.append("<pre><code>" + escape("\n".join(code_lines)) + "</code></pre>")
                code_lines.clear()
            in_code = not in_code
        elif in_code:
            code_lines.append(line)
        elif not line.strip():
            flush()
            close_list()
        elif heading := re.match(r"^(#{1,6})\s+(.+)$", line):
            flush()
            close_list()
            level = min(6, len(heading[1]) + 1)
            parts.append(f"<h{level}>" + _inline(heading[2]) + f"</h{level}>")
        elif item := re.match(r"^\s*([-*+]|\d+[.)])\s+(.+)$", line):
            flush()
            tag = "ol" if item[1][0].isdigit() else "ul"
            if tag != list_tag:
                close_list()
                list_tag = tag
                parts.append(f"<{tag}>")
            parts.append("<li>" + _inline(item[2]) + "</li>")
        else:
            close_list()
            paragraph.append(line)
    flush()
    close_list()
    if code_lines:
        parts.append("<pre><code>" + escape("\n".join(code_lines)) + "</code></pre>")
    return "".join(parts)


def _scalar(value) -> str:
    if value is None:
        return "—"
    if isinstance(value, (dict, list, bool)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def render_data(value, depth=0) -> str:
    if depth >= 8:
        return "<pre>" + escape(json.dumps(value, ensure_ascii=False, indent=2)) + "</pre>"
    if isinstance(value, list):
        if value and all(isinstance(item, dict) for item in value):
            keys = list(dict.fromkeys(key for item in value for key in item))
            head = "".join("<th>" + escape(str(key)) + "</th>" for key in keys)
            rows = "".join("<tr>" + "".join("<td>" + escape(_scalar(item.get(key))) + "</td>"
                                          for key in keys) + "</tr>" for item in value)
            return '<div class="table-wrap"><table><thead><tr>' + head + "</tr></thead><tbody>" + rows + "</tbody></table></div>"
        return "<ul>" + "".join("<li>" + render_data(item, depth + 1) + "</li>" for item in value) + "</ul>"
    if isinstance(value, dict):
        if set(value) == {"text"}:
            return render_text(value["text"])
        return "".join('<section class="field"><div class="field-label">' + escape(LABELS.get(key, key))
                       + "</div>" + render_data(item, depth + 1) + "</section>" for key, item in value.items())
    return render_text(_scalar(value))


def page_title(task, title: str = "") -> str:
    chosen = title.strip() or str((task.config or {}).get("page_title") or "").strip() or (
        "采集报告 · " + (urlsplit(task.target_url).hostname or "网页内容")
    )
    return chosen[:120]


def render_page(task, results, title: str = "") -> tuple[str, str]:
    title = page_title(task, title)
    articles = []
    for row in results:
        data = row.extracted_data
        if row.status != "ok" or (isinstance(data, dict) and (
            data.get("skipped") is True or set(data) == {"error"}
        )):
            continue
        # An empty AI result means no matching output; do not expose the full
        # source body as a fallback. Only tasks without generated data use raw text.
        if data is None:
            data = row.raw_text
        if data is None or data == "" or data == {} or data == []:
            continue
        if isinstance(data, str) and not data.strip():
            continue
        if isinstance(data, dict) and set(data) == {"text"} and not str(data["text"] or "").strip():
            continue
        source = escape(row.source_url, quote=True)
        parsed = urlsplit(row.source_url)
        link = (f'<a href="{source}" target="_blank" rel="noopener noreferrer">{source}</a>'
                if parsed.scheme in {"http", "https"} and parsed.hostname else source)
        articles.append(f'<article><div class="source">来源：{link}</div>{render_data(data)}</article>')
    if not articles:
        raise HTTPException(422, "没有可发布的正文或提取结果，请先完成抓取")
    generated = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    html = ('<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            '<meta name="referrer" content="no-referrer"><meta name="robots" content="noindex,nofollow">'
            f'<title>{escape(title)}</title><style>{CSS}</style></head><body><main><header>'
            f'<div class="eyebrow">网页采集报告</div><h1>{escape(title)}</h1>'
            f'<div class="meta">{len(articles)} 个来源 · 生成于 {generated}</div></header>'
            + "".join(articles) + '<footer>内容来自以上来源页面，请核对原文。</footer></main></body></html>')
    return title, html


def html_response(html: str, filename: str | None = None) -> HTMLResponse:
    headers = dict(PAGE_HEADERS)
    if filename:
        headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    return HTMLResponse(html, headers=headers)
