# 自助爬虫平台

输入 URL 和自定义 Prompt，平台会抓取页面、清洗正文，再调用兼容 OpenAI 的接口抽出结构化结果。全站模式在同域名内做广度优先，并遵守深度、页数和 robots.txt 限制。

演示站点使用 [quotes.toscrape.com](https://quotes.toscrape.com/)。请只抓取你有权访问的页面。

改造方向、分期需求和验收标准见 [平台改造产品规划](docs/PLATFORM-ROADMAP-v2.md)。

## P1 任务管理与持续采集

任务中心支持命名、标签、搜索、状态/域名/日期筛选与分页；结果页支持独立运行历史和历史导出，重跑保留旧结果。采集模板由服务端管理，可保存私有模板与字段校验规则，先试跑 1 页再正式执行；已有正文可重新提取，失败页面可单独重试。

每日、每周计划已接入真实调度，支持暂停/恢复、重叠跳过和停机后最近一期补跑。Docker Compose 包含 scheduler 服务，本地需另启 `python api/scripts/scheduler.py`。可设置本次运行的 Token 预算，达到阈值后停止后续 AI 请求。

已有数据升级先运行 `.venv/Scripts/python.exe api/scripts/migrate_v2.py`，再重启服务。脚本幂等并保留旧任务、结果和发布链接；旧 `daily` 配置不会自动激活。步骤、数据模型和验收范围见 [P1 升级说明](docs/P1-UPGRADE.md)。

## 提示词与网页发布

新建任务时填写「任务提示词」，可以指定提取字段、摘要或文章要求。指令会随任务保存，在抓取与清洗后传入 AI；全站任务对命中的每个页面分别生成内容，发布时将这些结果合并排版为一个 HTML 页面。留空时跳过 AI，仍可发布清洗后的正文。

任务结束后，在结果页的「网页发布」区域填写标题，可预览、一键发布、复制公开链接、更新发布或撤下；也可直接下载独立 HTML 文件。管理员和任务所有者中的普通成员可以发布，只读用户只能预览和下载自己的结果。发布页无需登录，任何持有链接的人都可访问。

发布链接形如 `https://www.zhengaihe.top/crawler/<hashid>`，hashid 为每个发布页生成的 32 位随机十六进制标识。设置 `PUBLIC_BASE_URL=https://www.zhengaihe.top/crawler`，在现有服务器上将 `/crawler/<32位hashid>` 原样反向代理到 API，其他平台页面保持原路由。留空时使用本地 API 的 `/p/<hashid>` 地址。完整配置步骤与 Nginx 示例见 [网页发布部署说明](docs/HTML-PUBLISHING.md)。

发布保存独立快照，不额外调用 AI；任务重跑不会覆盖线上内容，点击「更新发布」才会更新同一链接。撤下后原链接失效，再次发布会生成新链接；删除任务会删除其发布页。已被访问者下载或转存的副本无法随撤下一并收回。

## 登录与权限

平台支持账号密码登录和注册。两种操作均需要输入图片验证码，验证码 3 分钟过期，每张只能提交一次。注册会创建待启用的普通成员账号，管理员可在「用户管理」启用。首次启动后，在仓库根目录创建管理员：

```powershell
# 本地：使用已有 .env 中的 DATABASE_URL；密码交互输入，不会显示在终端
.venv/Scripts/python.exe api/scripts/create_admin.py admin

# Docker：使用容器连接的数据库；本地和 Docker 两种方式任选其一
docker compose exec api python scripts/create_admin.py admin
```

账号为 3–64 位字母、数字、点、下划线或横线，统一小写；新密码为 12–128 位。登录前端后，管理员可进入「用户管理」新增用户、调整角色、启用/禁用账号和重置密码。

- 管理员：操作全部任务，管理账号；可查看升级前 `owner_id=local` 等历史任务。
- 普通成员：创建、查看、导出和操作自己的任务。
- 只读用户：仅查看、导出归属于自己的任务；成员降为只读后可继续查看历史任务，新建只读账号默认没有任务。项目共享与任务分配尚未实现。

所有任务接口均要求登录，任务归属由后端确定。修改密码、管理员修改账号信息会撤销该账号所有会话；退出只撤销当前会话。禁用账号不会自动停止其已有采集任务，管理员可单独停止。健康检查无需登录。

会话默认有效 8 小时，Token 在服务端只存摘要，浏览器使用 sessionStorage，通过 Bearer 请求头发送。密码使用随机盐与 PBKDF2-HMAC-SHA256（600,000 次）。同账号 15 分钟内最多 10 次登录尝试，成功后清除账号计数；同连接来源 15 分钟最多 60 次，超限返回 429。代理部署应配置可信代理的真实来源识别。

`CORS_ORIGINS` 设置允许访问 API 的前端来源，多个来源用逗号分隔；`AUTH_SESSION_HOURS` 控制会话有效期（1–168）。默认允许 localhost 与 127.0.0.1 的 3000 端口。生产部署配置 HTTPS 与实际前端来源。

升级时启动 API 会创建认证所需的新表，保留原任务与结果，无需清空数据库。后续修改已有表结构应使用迁移。若忘记全部管理员密码，可在服务器上运行相同命令创建另一个账号名的恢复管理员；已有账号不会被命令覆盖。

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

状态：`pending → crawling → cleaning → ai_processing → completed | failed`。用户停止后进入 `stopped`，已经入库的页面会保留。每轮绑定独立 `task_runs.id`，重复或过期的队列消息不会重新执行任务。

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

Postgres 数据在卷 `postgres_data`。首次启动会 `create_all` 建表；已有表的结构变更需要数据库迁移。不要通过删除数据卷升级。

阿里云上手动更新见 [jobs/使用说明.md](jobs/使用说明.md)。生产部署只使用脚本生成的单独 compose 文件，不要把本文件和服务器上的 `docker-compose.override.yml` 叠在一起执行，否则会多发布 5432、6379 和 8000。

### 冒烟：quotes.toscrape.com

服务起来之后：

```bash
docker compose exec api python scripts/smoke_test.py
```

脚本先输入已创建的账号密码，再按提示打开临时验证码图片并输入字符，然后创建单页任务并轮询。账号和密码也可由 `SMOKE_USERNAME`、`SMOKE_PASSWORD` 环境变量提供。配了 AI 密钥时期望 `completed`；没配密钥时期望 `failed`，且结果里仍有清洗后的正文，错误含 `OPENAI_API_KEY`。

也可以用 curl：

```bash
curl -s http://localhost:8000/api/v1/health

# 获取验证码图片和 captcha_id。image 是 PNG 的 data URL。
curl -s http://localhost:8000/api/v1/auth/captcha
# 读取图片后提交验证码。登录接口返回 access_token。
curl -s -X POST http://localhost:8000/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"<账号>","password":"<密码>","captcha_id":"<验证码 ID>","captcha_code":"<图片中的字符>"}'

# 后续请求都带上返回的 access_token：
curl -s -X POST http://localhost:8000/api/v1/tasks \
  -H 'Authorization: Bearer <access_token>' \
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
curl -s -H 'Authorization: Bearer <access_token>' http://localhost:8000/api/v1/tasks/<id>
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

需要本机 PostgreSQL、Redis 和 Python 3.10+。以下为 Bash 示例，Windows 可使用现有 `.venv/Scripts/python.exe`；在 PowerShell 中通过 `$env:变量名 = '值'` 设置环境变量。

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r api/requirements.txt -r api/requirements-dev.txt
export DATABASE_URL=postgresql://user:pass@localhost:5432/crawler_db
export REDIS_URL=redis://localhost:6379/0
export OPENAI_API_KEY=sk-...

python api/scripts/create_admin.py admin
cd api
uvicorn app.main:app --reload --port 8000
# 另一个终端
celery -A app.worker.tasks worker --loglevel=info
```

前端：

```bash
cd web
npm install
npm run dev -- --host 0.0.0.0 --port 3000
```

开发服务器使用根路径 `/`，生产构建默认使用现有站点的 `/crawler/` 路径与同源 `/api/v1`。可通过 `VITE_BASE` 和 `VITE_API_BASE_URL` 覆盖；GitHub Pages 如需旧路径，构建时设置 `VITE_BASE=/crawler-platform/`。

## 测试

```bash
pip install -r api/requirements.txt -r api/requirements-dev.txt
pytest
```

- `tests/test_links.py`：Scrapling Selector 抽出的链接、同域过滤、深度和页数上限。
- `tests/test_task_api.py`：创建、列表、详情、停止、重跑、导出、删除。
- `tests/test_ai_extractor.py`：缺密钥、分段 map-reduce。
- `tests/test_pipeline.py`：本地站点的 BFS / robots，以及 quotes.toscrape.com 的 Markdown。

任务测试使用 SQLite，并设置 `DISABLE_CELERY=1`，不会往 Redis 投递。

CRUD 测试使用替身跳过队列派发，Worker 测试显式执行本地站点流水线。`tests/test_auth.py` 覆盖登录、会话撤销、用户管理、任务隔离及只读权限。公网 quotes 测试需要外网 DNS/HTTPS；离线验证可运行 `pytest -k "not test_quotes_to_scrape_markdown_contains_quote"`，它仍包含本地抓取与 AI 流水线测试。

## 隐匿抓取（可选）

创建任务时引擎选「隐匿浏览器」会走 `AsyncStealthySession`。默认镜像没有浏览器二进制。在容器里安装：

```bash
docker compose exec worker scrapling install
```

这会下载 Chromium，镜像体积和内存占用都会明显增加。日常页面用 HTTP Fetcher 即可。

## API

除健康检查、登录与公开发布页外，下列接口需要 `Authorization: Bearer <access_token>`。

账号接口：`POST /api/v1/auth/login`、`GET /api/v1/auth/me`、`POST /api/v1/auth/logout`、`POST /api/v1/auth/password`（`current_password` / `new_password`）。管理员接口：`GET /api/v1/users`（`limit` / `offset`）、`POST /api/v1/users`（`username` / `password` / `role`）、`PATCH /api/v1/users/{id}`（`role` / `is_active` / `password`）。角色值为 `admin`、`member`、`viewer`。

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/v1/tasks` | 创建任务并入队 |
| GET | `/api/v1/tasks` | 任务列表 |
| GET | `/api/v1/tasks/{id}` | 详情，含 `crawl_results` |
| POST | `/api/v1/tasks/{id}/stop` | 停止 |
| POST | `/api/v1/tasks/{id}/rerun` | 归档旧结果并创建新的完整抓取 |
| DELETE | `/api/v1/tasks/{id}` | 删除 |
| GET | `/api/v1/tasks/{id}/export?format=json\|csv\|md\|html` | 导出 |
| GET | `/api/v1/tasks/{id}/preview?title=...` | 私有 HTML 预览 |
| GET | `/api/v1/tasks/{id}/publication` | 已发布信息，未发布为 null |
| POST | `/api/v1/tasks/{id}/publication` | 发布/更新快照，JSON 为 `{"title":"可选标题"}`，可传 `{}` |
| DELETE | `/api/v1/tasks/{id}/publication` | 撤下发布页 |
| GET | `/crawler/{slug}` | 免登录访问已发布 HTML，slug 为 32 位 hashid |
| GET | `/p/{slug}` | 本地调试公开页，兼容原 `/p/{slug}.html` |
| GET | `/api/v1/health` | 健康检查 |

全站配置在 `config` 里：`max_depth`（0–5，起始页深度为 0）、`max_pages`（最多 200）、`concurrency`（1–5）、`respect_robots`、`engine`（`http` 或 `stealth`）、`keywords`、`page_title`，以及 `name`、`tags`、`output_fields`、`template_id`、`template_version`、`token_budget`。调度由 `schedule`（`once` / `daily` / `weekly`）、`schedule_time`、`schedule_weekday`（0 周一至 6 周日）、`schedule_timezone`（`Asia/Shanghai` / `UTC`）配置。`is_preview=true` 会强制单页、单次执行。

任务中心接口为 `GET /api/v1/task-center`，参数 `q`、`status`、`domain`、`created_after`、`created_before`、`limit`、`offset`。运行记录为 `GET /api/v1/tasks/{id}/runs`；详情和导出可传 `run_id` 查询历史。`GET/PUT /api/v1/tasks/{id}/schedule` 管理计划，`GET /api/v1/tasks/{id}/schedule-events` 查看调度记录。`POST /api/v1/tasks/{id}/reextract` 接收 `prompt` 和可选 `output_fields`；`POST /api/v1/tasks/{id}/retry-failed` 仅重试失败页面。模板使用 `GET/POST /api/v1/templates`、`PUT /api/v1/templates/{id}`；调度在线状态为 `GET /api/v1/service-status`。

单页模式忽略深度，只抓起始 URL。
