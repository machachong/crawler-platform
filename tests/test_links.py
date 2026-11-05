from scrapling import Selector

from app.crawler.links import (
    dedupe_key,
    discover_links,
    is_blocked_address,
    normalize_url,
    plan_next_links,
    same_site,
)


ROOT = "https://quotes.toscrape.com/"
PAGE = "https://quotes.toscrape.com/page/1/"


def test_selector_hrefs_stay_on_site():
    html = """
    <html><body>
      <a href="/author/Albert-Einstein">einstein</a>
      <a href="https://quotes.toscrape.com/tag/love/page/1/">love</a>
      <a href="https://example.com/ad">ad</a>
      <a href="mailto:editor@example.com">mail</a>
      <a href="javascript:void(0)">js</a>
      <a href="#top">frag</a>
      <a href="/static/logo.png">img</a>
      <a href="/author/Albert-Einstein?utm_source=rss">dup</a>
    </body></html>
    """
    hrefs = Selector(content=html).css("a::attr(href)").getall()
    links = discover_links(hrefs, PAGE, ROOT)
    assert links == [
        "https://quotes.toscrape.com/author/Albert-Einstein",
        "https://quotes.toscrape.com/tag/love/page/1/",
    ]


def test_plan_next_links_respects_depth_budget_and_seen_keys():
    hrefs = ["/one", "/two", "https://example.com/out", "/one"]
    assert plan_next_links(hrefs, ROOT, ROOT, depth=2, max_depth=2, scheduled_keys=set(), max_pages=10) == []

    full = {
        dedupe_key("https://quotes.toscrape.com/one"),
        dedupe_key("https://quotes.toscrape.com/two"),
    }
    assert plan_next_links(hrefs, ROOT, ROOT, depth=0, max_depth=2, scheduled_keys=full, max_pages=2) == []

    scheduled = {dedupe_key("https://quotes.toscrape.com/one")}
    chosen = plan_next_links(hrefs, ROOT, ROOT, depth=0, max_depth=1, scheduled_keys=scheduled, max_pages=2)
    assert chosen == ["https://quotes.toscrape.com/two"]

    fresh = plan_next_links(hrefs, ROOT, ROOT, depth=0, max_depth=1, scheduled_keys=set(), max_pages=10)
    assert fresh == [
        "https://quotes.toscrape.com/one",
        "https://quotes.toscrape.com/two",
    ]


def test_bfs_walk_stops_at_depth_and_page_cap():
    graph = {
        "https://example.com/": ["/a", "/b", "https://other.test/x"],
        "https://example.com/a": ["/a/1", "/b"],
        "https://example.com/b": ["/b/1"],
        "https://example.com/a/1": ["/a/1/deep"],
    }

    def children(url):
        return [normalize_url(href, url) for href in graph.get(url, [])]

    scheduled = {dedupe_key("https://example.com/")}
    order = ["https://example.com/"]
    frontier = [("https://example.com/", 0)]
    while frontier:
        current, depth = frontier.pop(0)
        nxt = plan_next_links(
            graph.get(current, []),
            current,
            "https://example.com/",
            depth,
            max_depth=1,
            scheduled_keys=scheduled,
            max_pages=4,
        )
        for link in nxt:
            scheduled.add(dedupe_key(link))
            order.append(link)
            frontier.append((link, depth + 1))
    assert order == [
        "https://example.com/",
        "https://example.com/a",
        "https://example.com/b",
    ]
    assert all(same_site(url, "https://example.com/") for url in order)
    assert children("https://example.com/")[2] is None or not same_site(
        children("https://example.com/")[2] or "", "https://example.com/"
    )


def test_blocked_metadata_address():
    assert normalize_url("http://169.254.169.254/latest/meta-data") is None
    assert is_blocked_address("169.254.169.254") is True
    assert is_blocked_address("quotes.toscrape.com") is False
