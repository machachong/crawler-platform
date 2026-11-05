"""Legacy import path. Fetching is done by the Scrapling engine."""

from __future__ import annotations

import logging

from ..crawler.engine import fetch_and_clean_sync

logger = logging.getLogger(__name__)


async def fetch_and_clean(url: str):
    """Fetch one URL with Scrapling Fetcher and return cleaned Markdown."""
    try:
        return fetch_and_clean_sync(url)
    except Exception as exc:
        logger.error("Error crawling %s: %s", url, exc)
        return None
