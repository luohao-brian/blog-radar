import os
import json
import asyncio
import datetime
import re
import sys
import logging
from abc import ABC, abstractmethod
from typing import Optional, List, Dict

from agent import Agent
from logger import setup_logging


class Retriever(ABC):
    """
    通用文章抓取基类 (Base Retriever)
    负责 Agent 初始化、MCP 配置加载、通用文件操作和核心抓取逻辑。
    """

    def __init__(
        self, config_path: str = "mcp-settings.json", output_dir: str = "articles"
    ):
        self.logger = setup_logging(f"blog_radar.{self.__class__.__name__}")
        self.config = self._load_config(config_path)
        self.output_base_dir = output_dir

        # 初始化 Agent
        # 模型名称目前硬编码，后续可由 config 传入
        model_name = self.config.get("model_name", "doubao-seed-1-6-251015")
        
        # 限制 Agent 使用的工具，减少上下文 Token 占用
        allowed_tools = [
            "navigate_page",
            "evaluate_script", 
            "take_snapshot"
        ]
        
        self.agent = Agent(
            model_name=model_name, 
            mcp_config=self.config.get("mcp_config"),
            allowed_tools=allowed_tools
        )
        self.tasks = set()  # Track async tasks

    def _load_config(self, path: str) -> Dict:
        """加载 JSON 配置文件"""
        try:
            # 处理相对路径
            if not os.path.exists(path):
                script_dir = os.path.dirname(os.path.abspath(__file__))
                alt_path = os.path.join(script_dir, path)
                if os.path.exists(alt_path):
                    path = alt_path

            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            self.logger.error(f"Failed to load config from {path}: {e}")
            # 如果是基类，配置加载失败可能导致无法运行，从简暂时 exit
            sys.exit(1)

    def sanitize_filename(self, name: str) -> str:
        """清洗文件名"""
        return re.sub(r"[\\/*?:\"<>|]", "", name).strip().replace(" ", "_")[:100]

    def save_article_to_file(
        self,
        title: str,
        url: str,
        category: str,
        content: str,
        feed_url: str = None,
        suffix: str = "",
    ) -> bool:
        """保存文章到 Markdown 文件"""
        today_str = datetime.date.today().isoformat()
        output_dir = os.path.join(
            self.output_base_dir, today_str, self.sanitize_filename(category)
        )

        if not os.path.exists(output_dir):
            os.makedirs(output_dir)
            self.logger.info(f"创建目录: {output_dir}")

        filename = f"{self.sanitize_filename(title)}{suffix}.md"
        filepath = os.path.join(output_dir, filename)

        if os.path.exists(filepath):
            self.logger.info(f"  跳过 (文章已存在): '{filename}'")
            return False

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
            self.logger.info(f"  文章已保存至: {filepath}")
            return True
        except Exception as e:
            self.logger.error(f"  保存文章 '{title}' 到文件 '{filepath}' 失败: {e}")
            return False

    async def fetch_article_content(self, url: str) -> Optional[str]:
        """
        核心逻辑：使用 Agent + MCP 抓取网页内容（仅提取，不翻译）
        优化：拦截工具输出直接获取内容，避免 LLM 复述
        """
        self.logger.info(f"正在抓取内容 (Agent + MCP): {url}")

        prompt = self.get_extraction_prompt(url)
        messages = [{"role": "user", "content": prompt}]

        try:
            # 使用新方法获取完整状态
            result_state = await self.agent.achat_with_tools_full(messages)
            
            messages_history = result_state.get("messages", [])
            content = None
            
            # Debug: 打印所有 ToolMessage 用于排查
            for i, msg in enumerate(messages_history):
                if hasattr(msg, "tool_call_id"):
                    self.logger.debug(f"MSG[{i}] ToolMessage: name={getattr(msg, 'name', 'N/A')}, content_len={len(str(msg.content))}")
            
            # 倒序查找 ToolMessage
            for msg in reversed(messages_history):
                if hasattr(msg, "tool_call_id") and hasattr(msg, "content"):
                     msg_name = getattr(msg, "name", "")
                     raw_content = msg.content
                     extracted_text = ""

                     # 提取文本内容
                     if isinstance(raw_content, list):
                         for item in raw_content:
                             if isinstance(item, dict) and item.get("type") == "text":
                                 extracted_text += item.get("text", "")
                     else:
                         extracted_text = str(raw_content)
                    
                     # 判读逻辑：
                     # 1. 如果有 name 属性且等于 evaluate_script，那就是它
                     # 2. 如果没有 name (兼容性)，则检查内容特征 (包含 "evaluate_script response" 或长度极大且不像 navigate 响应)
                     
                     is_target = False
                     if msg_name == "evaluate_script":
                         is_target = True
                     elif "navigate_page" in extracted_text and len(extracted_text) < 500:
                         # 肯定是导航响应，跳过
                         continue
                     elif len(extracted_text) > 200:
                         # 长度足够长，极有可能是文章
                         is_target = True
                    
                     if is_target:
                         content = extracted_text
                         self.logger.info(f"  已从 ToolMessage (name={msg_name}) 中拦截到原始内容，长度: {len(content)}")
                         break
            
            if content:
                # 清洗数据: 移除 "evaluate_script response..." 前缀
                if "Script ran on page and returned:" in content:
                    content = content.split("Script ran on page and returned:")[-1].strip()
                
                # 移除 Markdown 代码块标记 (```json ... ```)
                if content.startswith("```json"):
                    content = content[7:]
                if content.endswith("```"):
                    content = content[:-3]
                
                content = content.strip()
                
                # 处理 JSON 字符串转义: JS 返回的是带双引号的 JSON 字符串
                # 例如: "\n# Title\n\nContent..."
                if content.startswith('"') and content.endswith('"'):
                    try:
                        # 使用 json.loads 解码转义字符 (\n, \t 等)
                        content = json.loads(content)
                    except json.JSONDecodeError:
                        # Fallback: 手动处理最基本的转义
                        self.logger.warning("  JSON decode failed, attempting manual cleanup")
                        content = content[1:-1].replace('\\n', '\n').replace('\\"', '"')
                
                self.logger.info(f"  Fetch 最终提取内容预览 (前100字符): {content[:100].replace(chr(10), ' ')}...")
                return content
                
            # Fallback
            final_msg = messages_history[-1]
            return str(final_msg.content)

        except Exception as e:
            self.logger.error(f"  抓取失败: {e}")
            return None

    async def translate_article(self, content: str) -> Optional[str]:
        """
        翻译逻辑：将内容翻译为中文
        """
        self.logger.info(f"正在翻译内容 (Input Length: {len(content)})...")
        self.logger.debug(f"翻译输入预览: {content[:100].replace(chr(10), ' ')}...")
        
        prompt = self.get_translation_prompt(content)
        messages = [{"role": "user", "content": prompt}]

        try:
            # 清除 Graph 历史状态（如果可能），或者创建一个新的 Agent 实例
            # 但这里我们复用 Agent，所以要注意 Context 累积
            # 如果 Agent 是有记忆的，这里可能会把之前的 Tool Output 也带进去
            # 暂时假设 Prompt 足够强
            
            # 使用 achat (无 Tools) 进行纯文本翻译任务
            translated_content = await self.agent.achat(messages)
            if translated_content and len(translated_content) > 50:
                 return translated_content
            else:
                 self.logger.warning("  翻译结果为空或过短")
                 return None
        except Exception as e:
            self.logger.error(f"  翻译失败: {e}")
            return None

    def get_extraction_prompt(self, url: str) -> str:
        """返回抓取使用的 Prompt (优化版：JS 优先，禁用 Snaphot)"""
        js_code = """
        () => {
            const article = document.querySelector('article') || document.querySelector('main') || document.body;
            // 移除干扰元素
            const trash = ['script', 'style', 'iframe', 'noscript', 'header', 'footer', 'nav', '.ad', '.advertisement', '[role="complementary"]'];
            trash.forEach(sel => article.querySelectorAll(sel).forEach(el => el.remove()));
            // 提取 Markdown (简易版)
            let text = '';
            const walk = (node) => {
                if (node.nodeType === 3) { // Text
                    text += node.textContent;
                } else if (node.nodeType === 1) { // Element
                    const tag = node.tagName.toLowerCase();
                    if (tag === 'h1') text += `\\n# ${node.innerText}\\n\\n`;
                    else if (tag === 'h2') text += `\\n## ${node.innerText}\\n\\n`;
                    else if (tag === 'h3') text += `\\n### ${node.innerText}\\n\\n`;
                    else if (tag === 'p') text += `\\n${node.innerText}\\n\\n`;
                    else if (tag === 'li') text += `- ${node.innerText}\\n`;
                    else if (tag === 'pre' || tag === 'code') text += `\\n\`\`\`\\n${node.innerText}\\n\`\`\`\\n\\n`;
                    else Array.from(node.childNodes).forEach(walk);
                }
            };
            walk(article);
            return text;
        }
        """
        return f"""
        目标：使用 JavaScript 高效提取这篇文章的原始内容：{url}

        请严格按顺序执行以下 4 步：

        1. **导航 (Fast)**：
           - 调用 `navigate_page` 打开链接。
           - **必须设置 `timeout` 参数为 15000** (15秒)，防止页面卡死。

        2. **提取 (JS Injection)**：
           - **直接且仅使用** `evaluate_script` 工具。
           - 将以下代码作为 `function` 参数传入（完全复制，不要修改）：
             {js_code}

        3. **验证**：
           - 检查 `evaluate_script` 的返回值。
           - 如果返回值包含有效的 Markdown 文本（通常很长），说明提取成功。
           - **严禁** 调用 `take_snapshot`，除非 `evaluate_script` 报错或返回空字符串。

        4. **输出**：
           - **CRITICAL**: 如果提取成功，**不要**在回复中包含文章内容。
           - 仅回复一个单词：`SUCCESS`。
           - 如果失败，回复错误原因。
        """

    def get_translation_prompt(self, content: str) -> str:
        """返回翻译使用的 Prompt"""
        return f"""
        目标：将以下 Markdown 内容翻译为专业、流畅的中文，并清理排版。

        内容：
        {content}

        要求：
        1. **清洗与排版**：
           - **去除噪音**：在翻译过程中，智能识别并去除原文中混入的网页 UI 元素文本（如 "Listen", "Share", "Follow", "Just now", "min read", "Press enter to view" 等）。
           - **格式规范**：修复多余的空行，确保段落之间只有一行空行。
        2. **准确翻译**：
           - 准确传达原意，行文流畅，符合中文技术阅读习惯。
           - **术语保留**：对于专业术语 (Technical Terms)、特有概念或不确定的表达，必须采用 **"中文翻译 (Original English Phrase)"** 的格式。例如："提示工程 (Prompt Engineering)" 或 "上下文感知 (Context Awareness)"。
        3. **结构保持**：保持原文的核心 Markdown 结构（标题、代码块、列表），但可以根据上述清洗要求微调。
        4. **输出**：仅返回翻译后的 Markdown 内容，不要包含任何额外的解释。
        """

    async def fetch_and_save(
        self, url: str, title: str, category: str, feed_url: str = None
    ) -> bool:
        """组合方法：抓取 -> 保存原文 -> 翻译 -> 保存译文"""
        self.logger.info(f"\n  --- 处理文章: '{title}' ---")

        # 检查原文是否已存在（避免重复抓取）
        today_str = datetime.date.today().isoformat()
        original_filename = f"{self.sanitize_filename(title)}.md"
        output_dir = os.path.join(
            self.output_base_dir, today_str, self.sanitize_filename(category)
        )
        original_filepath = os.path.join(output_dir, original_filename)

        content = None
        if os.path.exists(original_filepath):
             self.logger.info(f"  原文已存在，跳过抓取: '{original_filename}'")
             # 如果原文存在，尝试读取它以便后续翻译（如果译文不存在）
             # 但为了简单，这里假设如果原文存在就不重新抓取了。
             # 除非我们想只补全译文。这是一个优化点。
             # 现在的逻辑：如果原文存在，我们假设不需要再做任何事，或者读取它？
             # 让我们读取它，以防译文需要生成。
             try:
                 with open(original_filepath, "r", encoding="utf-8") as f:
                     # Skip frontmatter/headers we added
                     lines = f.readlines()
                     # Find where content starts (after "---")
                     try:
                        sep_idx = lines.index("---\n")
                        content = "".join(lines[sep_idx+2:]) # +2 to skip --- and blank line
                     except ValueError:
                        content = "".join(lines)
             except Exception:
                 pass
        else:
            # 抓取原文
            content = await self.fetch_article_content(url)
            if content:
                self.save_article_to_file(title, url, category, content, feed_url, suffix="")
            else:
                self.logger.warning(f"  未能抓取文章 '{title}' 的内容。")
                return False

        # 翻译并保存
        if content:
            # 检查译文是否存在
            cn_filename = f"{self.sanitize_filename(title)}_cn.md"
            cn_filepath = os.path.join(output_dir, cn_filename)
            if os.path.exists(cn_filepath):
                self.logger.info(f"  译文已存在，跳过翻译: '{cn_filename}'")
                return True

            # 异步触发翻译任务
            task = asyncio.create_task(
                self.process_translation(title, url, category, content, feed_url, output_dir, cn_filename)
            )
            self.tasks.add(task)
            task.add_done_callback(self.tasks.discard)
            self.logger.info(f"  已启动后台翻译任务: {title}")
            return True
        
        return False

    async def process_translation(self, title, url, category, content, feed_url, output_dir, cn_filename):
        """后台翻译任务处理"""
        cn_filepath = os.path.join(output_dir, cn_filename)
        if os.path.exists(cn_filepath):
             return

        translated_content = await self.translate_article(content)
        if translated_content:
            self.save_article_to_file(title, url, category, translated_content, feed_url, suffix="_cn")
        
        return False

    async def run_context(self):
        """Helper to use in async with"""
        return self.agent
