import yaml
import feedparser
import trafilatura
import requests
import time
import os
import datetime
import re
import glob
import logging
from urllib.parse import quote, urlparse

# --- Configuration & Setup ---

FEEDS_DIR = 'feeds'
ARTICLES_DIR = 'articles'
LOGS_DIR = 'logs'

def setup_logging():
    """
    配置日志模块，支持同时向控制台和文件输出中文日志。
    """
    # 确保日志目录存在
    os.makedirs(LOGS_DIR, exist_ok=True)

    # 生成带时间戳的日志文件名
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file_path = os.path.join(LOGS_DIR, f"medium-feed-retriever.{timestamp}.log")

    logger = logging.getLogger("medium_feed_retriever")
    logger.setLevel(logging.INFO) # 设置最低日志级别

    # 清除旧的处理器，避免重复输出
    if logger.hasHandlers():
        logger.handlers.clear()

    # 控制台处理器
    console_handler = logging.StreamHandler()
    console_formatter = logging.Formatter('%(levelname)s: %(message)s')
    console_handler.setFormatter(console_formatter)
    logger.addHandler(console_handler)

    # 文件处理器
    file_handler = logging.FileHandler(log_file_path, encoding='utf-8')
    file_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    file_handler.setFormatter(file_formatter)
    logger.addHandler(file_handler)

    logger.info(f"日志已配置，将输出到控制台和文件: {log_file_path}")
    return logger

LOGGER = setup_logging() # 全局日志器实例

def load_configs():
    configs = []
    feed_files = glob.glob(os.path.join(FEEDS_DIR, '*.yaml'))
    if not feed_files:
        LOGGER.warning(f"在 '{FEEDS_DIR}' 目录下未找到任何 YAML 配置文件。")
    for file_path in feed_files:
        LOGGER.info(f"正在加载配置文件: {file_path}")
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = yaml.safe_load(f)
                if data and 'feeds' in data:
                    configs.extend(data['feeds'])
        except Exception as e:
            LOGGER.error(f"加载配置文件 '{file_path}' 失败: {e}")
    return configs

def sanitize_filename(name):
    # 移除文件名中不允许的字符，替换空格为下划线，并限制长度
    return re.sub(r'[\\/*?:"<>|]', "", name).strip().replace(" ", "_")[:100]

def determine_category(feed_url):
    """
    解析 Medium RSS URL 以确定子目录名称。
    例如:
    - https://medium.com/feed/@username -> @username
    - https://medium.com/feed/publication-name -> publication-name
    - https://medium.com/feed/tag/tag-name -> tag_tag-name
    """
    try:
        parsed = urlparse(feed_url)
        path = parsed.path # 例如: /feed/tag/prompt-engineering
        
        if '/feed/' not in path:
            return "未知类别"
            
        suffix = path.split('/feed/')[-1]
        
        # 特殊处理 tag 以避免子目录
        if suffix.startswith('tag/'):
            return suffix.replace('/', '_')
            
        # 清理末尾斜杠
        return suffix.strip('/')
    except Exception as e:
        LOGGER.error(f"解析 Feed URL 类别失败 '{feed_url}': {e}")
        return "未知类别"

# --- 抓取策略 ---

def fetch_direct(url):
    try:
        downloaded = trafilatura.fetch_url(url)
        text = trafilatura.extract(downloaded) if downloaded else None
        if text and len(text) > 300:
            return text, "直接抓取"
    except Exception as e:
        LOGGER.warning(f"  直接抓取失败: {e}")
    return None, None

def fetch_jina(url):
    try:
        jina_url = f"https://r.jina.ai/{url}"
        r = requests.get(jina_url, timeout=20)
        if r.status_code == 200 and len(r.text) > 300:
            # 检查是否为错误页面
            if "Access denied" not in r.text[:500] and "Error" not in r.text[:500]:
                return r.text, "Jina 阅读器"
            else:
                LOGGER.warning(f"  Jina 阅读器返回内容异常或拒绝访问。" )
        else:
            LOGGER.warning(f"  Jina 阅读器 HTTP 状态码非 200 或内容过短 ({r.status_code})。" )
    except requests.exceptions.Timeout:
        LOGGER.warning(f"  Jina 阅读器请求超时。" )
    except Exception as e:
        LOGGER.warning(f"  Jina 阅读器抓取失败: {e}")
    return None, None

def fetch_wayback(url):
    try:
        api_url = f"https://archive.org/wayback/available?url={url}"
        r = requests.get(api_url, timeout=10)
        data = r.json()
        if 'archived_snapshots' in data and 'closest' in data['archived_snapshots']:
            wb_url = data['archived_snapshots']['closest']['url']
            LOGGER.info(f"  找到 Wayback Machine 快照: {wb_url}")
            downloaded = trafilatura.fetch_url(wb_url)
            text = trafilatura.extract(downloaded) if downloaded else None
            if text and len(text) > 300:
                return text, "Wayback Machine"
        else:
            LOGGER.info(f"  Wayback Machine 未找到快照。" )
    except Exception as e:
        LOGGER.warning(f"  Wayback Machine 抓取失败: {e}")
    return None, None

def fetch_google_cache(url):
    try:
        # 注意: 此方法不稳定，取决于 Google 缓存的可用性
        cache_url = f"http://webcache.googleusercontent.com/search?q=cache:{quote(url)}"
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        r = requests.get(cache_url, headers=headers, timeout=10)
        if r.status_code == 200:
            # 检查 Google 的 404 页面内容
            if "That’s an error." in r.text and "The requested URL was not found" in r.text:
                LOGGER.info(f"  Google 缓存返回 404 页面。" )
                return None, None
            text = trafilatura.extract(r.text)
            if text and len(text) > 300:
                return text, "Google 缓存"
        else:
            LOGGER.warning(f"  Google 缓存 HTTP 状态码非 200 ({r.status_code})。" )
    except Exception as e:
        LOGGER.warning(f"  Google 缓存抓取失败: {e}")
    return None, None

def fetch_content_with_fallbacks(url):
    LOGGER.info(f"正在抓取内容: {url}")
    
    strategies = [fetch_direct, fetch_jina, fetch_google_cache, fetch_wayback]
    
    for strategy in strategies:
        strategy_name = strategy.__name__.replace("fetch_", "").replace("_", " ").title()
        LOGGER.info(f"  尝试策略: {strategy_name}..." )
        
        content, source = strategy(url)
        if content:
            LOGGER.info(f"  内容抓取成功，来源: {source}")
            return content, source
            
    return None, None

# --- 主逻辑 ---

def main():
    LOGGER.info("程序启动：Medium Feed 信息抓取器")
    feeds_to_process = load_configs()
    if not feeds_to_process:
        LOGGER.error("未找到任何待处理的 Feed 配置。程序退出。" )
        return

    today_str = datetime.date.today().isoformat()
    
    for feed_url in feeds_to_process:
        LOGGER.info(f"\n开始处理 Feed: {feed_url}")
        category = determine_category(feed_url)
        
        output_dir = os.path.join(ARTICLES_DIR, today_str, sanitize_filename(category))
        if not os.path.exists(output_dir):
            os.makedirs(output_dir)
            LOGGER.info(f"创建目录: {output_dir}")
            
        d = feedparser.parse(feed_url)
        
        if not d.entries:
            LOGGER.warning(f"  Feed '{feed_url}' 未找到任何文章条目。" )
            continue

        entries = d.entries
        
        for i, entry in enumerate(entries):
            title = entry.title
            link = entry.link
            
            filename = f"{sanitize_filename(title)}.md"
            filepath = os.path.join(output_dir, filename)
            
            if os.path.exists(filepath):
                LOGGER.info(f"  跳过 (文章已存在): '{title}'" )
                continue

            LOGGER.info(f"\n  --- 处理文章 {i+1}: '{title}' ---")
            
            content, source = fetch_content_with_fallbacks(link)
            
            if content:
                try:
                    with open(filepath, 'w', encoding='utf-8') as f:
                        f.write(f"# {title}\n\n")
                        f.write(f"**源链接**: {link}\n")
                        f.write(f"**Feed**: {feed_url}\n")
                        f.write(f"**类别**: {category}\n")
                        f.write(f"**抓取来源**: {source}\n")
                        f.write(f"**抓取日期**: {datetime.datetime.now().isoformat()}\n\n")
                        f.write("---\n\n")
                        f.write(content)
                    LOGGER.info(f"  文章已保存至: {filepath}")
                except Exception as e:
                    LOGGER.error(f"  保存文章 '{title}' 到文件 '{filepath}' 失败: {e}")
            else:
                LOGGER.warning(f"  未能从所有来源抓取文章 '{title}' 的内容。" )
            
            time.sleep(1) # 礼貌性等待

if __name__ == "__main__":
    main()