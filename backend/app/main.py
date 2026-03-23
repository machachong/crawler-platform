from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from typing import List
from . import schemas
import uuid
from datetime import datetime

app = FastAPI(title="Self-Service Crawler API")

# 开启 CORS 跨域支持
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 生产环境应限制为前端域名
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mock Database
tasks_db = {}

@app.post("/api/v1/tasks", response_model=schemas.TaskRead)
async def create_task(task: schemas.TaskCreate):
    task_id = uuid.uuid4()
    new_task = {
        "id": task_id,
        "target_url": str(task.target_url),
        "mode": task.mode,
        "prompt": task.prompt,
        "config": task.config,
        "status": "pending",
        "created_at": datetime.utcnow()
    }
    tasks_db[task_id] = new_task
    # TODO: Trigger Celery Task here
    return new_task

@app.get("/api/v1/tasks/{task_id}", response_model=schemas.TaskRead)
async def get_task(task_id: uuid.UUID):
    if task_id not in tasks_db:
        raise HTTPException(status_code=404, detail="Task not found")
    return tasks_db[task_id]

@app.get("/api/v1/health")
async def health_check():
    return {"status": "healthy", "timestamp": datetime.utcnow()}
