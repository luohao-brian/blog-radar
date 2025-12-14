import os
import json
import asyncio
import click
import logging
import datetime
from typing import List, Optional, Dict

from agent import Agent
from logger import setup_logging

class Translator:
    def __init__(self, config_path: str = "mcp-settings.json"):
        self.logger = setup_logging("blog_radar.Translator")
        self.config = self._load_config(config_path)
        
        # 初始化 Agent (翻译任务不需要 Tools)
        model_name = self.config.get("model_name", "doubao-seed-1-6-251015")
        self.agent = Agent(
            model_name=model_name, 
            mcp_config=None, # 纯翻译不需要 MCP
            allowed_tools=[] 
        )

    def _load_config(self, path: str) -> Dict:
        """加载 JSON 配置"""
        try:
            if not os.path.exists(path):
                # 尝试相对于脚本的路径
                script_dir = os.path.dirname(os.path.abspath(__file__))
                alt_path = os.path.join(script_dir, path)
                if os.path.exists(alt_path):
                    path = alt_path
            
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            self.logger.error(f"加载配置文件失败 {path}: {e}")
            return {}

    def get_translation_prompt(self, content: str) -> str:
        """返回翻译用的 Prompt"""
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

    def _split_text_smart(self, text: str, max_length: int) -> List[str]:
        """智能分段：按段落分割文本"""
        if len(text) <= max_length:
            return [text]
            
        paragraphs = text.split('\n\n')
        chunks = []
        current_chunk = []
        current_len = 0
        
        for p in paragraphs:
            p_len = len(p) + 2 # +2 for \n\n
            
            if current_len + p_len > max_length:
                if current_chunk:
                    chunks.append("\n\n".join(current_chunk))
                current_chunk = [p]
                current_len = p_len
            else:
                current_chunk.append(p)
                current_len += p_len
                
        if current_chunk:
            chunks.append("\n\n".join(current_chunk))
            
        return chunks

    async def translate_content(self, content: str) -> Optional[str]:
        """核心翻译逻辑，支持分片"""
        self.logger.info(f"正在翻译内容 (长度: {len(content)})...")
        
        # 模型最大输出支持 32k token (约 40-50k 字符)，保守设置为 20000 字符以兼顾效率与安全
        CHUNK_SIZE = 20000
        chunks = self._split_text_smart(content, CHUNK_SIZE)
        
        if len(chunks) > 1:
            self.logger.info(f"内容已拆分为 {len(chunks)} 个片段。")
        
        translated_chunks = []
        for i, chunk in enumerate(chunks):
            self.logger.debug(f"正在处理片段 {i+1}/{len(chunks)}...")
            prompt = self.get_translation_prompt(chunk)
            messages = [{"role": "user", "content": prompt}]
            
            try:
                resp = await self.agent.achat(messages)
                if resp:
                    translated_chunks.append(resp)
                else:
                    self.logger.warning(f"片段 {i+1} 返回为空。")
                    translated_chunks.append(f"\n[Translation Failed for Chunk {i+1}]\n{chunk}\n")
            except Exception as e:
                self.logger.error(f"片段 {i+1} 处理失败: {e}")
                translated_chunks.append(f"\n[Translation Error for Chunk {i+1}]\n")
        
        return "\n\n".join(translated_chunks)

    async def process_file(self, file_path: str):
        """处理单个文件"""
        self.logger.info(f"正在处理文件: {file_path}")
        
        if not os.path.exists(file_path):
            self.logger.error(f"文件未找到: {file_path}")
            return

        # 提前构建输出路径，检查是否存在
        # ./articles/{yyyy-mm-dd}/translated/filename_cn.md
        today_str = datetime.date.today().isoformat()
        output_dir = os.path.join("articles", today_str, "translated")
        
        base_name = os.path.basename(file_path)
        name_no_ext = os.path.splitext(base_name)[0]
        output_filename = f"{name_no_ext}_cn.md"
        output_path = os.path.join(output_dir, output_filename)

        if os.path.exists(output_path):
            self.logger.info(f"翻译已存在，跳过: {output_path}")
            return

        if not os.path.exists(output_dir):
            os.makedirs(output_dir)

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()
            
            # 简单检查，避免重复翻译或处理空文件
            if "_cn.md" in file_path:
                self.logger.info(f"跳过已翻译文件 (基于文件名判断): {file_path}")
                return
            
            # 简单的头部提取，以便复用元数据
            # 目前逻辑：直接翻译整个正文
            # 假设该文件是由 Retriever 保存的"原文"，它包含头部信息。
            # 我们可能希望保留头部，或者直接全部翻译？
            # Prompt 中要求清洗元数据。
            # 理想情况下，我们应该验证语言。
            
            # 我们先去除自定义头部（如果存在），以免被错误翻译
            # 头部通常由 Retrieve 模块添加:
            # # Title
            # **源链接**: ...
            # ...
            # ---
            
            body_content = content
            header_block = ""
            
            if "\n---\n" in content:
                parts = content.split("\n---\n", 1)
                header_block = parts[0] + "\n---\n"
                body_content = parts[1]
            
            translated_body = await self.translate_content(body_content)
            
            if not translated_body:
                self.logger.error(f"文件 {file_path} 翻译失败")
                return

            final_content = header_block + translated_body

            with open(output_path, "w", encoding="utf-8") as f:
                f.write(final_content)
                
            self.logger.info(f"翻译已保存至: {output_path}")

        except Exception as e:
            self.logger.error(f"处理文件 {file_path} 时发生错误: {e}")

    async def run(self, files: List[str]):
        if not files:
            self.logger.warning("未指定任何文件。")
            return

        self.logger.info(f"开始翻译 {len(files)} 个文件 (无并发限制)...")
        
        tasks = []
        for f in files:
            task = asyncio.create_task(self.process_file(f))
            # 使用默认参数绑定变量 f
            task.add_done_callback(lambda t, filename=f: self.logger.info(f"任务完成: {filename}"))
            tasks.append(task)
            
        await asyncio.gather(*tasks)
        self.logger.info("所有翻译任务已完成。")


@click.command()
@click.argument("files", nargs=-1, type=click.Path(exists=True))
def main(files):
    """
    批量翻译 Markdown 文件。
    支持直接传入多个文件路径 (e.g. *.md)。
    """
    if not files:
        print("请指定至少一个文件路径。", file=sys.stderr)
        return
        
    translator = Translator()
    asyncio.run(translator.run(files))

if __name__ == "__main__":
    main()
