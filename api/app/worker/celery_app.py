from celery import Celery

from ..config import DISABLE_CELERY, REDIS_URL

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
if DISABLE_CELERY:
    celery_app.conf.task_always_eager = True
    celery_app.conf.task_eager_propagates = True
    celery_app.conf.broker_url = "memory://"
    celery_app.conf.result_backend = "cache+memory://"
