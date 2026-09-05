"""Pure discovery-query repairs; never change the evidence question or gates."""

import re


def focused_discovery_query(query: str) -> str:
    """Recover an explicit title/name buried in a noisy CJK keyword query.

    Public search can ignore the subject in long keyword lists. A quoted
    contiguous name is a bounded alternative, not an inferred fact or URL.
    Callers retain the original evidence question for scope/date validation.
    Existing site restrictions and version/identifier tokens are preserved;
    standalone years stay in the original evidence question, not this broad
    document-discovery probe. English and
    already focused queries keep their established discovery path.
    """
    titles = re.findall(r"《([^《》\n]{4,80})》", query)
    if len(titles) > 1:
        return ""  # A combined task must be decomposed by its existing planner.
    if titles:
        anchor = titles[0].strip()
    else:
        # Only whitespace-delimited names, not pieces sliced from sentences.
        anchor = next((part for part in query.split() if re.fullmatch(r"[\u4e00-\u9fff]{8,64}", part)), "")
    if not anchor or query.strip().strip('"') == anchor:
        return ""
    constraints = re.findall(r"(?<!\S)site:[^\s]+|(?<!\S)[A-Za-z0-9_.-]*\d[A-Za-z0-9_.-]*(?!\S)", query)
    constraints = [value for value in constraints if not re.fullmatch(r"(?:19|20)\d{2}", value)]
    return " ".join([f'"{anchor}"', *dict.fromkeys(constraints)])
