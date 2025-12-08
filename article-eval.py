from agent import Agent
from logger import setup_logging
import os
import sys
import json
import yaml
import click
import datetime
import re

# 配置日志
logger = setup_logging("article_eval")

# --- Prompts ---

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
    # 移除文件名中不允许的字符，替换空格为下划线，并限制长度
    return re.sub(r'[\\/*?:"<>|]', "", name).strip().replace(" ", "_")[:100]


def extract_yaml_from_text(text):
    """尝试从混合文本中提取 YAML 代码块"""
    # 尝试匹配 ```yaml ... ```
    match = re.search(r"```yaml\s*(.*?)\s*```", text, re.DOTALL)
    if match:
        return match.group(1)
    # 尝试匹配 ``` ... ```
    match = re.search(r"```\s*(.*?)\s*```", text, re.DOTALL)
    if match:
        return match.group(1)
    # 如果没有代码块，尝试直接寻找 score: 开头的内容
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


def fallback_parse_score(text):
    """最后的手段：尝试用正则提取分数和综述"""
    try:
        score_match = re.search(r"score:\s*(\d+)", text)
        summary_match = re.search(r"overall_summary:\s*(.+)", text)

        if score_match:
            return {
                "score": int(score_match.group(1)),
                "overall_summary": (
                    summary_match.group(1)
                    if summary_match
                    else "解析失败，无法提取综述"
                ),
                "analysis": {},
                "reasoning_summary": "YAML 解析失败，仅提取到分数。",
            }
    except Exception:
        pass
    return None


def evaluate_article(agent, content, article_title, retry=True):
    try:
        messages = [
            {"role": "system", "content": PROMPT_EVALUATION_SYSTEM},
            {"role": "user", "content": content},
        ]
        
        content_out = agent.chat(messages)
        
        is_valid, _ = validate_yaml(content_out)
        
        if not is_valid and retry:
            logger.warning("模型输出格式不正确，正在重试...")
            messages.append({"role": "assistant", "content": content_out})
            messages.append({"role": "user", "content": PROMPT_RETRY_FORMAT_ERROR})
            
            content_out = agent.chat(messages)
        
        return content_out

    except Exception as e:
        logger.error(f"评估失败: {e}")
        sys.exit(1)


@click.command()
@click.argument("file_path", type=click.Path(exists=True, dir_okay=False))
def main(file_path):
    """
    一个技术文章评分工具。
    根据预定义维度对文章进行评分，并提供详细分析和原文引用。
    """
    logger.info(f"正在评估文章: {file_path} ...")

    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()

    # 从文件路径中提取文章标题
    article_title = os.path.splitext(os.path.basename(file_path))[0]
    
    # 构建评估结果文件的路径 (只计算一次)
    output_dir = os.path.dirname(file_path)
    output_filename = f"{sanitize_filename(article_title)}_eval.yaml"
    output_path = os.path.join(output_dir, output_filename)
    
    # 检查评估结果是否已存在
    if os.path.exists(output_path):
        logger.info(f"评估结果已存在，跳过: {output_path}")
        return

    # 初始化 Agent，使用默认模型
    agent = Agent(model_name="doubao-seed-1-6-flash-250828")

    result_yaml = evaluate_article(
        agent, content, article_title
    )
    print(result_yaml)
    
    # 保存评估结果到文件
    try:
        # 清理 YAML 内容（去除可能的 markdown 标记）以便保存纯净的 YAML
        clean_yaml_content = extract_yaml_from_text(result_yaml)
        
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(clean_yaml_content)
        logger.info(f"评估结果已保存至: {output_path}")
    except Exception as e:
        logger.error(f"保存评估结果失败: {e}")



if __name__ == "__main__":
    main()

