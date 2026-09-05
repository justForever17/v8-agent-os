"""Small format checks, not authority or factual-validity judgments."""

import re


_LABEL_CELL = re.compile(r"(?<=\|)\s*([^|\n:：]{1,60})\s*[:：]\s*[^|\n]{2,300}(?=\|)")
_TABLE_SEPARATOR = re.compile(r"^\s*\|(?:\s*:?-{3,}:?\s*\|){2,}\s*$")


def is_site_chrome_excerpt(text: str, *, question: str = "") -> bool:
    """Recognize a standalone footer, not paragraphs containing copyright law."""
    if re.search(r"网站(?:页脚|版权|备案|联系)|备案号|\b(?:footer|copyright notice|site owner)\b", question, re.I):
        return False
    if has_readable_table(text) or not re.search(r"版权所有|©|all rights reserved", text, re.I):
        return False
    prose = re.sub(r"https?://\S+|\b[\w.+-]+@[\w.-]+\b", "", text)
    if re.search(r"[。！？;；]|\.(?:\s|$)", prose):
        return False
    labels = ("联系我们", "返回顶部", "ICP备", "公网安备", "技术支持", "PC版", "手机版", "学习强国")
    return sum(label in text for label in labels) >= 2


def has_readable_table(text: str) -> bool:
    # Extractors often emit HTML record tables as one labelled value per row,
    # without a Markdown separator. Keep identity/name/status as source facts;
    # this does not imply that a record page contains the document's full text.
    if len({match.group(1).strip().casefold() for match in _LABEL_CELL.finditer(text)}) >= 2:
        return True
    lines = text.splitlines()
    for index, line in enumerate(lines[1:-1], start=1):
        if not _TABLE_SEPARATOR.fullmatch(line):
            continue
        columns = line.count("|")
        if all(
            row.strip().startswith("|") and row.strip().endswith("|")
            and row.count("|") == columns and re.search(r"\w", row)
            for row in (lines[index - 1], lines[index + 1])
        ):
            return True
    return False
