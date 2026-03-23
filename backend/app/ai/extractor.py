import httpx
import os
import json
from .prompts import SYSTEM_PROMPT, USER_EXTRACT_TEMPLATE
import logging

logger = logging.getLogger(__name__)

# 支持 OpenAI / DeepSeek 等兼容 OpenAI 格式的 API
API_KEY = os.getenv("OPENAI_API_KEY")
BASE_URL = os.getenv("OPENAI_API_BASE", "https://api.openai.com/v1")
MODEL_NAME = os.getenv("AI_MODEL", "gpt-4-turbo")

async def extract_with_ai(cleaned_content: str, user_prompt: str):
    """调用 LLM 根据用户指令从正文中提取结构化数据"""
    if not cleaned_content:
        return {"error": "No content to extract"}

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                f"{BASE_URL}/chat/completions",
                headers={"Authorization": f"Bearer {API_KEY}"},
                json={
                    "model": MODEL_NAME,
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": USER_EXTRACT_TEMPLATE.format(
                            user_prompt=user_prompt,
                            cleaned_content=cleaned_content
                        )}
                    ],
                    "temperature": 0.1,  # 降低随机性，保证结构稳定
                }
            )
            
            if response.status_code != 200:
                logger.error(f"AI API failed: {response.status_code} - {response.text}")
                return {"error": f"AI service error: {response.status_code}"}
            
            result = response.json()
            extracted_text = result["choices"][0]["message"]["content"]
            
            # 尝试解析为 JSON (如果用户要求了 JSON)
            try:
                # 去掉可能存在的 Markdown 代码块标记 ```json ... ```
                cleaned_json_text = extracted_text.strip().lstrip("```json").rstrip("```").strip()
                return json.loads(cleaned_json_text)
            except:
                # 无法解析为 JSON，则原样返回
                return extracted_text

    except Exception as e:
        logger.error(f"Error in AI extraction: {str(e)}")
        return {"error": str(e)}
