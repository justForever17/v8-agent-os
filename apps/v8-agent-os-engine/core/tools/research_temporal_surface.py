from __future__ import annotations

import re


_TEMPORAL_QUESTION = re.compile(
    r"日期|何时|时间表|生效|施行|实施时间|发布时间|\b(?:date|when|timeline|effective|commencement)\b",
    re.IGNORECASE,
)
_DATED_STATEMENT = re.compile(
    r"\d{4}\s*(?:年|[-/])\s*\d{1,2}\s*(?:月|[-/])\s*\d{1,2}|"
    r"\b(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2}\b",
    re.IGNORECASE,
)
_DATE_ROLE = re.compile(
    r"发布|公布|施行|实施|生效|截止|修订|废止|\b(?:publish\w*|release\w*|effective|effect|force|expire\w*|deadline)\b",
    re.IGNORECASE,
)


def is_requested_date_evidence(question: str, excerpt: str) -> bool:
    """Rank an already-read dated statement, not its age, validity or authority."""
    return bool(_TEMPORAL_QUESTION.search(question) and _DATED_STATEMENT.search(excerpt) and _DATE_ROLE.search(excerpt))


RESEARCH_TEMPORAL_JUDGMENT_INSTRUCTION = (
    "资料时效由写作 Agent 按问题、领域变化速度、正文内容和适用版本评估；"
    "日期年龄、缺失、无法解析或晚于观察时点本身都不是禁止写入或拒绝答案的依据。"
    "运行依赖可能快速变化，政策也可能长期适用或已被修订，两者都不能套用固定期限。"
    "区分公告日、转载日、实施日与未来生效安排；说明影响结论的时效边界，不得伪造缺失日期，"
    "也不能把检索时间或草案当作已经生效的证明。"
)


def undated_source_boundary(*, role: str, chinese: bool) -> str:
    """Missing page metadata is not proof that the body has no dated facts."""
    if chinese:
        attribution = "二手资料须保留归属；" if role == "secondary" else ""
        return (
            attribution + "未解析到页面发布日期或版本；不据此断言资料过期或现行。"
            "正文中的日期事实与适用范围须按绑定摘录核验。"
        )
    attribution = "Secondary material must remain attributed; " if role == "secondary" else ""
    return (
        attribution + "no page publication date or version was resolved; this establishes neither "
        "obsolescence nor current applicability. Verify dated facts in the body and their scope "
        "against the bound excerpts."
    )
