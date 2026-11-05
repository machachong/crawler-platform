"""Redis-backed URL dedupe, with an in-process fallback when Redis is down."""

from __future__ import annotations

import logging
import time

from ..config import REDIS_URL

logger = logging.getLogger(__name__)

_client = None
_down_until = 0.0


def cancel_key(task_id: str) -> str:
    return f"crawl:{task_id}:cancel"


def seen_key(task_id: str) -> str:
    return f"crawl:{task_id}:seen"


def get_redis():
    global _client, _down_until
    if _client is not None:
        return _client
    if time.time() < _down_until:
        raise RuntimeError("redis temporarily unavailable")
    import redis

    client = redis.Redis.from_url(
        REDIS_URL,
        decode_responses=True,
        socket_connect_timeout=1,
        socket_timeout=1,
    )
    try:
        client.ping()
    except Exception as exc:
        _down_until = time.time() + 15
        raise RuntimeError(str(exc)) from exc
    _client = client
    return _client


class UrlDedupe:
    """Claim-once set. Redis SADD is the cross-worker lock; memory covers local runs."""

    def __init__(self, task_id: str, client=None):
        self.task_id = str(task_id)
        self.key = seen_key(self.task_id)
        self.memory: set[str] = set()
        self.client = client
        self._use_redis = client is not None
        if client is None:
            try:
                self.client = get_redis()
                self._use_redis = True
            except Exception as exc:
                logger.info("URL dedupe using memory fallback: %s", exc)
                self.client = None
                self._use_redis = False

    def claim(self, url_key: str) -> bool:
        if self._use_redis and self.client is not None:
            try:
                added = int(self.client.sadd(self.key, url_key))
                self.client.expire(self.key, 60 * 60 * 24)
                return added == 1
            except Exception as exc:
                logger.warning("Redis dedupe failed, switching to memory: %s", exc)
                self._use_redis = False
        if url_key in self.memory:
            return False
        self.memory.add(url_key)
        return True

    def clear(self) -> None:
        self.memory.clear()
        if self.client is None:
            return
        try:
            self.client.delete(self.key)
        except Exception as exc:
            logger.warning("Failed to clear dedupe set: %s", exc)


def request_cancel(task_id: str) -> None:
    try:
        client = get_redis()
        client.set(cancel_key(task_id), "1", ex=6 * 60 * 60)
    except Exception as exc:
        logger.info("Cancel flag kept in the database only: %s", exc)


def clear_cancel(task_id: str) -> None:
    try:
        client = get_redis()
        client.delete(cancel_key(task_id))
    except Exception:
        return


def is_cancelled(task_id: str) -> bool:
    try:
        client = get_redis()
        return client.get(cancel_key(task_id)) == "1"
    except Exception:
        return False
