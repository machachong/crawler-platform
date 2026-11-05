"""Persistent scheduler: python api/scripts/scheduler.py (run alongside Celery)."""

import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.database import init_db
from app.scheduling import dispatch_due

logging.basicConfig(level=logging.INFO)
init_db()
while True:
    try:
        result = dispatch_due()
        if result["dispatched"] or result["skipped"]:
            logging.info("Scheduler: %s", result)
    except Exception:
        logging.exception("Scheduler tick failed; retrying in 30 seconds")
    time.sleep(30)
