from sqlalchemy import Column, String, Text, Enum, DateTime, ForeignKey, BigInteger
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.ext.declarative import declarative_base
import uuid
import datetime

Base = declarative_base()

class Task(Base):
    __tablename__ = "tasks"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    target_url = Column(String, nullable=False)
    mode = Column(Enum("single", "site", name="task_mode"), default="single")
    prompt = Column(Text)
    status = Column(Enum("pending", "running", "completed", "failed", name="task_status"), default="pending")
    config = Column(JSONB, default={})
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)

class Result(Base):
    __tablename__ = "results"
    
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    task_id = Column(UUID(as_uuid=True), ForeignKey("tasks.id"), nullable=False)
    source_url = Column(String, nullable=False)
    raw_text = Column(Text)
    extracted_data = Column(JSONB)
    token_usage = Column(BigInteger, default=0)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
