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
logger = setup_logging(name="douyin_video")


def sanitize_filename(name: str) -> str:
    """清洗文件名：优化版 (User Preference: Hyphen style)"""
    if not name:
        return "video_download"
    
    # 0. 移除常见的后缀
    name = name.replace(" - 抖音", "")
    
    # 1. 直接移除的字符
    name = re.sub(r"[\"\'\(\)\[\]\{\}“”‘’（）【】《》「」]", "", name)

    # 2. 替换为连字符的字符
    name = re.sub(r"[\s\|\:\,\.\!\?\,\。\！\？\、_]+", "-", name)
    
    # 3. 移除非安全字符 (保留 \w 和 -)
    name = re.sub(r"[^\w\-]", "", name)
    
    # 4. 合并连续连字符
    name = name.replace("_", "-")
    name = re.sub(r"\-+", "-", name)
    
    return name.strip("-")[:100] or "video_download"


def download_stream(url, filename, desc):
    """流式下载文件"""
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": "https://www.douyin.com/",
        "Origin": "https://www.douyin.com",
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
                        ):
                            percent = (downloaded / total_size) * 100
                            print(
                                f"\r  进度: {percent:.1f}% ({downloaded//1024//1024}MB / {total_size//1024//1024}MB)",
                                end="",
                            )
            print() 
        logger.info(f"{desc} 下载完成: {filename}")
        return True
    except Exception as e:
        logger.error(f"下载失败 {desc}: {e}")
        return False



# 全局浏览器信号量，确保同一时刻只有一个任务在操作 MCP 嗅探（浏览器）
browser_sem = asyncio.Semaphore(1)

async def sniff_video_streams(target_url, model_name="doubao-seed-1-6-251015"):
    """使用 MCP Agent 嗅探音视频流地址"""

    mcp_config = {
        "command": "npx",
        "args": ["chrome-devtools-mcp@latest", "--browser-url=http://127.0.0.1:9333"],
    }

    allowed_tools = [
        "navigate_page",
        "list_network_requests",
        "evaluate_script",
        "wait_for",
        "new_page",
        "list_pages",
        "close_page",
    ]

    # 在信号量保护下，连接并使用浏览器
    async with browser_sem:
        agent = Agent(
            model_name=model_name, mcp_config=mcp_config, allowed_tools=allowed_tools
        )

        async with agent:
            logger.info(f"正在通过 Chrome 嗅探页面: {target_url}")

            prompt = f"""
            任务：深入分析抖音视频页面的网络请求，提取视频真实下载地址。
            
            目标页面：{target_url}
            
            背景：抖音 Web 端通常会发起一个较大的 MP4 文件请求（video/mp4），或者是音视频分离的流。
            
            执行步骤：
            1. **打开页面**: 
               - 使用 `new_page` 确保环境干净。
               - 使用 `navigate_page` 访问目标 URL。
            
            2. **交互与等待 (至关重要)**:
               - 页面加载后，可能会有登录弹窗或静音状态。
               - 请先等待 5 秒 (`evaluate_script`: `await new Promise(r => setTimeout(r, 5000))`)。
               - **尝试点击**: 使用 `evaluate_script` 模拟点击页面中心，触发播放或关闭遮罩。
                 例如: `try {{ document.elementFromPoint(window.innerWidth/2, window.innerHeight/2).click(); }} catch(e) {{}}`
               - 再强制等待 10 秒，让媒体流请求完全发出。
            
            3. **捕获请求**:
               - 使用 `list_network_requests`。注意：如果筛选资源类型，必须使用 ``["media"]``，**严禁**使用 ``["video"]`` (会导致参数错误)。
               - 筛选不仅是 `video` 或 `avc1`，也要注意 `mime_type` 为 `video/mp4` 的所有请求。
               - 对于抖音，往往有一个请求是完整的视频文件。优先找这个。
            
            4. **获取信息**:
               - 提取页面标题。
            
            5. **输出结果**:
               请严格返回如下 JSON 格式，不要包含 Markdown 标记：
               {{
                 "video_url": "HTTP_URL",
                 "audio_url": "HTTP_URL_OR_EMPTY",
                 "title": "PAGE_TITLE"
               }}
               
               - 如果找到了单一的 MP4 视频文件（既包含画面也包含声音），填入 video_url，audio_url 留空。
               - 如果是分流（视频流+音频流），分别填入。
               - 务必选择 URL 最长、包含 token/sign 参数的链接。
            """

            messages = [{"role": "user", "content": prompt}]

            logger.info("发送嗅探指令给 Agent...")
            try:
                response = await agent.achat_with_tools(messages)
                logger.info("Agent 嗅探结束")

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

                if video_url:
                    logger.info("嗅探成功，正在清理/关闭页面...")
                    try:
                        cleanup_prompt = f"""
                        SYSTEM_TASK: CLEANUP
                        1. Call `list_pages` to find the tab for {target_url}
                        2. Call `close_page` with that ID.
                        """
                        close_msg = [{"role": "user", "content": cleanup_prompt}]
                        await agent.achat_with_tools(close_msg)
                    except Exception as e:
                        logger.warning(f"关闭页面时遇到轻微错误: {e}")

                return video_url, audio_url, title

            except Exception as e:
                logger.error(f"嗅探过程出错: {e}")
                return None, None, None




import time

async def process_single_video(url, output, is_batch=False):
    """处理单个视频的下载流程"""
    # 1. 嗅探
    video_url, audio_url, title = await sniff_video_streams(url)

    if not video_url:
        logger.error(f"未能在此页面找到视频流链接: {url}")
        return False

    logger.info(
        f"捕获到的流地址:\n  Video: {video_url[:60]}...\n  Audio: {audio_url[:60] if audio_url else 'None'}..."
    )

    # Determine Output Path
    final_output = output
    # 如果是默认值或者处于批量模式，自动从标题生成文件名
    if final_output == "downloaded_video.mp4" or is_batch:
        today = datetime.date.today().isoformat()
        safe_title = sanitize_filename(title)
        video_dir = os.path.join("videos", today)
        os.makedirs(video_dir, exist_ok=True)
        final_output = os.path.join(video_dir, f"{safe_title}.mp4")
        logger.info(f"自动生成输出路径: {final_output}")

    # 2. 下载
    tmp_dir = os.path.join("videos", "tmp")
    os.makedirs(tmp_dir, exist_ok=True)

    # 使用时间戳避免临时文件冲突
    ts = int(time.time())
    temp_video = os.path.join(tmp_dir, f"douyin_temp_video_{ts}.mp4")
    temp_audio = os.path.join(tmp_dir, f"douyin_temp_audio_{ts}.m4a")
    
    if not download_stream(video_url, temp_video, "视频流"):
        return False

    has_audio = False
    if audio_url:
        if download_stream(audio_url, temp_audio, "音频流"):
            has_audio = True

    # 3. 合并或重命名
    success = False
    if has_audio:
        logger.info("正在合并音视频流 (ffmpeg)...")
        try:
            cmd = [
                "ffmpeg", "-y",
                "-i", temp_video,
                "-i", temp_audio,
                "-c", "copy",
                final_output,
            ]
            subprocess.run(
                cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE
            )
            logger.info(f"✅ 合并成功！文件已保存: {final_output}")
            success = True
        except subprocess.CalledProcessError as e:
            logger.error(f"ffmpeg 合并失败: {e.stderr.decode()}")
        finally:
            if os.path.exists(temp_video): os.remove(temp_video)
            if os.path.exists(temp_audio): os.remove(temp_audio)
    else:
        logger.info("未找到独立音频流，假设视频流包含音频或仅为纯视频。")
        output_dir = os.path.dirname(final_output)
        if output_dir and not os.path.exists(output_dir):
            os.makedirs(output_dir, exist_ok=True)

        if os.path.exists(final_output):
            os.remove(final_output)
        os.rename(temp_video, final_output)
        logger.info(f"文件已保存: {final_output}")
        success = True
        
    return success


@click.command()
@click.option("--url", "-u", required=True, multiple=True, help="视频页面 URL (支持多个 --url)")
@click.option("--output", "-o", default="downloaded_video.mp4", help="输出文件名 (仅在单 URL 时有效)")
def main(url, output):
    """
    抖音视频下载器 (基于 Chrome DevTool MCP)
    支持批量下载：uv run douyin_video_downloader.py -u URL1 -u URL2
    """
    urls = url # click returns a tuple for multiple=True
    total = len(urls)
    
    if total > 1:
        logger.info(f"开始批量下载任务，共 {total} 个视频")
    
    for i, target_url in enumerate(urls, 1):
        if total > 1:
            print(f"\n>>> [{i}/{total}] 正在下载: {target_url}")
            
        success = asyncio.run(process_single_video(target_url, output, is_batch=(total > 1)))
        
        if total > 1:
            if success:
                print(f"<<< [{i}/{total}] 下载成功")
            else:
                print(f"<<< [{i}/{total}] 下载失败")
            
            # 批量下载时增加短暂间隔
            if i < total:
                time.sleep(1)

