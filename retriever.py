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
from text_cleaner import TextCleaner


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
        
        # 浏览器操作互斥锁：防止并发导致的状态冲突（串号）
        # 因 MCP Server 控制的浏览器实例通常只有一个活动页面上下文
        self._lock = asyncio.Lock()

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
        """清洗文件名：仅保留字母、数字、中文、下划线和连字符"""
        # 1. 替换特殊连字符和空格为下划线
        name = name.replace(" ", "_").replace("—", "_").replace("–", "_")
        
        # 2. 移除非安全字符 (保留 \w: [a-zA-Z0-9_] 和汉字, 以及 -)
        safe_name = re.sub(r"[^\w\-]", "_", name)
        
        # 3. 合并连续下划线
        safe_name = re.sub(r"_+", "_", safe_name)
        
        # 4. 去除首尾下划线并截断
        return safe_name.strip("_")[:100]

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
        注意：加锁确保浏览器操作原子性
        """
        async with self._lock:
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
                    if content.startswith('"') and content.endswith('"'):
                        try:
                            content = json.loads(content)
                        except json.JSONDecodeError:
                            self.logger.warning("  JSON decode failed, attempting manual cleanup")
                            content = content[1:-1].replace('\\n', '\n').replace('\\"', '"')

                    # Medium 专用 Header 清洗
                    content = self._clean_medium_header(content)
                    
                    # --- 内容验证 ---
                    if not self._validate_content(content):
                        self.logger.warning("  内容验证未通过 (Invalid Content)")
                        return None
                    
                    self.logger.info(f"  Fetch 最终提取内容预览 (前100字符): {content[:100].replace(chr(10), ' ')}...")
                    return content
                    
                # Fallback
                final_msg = messages_history[-1]
                content = str(final_msg.content)
                
                # 对 Fallback 内容也做验证
                if self._validate_content(content):
                     return content
                else:
                     self.logger.warning("  Fallback 内容验证未通过")
                     return None

            except Exception as e:
                self.logger.error(f"  抓取失败: {e}")
                return None

    def _clean_medium_header(self, content: str) -> str:
        """
        专用过滤器: 清洗 Medium 的头部元数据 (Author, Following, Date, etc.)
        目标格式: [AuthorName](https://medium.com/@AuthorID)
        """
        # 1. 尝试提取作者信息
        # 匹配模式: [Name](/@id?...) 或 [Name](https://medium.com/@id?...)
        # 注意: 这里的 regex 需要适配 JS 提取出的 Markdown 格式
        # 常见格式: [![Name](img)](link) [Name](link) Following...
        
        # 查找作者链接 (通常在开头 1000 字符内)
        head_section = content[:2000]
        
        # Regex to capture Name and ID from a link like [Name](/@id?...)
        # We look for the patterns typically generated by the walker for the byline
        author_match = re.search(r"\[([^\]]+)\]\((?:https://medium\.com)?/@([^?/\)]+)", head_section)
        
        clean_header = ""
        if author_match:
            name = author_match.group(1).strip()
            user_id = author_match.group(2).strip()
            # 构造标准链接
            clean_header = f"[{name}](https://medium.com/@{user_id})\n\n"
        
        # 2. 移除干扰块
        lines = content.split('\n')
        new_lines = []
        
        header_processed = False
        is_in_noise_block = False
        
        for i, line in enumerate(lines):
            stripped = line.strip()
            
            # 标题行直接保留
            if stripped.startswith("# "):
                new_lines.append(line)
                continue
                
            # 如果是空行，保留
            if not stripped:
                new_lines.append(line)
                continue
            
            # 使用 TextCleaner 判断噪音行
            is_noise = TextCleaner.is_noise_line(line)
                
            if is_noise:
                # 只有当我们还没有插入 clean header 时，如果是干扰块的一部分，我们替换它
                if not header_processed and clean_header:
                    new_lines.append(clean_header)
                    header_processed = True
                is_in_noise_block = True
                # Skip this line
                continue
            else:
                # 遇到非干扰行
                # 如果是看起来像正文的段落，或者代码块，或者或者列表
                new_lines.append(line)

        # Re-assemble
        result = "\n".join(new_lines).strip()
        
        # 全局清洗 (替换行内噪音)
        return TextCleaner.clean_global_noise(result)

    def _validate_content(self, content: str) -> bool:
        """
        验证抓取内容是否有效
        1. 检查长度
        2. 检查错误关键词
        3. 检查 Medium 头部特征 (Author, Date, Read time 等)
        """
        if not content:
            return False
            
        # 1. 长度检查
        if len(content) < 200:
            self.logger.warning("  验证失败: 内容过短 (< 200 chars)")
            return False
            
        # 2. 错误关键词检查
        error_keywords = [
            "Navigation failed", 
            "net::ERR_", 
            "Page not found", 
            "One more step", 
            "Checking if the site connection is secure",
            "404 Not Found",
            "Sign in"
        ]
        for kw in error_keywords:
            if kw in content:
                self.logger.warning(f"  验证失败: 发现错误关键词 '{kw}'")
                return False
                
        # 3. Medium Header 特征检查 (可选，过于严格可能会误杀)
        # 检查是否包含类似 "min read" 或 "Following" 等特征
        # 或者检查是否包含 Markdown 的一级标题
        # 我们用一个宽松的规则：如果是 Medium 文章，通常会有 "min read"
        # 但有些非 Medium 域名的文章可能没有。我们只并在内容较短时作为辅助判断。
        if "min read" not in content and len(content) < 1000:
             self.logger.warning("  验证警示: 未发现 'min read' 且内容较短，可能非完整文章。")
             # 这里暂不强制返回 False，以免误杀 RSS 聚合的短文，但这是一个信号
             
        return True

    def _detect_language(self, content: str) -> str:
        """
        简单语言检测
        Return: 'zh', 'en', or 'other'
        """
        # 采样前 1000 字符
        sample = content[:1000]
        
        # 1. 检测中文: 统计中文字符比例
        # 常用汉字 Unicode 范围: \u4e00-\u9fff
        zh_chars = re.findall(r'[\u4e00-\u9fff]', sample)
        if len(zh_chars) > len(sample) * 0.05: # 如果超过 5% 是中文
             if len(zh_chars) > 10:
                 return 'zh'
        
        # 2. 检测英文: 简单的 ASCII 字母统计
        # 如果不是中文，且含有大量英文字符
        en_chars = re.findall(r'[a-zA-Z]', sample)
        if len(en_chars) > len(sample) * 0.5:
            return 'en'
            
        return 'other'

    def get_extraction_prompt(self, url: str) -> str:
        """返回抓取使用的 Prompt (优化版：JS 优先，禁用 Snaphot)"""
        js_code = """
        () => {
            const article = document.querySelector('article') || document.querySelector('main') || document.body;
            // 移除干扰元素
            const trash = ['script', 'style', 'iframe', 'noscript', 'header', 'footer', 'nav', '.ad', '.advertisement', '[role="complementary"]', 'button', 'label'];
            trash.forEach(sel => article.querySelectorAll(sel).forEach(el => el.remove()));
            
            // 提取 Markdown (递归遍历以保留链接和图片)
            let text = '';
            
            // 辅助函数：清理文本节点的空白
            const cleanText = (str) => {
                return str.replace(/[\\n\\t]+/g, ' ').replace(/\\s+/g, ' ');
            };

            const walk = (node) => {
                if (node.nodeType === 3) { // Text
                    text += cleanText(node.textContent);
                } else if (node.nodeType === 1) { // Element
                    const tag = node.tagName.toLowerCase();
                    
                    if (tag === 'h1') { text += `\\n# `; Array.from(node.childNodes).forEach(walk); text += `\\n\\n`; }
                    else if (tag === 'h2') { text += `\\n## `; Array.from(node.childNodes).forEach(walk); text += `\\n\\n`; }
                    else if (tag === 'h3') { text += `\\n### `; Array.from(node.childNodes).forEach(walk); text += `\\n\\n`; }
                    else if (tag === 'p') { text += `\\n`; Array.from(node.childNodes).forEach(walk); text += `\\n\\n`; }
                    else if (tag === 'li') { text += `- `; Array.from(node.childNodes).forEach(walk); text += `\\n`; }
                    else if (tag === 'ul' || tag === 'ol') { Array.from(node.childNodes).forEach(walk); text += `\\n`; }
                    else if (tag === 'pre' || tag === 'code') { 
                        // Code block / Inline code: 使用 innerText 保留原有格式
                        // 简单的判断：如果是 pre，视为代码块；code 视为行内(除非在 pre 内)
                        if (tag === 'pre') text += `\\n\\`\\`\\`\\n${node.innerText}\\n\\`\\`\\`\\n\\n`;
                        else text += `\\`${node.innerText}\\``; 
                    }
                    else if (tag === 'a') {
                        const href = node.getAttribute('href');
                        text += `[`; 
                        Array.from(node.childNodes).forEach(walk); 
                        text += `](${href})`;
                    }
                    else if (tag === 'img') {
                        const src = node.getAttribute('data-src') || node.getAttribute('src');
                        const alt = node.getAttribute('alt') || '';
                        if (src) text += `![${alt}](${src})`;
                    }
                    else if (tag === 'table') { text += `\n\n`; Array.from(node.childNodes).forEach(walk); text += `\n\n`; }
                    else if (tag === 'tr') { text += `\n- `; Array.from(node.childNodes).forEach(walk); }
                    else if (tag === 'td' || tag === 'th') { text += ` `; Array.from(node.childNodes).forEach(walk); }
                    else if (tag === 'br') {
                        text += '\\n';
                    }
                    else if (tag === 'div' || tag === 'section' || tag === 'span') {
                        // 通用容器，直接递归
                        Array.from(node.childNodes).forEach(walk);
                    }
                    else {
                        // 其他标签，直接递归
                        Array.from(node.childNodes).forEach(walk);
                    }
                }
            };
            
            walk(article);
            // 后处理：清理多余的换行
            return text.replace(/\\n{3,}/g, '\\n\\n').trim();
        }
        """
        return f"""
        目标：使用 JavaScript 高效提取这篇文章的原始内容：{url}

        请严格按顺序执行以下 4 步：

        1. **导航 (Fast)**：
           - 调用 `navigate_page` 打开链接。
           - **必须设置 `timeout` 参数为 30000** (30秒)，防止页面卡死。

        2. **滚动 (Lazy Load)**:
           - 在提取前，必须确保图片已加载。
           - 请执行 `evaluate_script` 运行 `window.scrollTo(0, document.body.scrollHeight);` 并**等待**至少 3 秒。

        3. **提取 (JS Injection)**：
           - **直接且仅使用** `evaluate_script` 工具。
           - 将以下代码作为 `function` 参数传入（完全复制，不要修改）：
             {js_code}

        4. **验证**：
           - 检查 `evaluate_script` 的返回值。
           - 如果返回值包含有效的 Markdown 文本（通常很长），说明提取成功。
           - **严禁** 调用 `take_snapshot`，除非 `evaluate_script` 报错或返回空字符串。

        5. **输出**：
           - **CRITICAL**: 如果提取成功，**不要**在回复中包含文章内容。
           - 仅回复一个单词：`SUCCESS`。
           - 如果失败，回复错误原因。
        """

    async def fetch_and_save(
        self, url: str, title: str, category: str, feed_url: str = None
    ) -> bool:
        """组合方法：抓取 -> 保存原文 (不翻译)"""
        self.logger.info(f"\\n  --- 处理文章: '{title}' ---")

        # 检查原文是否已存在（避免重复抓取）
        today_str = datetime.date.today().isoformat()
        original_filename = f"{self.sanitize_filename(title)}.md"
        output_dir = os.path.join(
            self.output_base_dir, today_str, self.sanitize_filename(category)
        )
        original_filepath = os.path.join(output_dir, original_filename)

        if os.path.exists(original_filepath):
             self.logger.info(f"  原文已存在，跳过抓取: '{original_filename}'")
             return True
        else:
            # 抓取原文 (带重试机制)
            max_retries = 3
            content = None
            
            for attempt in range(max_retries):
                if attempt > 0:
                    self.logger.info(f"  重试抓取 ({attempt+1}/{max_retries})...")
                    # 线性退避，避免过于频繁
                    await asyncio.sleep(2 * attempt)
                    
                content = await self.fetch_article_content(url)
                
                if content:
                    break
                else:
                    self.logger.warning(f"  抓取尝试 {attempt+1} 失败。")
            
            if not content:
                self.logger.warning(f"  在 {max_retries} 次尝试后仍未能抓取文章 '{title}' 的内容。")
                return False

            # --- 语言检测 ---
            lang = self._detect_language(content)
            self.logger.info(f"  语言检测结果: {lang}")
            
            if lang == 'other':
                self.logger.error(f"  文章语言非中英文 ({lang})，停止处理: {title}")
                return False
                
            # 保存原文 (无论中英文都保存)
            self.save_article_to_file(title, url, category, content, feed_url, suffix="")

        return True

    async def run_context(self):
        """Helper to use in async with"""
        return self.agent
