import httpx
from trafilatura import extract, fetch_url
import logging

logger = logging.getLogger(__name__)

async def fetch_and_clean(url: str):
    """抓取 URL 并使用 Trafilatura 清洗正文"""
    try:
        # 1. 抓取 HTML (使用 httpx 异步抓取)
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url, follow_redirects=True)
            if response.status_code != 200:
                logger.error(f"Failed to fetch {url}: {response.status_code}")
                return None
            
            # 2. 使用 Trafilatura 进行正文提取与清洗
            # include_links=True, include_images=False (根据需求可调)
            cleaned_text = extract(response.text, include_links=True, include_images=False)
            
            if not cleaned_text:
                # 备选方案：如果 Trafilatura 没提取出来，尝试直接取 body 文本（简单降噪）
                logger.warning(f"Trafilatura failed to extract from {url}, fallback needed.")
                return None
                
            return cleaned_text
            
    except Exception as e:
        logger.error(f"Error crawling {url}: {str(e)}")
        return None
