import yaml
import feedparser
import asyncio
import click
import sys
import datetime
from urllib.parse import urlparse

# 引入基类
from retriever import Retriever


class MediumRetriever(Retriever):
    """
    Medium 专用抓取器 (也可用于通用 RSS/Atom，逻辑相似)
    继承自 Retriever，增加了 Feed 解析和类别判断逻辑。
    """

    def __init__(self, config_path="mcp-settings.json"):
        super().__init__(config_path=config_path)

    def determine_category_from_feed(self, feed_url: str) -> str:
        """从 Feed URL 解析类别 (Medium 特有逻辑)"""
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
            self.logger.error(f"解析 Feed URL 类别失败 '{feed_url}': {e}")
            return "未知类别"

    def load_feeds_from_file(self, file_path: str) -> list:
        """加载 YAML 配置文件"""
        feeds = []
        self.logger.info(f"正在加载配置文件: {file_path}")
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
                if data and "feeds" in data:
                    feeds.extend(data["feeds"])
        except Exception as e:
            self.logger.error(f"加载配置文件 '{file_path}' 失败: {e}")
        return feeds

    async def fetch_with_sem(self, sem, *args, **kwargs):
        """带信号量的抓取 wrapper"""
        async with sem:
            await self.fetch_and_save(*args, **kwargs)

    async def process_single_feed(self, feed_url: str, tasks: list, sem: asyncio.Semaphore, limit: int = None):
        """处理单个 Feed 的所有文章 (添加任务到列表)"""
        self.logger.info(f"\n开始解析 Feed: {feed_url}")
        category = self.determine_category_from_feed(feed_url)

        # parse feed (sync) - feedparser is blocking, might want to run in executor if very slow, but usually fine
        d = feedparser.parse(feed_url)
        if not d.entries:
            self.logger.warning(f"  Feed '{feed_url}' 未找到任何文章条目。")
            return

        for i, entry in enumerate(d.entries):
            if limit and i >= limit:
                self.logger.info(f"  已达到限制 ({limit} 篇)，停止处理当前 Feed。")
                break

            title = entry.title
            link = entry.link
            
            # Add to tasks
            # tasks.append(
            #     self.fetch_with_sem(sem, link, title, category, feed_url)
            # )
            task = asyncio.create_task(self.fetch_with_sem(sem, link, title, category, feed_url))
            task.add_done_callback(lambda t, task_title=title: self.logger.info(f"任务完成: {task_title}"))
            tasks.append(task)
            
            self.logger.info(f"  [Feed Item {i+1}] 已加入抓取队列: {title}")

    async def run(self, urls=None, rss=None, limit=None):
        """执行主逻辑"""
        self.logger.info("程序启动：Medium Retriever (Concurrent)")

        # 限制并发数，防止浏览器或 LLM 过载
        CONCURRENCY_LIMIT = 3
        sem = asyncio.Semaphore(CONCURRENCY_LIMIT)
        tasks = []

        async with self.agent:
            if urls:
                # 处理 URL 列表
                for url in urls:
                    self.logger.info(f"加入 URL 任务: {url}")
                    category = "single_url_fetch"

                    # 简单提取标题
                    path_parts = urlparse(url).path.split("/")
                    title = path_parts[-1] if path_parts[-1] else "untitled_article"
                    title = title.replace("-", "_")

                    task = asyncio.create_task(self.fetch_with_sem(sem, url, title, category))
                    task.add_done_callback(lambda t, task_title=title: self.logger.info(f"任务完成: {task_title}"))
                    tasks.append(task)

            elif rss:
                # 处理 RSS 列表
                feeds = self.load_feeds_from_file(rss)
                if not feeds:
                    self.logger.error(f"从文件 '{rss}' 未加载到任何 Feed。")
                    return

                # 这里我们先解析所有 Feeds 生成任务列表
                for feed_url in feeds:
                    await self.process_single_feed(feed_url, tasks, sem, limit)
            else:
                self.logger.error("请提供 --url 或 --rss 参数")
                return

            if tasks:
                self.logger.info(f"\n开始执行 {len(tasks)} 个抓取任务 (并发度: {CONCURRENCY_LIMIT})...")
                await asyncio.gather(*tasks)
                self.logger.info("所有抓取任务已完成")
            else:
                self.logger.info("没有需要执行的任务。")


@click.command()
@click.option("--url", multiple=True, help="指定要抓取的单个文章 URL (可多次使用)。")
@click.option(
    "--rss", type=click.Path(exists=True), help="指定要加载的 RSS 配置文件 (YAML)。"
)
@click.option("--limit", default=None, type=int, help="每个 Feed 抓取的文章数量限制。")
def main(url, rss, limit):
    retriever = MediumRetriever()
    if not url and not rss:
        print("请提供 --url 或 --rss 参数", file=sys.stderr)
        sys.exit(1)

    try:
        asyncio.run(retriever.run(urls=url, rss=rss, limit=limit))
    except KeyboardInterrupt:
        print("\nInterrupted by user.")
    except Exception as e:
        if hasattr(retriever, "logger"):
            retriever.logger.error(f"Unexpected error: {e}")
        else:
            print(f"Unexpected error: {e}", file=sys.stderr)


if __name__ == "__main__":
    main()
