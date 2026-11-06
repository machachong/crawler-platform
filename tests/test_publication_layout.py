"""Report layout must preserve content and keep all untrusted markup inert."""

from types import SimpleNamespace

import pytest
from lxml import html

from app.publishing import render_page, render_text


def report(data, source="https://example.com/original?from=report"):
    task = SimpleNamespace(config={}, target_url="https://example.com/")
    row = SimpleNamespace(status="ok", extracted_data=data, raw_text=None, source_url=source)
    _, content = render_page(task, [row], "每日资讯")
    return html.fromstring(content), content


def test_news_markdown_becomes_overview_and_linked_rank_list():
    doc, content = report({"text": """# 新闻提取结果

> **重要说明**：原页面只有标题，没有新闻摘要，请核对原文。

## 提取概览

| 项目 | 结果 |
| --- | --- |
| 新闻条目 | **2 条** |
| 可提取摘要 | **0 条** |

---

## 热搜新闻列表

| 序号 | 新闻标题 | 标记 |
| :---: | --- | :---: |
| 📌 | [公开资讯](https://example.com/top?a=1&b=2) | `置顶` |
| 1 | [行业动态](https://example.com/news) | 🔴 `新` |

## 缺失信息

- 新闻摘要正文
- 发布时间
"""})
    assert doc.xpath("//h1/text()") == ["每日资讯"]
    assert len(doc.xpath('//dl[@class="stat-grid"]//dt')) == 2
    assert doc.xpath('string(//blockquote)').strip().startswith("重要说明")
    assert len(doc.xpath('//ol[@class="rank-list"]/li')) == 2
    link = doc.xpath('//div[@class="rank-title"]/a')[0]
    assert link.text == "公开资讯"
    assert link.get("href") == "https://example.com/top?a=1&b=2"
    assert link.get("target") == "_blank"
    assert link.get("rel") == "noopener noreferrer"
    assert len(doc.xpath("//hr")) == 1
    assert "没有新闻摘要" in doc.text_content()
    assert "| 项目 |" not in doc.text_content()
    assert not doc.xpath("//script | //img | //link | //iframe")
    assert "UTC+08:00" in doc.text_content()
    assert "@media(max-width:760px)" in content
    assert "@media print" in content


def test_directory_points_to_unique_ids_across_sources_and_repeated_headings():
    task = SimpleNamespace(config={}, target_url="https://example.com/")
    rows = [SimpleNamespace(status="ok", extracted_data={"text": "# 总览\n\n## 详情\n\n内容"},
                            source_url=f"https://example.com/{index}") for index in range(2)]
    _, content = render_page(task, rows)
    doc = html.fromstring(content)
    ids = doc.xpath("//*[@id]/@id")
    assert len(ids) == len(set(ids))
    anchors = doc.xpath('//nav[@aria-label="本页目录"]//a/@href')
    assert len(anchors) == 6
    assert all(anchor[1:] in ids for anchor in anchors)
    assert "https://example.com/0" not in doc.xpath('string(//article[@id="source-1"])')
    assert doc.xpath('//article[@id="source-1"]//div[@class="source"]//a/@href') == ["https://example.com/0"]


def test_plain_text_without_headings_still_has_a_working_directory():
    doc, _ = report("普通正文")
    assert doc.xpath('//nav[@aria-label="本页目录"]//a/@href') == ["#source-1"]
    assert doc.xpath('string(//div[@class="prose"])').strip() == "普通正文"


@pytest.mark.parametrize("url", [
    "javascript:alert(1)", "data:text/html,hello", "vbscript:bad", "file:///etc/passwd",
    "//evil.example/a", "mailto:someone@example.com", "https://user:secret@example.com/a",
])
def test_markdown_rejects_non_web_or_credential_links(url):
    doc = html.fromstring('<div>' + render_text(f'[链接]({url})\n\n![图片]({url})') + '</div>')
    assert not doc.xpath(".//a | .//img | .//script")


def test_table_cells_quotes_and_code_cannot_inject_html():
    doc, _ = report({"text": """# <script>alert(1)</script>

| 序号 | 新闻标题 | 标记 |
| --- | --- | --- |
| 1 | [<img src=x onerror=alert(1)>](https://example.com/) | <svg/onload=alert(2)> |

```html
<script>alert(3)</script>
```

![pixel](https://evil.example/track)
"""})
    assert not doc.xpath("//script | //img | //svg | //*[@onerror] | //*[@onload]")
    assert "<script>alert(3)</script>" in doc.xpath("string(//pre/code)")
    assert "<img src=x onerror=alert(1)>" in doc.xpath('string(//div[@class="rank-title"])')


def test_generic_markdown_tables_nested_lists_and_escaped_pipes():
    doc = html.fromstring('<div>' + render_text("""| 类别 | 内容 | 数量 |
| --- | --- | ---: |
| 科技 | A\\|B | 2 |

1. 第一项
   - 子项目
2. 第二项
""") + '</div>')
    assert doc.xpath(".//table/tbody/tr/td/text()") == ["科技", "A|B", "2"]
    assert doc.xpath(".//ol/li/ul/li/text()") == ["子项目"]
    assert doc.xpath('.//div[@class="table-wrap"]/@tabindex') == ["0"]


def test_rich_json_records_use_cards_and_keep_nested_content():
    doc, _ = report([{"title": "第一则", "points": ["要点甲", "要点乙"]},
                     {"title": "第二则", "summary": "长文" * 100}])
    assert len(doc.xpath('//section[@class="record-card"]')) == 2
    assert "要点甲" in doc.text_content() and "要点乙" in doc.text_content()
    assert not doc.xpath("//table")


def test_malformed_source_url_does_not_break_publication():
    doc, _ = report("正文", source="https://[invalid")
    assert not doc.xpath('//div[@class="source"]//a')
    assert "正文" in doc.text_content()
