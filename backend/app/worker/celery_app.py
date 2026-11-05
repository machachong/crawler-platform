from celery import Celery

from ..config import REDIS_URL

celery_app = Celery("crawler_worker", broker=REDIS_URL, backend=REDIS_URL)
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="Asia/Shanghai",
    enable_utc=True,
    task_track_started=True,
    broker_connection_retry_on_startup=True,
    task_soft_time_limit=60 * 25,
    task_time_limit=60 * 30,
    worker_prefetch_multiplier=1,
)
