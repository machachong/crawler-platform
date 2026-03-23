from .celery_app import celery_app
from .crawler import fetch_and_clean
from ..ai.extractor import extract_with_ai
from ..database import SessionLocal
from ..models import Task, Result
import asyncio
import logging
from uuid import UUID

logger = logging.getLogger(__name__)

@celery_app.task(name="tasks.crawl_page")
def crawl_page_task(task_id: str, url: str, prompt: str = None):
    """Celery 任务：执行爬取、清洗、AI 提取及持久化"""
    db = SessionLocal()
    try:
        # 1. 更新任务状态为 running
        task = db.query(Task).filter(Task.id == task_id).first()
        if not task:
            return {"error": "Task not found"}
        task.status = "running"
        db.commit()

        # 2. 执行爬取与清洗
        loop = asyncio.get_event_loop()
        cleaned_content = loop.run_until_complete(fetch_and_clean(url))
        
        if not cleaned_content:
            task.status = "failed"
            db.commit()
            return {"status": "failed", "reason": "Crawl failed"}

        # 3. 如果有 Prompt，执行 AI 提取
        ai_result = None
        if prompt:
            task.status = "ai_processing"
            db.commit()
            ai_result = loop.run_until_complete(extract_with_ai(cleaned_content, prompt))

        # 4. 存储结果
        new_result = Result(
            task_id=UUID(task_id),
            source_url=url,
            raw_text=cleaned_content,
            extracted_data=ai_result
        )
        db.add(new_result)
        
        # 5. 完成任务
        task.status = "completed"
        db.commit()
        
        logger.info(f"Task {task_id} completed successfully.")
        return {"status": "success"}

    except Exception as e:
        logger.error(f"Error in task {task_id}: {str(e)}")
        if task:
            task.status = "failed"
            db.commit()
        return {"status": "error", "message": str(e)}
    finally:
        db.close()
