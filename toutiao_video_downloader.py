import asyncio
import json
import logging
import os
import sys
import subprocess
import requests
import click
import datetime
from urllib.parse import urlparse
from agent import Agent
import re
from logger import setup_logging

# 配置日志
logger = setup_logging(name="toutiao_video")


def sanitize_filename(name: str) -> str:
    """清洗文件名：优化版 (User Preference: Hyphen style)
    1. 移除常见的后缀
    2. 移除引号、括号
    3. 将冒号、问号、空格等分隔符转换为连字符 '-'
    4. 移除其他非法字符
    """
    if not name:
        return "video_download"
    
    # 0. 移除常见的后缀
    name = name.replace(" - 今日头条", "")
    
    # 1. 直接移除的字符 (引号, 括号)
    # include: " ' ( ) [ ] { } “ ” ‘ ’ （ ） 【 】 《 》 「 」
    name = re.sub(r"[\"\'\(\)\[\]\{\}“”‘’（）【】《》「」]", "", name)

    # 2. 替换为连字符的字符 (空格, 常见分隔符: - | : ， 。 ！ ？ 、)
    # Note: We replace them with a SINGLE hyphen
    name = re.sub(r"[\s\|\:\,\.\!\?\,\。\！\？\、_]+", "-", name)
    
    # 3. 移除非安全字符 (保留 \w: [a-zA-Z0-9] 和汉字 和 -)
    # We allow hyphen now. And we remove underscore from allowed list if we want pure hyphen style?
    # Let's keep underscore in regex but we already replaced specific chars with hyphen.
    # regex \w includes underscore.
    name = re.sub(r"[^\w\-]", "", name)
    
    # 4. 合并连续连字符 (以及可能残留的下划线转连字符?)
    # 用户倾向于使用 - 
    name = name.replace("_", "-")
    name = re.sub(r"\-+", "-", name)
    
    # 5. 去除首尾连字符并截断
    return name.strip("-")[:100] or "video_download"


def download_stream(url, filename, desc):
    """流式下载文件"""
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": "https://www.toutiao.com/",  # 有些流需要 Referer
        "Origin": "https://www.toutiao.com",
    }

    logger.info(f"开始下载 {desc} ...")
    try:
        with requests.get(url, headers=headers, stream=True, timeout=30) as r:
            r.raise_for_status()
            total_size = int(r.headers.get("content-length", 0))
            downloaded = 0

            with open(filename, "wb") as f:
                for chunk in r.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        if (
                            total_size > 0 and downloaded % (1024 * 1024) == 0
                        ):  # Print every MB
                            percent = (downloaded / total_size) * 100
                            print(
                                f"\r  进度: {percent:.1f}% ({downloaded//1024//1024}MB / {total_size//1024//1024}MB)",
                                end="",
                            )
            print()  # Newline
        logger.info(f"{desc} 下载完成: {filename}")
        return True
    except Exception as e:
        logger.error(f"下载失败 {desc}: {e}")
        return False


async def sniff_video_streams(target_url, model_name="doubao-seed-1-6-251015"):
    """使用 MCP Agent 嗅探音视频流地址"""

    # MCP 配置
    mcp_config = {
        "command": "npx",
        "args": ["chrome-devtools-mcp@latest", "--browser-url=http://127.0.0.1:9333"],
    }

    # 允许的工具
    allowed_tools = [
        "navigate_page",
        "list_network_requests",
        "evaluate_script",
        "wait_for",
        "new_page",
        "list_pages",
        "close_page",
    ]

    agent = Agent(
        model_name=model_name, mcp_config=mcp_config, allowed_tools=allowed_tools
    )

    async with agent:
        logger.info(f"正在通过 Chrome 嗅探页面: {target_url}")

        prompt = f"""
        任务：深入分析该页面的网络请求，找到完整的视频下载方案。
        
        目标页面：{target_url}
        
        背景信息：
        现在的视频网站（如今日头条/抖音/B站）通常采用 DASH 技术，将视频画面（Video Stream）和音频声音（Audio Stream）分开传输。
        目标是解析出这两个分流的直接下载地址。
        
        请执行以下步骤：
        1. 使用 `new_page` 创建一个新页面（防止 No page selected 错误），然后使用 `navigate_page` 打开目标页面。
        2. **等待逻辑**: 请使用 `evaluate_script` 本身进行 15 秒的强制等待（例如执行 `await new Promise(r => setTimeout(r, 15000))` 或类似逻辑），**切勿**使用 `wait_for` 工具等待某个 DOM 选择器，因为这极易导致超时错误。确保视频开始播放且请求已发出即可。
        3. 使用 `list_network_requests` 获取所有网络请求。
        4. **关键步骤**：仔细分析请求列表，寻找以下两类 URL：
           - **视频流 (Video Stream)**: 通常包含 `video`, `avc1`, `mime_type=video_mp4`，且体积较大。
           - **音频流 (Audio Stream)**: 通常包含 `audio`, `aac`, `mp4a`，或者 `mime_type=audio_mp4`。
           - 请优先选择没有过期时间或签名的长链接，或者最新捕获的链接。
        5. **提取标题**: 获取页面标题。**如果标题包含“：”或“_”等分隔符，请尝试提取冒号后或最核心的描述部分，去除修饰性前缀（如“xxx的奇迹：...”）。**
        
        6. 输出报告：
           不要废话，请仅以纯 JSON 格式输出结果，不要包含 Markdown 标记（如 ```json ... ```）：
           {{
             "video_url": "URL_STRING_HERE",
             "audio_url": "URL_STRING_HERE",
             "title": "PAGE_TITLE_HERE"
           }}
           
           如果只找到其中一个，另一个留空字符串。如果都找不到，返回空 JSON。
        """

        messages = [{"role": "user", "content": prompt}]

        logger.info("发送嗅探指令给 Agent...")
        try:
            response = await agent.achat_with_tools(messages)
            logger.info("Agent 嗅探结束")

            # 清理 Markdown 代码块标记（如果 Agent 还是输出了）
            cleaned_response = response.strip()
            if cleaned_response.startswith("```json"):
                cleaned_response = cleaned_response[7:]
            if cleaned_response.endswith("```"):
                cleaned_response = cleaned_response[:-3]

            video_url, audio_url, title = None, None, None

            try:
                data = json.loads(cleaned_response)
                video_url = data.get("video_url")
                audio_url = data.get("audio_url")
                title = data.get("title")
            except json.JSONDecodeError:
                logger.error(f"无法解析 Agent 返回的 JSON: {response}")

            # 如果成功获取视频链接，尝试关闭页面
            if video_url:
                logger.info("嗅探成功，正在清理/关闭页面...")
                try:
                    # 构造更明确的清理 Prompt，因为 Agent 没有上下文记忆
                    cleanup_prompt = f"""
                    SYSTEM_TASK: CLEANUP
                    1. Call `list_pages` to see all open tabs.
                    2. Find the tab that matches the URL: {target_url}
                       (It might be slightly different due to redirects, so look for the main part).
                    3. Call `close_page` with the ID of that tab.
                    """
                    close_msg = [{"role": "user", "content": cleanup_prompt}]
                    await agent.achat_with_tools(close_msg)
                except Exception as e:
                    logger.warning(f"关闭页面时遇到轻微错误 (不影响后续流程): {e}")

            return video_url, audio_url, title

        except Exception as e:
            logger.error(f"嗅探过程出错: {e}")
            return None, None, None


@click.command()
@click.option("--url", required=True, help="视频页面 URL")
@click.option("--output", "-o", default="downloaded_video.mp4", help="输出文件名")
def main(url, output):
    """
    基于 Chrome DevTool MCP 的视频下载器。
    自动嗅探 DASH 视频流和音频流，下载并合并。
    """

    # 1. 嗅探
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    video_url, audio_url, title = loop.run_until_complete(sniff_video_streams(url))

    if not video_url:
        logger.error("未能在此页面找到视频流链接。")
        sys.exit(1)

    logger.info(
        f"捕获到的流地址:\n  Video: {video_url[:60]}...\n  Audio: {audio_url[:60] if audio_url else 'None'}..."
    )

    # Determine Output Path
    if output == "downloaded_video.mp4":
        today = datetime.date.today().isoformat()
        # Sanitize title
        safe_title = sanitize_filename(title)

        # Structure: videos/YYYY-MM-DD/Title.mp4
        video_dir = os.path.join("videos", today)
        os.makedirs(video_dir, exist_ok=True)
        output = os.path.join(video_dir, f"{safe_title}.mp4")
        logger.info(f"自动生成输出路径: {output}")

    # 2. 下载
    tmp_dir = os.path.join("videos", "tmp")
    os.makedirs(tmp_dir, exist_ok=True)

    temp_video = os.path.join(tmp_dir, "temp_video.mp4")
    temp_audio = os.path.join(tmp_dir, "temp_audio.m4a")

    if not download_stream(video_url, temp_video, "视频流"):
        sys.exit(1)

    has_audio = False
    if audio_url:
        if download_stream(audio_url, temp_audio, "音频流"):
            has_audio = True

    # 3. 合并
    if has_audio:
        logger.info("正在合并音视频流 (ffmpeg)...")
        try:
            cmd = [
                "ffmpeg",
                "-y",
                "-i",
                temp_video,
                "-i",
                temp_audio,
                "-c",
                "copy",
                output,
            ]
            # 隐藏 ffmpeg 冗长的输出，只显示错误
            subprocess.run(
                cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE
            )
            logger.info(f"✅ 合并成功！文件已保存: {output}")
        except subprocess.CalledProcessError as e:
            logger.error(f"ffmpeg 合并失败: {e.stderr.decode()}")
        finally:
            # 清理临时文件
            if os.path.exists(temp_video):
                os.remove(temp_video)
            if os.path.exists(temp_audio):
                os.remove(temp_audio)
    else:
        logger.warning("未找到音频流，仅保存视频画面。")
        # Ensure output directory exists
        output_dir = os.path.dirname(output)
        if output_dir and not os.path.exists(output_dir):
            os.makedirs(output_dir, exist_ok=True)

        if os.path.exists(output):
            os.remove(output)
        os.rename(temp_video, output)
        logger.info(f"文件已保存: {output}")


if __name__ == "__main__":
    main()
