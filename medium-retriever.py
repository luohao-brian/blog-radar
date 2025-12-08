import yaml
import feedparser
import time
import os
import datetime
import re
import glob
import click
from urllib.parse import urlparse
from agent import Agent
from logger import setup_logging

# --- Configuration & Setup ---

FEEDS_DIR = "feeds"
ARTICLES_DIR = "articles"

# 使用统一的日志模块
LOGGER = setup_logging("medium_feed_retriever")

# --- Helper Functions ---


def sanitize_filename(name):
    """移除文件名中不允许的字符，替换空格为下划线，并限制长度"""
    return re.sub(r"[\\/*?:\"<>|]", "", name).strip().replace(" ", "_")[:100]


def determine_category_from_feed(feed_url):
    """从 Feed URL 解析类别"""
    try:
        parsed = urlparse(feed_url)
        path = parsed.path
        if "/feed/" not in path:
            return "未知类别"
        suffix = path.split("/feed/")[-1]
        if suffix.startswith("tag/"):
            return suffix.replace("/", "_")
        return suffix.strip("/")
    except Exception as e:
        LOGGER.error(f"解析 Feed URL 类别失败 '{feed_url}': {e}")
        return "未知类别"


def load_feeds_from_file(file_path):
    """从单个 YAML 文件加载 Feeds"""
    feeds = []
    LOGGER.info(f"正在加载配置文件: {file_path}")
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
            if data and "feeds" in data:
                feeds.extend(data["feeds"])
    except Exception as e:
        LOGGER.error(f"加载配置文件 '{file_path}' 失败: {e}")
    return feeds


def save_article_to_file(filepath, title, url, category, content, feed_url=None):
    """将文章内容写入 Markdown 文件"""
    try:
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(f"# {title}\n\n")
            f.write(f"**源链接**: {url}\n")
            if feed_url:
                f.write(f"**Feed**: {feed_url}\n")
            f.write(f"**类别**: {category}\n")
            f.write(f"**抓取来源**: Agent + Chrome MCP\n")
            f.write(f"**抓取日期**: {datetime.datetime.now().isoformat()}\n\n")
            f.write("---\n\n")
            f.write(content)
        LOGGER.info(f"  文章已保存至: {filepath}")
        return True
    except Exception as e:
        LOGGER.error(f"  保存文章 '{title}' 到文件 '{filepath}' 失败: {e}")
        return False


# --- Core Logic ---


def fetch_article_content(agent: Agent, url: str) -> str:
    """使用 Agent 调用 Chrome DevTools MCP 工具打开并提取网页内容"""
    LOGGER.info(f"正在抓取内容 (Agent + MCP): {url}")

    prompt = f"""
    目标：抓取并翻译这篇文章：{url}
    
    请执行以下步骤：
    1. **导航**：调用 `navigate_page` 打开链接。
    2. **等待**：确保页面加载完成。
    3. **提取**：
       - 优先尝试使用 `evaluate_script` 运行 JavaScript 提取正文内容（例如提取 `<article>` 标签或 `h1` + 正文）。
       - 如果脚本提取失败，再尝试使用 `take_snapshot` 获取页面结构。
    4. **处理**：
       - 过滤掉广告、推荐阅读、评论区和页脚。
       - **将提取到的核心内容翻译为流畅、专业的中文。对于不确定的名称和含义，不要假设，同时保留中英文。**
    5. **输出**：
       - 返回翻译后的 Markdown 内容。
       - 格式要求：标题使用 #，子标题使用 ##，代码块保留原文。
    """

    messages = [{"role": "user", "content": prompt}]

    try:
        content = agent.chat_with_tools(messages)
        if content and len(content) > 100:
            return content
        else:
            LOGGER.warning(
                f"  抓取内容过短或为空: {content[:100] if content else 'None'}..."
            )
            return None
    except Exception as e:
        LOGGER.error(f"  抓取失败: {e}")
        return None


def fetch_and_process_article(agent, url, title, category, output_dir, feed_url=None):
    """处理单篇文章：检查存在、抓取、保存"""
    filename = f"{sanitize_filename(title)}.md"
    filepath = os.path.join(output_dir, filename)

    if os.path.exists(filepath):
        LOGGER.info(f"  跳过 (文章已存在): '{title}'")
        return False

    LOGGER.info(f"\n  --- 处理文章: '{title}' ---")
    content = fetch_article_content(agent, url)

    if content:
        save_article_to_file(filepath, title, url, category, content, feed_url)
        # 抓取成功后礼貌性等待，避免触发频率限制
        time.sleep(2)
        return True
    else:
        LOGGER.warning(f"  未能抓取文章 '{title}' 的内容。")
        return False


def process_single_feed(agent, feed_url, today_str):
    """处理单个 Feed"""
    LOGGER.info(f"\n开始处理 Feed: {feed_url}")
    category = determine_category_from_feed(feed_url)

    output_dir = os.path.join(ARTICLES_DIR, today_str, sanitize_filename(category))
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)
        LOGGER.info(f"创建目录: {output_dir}")

    d = feedparser.parse(feed_url)
    if not d.entries:
        LOGGER.warning(f"  Feed '{feed_url}' 未找到任何文章条目。")
        return

    for i, entry in enumerate(d.entries):
        title = entry.title
        link = entry.link
        LOGGER.info(f"  [Feed Item {i+1}]")
        fetch_and_process_article(agent, link, title, category, output_dir, feed_url)


def process_single_url(agent, url):
    """处理单个 URL"""
    LOGGER.info(f"\n开始抓取指定 URL: {url}")
    category = "single_url_fetch"
    today_str = datetime.date.today().isoformat()

    output_dir = os.path.join(ARTICLES_DIR, today_str, sanitize_filename(category))
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)
        LOGGER.info(f"创建目录: {output_dir}")

    # 尝试提取标题
    path_parts = urlparse(url).path.split("/")
    title = path_parts[-1] if path_parts[-1] else "untitled_article"
    title = title.replace("-", "_")

    fetch_and_process_article(agent, url, title, category, output_dir)


# --- CLI & Main ---


@click.command()
@click.option("--url", default=None, help="指定要抓取的单个文章 URL。")
@click.option(
    "--rss", type=click.Path(exists=True), help="指定要加载的 RSS 配置文件 (YAML)。"
)
def main(url, rss):
    LOGGER.info("程序启动：Medium Feed 信息抓取器 (MCP Enhanced)")

    # MCP 配置
    mcp_config = {
        "chrome-devtools": {
            "command": "npx",
            "args": [
                "chrome-devtools-mcp@latest",
                "--browser-url=http://127.0.0.1:9333",
            ],
        }
    }

    agent = Agent(model_name="doubao-seed-1-6-251015", mcp_config=mcp_config)

    if not agent.mcp_client:
        LOGGER.error("MCP Client 启动失败。请检查 Chrome 调试端口 9333 是否开启。")
        return

    try:
        if url:
            process_single_url(agent, url)
        elif rss:
            feeds = load_feeds_from_file(rss)
            if not feeds:
                LOGGER.error(f"从文件 '{rss}' 未加载到任何 Feed。")
                return

            today_str = datetime.date.today().isoformat()
            for feed_url in feeds:
                process_single_feed(agent, feed_url, today_str)
        else:
            LOGGER.error(
                "请提供 --url 参数指定单个文章，或 --rss 参数指定 Feed 配置文件。"
            )
            # Click 会自动处理，这里可以退出或者 click.echo + sys.exit
            sys.exit(1)

    finally:
        LOGGER.info("正在关闭 Agent...")
        agent.close()


if __name__ == "__main__":
    main()
