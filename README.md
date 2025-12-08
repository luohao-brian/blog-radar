# blog-radar

## 项目概览
Blog Radar 是一个博客监控与信息聚合系统，旨在自动化收集、分析指定 RSS Feed 中的文章内容。目前专注于 Medium 平台，利用 LLM (Large Language Models) 和浏览器自动化工具 (MCP - Model Context Protocol) 来应对现代网页的复杂性。

## 特性
- **智能抓取**: 使用 `doubao-seed-1-6-flash-250828` 模型结合 Chrome DevTools MCP，模拟真实浏览器行为获取文章内容，有效绕过反爬虫机制。
- **多模式支持**: 支持抓取单个文章 URL 或批量处理 RSS Feed。
- **文章评分**: 内置评分工具 (`article-eval.py`)，根据问题具体性、场景描述、解决方案和可验证性对技术文章进行评分。
- **结构化存储**: 抓取的文章自动转换为 Markdown 格式，并按日期和类别分类存储。

## 环境要求
- Python 3.12+
- `uv` 包管理器
- Google Chrome 浏览器 (用于 MCP 抓取)

## 快速开始

### 1. 初始化环境
```bash
uv sync
```

### 2. 启动 Chrome 调试模式
MCP 依赖于 Chrome 的远程调试协议。请使用以下命令启动 Chrome：

**macOS:**
```bash
/Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome --remote-debugging-port=9333 --user-data-dir=remote-profile
```

### 3. 使用 medium-retriever 抓取文章

**抓取单个 URL:**
```bash
uv run medium-retriever.py --url <ARTICLE_URL>
```

**从 RSS 配置文件批量抓取:**
```bash
uv run medium-retriever.py --rss feeds/medium.com.yaml
```

### 4. 使用 article-eval 评分文章
```bash
uv run article-eval.py <path_to_article.md>
```

## 配置文件 (feeds/*.yaml)
RSS 源配置文件格式如下：
```yaml
feeds:
  - https://medium.com/feed/tag/prompt-engineering
  - https://medium.com/feed/@username
```

## 目录结构
- `medium-retriever.py`: 核心抓取脚本。
- `article-eval.py`: 文章评分脚本。
- `agent.py`: 封装了 OpenAI Client 和 MCP Client 的 Agent 类。
- `logger.py`: 统一日志模块。
- `feeds/`: 存放 RSS 源配置文件。
- `articles/`: 存放抓取的 Markdown 文章。
- `logs/`: 存放运行日志。
