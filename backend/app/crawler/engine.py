"""Crawl executor.

HTTP pages go through Scrapling ``FetcherSession`` inside a ``Spider``.
Same-domain BFS, depth and page budget live in ``plan_next_links``.
``robots_txt_obey`` is Scrapling's own robots.txt check.
Optional ``engine=stealth`` switches the spider session to ``AsyncStealthySession``
(Chromium is not installed in the default image).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from urllib.parse import urlparse

from ..config import CRAWLER_USER_AGENT
from .dedupe import UrlDedupe
from .links import dedupe_key, normalize_url, plan_next_links

logger = logging.getLogger(__name__)

STEALTH_HELP = (
    "隐匿浏览器未安装或启动失败。请在运行环境执行 `scrapling install` 下载 Chromium 后重试。"
    "默认镜像只启用 HTTP Fetcher。原始错误: "
)


@dataclass
class CrawlJob:
    root_url: str
    mode: str
    max_depth: int
    max_pages: int
    concurrency: int
    respect_robots: bool
    engine: str = "http"
    download_delay: float = 0.2


@dataclass
class CrawlSummary:
    pages_ok: int = 0
    pages_failed: int = 0
    robots_disallowed: int = 0
    requests_count: int = 0
    failures: list[dict] = field(default_factory=list)


def build_http_session():
    from scrapling.fetchers import FetcherSession

    return FetcherSession(
        impersonate=None,
        stealthy_headers=False,
        headers={
            "User-Agent": CRAWLER_USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        },
        timeout=30,
        retries=2,
        retry_delay=1,
        follow_redirects="safe",
    )


def page_markdown(page) -> str:
    text = ""
    if hasattr(page, "markdown"):
        try:
            text = page.markdown(main_content_only=True) or ""
        except Exception as exc:
            logger.warning("page.markdown failed, falling back to text: %s", exc)
            text = ""
    if not str(text).strip() and hasattr(page, "get_all_text"):
        try:
            text = page.get_all_text(
                separator="\n",
                strip=True,
                ignore_tags=("script", "style", "noscript", "nav", "footer", "svg"),
            )
        except Exception as exc:
            logger.warning("get_all_text failed: %s", exc)
            text = ""
    return _clean_text(str(text or ""))


def _clean_text(text: str) -> str:
    lines = [line.rstrip() for line in text.replace("\r\n", "\n").split("\n")]
    compact: list[str] = []
    blank = 0
    for line in lines:
        if not line.strip():
            blank += 1
            if blank <= 2:
                compact.append("")
            continue
        blank = 0
        compact.append(line.strip())
    cleaned = "\n".join(compact).strip()
    if len(cleaned) > 200_000:
        cleaned = cleaned[:200_000] + "\n\n[正文已截断]"
    return cleaned


def hrefs_of(page) -> list[str]:
    try:
        values = page.css("a::attr(href)").getall()
    except Exception:
        values = []
    return [str(item) for item in values or [] if item]


def fetch_and_clean_sync(url: str) -> str | None:
    """Single-page helper used by smoke checks and the legacy crawler entrypoint."""
    from scrapling.fetchers import Fetcher

    normalized = normalize_url(url)
    if not normalized:
        return None
    page = Fetcher.get(
        normalized,
        impersonate=None,
        stealthy_headers=False,
        headers={"User-Agent": CRAWLER_USER_AGENT},
        timeout=30,
        retries=2,
        follow_redirects="safe",
    )
    status = int(getattr(page, "status", 0) or 0)
    if status >= 400:
        return None
    text = page_markdown(page)
    return text or None


def friendly_crawl_error(exc: Exception) -> str:
    text = str(exc)
    lowered = text.lower()
    if any(token in lowered for token in ("playwright", "patchright", "executable doesn't exist", "browser")):
        return STEALTH_HELP + text[:300]
    return "抓取失败: " + text[:500]


class TaskCrawlSpider:
    """Constructed lazily so unit tests can import link policy without Scrapling spiders."""

    @staticmethod
    def build(job: CrawlJob, dedupe: UrlDedupe, should_stop):
        from scrapling.spiders import Request, Response, Spider

        class _Spider(Spider):
            name = "task_crawl"
            logging_level = logging.INFO

            def __init__(self):
                self.job = job
                self.dedupe = dedupe
                self.should_stop = should_stop
                self.start_urls = [job.root_url]
                parsed_root = urlparse(job.root_url)
                # Scrapling compares request.netloc (host plus non-default port).
                self.allowed_domains = {parsed_root.netloc} if parsed_root.netloc else set()
                self.concurrent_requests = max(1, job.concurrency)
                self.concurrent_requests_per_domain = max(1, job.concurrency)
                self.robots_txt_obey = bool(job.respect_robots)
                self.download_delay = float(job.download_delay)
                self.max_blocked_retries = 1
                self.failures: list[dict] = []
                self.final_stats = None
                self._scheduled = {dedupe_key(job.root_url)}
                super().__init__()

            def configure_sessions(self, manager):
                if self.job.engine == "stealth":
                    from scrapling.fetchers import AsyncStealthySession

                    manager.add("default", AsyncStealthySession(headless=True), default=True)
                    return
                manager.add("default", build_http_session(), default=True)

            async def on_error(self, request: Request, error: Exception) -> None:
                self.failures.append({"url": str(request.url), "error": str(error)})

            async def on_close(self) -> None:
                try:
                    self.final_stats = self.stats
                except Exception:
                    self.final_stats = None

            async def is_blocked(self, response: Response) -> bool:
                # 403/404 should be recorded, not retried until dropped.
                return int(response.status or 0) in {429, 503}

            def _pause(self) -> None:
                try:
                    self.pause()
                except Exception:
                    return

            async def parse(self, response: Response):
                depth = int((response.meta or {}).get("depth") or 0)
                status = int(response.status or 0)
                url = str(response.url)
                if status >= 400:
                    yield {
                        "url": url,
                        "depth": depth,
                        "status_code": status,
                        "markdown": "",
                        "error": f"HTTP {status}",
                    }
                else:
                    yield {
                        "url": url,
                        "depth": depth,
                        "status_code": status,
                        "markdown": page_markdown(response),
                        "error": None,
                    }
                if self.should_stop() or status >= 400:
                    if self.should_stop():
                        self._pause()
                    return
                links = plan_next_links(
                    hrefs_of(response),
                    url,
                    self.job.root_url,
                    depth,
                    self.job.max_depth,
                    self._scheduled,
                    self.job.max_pages,
                )
                for link in links:
                    if self.should_stop():
                        self._pause()
                        return
                    key = dedupe_key(link)
                    if not self.dedupe.claim(key):
                        continue
                    self._scheduled.add(key)
                    yield response.follow(
                        link,
                        callback=self.parse,
                        priority=1000 - depth,
                        meta={"depth": depth + 1},
                    )

        return _Spider()


async def run_crawl(job: CrawlJob, on_item, should_stop, dedupe: UrlDedupe) -> CrawlSummary:
    summary = CrawlSummary()
    dedupe.clear()
    dedupe.claim(dedupe_key(job.root_url))
    spider = TaskCrawlSpider.build(job, dedupe, should_stop)
    async for item in spider.stream():
        error = item.get("error")
        if error:
            summary.pages_failed += 1
        else:
            summary.pages_ok += 1
        on_item(item)
        if should_stop():
            try:
                spider.pause()
            except Exception:
                pass
    for failure in spider.failures:
        summary.pages_failed += 1
        summary.failures.append(failure)
        on_item(
            {
                "url": failure.get("url") or job.root_url,
                "depth": 0,
                "status_code": 0,
                "markdown": "",
                "error": failure.get("error") or "抓取失败",
            }
        )
    stats = spider.final_stats
    if stats is not None:
        summary.robots_disallowed = int(getattr(stats, "robots_disallowed_count", 0) or 0)
        summary.requests_count = int(getattr(stats, "requests_count", 0) or 0)
    return summary
