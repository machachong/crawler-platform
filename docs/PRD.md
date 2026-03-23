# 自助爬虫平台 PRD (Product Requirement Document) - v1.0

## 1. 项目愿景
打造一个“懂业务”的自助爬虫平台，通过 AI 提示词（Prompt）降低网页数据提取的门槛，让非技术用户也能高效获取结构化信息。

## 2. 核心业务流程
1. **任务创建**: 用户输入起始 URL + 爬取范围（全站/单页） + 关键词（正则/匹配） + AI 提取 Prompt。
2. **异步执行**: 后端接收任务进入 Redis 队列，多 Worker 异步并行爬取。
3. **内容清洗**: 
   - 过滤 HTML 噪声（广告、导航栏）。
   - 命中关键词的页面进入 AI 提取阶段。
4. **AI 结构化**: 调用 LLM 根据用户 Prompt 将正文转化为 JSON/Markdown。
5. **持久化**: 结果存入 PostgreSQL，支持导出。

## 3. 功能模块
### 3.1 任务管理 (Frontend & Backend)
- 任务列表：显示 ID、目标 URL、状态（等待中/进行中/已完成/失败）、进度条。
- 创建面板：表单提交核心参数。

### 3.2 爬虫引擎 (Backend - Crawler)
- 异步抓取：支持并发请求，支持基础 User-Agent 伪造。
- 链接发现：全站模式下自动提取同域名内的 <a> 标签进入队列。

### 3.3 数据提取与 AI (Backend - AI)
- **通用正文提取**: 使用 Readability 算法初步提取纯净正文，作为 AI 的原始上下文。
- **Ultra-Flexible Prompt (核心竞争力)**:
    - **支持提取什么**: 用户指定目标（如“所有 2024 年以后的手机型号”）。
    - **支持指定哪部分**: 用户指定范围（如“只看该页面的侧边栏推荐位”）。
    - **支持指定样貌**: 用户指定输出格式（如“CSV”、“JSON”或“300 字摘要”）。
    - **执行逻辑**: 将用户完整的自定义 Prompt + 原始上下文 整体喂给 LLM。

## 4. 数据表结构 (DB Design)
- `tasks`: id, url, prompt, keywords, status, created_at, updated_at
- `results`: id, task_id, source_url, content_json, markdown_summary

## 5. 待分发任务 (Next Actions)
- [ ] **Sub-Agent: Dev** -> 搭建 FastAPI + Redis + Celery 基础脚手架。
- [ ] **Sub-Agent: Dev** -> 编写 Vue3 任务提交页面。
- [ ] **Sub-Agent: Test** -> 准备 3 个不同类型的测试站点进行爬取实验。

---
最后更新：2026-03-23
负责人：Main Agent (PM)
