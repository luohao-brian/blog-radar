# 项目：blog-radar

## 项目概览
本项目是一个博客监控与信息聚合系统，旨在自动化收集、分析指定 RSS Feed 中的文章内容。

## 开发环境与规范 (继承自全局配置)

### 语言偏好
- **第一语言**: 中文 (Chinese) - 包括代码注释、日志输出及文档。

### 技术栈规范 (Python)
- **编程语言**: Python 3.12
- **环境管理**: [uv](https://github.com/astral-sh/uv)
- **核心依赖**:
    - `feedparser`: 解析 RSS/Atom feeds
    - `langchain-openai`: LLM 交互框架
    - `langgraph`: Agent 编排
    - `mcp`: Model Context Protocol 协议支持
    - `pyyaml`: 配置文件读取
    - `logging`: 标准日志模块
    - `click`: CLI 命令行工具

### 常用命令
- **初始化环境**: `uv sync`
- **运行抓取**: 
    - 单篇: `uv run medium_retriever.py --url <URL>`
    - RSS: `uv run medium_retriever.py --rss <YAML_PATH>`
- **运行翻译**: `uv run translate.py <FILE_PATH>`
- **运行评分**: `uv run eval.py <FILE_PATH>`

## 系统设计与规范

### 1. 日志系统 (Logging)
- **模块**: `logger.py`
- **输出模式**: 双重输出 (Dual Output)
    - **Console**: 实时打印简要信息 (INFO 级别)。
    - **File**: 详细记录运行轨迹 (DEBUG 级别)，精确到秒。
- **日志路径**: `./logs/[module]_[timestamp].log`
- **版本控制**: `logs/` 目录默认添加至 `.gitignore`。

### 2. 智能抓取与 Agent (Architecture)
系统摒弃了传统的 HTTP 爬虫，转而采用 **Agent + MCP (Model Context Protocol)** 架构：
- **Agent**: 基于 LangChain 和 `doubao-seed-1-6-flash-250828` 模型，负责理解任务、规划行动。
- **MCP Client**: 通过 `npx chrome-devtools-mcp` 连接本地 Chrome 浏览器 (Port 9333)，执行 `navigate`、`snapshot`、`evaluate_script` 等操作，模拟真实用户行为。
- **并发控制**: 使用 `asyncio.Semaphore` 控制并发抓取数量，防止浏览器过载。

### 3. 翻译模块 (Translation)
- **模块**: `translate.py`
- **长文处理**: 自动将长文章智能拆分为 20k 字符的片段 (Chunks) 并行翻译，防止上下文丢失。
- **清洗优化**: 翻译过程中自动去除网页噪音（如 "Share", "Follow" 等），并保留 Markdown 结构。

### 4. 使用指南

#### 配置订阅源
YAML 配置文件格式：
```yaml
feeds:
  - https://medium.com/feed/tag/prompt-engineering
  - https://medium.com/feed/@username
```

#### 抓取流程 (Fetch)
1.  **启动 Chrome**: 开启远程调试端口 9333。
2.  **运行脚本**: `medium_retriever.py` 初始化 Agent。
3.  **任务执行**: Agent 指挥浏览器打开链接，等待加载，提取正文。
4.  **归档保存**: 内容保存至 `./articles/[Date]/[Category]/[Title].md`。

#### 翻译流程 (Translate)
运行 `translate.py` 对抓取的 Markdown 文件进行翻译。
- 结果保存至: `./articles/[Date]/translated/[Title]_cn.md`

#### 文章评分 (Evaluate)
运行 `eval.py`，对抓取的 Markdown 文件进行多维度评分（问题具体性、场景描述、解决方案、可验证性）。
- 结果保存至: `./articles/[Date]/eval/[Title].yaml`
