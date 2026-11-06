"""Safe Markdown with readable report tables, headings and external links."""

import re
from html import escape
from urllib.parse import urlsplit

from markdown_it import MarkdownIt
from markdown_it.token import Token


def web_url(value: str) -> bool:
    """Only explicit HTTP(S) links; no credentials, scripts or local resources."""
    try:
        parsed = urlsplit(value)
        return bool(
            parsed.scheme in {"http", "https"} and parsed.hostname
            and not parsed.username and not parsed.password
            and not re.search(r"[\x00-\x20\x7f]", value)
        )
    except ValueError:
        return False


def _link_open(renderer, tokens, index, options, env):
    tokens[index].attrSet("target", "_blank")
    tokens[index].attrSet("rel", "noopener noreferrer")
    return renderer.renderToken(tokens, index, options, env)


def _code_block(renderer, tokens, index, options, env):
    # Code is always inert text, including fenced blocks labelled html or svg.
    return "<pre><code>" + escape(tokens[index].content.rstrip("\n")) + "</code></pre>\n"


def _plain(token) -> str:
    return "".join(
        child.content if child.type in {"text", "code_inline"} else " "
        if child.type in {"softbreak", "hardbreak"} else ""
        for child in token.children or []
    ).strip()


def _table_rows(tokens):
    rows = []
    for token in tokens:
        if token.type == "tr_open":
            rows.append([])
        elif token.type == "inline" and rows:
            rows[-1].append(token)
    return rows


class ReportMarkdown:
    """One context per document, so generated section IDs never collide."""

    def __init__(self):
        self.headings: list[tuple[str, str, int]] = []
        self.characters = 0
        self.md = MarkdownIt("commonmark", {"html": False, "breaks": False})
        self.md.enable(["table", "strikethrough"]).disable("image")
        self.md.validateLink = web_url
        self.md.add_render_rule("link_open", _link_open)
        self.md.add_render_rule("fence", _code_block)
        self.md.add_render_rule("code_block", _code_block)

    def heading(self, label: str, level: int = 2) -> str:
        section_id = f"section-{len(self.headings) + 1}"
        self.headings.append((section_id, label, level))
        return section_id

    def _inline(self, token) -> str:
        return self.md.renderer.renderInline(token.children or [], self.md.options, {})

    def _feature_table(self, tokens) -> str | None:
        rows = _table_rows(tokens)
        if len(rows) < 2:
            return None
        labels = [_plain(cell).lower() for cell in rows[0]]
        data = rows[1:]
        if len(labels) in {2, 3} and labels[0] in {"序号", "排名", "排行", "rank", "no.", "#"} and any(
            word in labels[1] for word in ("标题", "新闻", "名称", "title", "headline", "name")
        ):
            items = []
            for cells in data:
                number = _plain(cells[0])
                pinned = "📌" in number or "置顶" in number
                number_class = "rank-number pinned" if pinned else "rank-number"
                if number in {"1", "2", "3"}:
                    number_class += " leading"
                badge_text = _plain(cells[2]) if len(cells) > 2 else ""
                badge = (f'<span class="rank-badge">{self._inline(cells[2])}</span>'
                         if badge_text not in {"", "—", "-"} and not (pinned and badge_text == "置顶") else "")
                items.append(
                    f'<li class="rank-item"><span class="{number_class}">'
                    + ("置顶" if pinned else escape(number)) + '</span><div class="rank-title">'
                    + self._inline(cells[1]) + "</div>" + badge + "</li>"
                )
            return '<ol class="rank-list" aria-label="' + escape(_plain(rows[0][1]), quote=True) + '">' + "".join(items) + "</ol>"
        if len(labels) == 2 and len(data) <= 8 and labels[0] in {"项目", "指标", "metric", "item"} and labels[1] in {
            "结果", "数值", "数量", "value", "result"
        }:
            cards = []
            for cells in data:
                detail = " stat-detail" if len(_plain(cells[1])) > 12 else ""
                cards.append('<div class="stat-card"><dt>' + self._inline(cells[0])
                             + f'</dt><dd class="stat-value{detail}">' + self._inline(cells[1]) + "</dd></div>")
            return '<dl class="stat-grid">' + "".join(cards) + "</dl>"
        return None

    def render(self, text: str) -> str:
        tokens = self.md.parse(str(text))
        self.characters += sum(len(_plain(token)) for token in tokens if token.type == "inline")
        result = []
        index = 0
        while index < len(tokens):
            token = tokens[index]
            if token.type == "heading_open":
                level = min(6, int(token.tag[1:]) + 1)  # The page owns the only h1.
                token.tag = f"h{level}"
                tokens[index + 2].tag = token.tag
                token.attrSet("id", self.heading(_plain(tokens[index + 1]), level))
            if token.type == "table_open":
                end = next(i for i in range(index + 1, len(tokens)) if tokens[i].type == "table_close")
                featured = self._feature_table(tokens[index:end + 1])
                if featured:
                    block = Token("html_block", "", 0)
                    block.content = featured
                    result.append(block)
                    index = end + 1
                    continue
                block = Token("html_block", "", 0)
                block.content = '<div class="table-wrap" role="region" tabindex="0" aria-label="数据表，可横向滚动">'
                result.append(block)
            result.append(token)
            if token.type == "table_close":
                block = Token("html_block", "", 0)
                block.content = "</div>"
                result.append(block)
            index += 1
        return self.md.renderer.render(result, self.md.options, {})
