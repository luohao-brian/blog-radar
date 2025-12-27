# Blog Radar

Blog Radar 是一个基于 Agent 的博客监控与信息聚合系统。它利用 LLM 和 **Model Context Protocol (MCP)** 驱动本地浏览器，自动化收集、翻译和评估技术文章。

## 核心特性

*   **Agent 驱动抓取 (`medium_retriever.py`)**:
    *   通过 Chrome DevTools MCP 连接本地浏览器。
    *   使用 JavaScript 注入 (JS Injection) 高效提取正文，支持清洗 Medium 等平台的干扰元素。
    *   **并发支持**: 支持多任务并发抓取，通过 `asyncio.Lock` 保证浏览器操作的原子性。
    *   **健壮性**: 内置内容长度校验、错误关键词检测和自动重试机制。
*   **视频嗅探与下载 (`douyin_video_downloader.py`, `toutiao_video_downloader.py`)**:
    *   **多平台支持**: 针对抖音（Douyin）和今日头条（Toutiao）的 DASH 流进行定向嗅探。
    *   **智能交互**: 自动处理静音播放、点击开始等页面状态，确保流地址触发。
    *   **批量处理**: 支持通过 `-u` 传入多个 URL 依次执行下载。
    *   **并发安全**: 内置全局信号量，确保同一时刻只有一个任务占用浏览器。
    *   **自动合并**: 自动调用 `ffmpeg` 完成音视频合并。
*   **智能翻译 (`translate.py`)**:
    *   独立翻译模块，支持批量处理。
    *   **长文支持**: 自动将长文章切分为 20k 字符的片段 (Chunks) 并行翻译，防止上下文丢失。
    *   **幂等性**: 自动跳过已翻译的文件。
*   **深度评估 (`eval.py`)**:
    *   从问题具体性、场景描述、解决方案、可验证性四个维度对文章进行打分。
    *   输出结构化的 YAML 报告。

## 技术栈

*   **Python**: 3.12
*   **依赖管理**: [uv](https://github.com/astral-sh/uv)
*   **LLM 框架**: LangChain
*   **Agent 协议**: [Model Context Protocol (MCP)](https://modelcontextprotocol.io/)
*   **Browser Automation**: Chrome DevTools Protocol

## 快速开始

### 1. 环境准备

安装 `uv` 并同步依赖：

```bash
uv sync
```

### 2. 启动 Chrome (Debugging Mode)

MCP 需要连接到一个开启了远程调试端口的 Chrome 实例。

**macOS:**
```bash
/Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome --remote-debugging-port=9333 --user-data-dir=remote-profile
```

### 3. 使用指南

本系统设计为三个独立的流水线步骤：抓取 -> 翻译 -> 评估。

#### 步骤 1: 抓取 (Fetch)

使用 `medium_retriever.py` 抓取文章。支持 RSS 订阅源或单个 URL。

*   **从 RSS 批量抓取**:
    ```bash
    uv run medium_retriever.py --rss feeds/medium.com.yaml --limit 5
    ```
*   **抓取单个 URL**:
    ```bash
    uv run medium_retriever.py --url "https://medium.com/..."
    ```

> 结果保存在: `articles/{yyyy-mm-dd}/{category}/`

#### 步骤 1.5: 视频下载 (Optional Video Download)

使用专用的下载程序抓取抖音或头条视频。

*   **抖音下载 (支持单个或多个)**:
    ```bash
    # 下载单个
    uv run douyin_video_downloader.py -u "https://www.douyin.com/video/..."
    # 批量依次下载
    uv run douyin_video_downloader.py -u URL1 -u URL2 -u URL3
    ```
*   **今日头条下载**:
    ```bash
    uv run toutiao_video_downloader.py -u "https://www.toutiao.com/video/..."
    ```

> 结果保存在: `videos/{yyyy-mm-dd}/` (自动合成 MP4)

#### 步骤 2: 翻译 (Translate)

使用 `translate.py` 批量翻译 Markdown 文件。支持 Shell 通配符。

```bash
# 翻译当天类目下的所有文章
uv run translate.py articles/2025-12-14/tag_prompt-engineering/*.md
```

> 结果保存在: `articles/{yyyy-mm-dd}/translated/`

#### 步骤 3: 评估 (Evaluate)

使用 `eval.py` 对翻译后（或原文）进行质量评估。

```bash
# 评估已翻译的文章
uv run eval.py articles/2025-12-14/translated/*.md
```

> 结果保存在: `articles/{yyyy-mm-dd}/eval/{title}.yaml`

## 目录结构

```text
.
├── medium_retriever.py   # [Entry] 文章抓取入口 (RSS/URL)
├── douyin_video_downloader.py # [Entry] 抖音视频下载器
├── toutiao_video_downloader.py # [Entry] 今日头条视频下载器
├── retriever.py          # [Core] 定义抓取逻辑、浏览器控制、内容验证
├── translate.py          # [Tool] 独立翻译脚本
├── eval.py               # [Tool] 独立评估脚本
├── agent.py              # [Core] LLM Agent 封装 (LangChain + MCP)
├── feeds/                # RSS 配置文件
├── articles/             # 文章存储
│   └── 2025-12-14/
│       ├── category/     # 原文 (.md)
│       └── translated/   # 译文 (.md)
├── videos/               # 视频存储
│   └── 2025-12-27/       # 按日期分类 (.mp4)
└── logs/                 # 运行日志
```

## 配置

在 `.env` 或环境变量中配置：

*   `OPENAPI_API_KEY`: 模型 API Key
*   `OPENAPI_ENDPOINT`: 模型 Endpoint (e.g. Volcengine Ark)
