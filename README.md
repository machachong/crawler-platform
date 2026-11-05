# 自助爬虫平台

输入 URL 和自定义 Prompt，平台会抓取页面、清洗正文，再调用兼容 OpenAI 的接口抽出结构化结果。全站模式在同域名内做广度优先，并遵守深度、页数和 robots.txt 限制。

演示站点使用 [quotes.toscrape.com](https://quotes.toscrape.com/)。请只抓取你有权访问的页面。

## 架构

```
浏览器 (Vue3 + Ant Design Vue)
        │  /api/v1/tasks
        ▼
FastAPI  ──写入──►  PostgreSQL (tasks, crawl_results)
        │
        │  Celery
        ▼
Redis 队列 / URL 去重
        │
        ▼
Worker
  1. crawling    Scrapling Spider + FetcherSession（HTTP）
  2. cleaning    Response.markdown(main_content_only=True)
  3. ai_processing  兼容 OpenAI 的 chat/completions，长文 map-reduce
        │
        ▼
completed / failed / stopped
```

Scrapling 的接入点：

- 默认引擎是 `Fetcher` / `FetcherSession`（`curl_cffi`），不下载浏览器。
- 全站抓取使用 `scrapling.spiders.Spider`：`concurrent_requests`、`robots_txt_obey`、`allowed_domains`。
- 同域链接来自 `response.css("a::attr(href)")`，深度和页数上限在 `app/crawler/links.py`。
- 已访问 URL 用 Redis `SADD` 去重；Redis 不可用时退回进程内集合。
- 清洗文本调用 `response.markdown()`，失败时退回 `get_all_text()`。
- 任务里把引擎选成 `stealth` 时，会话换成 `AsyncStealthySession`。默认镜像不含 Chromium。

状态：`pending → crawling → cleaning → ai_processing → completed | failed`。用户停止后进入 `stopped`，已经入库的页面会保留。

没有 `OPENAI_API_KEY` 时，抓取和清洗仍会完成，任务变为 `failed`，错误信息会说明正文已保存。不填 Prompt 时跳过 AI，状态为 `completed`。

## Docker Compose

```bash
cp .env.example .env
# 编辑 .env，填入 OPENAI_API_KEY；留空则只做抓取和清洗

docker compose up --build
```

- 前端：http://localhost:3000
- API 文档：http://localhost:8000/docs
- 健康检查：http://localhost:8000/api/v1/health

Postgres 数据在卷 `postgres_data`。首次启动会 `create_all` 建表。改了表结构时执行 `docker compose down -v` 后重新启动。

### 冒烟：quotes.toscrape.com

服务起来之后：

```bash
docker compose exec backend python scripts/smoke_test.py
```

脚本会创建单页任务并轮询。配了密钥时期望 `completed`；没配密钥时期望 `failed`，且结果里仍有清洗后的正文，错误含 `OPENAI_API_KEY`。

也可以用 curl：

```bash
curl -s http://localhost:8000/api/v1/health

curl -s -X POST http://localhost:8000/api/v1/tasks \
  -H 'Content-Type: application/json' \
  -d '{
    "target_url": "https://quotes.toscrape.com/",
    "mode": "single",
    "prompt": "提取所有名言，返回 JSON 数组，每项包含 text 和 author。",
    "config": {"respect_robots": true, "engine": "http"}
  }'
```

把返回的 `id` 填进：

```bash
curl -s http://localhost:8000/api/v1/tasks/<id>
```

## 环境变量

| 变量 | 作用 | 默认 |
| --- | --- | --- |
| `DATABASE_URL` | SQLAlchemy 连接串。`postgresql://` 会自动改用已安装的 psycopg2 驱动 | `postgresql://user:pass@db:5432/crawler_db` |
| `REDIS_URL` | Celery 与 URL 去重 | `redis://redis:6379/0` |
| `OPENAI_API_KEY` | 提取用的密钥；为空则 AI 阶段失败并保留正文 | 空 |
| `OPENAI_API_BASE` | 兼容 OpenAI 的根路径，不含末尾斜杠以外的路径前缀 | `https://api.openai.com/v1` |
| `AI_MODEL` | `model` 字段 | `gpt-4o-mini` |
| `AI_CHUNK_CHARS` | 超过该长度则分段提取再汇总 | `12000` |
| `CRAWLER_USER_AGENT` | HTTP Fetcher 的 UA | `CrawlerPlatformBot/1.0` |
| `VITE_API_BASE_URL` | 前端请求前缀 | `http://localhost:8000/api/v1` |

`OPENAI_API_BASE` 可以指向 DeepSeek 等兼容实现，例如 `https://api.deepseek.com/v1`。

## 本地开发（不用 Docker）

需要本机 PostgreSQL、Redis 和 Python 3.10+。

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt -r backend/requirements-dev.txt
export DATABASE_URL=postgresql://user:pass@localhost:5432/crawler_db
export REDIS_URL=redis://localhost:6379/0
export OPENAI_API_KEY=sk-...

cd backend
uvicorn app.main:app --reload --port 8000
# 另一个终端
celery -A app.worker.tasks worker --loglevel=info
```

前端：

```bash
cd frontend
npm install
npm run dev -- --host 0.0.0.0 --port 3000
```

开发服务器默认不带 GitHub Pages 的 `/crawler-platform/` 前缀。Pages 构建时设置 `VITE_BASE=/crawler-platform/`。

## 测试

```bash
pip install -r backend/requirements.txt -r backend/requirements-dev.txt
pytest
```

- `tests/test_links.py`：Scrapling Selector 抽出的链接、同域过滤、深度和页数上限。
- `tests/test_task_api.py`：创建、列表、详情、停止、重跑、导出、删除。
- `tests/test_ai_extractor.py`：缺密钥、分段 map-reduce。
- `tests/test_pipeline.py`：本地站点的 BFS / robots，以及 quotes.toscrape.com 的 Markdown。

任务测试使用 SQLite，并设置 `DISABLE_CELERY=1`，不会往 Redis 投递。

## 隐匿抓取（可选）

创建任务时引擎选「隐匿浏览器」会走 `AsyncStealthySession`。默认镜像没有浏览器二进制。在容器里安装：

```bash
docker compose exec worker scrapling install
```

这会下载 Chromium，镜像体积和内存占用都会明显增加。日常页面用 HTTP Fetcher 即可。

## API

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/v1/tasks` | 创建任务并入队 |
| GET | `/api/v1/tasks` | 任务列表 |
| GET | `/api/v1/tasks/{id}` | 详情，含 `crawl_results` |
| POST | `/api/v1/tasks/{id}/stop` | 停止 |
| POST | `/api/v1/tasks/{id}/rerun` | 清空结果后重跑 |
| DELETE | `/api/v1/tasks/{id}` | 删除 |
| GET | `/api/v1/tasks/{id}/export?format=json\|csv\|md` | 导出 |
| GET | `/api/v1/health` | 健康检查 |

全站配置在 `config` 里：`max_depth`（0–5，起始页深度为 0）、`max_pages`（最多 200）、`concurrency`（1–5）、`respect_robots`、`engine`（`http` 或 `stealth`）、`keywords`、`schedule`（`once` / `daily`）。`daily` 会写入配置；当前版本在创建时执行一次。

单页模式忽略深度，只抓起始 URL。
