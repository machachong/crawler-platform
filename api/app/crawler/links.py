"""Same-domain link discovery and BFS follow policy.

Scrapling parses anchors (`page.css("a::attr(href)")`). This module decides
which of those hrefs are eligible for the next BFS hop.
"""

from __future__ import annotations

import ipaddress
from urllib.parse import parse_qsl, urldefrag, urlencode, urljoin, urlparse, urlunparse

SKIP_PREFIXES = ("mailto:", "javascript:", "tel:", "data:", "sms:", "blob:")
SKIP_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".webp",
    ".svg",
    ".ico",
    ".pdf",
    ".zip",
    ".rar",
    ".7z",
    ".css",
    ".js",
    ".mjs",
    ".mp4",
    ".mp3",
    ".woff",
    ".woff2",
    ".ttf",
    ".gz",
    ".tar",
}
TRACKING_PARAMS = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "spm",
    "fbclid",
    "gclid",
}


def _as_text(value) -> str:
    if value is None:
        return ""
    return str(value).strip()


def site_key(url: str) -> str:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if not host:
        return ""
    port = parsed.port
    if port and not (
        (parsed.scheme == "http" and port == 80) or (parsed.scheme == "https" and port == 443)
    ):
        return f"{host}:{port}"
    return host


def is_blocked_address(hostname: str | None) -> bool:
    """Reject link-local and unspecified targets (for example cloud metadata IPs)."""
    if not hostname:
        return True
    try:
        ip = ipaddress.ip_address(hostname)
    except ValueError:
        return False
    return bool(
        ip.is_link_local
        or ip.is_multicast
        or ip.is_unspecified
        or ip.is_reserved
    )


def normalize_url(raw: str, base: str | None = None) -> str | None:
    text = _as_text(raw)
    if not text:
        return None
    lowered = text.lower()
    if lowered.startswith("#") or lowered.startswith(SKIP_PREFIXES):
        return None
    absolute = urljoin(base or "", text)
    absolute, _fragment = urldefrag(absolute)
    parsed = urlparse(absolute)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return None
    if is_blocked_address(parsed.hostname):
        return None
    path = parsed.path or "/"
    if any(path.lower().endswith(ext) for ext in SKIP_EXTENSIONS):
        return None
    pairs = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key.lower() not in TRACKING_PARAMS
    ]
    query = urlencode(pairs)
    netloc = site_key(absolute)
    return urlunparse((parsed.scheme.lower(), netloc, path, "", query, ""))


def dedupe_key(url: str) -> str:
    parsed = urlparse(url)
    path = parsed.path or "/"
    if path != "/" and path.endswith("/"):
        path = path[:-1]
    return urlunparse((parsed.scheme.lower(), site_key(url), path, "", parsed.query, ""))


def same_site(url: str, root_url: str) -> bool:
    return site_key(url) == site_key(root_url) and bool(site_key(url))


def discover_links(hrefs: list, page_url: str, root_url: str) -> list[str]:
    """Return in-order, same-site http(s) links discovered on a page."""
    found: list[str] = []
    seen: set[str] = set()
    for href in hrefs or []:
        normalized = normalize_url(_as_text(href), page_url)
        if not normalized or not same_site(normalized, root_url):
            continue
        key = dedupe_key(normalized)
        if key in seen:
            continue
        seen.add(key)
        found.append(normalized)
    return found


def plan_next_links(
    hrefs: list,
    page_url: str,
    root_url: str,
    depth: int,
    max_depth: int,
    scheduled_keys: set[str],
    max_pages: int,
) -> list[str]:
    """Choose the next BFS hop without exceeding depth or the page budget."""
    if depth >= max_depth or len(scheduled_keys) >= max_pages:
        return []
    chosen: list[str] = []
    seen = set(scheduled_keys)
    for link in discover_links(hrefs, page_url, root_url):
        if len(seen) >= max_pages:
            break
        key = dedupe_key(link)
        if key in seen:
            continue
        seen.add(key)
        chosen.append(link)
    return chosen
