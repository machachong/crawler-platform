# 爬虫任务提示词与 HTML 发布部署说明

## 使用流程

1. 创建任务，填写 URL 和「任务提示词」。例如：“根据正文生成中文行业观察，包含标题、摘要、三个要点与结论，使用 Markdown，不编造信息”。发布页标题可提前填写，也可以在发布前修改。
2. 等待抓取、清洗和 AI 处理结束，在结果页核对内容。提示词会保存在任务中，可展开查看。全站模式对各命中页面分别生成内容，最终网页按来源分节展示。
3. 点击「预览网页」核对排版。预览仅登录后的授权用户可见，不会生成公开链接。
4. 点击「一键发布 HTML」，复制链接或打开网页。公开页无需登录；发布前应确认内容适合公开分享。
5. 后续重跑任务时，线上快照保持原样。检查新结果后点击「更新发布」，同一链接会展示新快照。
6. 点击「撤下网页」使链接失效。再次发布会分配新链接；删除任务也会删除其发布页。

支持将 JSON 对象、JSON 数组、Markdown 和纯文本排版为独立 HTML。页面采用简报式排版，包含标题、来源、北京时间、预计阅读时长和自动目录；桌面使用侧边目录，手机使用可展开目录，并提供打印样式。来源展示简短域名，保留完整的原文链接。

Markdown 支持标题、段落、粗体、删除线、引用、分隔线、行内代码、代码块、嵌套列表及表格。HTTP(S) 链接可直接点击；原始 HTML 仍作为文字显示，不加载图片或执行脚本。「序号 / 新闻标题 / 标记」一类的排行表自动展示为榜单，「项目 / 结果」的小型概览表展示为信息卡片，其他表格保持表格形式，并在窄屏支持横向滚动。简单 JSON 数组排版为表格，含长正文或嵌套内容的记录使用卡片。

网页不依赖外部脚本、字体或样式文件，下载后可以离线打开。预览、HTML 下载和一键发布共用同一个模板；排版不会额外调用 AI，也不会补写原文缺失的摘要。

### 已发布页面如何使用新版排版

升级代码后重建 API（新增 `markdown-it-py` 依赖）：`docker compose up -d --build api`。进入已有任务的结果页，先点击「预览网页」，确认后点击「更新发布」。链接的 hashid 保持不变，新 HTML 替换原来的发布快照。旧快照不会仅因为部署或重跑任务而自动改变；更新发布使用当前任务的结果，请先确认该结果就是希望公开的版本。

发布使用已经保存的内容，不会额外请求 AI。没有配置提示词、AI 密钥缺失但正文已保存，或任务停止且已有部分结果时，仍可发布可用正文。执行中的任务暂不能生成快照；失败抓取、关键词跳过和 AI 错误占位结果不纳入发布。对原始 HTML 标签按文本显示，不能通过提示词植入脚本或自定义可执行页面。

## 公开地址配置

当前公开地址使用已有域名的 `/crawler/` 路径，API 环境变量配置为：

```dotenv
PUBLIC_BASE_URL=https://www.zhengaihe.top/crawler
```

URL 格式为 `https://www.zhengaihe.top/crawler/<hashid>`。hashid 是发布页独立生成的 32 位随机十六进制标识，不使用连续任务编号。接口直接提供 `/crawler/{hashid}` HTML 路由，反向代理保留完整路径即可，不需要添加 `.html` 扩展名。

不设置时，API 使用本次请求的基地址与 `/p/` 返回链接，方便本地测试，例如 `http://localhost:8000/p/<hashid>`。原 `/p/<hashid>.html` 仍可访问。本机地址不能直接发给外网用户访问。该配置只决定地址，不会修改域名现有的 DNS、证书或服务器配置。

现有域名已经用于平台时，沿用它的 DNS 和 HTTPS，在现有虚拟主机中补充 hashid 路由转发即可。其他 `/crawler/` 路径继续服务管理平台，公开页与管理页面共用域名。

## Nginx 示例

把以下 location 加到 `www.zhengaihe.top` 现有 HTTPS server 中，不替换其他站点的配置。API 上游示例为同机 8000 端口；容器中的 Nginx 应使用可到达的 API 服务地址。可直接参考 [Nginx 路由片段](nginx/crawler-publishing.conf)。

```nginx
location ~ "^/crawler/[0-9a-f]{32}$" {
    proxy_pass http://127.0.0.1:8000;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_cache off;
}

# 如果站点还没有 /api/ 转发，增加这一段供管理平台调用。
location /api/ {
    proxy_pass http://127.0.0.1:8000;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
}
```

保留管理平台已有的 `/crawler/` 静态文件与 SPA 回退规则。公开链接只匹配 32 位 hashid，`/crawler/create` 和 `/crawler/results/...` 不会被该正则接管。如果已有 `location ^~ /crawler/`，需要改成普通的 `location /crawler/`，否则会阻止上述正则匹配。若有其他更宽泛的正则，应把该具体规则放在它们前面。

前端生产构建默认以 `/crawler/` 为基础路径，并向同源 `/api/v1` 发请求；开发模式仍使用本地端口。已有环境如使用不同的 API 地址，可通过 `VITE_API_BASE_URL` 覆盖；`VITE_BASE` 可覆盖前端基础路径。构建后将 `web/dist` 部署到现有 `/crawler/` 静态目录。

Docker Compose 已透传 `PUBLIC_BASE_URL`。修改仓库 `.env` 后重建 API 容器，使环境变量生效：

```bash
docker compose up -d --build api
```

首次启动会新增 `published_pages` 表，无需清空已有任务和结果。在服务器检查 Nginx 配置并重新加载，再部署前端新构建产物；本地开发时只需重启 API 和前端。

## 权限与数据边界

- 管理员可以发布和撤下任意任务，普通成员只能管理自己的发布页。
- 只读用户可预览、下载属于自己的结果，无权发布、更新或撤下。
- 匿名用户只能通过公开链接读取已发布快照，不能访问任务详情、提示词、日志、用户管理或发布接口。
- 公开网页包含生成内容和相应来源 URL；内部提示词、账号 ID、配置、Token 用量及错误日志不会嵌入发布页。
- 发布快照保存在数据库中，重启服务后继续有效；备份时应包含 `published_pages`。
- 禁用账号会撤销登录会话，已有公开网页仍保持发布状态，需要管理员另外撤下。访问者已经保存的副本无法收回。
- API 返回 `Cache-Control: no-store`，代理/CDN 也应禁用该路径的缓存，避免撤下后继续通过缓存提供旧内容。公开链接默认要求搜索引擎不索引，但它不是访问密码。

## 验证方式

先用内部任务完成一次发布，浏览器退出平台登录后直接打开公开链接，确认可访问 HTML；撤下后相同链接应返回 404。重启 API 后仍应可访问未撤下的网页。发布域名验证还需实际检查 DNS、证书与反向代理，本地自动化测试不替代这些部署检查。

仓库自动化测试覆盖提示词传递、私有预览、HTML 下载、公开访问、任务隔离、只读限制、发布快照、更新与撤下，以及脚本内容转义：

```bash
pytest -q -k "not test_quotes_to_scrape_markdown_contains_quote"
```
