"""Small format checks, not authority or factual-validity judgments."""

import re


_LABEL_CELL = re.compile(r"(?<=\|)\s*([^|\n:：]{1,60})\s*[:：]\s*[^|\n]{2,300}(?=\|)")
_TABLE_SEPARATOR = re.compile(r"^\s*\|(?:\s*:?-{3,}:?\s*\|){2,}\s*$")


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
