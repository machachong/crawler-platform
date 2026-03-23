import pytest
from fastapi.testclient import TestClient
import sys
import os

# 将 backend 路径加入 sys.path
sys.path.append(os.path.join(os.path.dirname(__file__), "..", "backend"))
from app.main import app

client = TestClient(app)

def test_create_task_flow():
    """测试创建任务 API 联调流程"""
    payload = {
        "target_url": "https://www.feishu.cn",
        "mode": "single",
        "prompt": "提取页面中的核心功能点，返回 JSON 数组格式。",
        "config": {}
    }
    
    # 1. 发送创建任务请求
    response = client.post("/api/v1/tasks", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert "id" in data
    assert data["status"] == "pending"
    task_id = data["id"]
    
    # 2. 查询任务状态
    get_response = client.get(f"/api/v1/tasks/{task_id}")
    assert get_response.status_code == 200
    get_data = get_response.json()
    assert get_data["id"] == task_id
    assert get_data["target_url"] == "https://www.feishu.cn/"

def test_health_check():
    """测试系统健康状态"""
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json()["status"] == "healthy"

if __name__ == "__main__":
    pytest.main([__file__])
