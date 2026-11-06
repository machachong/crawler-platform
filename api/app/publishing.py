"""Render saved results as self-contained HTML. All source/model text is escaped."""

import json
import math
from datetime import datetime, timedelta, timezone
from html import escape
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import HTTPException
from fastapi.responses import HTMLResponse

from .publication_markdown import ReportMarkdown, web_url

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
CSS = Path(__file__).with_name("publication.css").read_text(encoding="utf-8")


def render_text(text: str) -> str:
    """Markdown is parsed as content, never as executable HTML."""
    return ReportMarkdown().render(text)


def _scalar(value) -> str:
    if value is None:
        return "—"
    if isinstance(value, (dict, list, bool)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def render_data(value, depth=0, context=None) -> str:
    context = context if context is not None else ReportMarkdown()
    if depth >= 8:
        return "<pre>" + escape(json.dumps(value, ensure_ascii=False, indent=2)) + "</pre>"
    if isinstance(value, list):
        if value and all(isinstance(item, dict) for item in value):
            if any(isinstance(cell, (dict, list)) or len(str(cell)) > 160
                   for item in value for cell in item.values()):
                return '<div class="record-grid">' + "".join(
                    '<section class="record-card">' + render_data(item, depth + 1, context) + "</section>"
                    for item in value
                ) + "</div>"
            keys = list(dict.fromkeys(key for item in value for key in item))
            head = "".join("<th>" + escape(LABELS.get(key, str(key))) + "</th>" for key in keys)
            rows = "".join("<tr>" + "".join("<td>" + escape(_scalar(item.get(key))) + "</td>"
                                          for key in keys) + "</tr>" for item in value)
            context.characters += sum(len(_scalar(cell)) for item in value for cell in item.values())
            return '<div class="table-wrap" role="region" tabindex="0" aria-label="数据表，可横向滚动"><table><thead><tr>' + head + "</tr></thead><tbody>" + rows + "</tbody></table></div>"
        return "<ul>" + "".join("<li>" + render_data(item, depth + 1, context) + "</li>" for item in value) + "</ul>"
    if isinstance(value, dict):
        if set(value) == {"text"}:
            return context.render(_scalar(value["text"]))
        fields = []
        for key, item in value.items():
            label = LABELS.get(key, str(key))
            if depth == 0:
                section_id = context.heading(label)
                heading = f'<h2 class="field-label" id="{section_id}">{escape(label)}</h2>'
            else:
                heading = '<div class="field-label">' + escape(label) + "</div>"
            fields.append('<section class="field">' + heading + render_data(item, depth + 1, context) + "</section>")
        return "".join(fields)
    return context.render(_scalar(value))


def page_title(task, title: str = "") -> str:
    chosen = title.strip() or str((task.config or {}).get("page_title") or "").strip() or (
        "采集报告 · " + (urlsplit(task.target_url).hostname or "网页内容")
    )
    return chosen[:120]


def render_page(task, results, title: str = "") -> tuple[str, str]:
    title = page_title(task, title)
    articles = []
    sources = set()
    context = ReportMarkdown()
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
        safe_source = web_url(row.source_url)
        domain = urlsplit(row.source_url).hostname if safe_source else "来源页面"
        link = (f'<a href="{source}" target="_blank" rel="noopener noreferrer">{escape(domain)}</a>'
                if safe_source else escape(row.source_url))
        sources.add(row.source_url)
        article_id = f"source-{len(articles) + 1}"
        context.headings.append((article_id, domain, 2))
        articles.append(
            f'<article class="report-article" id="{article_id}"><div class="source">'
            '<div class="source-origin"><span class="source-dot" aria-hidden="true"></span>'
            f'原文来源 · {link}</div><span class="source-index">SOURCE {len(articles) + 1:02d}</span>'
            '</div><div class="prose">' + render_data(data, context=context) + "</div></article>"
        )
    if not articles:
        raise HTTPException(422, "没有可发布的正文或提取结果，请先完成抓取")
    generated = datetime.now(timezone(timedelta(hours=8)))
    minutes = max(1, math.ceil(context.characters / 500))
    sections = [(key, label, level) for key, label, level in context.headings
                if level <= 3 and (len(articles) > 1 or not key.startswith("source-"))]
    if not sections:
        sections = [("source-1", "正文内容", 2)]
    toc_items = "".join(
        f'<li class="{"toc-first" if index == 0 else "toc-child" if level > 2 else ""}">'
        f'<a href="#{key}">{escape(label)}</a></li>'
        for index, (key, label, level) in enumerate(sections)
    )
    toc = '<ul class="toc-list">' + toc_items + "</ul>"
    html = ('<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            '<meta name="referrer" content="no-referrer"><meta name="robots" content="noindex,nofollow">'
            f'<title>{escape(title)}</title><style>{CSS}</style></head><body>'
            '<a class="skip-link" href="#reading">跳到正文</a><div class="shell">'
            '<div class="masthead"><div class="brand"><span class="brand-mark" aria-hidden="true">阅</span>'
            '采集简报</div><span class="masthead-note">公开阅读 · CONTENT BRIEF</span></div>'
            '<header class="hero"><div><div class="eyebrow">网页采集报告 / THE READING EDITION</div>'
            f'<h1>{escape(title)}</h1><p class="hero-description">信息有出处，阅读有条理。以下内容整理自采集到的来源页面。</p>'
            f'<div class="hero-meta"><span>{len(sources)} 个来源</span><span>约 {minutes} 分钟阅读</span>'
            f'<span>生成于 <time datetime="{generated.isoformat(timespec="minutes")}">'
            + generated.strftime("%Y.%m.%d %H:%M") + ' (UTC+08:00)</time></span></div></div>'
            '<div class="edition" aria-hidden="true"><div class="edition-label">READING NOTES</div>'
            f'<div class="edition-date">{generated:%m / %d}</div><div class="edition-year">{generated:%Y}</div></div></header>'
            '<div class="reading-layout"><aside class="sidebar"><div class="toc-label">ON THIS PAGE</div>'
            '<div class="toc-title">本页目录</div><nav aria-label="本页目录">' + toc + '</nav>'
            '<p class="sidebar-note">保留信息的原貌，<br>也让它更好读。</p></aside><main class="document" id="reading">'
            '<details class="mobile-toc"><summary>本页目录 · 展开查看</summary><nav aria-label="移动端目录">' + toc + '</nav></details>'
            + "".join(articles) + '<div class="document-end" aria-label="正文结束">END OF BRIEF</div></main></div>'
            '<footer class="footer"><p>内容来自以上来源页面，请核对原文。</p>'
            '<p>采集简报 · 网页内容整理与分享 <a href="#reading">回到正文 ↑</a></p></footer></div></body></html>')
    return title, html


def html_response(html: str, filename: str | None = None) -> HTMLResponse:
    headers = dict(PAGE_HEADERS)
    if filename:
        headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    return HTMLResponse(html, headers=headers)
