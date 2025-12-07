# 项目：blog-radar

## 项目概览
本项目是一个博客监控与信息聚合系统，旨在自动化收集、分析指定 RSS Feed 中的文章内容，目前专注于 Medium 平台。

## 开发环境与规范 (继承自全局配置)

### 语言偏好
- **第一语言**: 中文 (Chinese) - 包括代码注释、日志输出及文档。

### 技术栈规范 (Python)
- **编程语言**: Python 3.12
- **环境管理**: [uv](https://github.com/astral-sh/uv)
- **核心依赖**:
    - `feedparser`: 解析 RSS/Atom feeds
    - `trafilatura`: 网页正文提取
    - `requests`: HTTP 请求
    - `pyyaml`: 配置文件读取
    - `logging`: 标准日志模块

### 常用命令
- **初始化环境**: `uv sync`
- **运行 Medium 抓取**: `uv run medium-feed-retriever.py`

## 系统设计与规范

### 1. 日志系统 (Logging)
- **输出模式**: 双重输出 (Dual Output)
    - **Console**: 实时打印简要信息。
    - **File**: 详细记录运行轨迹。
- **日志路径**: `./logs/medium-feed-retriever.[timestamp].log`
- **版本控制**: `logs/` 目录默认添加至 `.gitignore`，**严禁**提交日志文件。
- **语言**: 所有日志信息强制使用中文。

### 2. 抓取与回退策略 (Fallback Strategy)
系统采用多级回退机制以应对反爬虫和 Paywall：
1.  **Direct Fetch**: 使用 `trafilatura` 直接提取。
2.  **Jina Reader**: 使用 `https://r.jina.ai/` 代理提取。
3.  **Google Cache**: 尝试获取 Google 网页快照。
4.  **Wayback Machine**: 尝试获取 Internet Archive 历史版本。

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
1.  **初始化**: 设置日志系统，生成带时间戳的日志文件。
2.  **扫描配置**: 读取 `feeds/` 目录下所有 `.yaml` 文件。
3.  **解析 Feed**: 识别 Feed 类型（Author, Publication, Tag）并确定归档目录。
4.  **全量获取**: 遍历 Feed 中**所有**条目，跳过已存在的本地文件。
5.  **归档保存**: 内容保存至 `./articles/[Date]/[Category]/[Title].md`。

### 3. 目录结构示例
```text
articles/
└── 2025-12-07/                  # 按日期归档
    ├── tag_prompt-engineering/  # 按 Tag/Author/Publication 分类
    │   └── How_to_Prompt.md
    └── @genai.works/
        └── AI_Trends.md
logs/
└── medium-feed-retriever.20251207_120000.log  # 运行日志
```
