import os
import sys
import asyncio
import re
import yaml
import click
import datetime
from typing import List

from agent import Agent
from logger import setup_logging

PROMPT_EVALUATION_SYSTEM = """
你是一位严格的技术文章评审专家。请根据以下四个维度对给定的技术文章进行打分（0-100分）。

**评分维度（各占25分）：**
1. **问题具体性 (Specific Problem)**: 文章是否提出了具体的问题和应用场景？
2. **场景描述 (Scenario Detail)**: 是否对具体场景进行了详细描述和说明？
3. **解决方案 (Concrete Solution)**: 是否给出了针对该场景和问题的具体解决方案（代码/Prompt/步骤）？
4. **可验证性 (Verifiable Metrics)**: 是否提供了可验证、可度量的指标或评测结果？

**关键指令：**
- 必须且只能返回一段 **YAML** 格式的文本。
- 严禁包含 Markdown 标记（如 ```yaml ... ```）。
- 严禁包含任何前言、后语或解释性文字。
- 如果找不到原文引用，lists 必须为空 []。

**YAML 输出模板（请严格填充）：**
score: <0-100的整数>
analysis:
  problem:
    evaluation: "<评价内容>"
    quotes:
      - "<原文引用1>"
      - "<原文引用2>"
  scenario:
    evaluation: "<评价内容>"
    quotes: []
  solution:
    evaluation: "<评价内容>"
    quotes: []
  metrics:
    evaluation: "<评价内容>"
    quotes: []
reasoning_summary: "<评分理由>"
overall_summary: "<一句话综述>"
"""

PROMPT_RETRY_FORMAT_ERROR = """
输出格式错误。请仅输出标准的 YAML 格式，包含 score, analysis, reasoning_summary, overall_summary 字段。
不要输出 Markdown 标题或其他文本。
"""

def sanitize_filename(name):
    """清洗文件名：仅保留字母、数字、中文、下划线和连字符"""
    # 1. 替换特殊连字符和空格为下划线
    name = name.replace(" ", "_").replace("—", "_").replace("–", "_")
    
    # 2. 移除非安全字符 (保留 \w: [a-zA-Z0-9_] 和汉字, 以及 -)
    safe_name = re.sub(r"[^\w\-]", "_", name)
    
    # 3. 合并连续下划线
    safe_name = re.sub(r"_+", "_", safe_name)
    
    # 4. 去除首尾下划线并截断
    return safe_name.strip("_")[:100]


def extract_yaml_from_text(text):
    """尝试从混合文本中提取 YAML 代码块"""
    match = re.search(r"```yaml\s*(.*?)\s*```", text, re.DOTALL)
    if match:
        return match.group(1)
    match = re.search(r"```\s*(.*?)\s*```", text, re.DOTALL)
    if match:
        return match.group(1)
    if "score:" in text:
        return text
    return text


def validate_yaml(text):
    """验证文本是否包含有效的评分 YAML"""
    try:
        clean = extract_yaml_from_text(text)
        data = yaml.safe_load(clean)
        if isinstance(data, dict) and "score" in data:
            return True, data
    except Exception:
        pass
    return False, None


class Evaluator:
    def __init__(self):
        self.logger = setup_logging("blog_radar.Evaluator")
        self.agent = Agent(model_name="doubao-seed-1-6-flash-250828")

    async def evaluate_article(self, content, article_title, retry=True):
        try:
            messages = [
                {"role": "system", "content": PROMPT_EVALUATION_SYSTEM},
                {"role": "user", "content": content},
            ]
            
            content_out = await self.agent.achat(messages)
            
            is_valid, _ = validate_yaml(content_out)
            
            if not is_valid and retry:
                self.logger.warning(f"[{article_title}] 模型输出格式不正确，正在重试...")
                messages.append({"role": "assistant", "content": content_out})
                messages.append({"role": "user", "content": PROMPT_RETRY_FORMAT_ERROR})
                
                content_out = await self.agent.achat(messages)
            
            return content_out

        except Exception as e:
            self.logger.error(f"[{article_title}] 评估核心逻辑出错: {e}")
            return None

    async def process_file(self, file_path):
        self.logger.info(f"正在处理文件: {file_path}")
        
        if not os.path.exists(file_path):
            self.logger.error(f"文件未找到: {file_path}")
            return

        # 从文件路径中提取文章标题
        article_title = os.path.splitext(os.path.basename(file_path))[0]
        
        # 构建评估结果文件的路径
        # 固定目录: ./articles/{yyyy-mm-dd}/eval/{title}.yaml
        today_str = datetime.date.today().isoformat()
        output_dir = os.path.join("articles", today_str, "eval")
        
        if not os.path.exists(output_dir):
            os.makedirs(output_dir)
        
        output_filename = f"{sanitize_filename(article_title)}.yaml"
        output_path = os.path.join(output_dir, output_filename)
        
        if os.path.exists(output_path):
            self.logger.info(f"评估结果已存在，跳过: {output_path}")
            return

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()

            result_yaml = await self.evaluate_article(content, article_title)
            
            if result_yaml:
                clean_yaml_content = extract_yaml_from_text(result_yaml)
                with open(output_path, "w", encoding="utf-8") as f:
                    f.write(clean_yaml_content)
                self.logger.info(f"评估结果已保存至: {output_path}")
            else:
                self.logger.error(f"[{article_title}] 评估失败，未获取到结果")
                
        except Exception as e:
            self.logger.error(f"处理文件 {file_path} 失败: {e}")

    async def run(self, files: List[str]):
        if not files:
            self.logger.warning("未指定任何文件。")
            return

        self.logger.info(f"开始评估 {len(files)} 个文件 (无并发限制)...")
        
        tasks = []
        for f in files:
            task = asyncio.create_task(self.process_file(f))
            task.add_done_callback(lambda t, filename=f: self.logger.info(f"任务完成: {filename}"))
            tasks.append(task)
            
        await asyncio.gather(*tasks)
        self.logger.info("所有评估任务已完成。")


@click.command()
@click.argument("files", nargs=-1, type=click.Path(exists=True))
def main(files):
    """
    批量评估技术文章。
    支持直接传入多个文件路径 (e.g. *.md)。
    """
    if not files:
        print("请指定至少一个文件路径。", file=sys.stderr)
        return
        
    evaluator = Evaluator()
    asyncio.run(evaluator.run(files))


if __name__ == "__main__":
    main()
