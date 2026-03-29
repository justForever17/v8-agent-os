"""
Scope 自动检测器 — 根据消息内容推断 memory scope

Scope 优先级: project:{name} > app:{type} > global
"""

import re
from typing import Optional


# 关键词 → scope 映射
_CODING_KEYWORDS = [
    "代码", "函数", "变量", "类型", "bug", "报错", "异常", "调试", "debug",
    "API", "接口", "部署", "deploy", "git", "commit", "push", "merge",
    "数据库", "SQL", "ORM", "模型", "schema", "migration",
    "import", "export", "module", "package", "依赖", "npm", "pip",
    "TypeScript", "Python", "JavaScript", "React", "Next.js", "FastAPI",
    "编译", "构建", "build", "test", "测试", "CI", "Docker",
    "重构", "refactor", "优化", "performance", "缓存", "cache",
    "算法", "数据结构", "排序", "递归", "async", "await",
    "endpoint", "route", "middleware", "controller", "service",
    "component", "hook", "state", "props", "render",
]

_CHAT_KEYWORDS = [
    "聊聊", "闲聊", "心情", "感觉", "开心", "难过", "无聊",
    "哈哈", "嘻嘻", "😂", "🤣", "❤️", "👍",
    "你好", "早安", "晚安", "谢谢", "辛苦",
    "推荐", "建议", "看法", "觉得", "认为",
    "今天", "昨天", "周末", "假期", "天气",
]

_WRITING_KEYWORDS = [
    "写作", "文章", "文档", "文案", "翻译", "translate",
    "README", "文档", "说明", "教程", "博客", "blog",
    "小说", "故事", "剧本", "台词", "角色",
    "摘要", "总结", "报告", "周报", "月报",
]

# 预编译正则：检测代码块
_CODE_BLOCK_RE = re.compile(r'```\w*\n', re.MULTILINE)
_IMPORT_RE = re.compile(r'\b(import|from|require|const|let|var|def|class|function)\b')


def detect_scope(user_message: str, project_name: Optional[str] = None) -> str:
    """
    基于消息内容自动推断 memory scope。
    
    优先级:
    1. 显式 project 上下文 → project:{name}
    2. 代码块/导入语句 → app:coding
    3. 关键词匹配 → app:{type}
    4. 默认 → global
    """
    if not user_message:
        return "global"
    
    msg_lower = user_message.lower()
    
    # 1. 如果有明确的 project 上下文
    if project_name:
        return f"project:{project_name}"
    
    # 2. 代码块检测（强信号）
    if _CODE_BLOCK_RE.search(user_message) or _IMPORT_RE.search(user_message):
        return "app:coding"
    
    # 3. 关键词评分
    coding_score = sum(1 for kw in _CODING_KEYWORDS if kw.lower() in msg_lower)
    chat_score = sum(1 for kw in _CHAT_KEYWORDS if kw.lower() in msg_lower)
    writing_score = sum(1 for kw in _WRITING_KEYWORDS if kw.lower() in msg_lower)
    
    max_score = max(coding_score, chat_score, writing_score)
    
    if max_score == 0:
        return "global"
    
    if coding_score == max_score:
        return "app:coding"
    elif writing_score == max_score:
        return "app:writing"
    elif chat_score == max_score:
        return "app:chat"
    
    return "global"
