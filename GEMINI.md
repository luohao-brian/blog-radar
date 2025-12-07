# 项目：blog-radar

## 项目概览
本项目是一个博客监控与信息聚合系统，旨在自动化收集、分析指定 RSS Feed 中的文章内容，目前专注于 Medium 平台。

## 开发环境与规范 (继承自全局配置)

### 语言偏好
- **第一语言**: 中文 (Chinese)

### 技术栈规范 (Python)
- **编程语言**: Python 3.12
- **环境管理**: [uv](https://github.com/astral-sh/uv)
- **核心依赖**:
    - `feedparser`: 解析 RSS/Atom feeds
    - `trafilatura`: 网页正文提取
    - `requests`: HTTP 请求
    - `pyyaml`: 配置文件读取

### 常用命令
- **初始化环境**: `uv sync`
- **运行 Medium 抓取**: `uv run medium-feed-retriever.py`

## 使用指南：信息收集

### 1. 配置订阅源
在 `feeds/` 目录下创建或修改 YAML 文件（例如 `medium.com.yaml`）：

```yaml
feeds:
  - https://medium.com/feed/tag/prompt-engineering
  - https://medium.com/feed/@username
  - https://medium.com/feed/publication-name
```

### 2. 执行抓取
运行 `uv run medium-feed-retriever.py`。
程序将执行以下流程：
1.  **扫描配置**: 读取 `feeds/` 目录下所有 `.yaml` 文件。
2.  **解析 Feed**: 识别 Feed 类型（Author, Publication, Tag）并确定归档目录。
3.  **获取文章**: 尝试 Direct -> Jina Reader -> Google Cache -> Wayback Machine 的多重 Fallback 策略。
4.  **归档保存**: 内容保存至 `./articles/[Date]/[Category]/[Title].md`。

### 3. 目录结构示例
```text
articles/
└── 2025-12-07/
    ├── tag_prompt-engineering/
    │   └── How_to_Prompt.md
    ├── @genai.works/
    │   └── AI_Trends.md
    └── the-startup/
        └── Startup_Advice.md
```