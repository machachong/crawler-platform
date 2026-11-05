"""Idempotently add v2 tables and archive legacy task state without deleting data."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.database import init_db, SessionLocal, engine
from app.models import Task
from app.runs import ensure_run
from app.templates import seed_templates


def main():
    init_db()
    for index in Task.__table__.indexes:
        index.create(engine, checkfirst=True)
    total, cursor = 0, None
    while True:
        with SessionLocal() as db:
            query = db.query(Task).order_by(Task.id)
            if cursor:
                query = query.filter(Task.id > cursor)
            tasks = query.limit(100).all()
            if not tasks:
                seed_templates(db)
                break
            for task in tasks:
                ensure_run(db, task)
                total += 1
            cursor = tasks[-1].id
            db.commit()
    print(f"Migration complete: {total} tasks checked; existing tasks, results and public URLs preserved.")


if __name__ == "__main__":
    main()
