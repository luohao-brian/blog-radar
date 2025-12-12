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

    async def process_single_feed(self, feed_url: str, limit: int = None):
        """处理单个 Feed 的所有文章"""
        self.logger.info(f"\n开始处理 Feed: {feed_url}")
        category = self.determine_category_from_feed(feed_url)

        # parse feed (sync)
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
            self.logger.info(f"  [Feed Item {i+1}]")
            await self.fetch_and_save(link, title, category, feed_url)

    async def run(self, url=None, rss=None, limit=None):
        """执行主逻辑"""
        self.logger.info("程序启动：Medium Retriever")

        async with self.agent:
            if url:
                # 处理单个 URL
                self.logger.info(f"\n开始抓取指定 URL: {url}")
                category = "single_url_fetch"

                # 简单提取标题
                path_parts = urlparse(url).path.split("/")
                title = path_parts[-1] if path_parts[-1] else "untitled_article"
                title = title.replace("-", "_")

                await self.fetch_and_save(url, title, category)

            elif rss:
                # 处理 RSS 列表
                feeds = self.load_feeds_from_file(rss)
                if not feeds:
                    self.logger.error(f"从文件 '{rss}' 未加载到任何 Feed。")
                    return

                for feed_url in feeds:
                    await self.process_single_feed(feed_url, limit)
            else:
                self.logger.error("请提供 --url 或 --rss 参数")

            # 等待所有后台任务完成
            if self.tasks:
                self.logger.info(f"等待 {len(self.tasks)} 个后台翻译任务完成...")
                await asyncio.gather(*self.tasks, return_exceptions=True)
                self.logger.info("所有后台任务已完成")


@click.command()
@click.option("--url", default=None, help="指定要抓取的单个文章 URL。")
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
        asyncio.run(retriever.run(url=url, rss=rss, limit=limit))
    except KeyboardInterrupt:
        print("\nInterrupted by user.")
    except Exception as e:
        if hasattr(retriever, "logger"):
            retriever.logger.error(f"Unexpected error: {e}")
        else:
            print(f"Unexpected error: {e}", file=sys.stderr)


if __name__ == "__main__":
    main()
