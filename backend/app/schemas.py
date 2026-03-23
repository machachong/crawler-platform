from pydantic import BaseModel, HttpUrl
from typing import Optional, List, Dict, Any
from uuid import UUID
from datetime import datetime

class TaskBase(BaseModel):
    target_url: HttpUrl
    mode: str = "single"
    prompt: Optional[str] = None
    config: Optional[Dict[str, Any]] = {}

class TaskCreate(TaskBase):
    pass

class TaskRead(TaskBase):
    id: UUID
    status: str
    created_at: datetime

    class Config:
        from_attributes = True

class ResultRead(BaseModel):
    id: int
    task_id: UUID
    source_url: str
    extracted_data: Optional[Dict[str, Any]]
    
    class Config:
        from_attributes = True
