import re
from typing import List, Set

class TextCleaner:
    """
    文本清洗工具类，用于集中管理噪音字典和清洗逻辑。
    """

    # 1. 全局替换噪音 (Global Replace)
    # 这些文本只要出现就会被移除，不限于整行
    GLOBAL_NOISE_PATTERNS = [
        r"Press enter or click to view image in full size",
        r"按Enter或点击以查看图片全尺寸",
    ]

    # 2. 精确行噪音 (Exact Line Match)
    # 如果一行经 strip() 后完全等于这些字符串，则视为噪音行删除
    EXACT_LINE_NOISE: Set[str] = {
        "Listen", 
        "Share", 
        "More", 
        "Open in app", 
        "Member-only story",
    }

    # 3. 包含特征噪音 (Contains Match)
    # 如果一行包含这些子串，则视为噪音行 (需谨慎使用，避免误杀)
    CONTAINS_NOISE_TOKENS: List[str] = [
        "min read", # 阅读时间，如 "5 min read"
    ]

    # 4. 正则行噪音 (Regex Line Match)
    # 如果一行匹配这些正则，则视为噪音行
    REGEX_LINE_PATTERNS: List[str] = [
        r"^\d+(\.\d+)?[KkMm]?$",  # 纯数字或带单位计数 (如 1.2K, 500)
    ]

    @classmethod
    def clean_global_noise(cls, content: str) -> str:
        """执行全局字符串替换清洗"""
        if not content:
            return content
        
        cleaned = content
        for pattern in cls.GLOBAL_NOISE_PATTERNS:
            cleaned = re.sub(pattern, "", cleaned)
        return cleaned

    @classmethod
    def is_noise_line(cls, line: str) -> bool:
        """判断单行文本是否为噪音"""
        stripped = line.strip()
        if not stripped:
            return False # 空行不由噪音过滤器处理，由结构过滤器处理

        # 1. 精确匹配
        if stripped in cls.EXACT_LINE_NOISE:
            return True

        # 2. 包含匹配
        # 特殊逻辑：Following + read 组合
        if "Following" in stripped and "read" in stripped:
            return True
            
        for token in cls.CONTAINS_NOISE_TOKENS:
            if token in stripped:
                return True

        # 3. 作者头像链接特征 (Medium 特有: [![Name](...)...](/@...))
        if stripped.startswith("[![") and "](/@" in stripped:
            return True

        # 4. 正则匹配
        for pattern in cls.REGEX_LINE_PATTERNS:
            if re.match(pattern, stripped):
                return True

        return False
