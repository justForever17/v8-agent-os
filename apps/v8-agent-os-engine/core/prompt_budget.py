from __future__ import annotations

from dataclasses import dataclass
import math


DEFAULT_SUPERVISOR_PROMPT_BUDGET_TOKENS = 10_000
DEFAULT_WORKSPACE_RULES_BUDGET_TOKENS = 10_000


@dataclass(frozen=True)
class PromptBudgetResult:
    source: str
    text: str
    estimated_tokens: int
    budget_tokens: int
    truncated: bool = False
    save_rejected: bool = False
    omitted_reason: str = ""

    def diagnostic(self) -> dict[str, object]:
        return {
            "source": self.source,
            "estimatedTokens": self.estimated_tokens,
            "budgetTokens": self.budget_tokens,
            "truncated": self.truncated,
            "saveRejected": self.save_rejected,
            "omittedReason": self.omitted_reason,
        }


def estimate_prompt_tokens(text: object) -> int:
    """Conservative model-agnostic token estimate for prompt budgeting.

    CJK characters are counted one-for-one; non-CJK visible characters are
    roughly four characters per token. This intentionally over-estimates mixed
    prompt/rules content so budgets fail safe without binding to one tokenizer.
    """

    raw = str(text or "")
    if not raw:
        return 0
    cjk_count = 0
    non_cjk_visible = 0
    for char in raw:
        codepoint = ord(char)
        if (
            0x4E00 <= codepoint <= 0x9FFF
            or 0x3400 <= codepoint <= 0x4DBF
            or 0x3040 <= codepoint <= 0x30FF
            or 0xAC00 <= codepoint <= 0xD7AF
        ):
            cjk_count += 1
        elif not char.isspace():
            non_cjk_visible += 1
    return cjk_count + int(math.ceil(non_cjk_visible / 4))


def truncate_to_estimated_tokens(text: object, budget_tokens: int) -> str:
    raw = str(text or "")
    budget = max(0, int(budget_tokens or 0))
    if not raw or estimate_prompt_tokens(raw) <= budget:
        return raw
    low = 0
    high = len(raw)
    best = ""
    while low <= high:
        mid = (low + high) // 2
        candidate = raw[:mid].rstrip()
        if estimate_prompt_tokens(candidate) <= budget:
            best = candidate
            low = mid + 1
        else:
            high = mid - 1
    return best.rstrip()


def enforce_prompt_budget(
    *,
    source: str,
    text: object,
    budget_tokens: int,
    truncate: bool,
    omission_reason: str = "",
) -> PromptBudgetResult:
    raw = str(text or "")
    budget = max(0, int(budget_tokens or 0))
    estimated = estimate_prompt_tokens(raw)
    if estimated <= budget:
        return PromptBudgetResult(
            source=source,
            text=raw,
            estimated_tokens=estimated,
            budget_tokens=budget,
        )
    if not truncate:
        return PromptBudgetResult(
            source=source,
            text=raw,
            estimated_tokens=estimated,
            budget_tokens=budget,
            save_rejected=True,
            omitted_reason=omission_reason or "prompt_budget_exceeded",
        )
    truncated_text = truncate_to_estimated_tokens(raw, budget)
    return PromptBudgetResult(
        source=source,
        text=truncated_text,
        estimated_tokens=estimated,
        budget_tokens=budget,
        truncated=True,
        omitted_reason=omission_reason or "prompt_budget_truncated",
    )
