# 自助爬虫平台 (Self-Service Crawler Platform) — 完整 PRD (v1.1)

## 1. 项目概述
本平台旨在通过“URL + 自定义 Prompt”的模式，将网页非结构化数据转化为用户所需的任何形式。支持全站/单页抓取，并利用 LLM 实现高度自定义的内容提取逻辑。

---

## 2. 页面与交互细节定义

### 2.1 任务中心 (Dashboard)
- **任务列表卡片**: 
    - 展示字段：任务ID、目标域名、当前状态（排队中/抓取中/清洗中/AI处理中/已完成/失败）、进度条。
    - 操作：查看详情、停止任务、重新运行、删除。
- **状态流转**: 
    - 🔴 失败时悬浮显示错误详情（如：403 Forbidden, Prompt Overlength）。

### 2.2 任务创建页 (Task Config)
- **URL 配置区**: 
    - 支持单个起始 URL 输入。
    - 抓取模式切换：[单页模式] / [全站模式]。
    - 全站深度限制：默认 2 层，最高 5 层。
- **AI 提取实验室 (核心)**:
    - **Prompt 编辑器**: 宽大的文本域，支持 Markdown 语法高亮。
    - **提取预设 (Templates)**: 提供常用场景，如“提取评论”、“提取价格表”、“总结要点”。
    - **自定义指令**: 提示用户可以指定“提取什么”、“从哪提取”、“输出成什么样”。
- **高级配置**:
    - 并发数控制：1 - 5 个并发 Worker。
    - 定时任务开关：[单次运行] / [每日定时]。

### 2.3 结果展示页 (Result Viewer)
- **多维度视图**:
    - **结构化视图**: 自动渲染 AI 返回的 JSON 数据为表格或树形结构。
    - **原文对比**: 左侧显示抓取的清洗后正文，右侧显示 AI 提取结果。
    - **原始日志**: 显示每一步的爬取状态和 Token 消耗。
- **导出功能**: 支持一键导出为 CSV、JSON、Markdown 文件。

---

## 3. 功能细节逻辑

### 3.1 爬虫与链接发现逻辑
- **全站模式**: 
    - 后端启动 BFS (广度优先搜索) 算法。
    - 仅抓取同域名下的内部链接，自动跳过外部广告/友链。
    - 使用布隆过滤器 (Redis-based) 确保 URL 不重复。
- **反爬初步应对**: 随机 User-Agent 池 + 基础请求头模拟。

### 3.2 AI 智能提取逻辑
- **内容剪裁**: 为节省 Token，系统自动剔除 HTML 中的 `<script>`, `<style>`, `<nav>`, `<footer>` 等无关标签。
- **分段处理**: 若网页过长，采用“分段提取+最后汇总”的 MapReduce 策略。

---

## 4. 后端数据库设计 (PostgreSQL)

### 4.1 `tasks` (任务主表)
| 字段名 | 类型 | 说明 |
| :--- | :--- | :--- |
| `id` | UUID | 主键 |
| `owner_id` | String | 用户 OpenID |
| `target_url` | String | 初始 URL |
| `mode` | Enum | single / site |
| `prompt` | Text | 用户完整的自定义指令 |
| `status` | Enum | pending / crawling / cleaning / ai_processing / completed / failed |
| `config` | JSONB | 存储最大深度、并发数、定时规则等 |
| `created_at` | Timestamp | 创建时间 |

### 4.2 `crawl_results` (抓取明细表)
| 字段名 | 类型 | 说明 |
| :--- | :--- | :--- |
| `id` | BigInt | 主键 |
| `task_id` | UUID | 关联任务 |
| `source_url` | String | 具体页面链接 |
| `raw_text` | Text | 清洗后的正文文本 (用于 AI 提取) |
| `extracted_data`| JSONB | AI 提取出的结构化结果 |
| `token_usage` | Integer | 该次处理消耗的 Token 数 |

---

## 5. Sub-Agent 任务清单
- **Dev**: 基于此文档实现 Pydantic 模型与 SQLAlchemy 映射。
- **Dev**: 构建 FastAPI 任务下发逻辑与 Celery Worker 监听。
- **Test**: 编写单元测试验证不同深度下的链接发现逻辑。
