"""AI website discovery, kept separate from article fetching and Research judgment."""
from __future__ import annotations

import hashlib
from urllib.parse import urlparse

CHAT_SITES = {"metaso": "https://metaso.cn/", "chatgpt": "https://chatgpt.com/"}


def search_chat_page(*, provider: str, query: str, limit: int, timeout_seconds: float, reuse_profile: bool) -> dict:
    from core.storage import storage
    from runtimes.computer_use.browser_automation import agent_browser_automation

    agent_browser_automation.configure(dict(storage.get_computer_use_config() or {}))
    try:
        raw = agent_browser_automation.query_chat_page(provider=provider, query=query,
            timeout_seconds=timeout_seconds, reuse_profile=reuse_profile)
    except RuntimeError as exc:
        if str(exc) != "agent_browser_chat_verification_required":
            raise
        return {"ok": False, "failureClass": "provider_challenge", "error": str(exc), "retryable": False,
                "recommendedNextAction": "该网站正在要求浏览器验证；请在 Agent 浏览器完成验证后再试。当前状态不证明登录态失效，请勿重复提交查询。"}
    text = str(raw.get("text") or "")
    if raw.get("ok") is not True or not text or raw.get("querySubmitted") is not True:
        return {"ok": False, "failureClass": "no_results", "error": "agent_browser_chat_no_answer"}
    references = []
    for item in raw.get("references") or []:
        url = str(item.get("url") or "")
        parsed = urlparse(url)
        if parsed.scheme in {"http", "https"} and parsed.hostname and not parsed.username and not parsed.password:
            references.append({"title": str(item.get("title") or parsed.hostname), "url": url,
                               "snippet": "Citation surfaced by the AI website; open the source to verify its contents.",
                               "discoveredBy": provider})
    answer = {"text": text, "url": raw.get("url") or CHAT_SITES[provider],
              "title": f"{provider}: {query}", "sourceKind": "ai_generated_answer",
              "completion": raw.get("completion"), "textTruncated": bool(raw.get("textTruncated")),
              "originalContentChars": max(len(text), int(raw.get("textChars") or len(text))),
              "referencesCount": max(len(references), int(raw.get("referencesCount") or len(references))),
              "referencesTruncated": bool(raw.get("referencesTruncated")),
              "contentHash": hashlib.sha256(text.encode()).hexdigest(), "citationsVerified": False,
              "references": references, "contextReused": raw.get("contextReused") is True}
    answer["blockedEmbeddedHosts"] = raw.get("blockedEmbeddedHosts") or []
    return {"ok": True, "query": query, "provider": provider, "results": references[:max(1, min(limit, 10))],
            "resultCount": min(len(references), max(1, min(limit, 10))), "webChatAnswer": answer,
            "warnings": ["AI 网页回答是二手材料；其中的来源链接尚未被独立实读，不能冒充官方原文。"],
            "fetchMode": "browser_chat"}


def captured_chat_source(payload: dict, *, allowed_domains=(), blocked_domains=(), site_domains=(), source_intent="") -> tuple[list, list]:
    """Preserve the body just observed, including guest pages that cannot reload.

    Only the trusted acquisition result calls this, never an Agent-supplied text
    field. These snapshots retain the AI-site identity, not the cited publisher.
    """
    answer = payload.get("webChatAnswer") or {}
    text, url = str(answer.get("text") or ""), str(answer.get("url") or "")
    host = urlparse(url).hostname or ""
    matches = lambda domains: any(host == d or host.endswith('.' + d) for d in domains)
    if (not text or answer.get("sourceKind") != "ai_generated_answer"
            or hashlib.sha256(text.encode()).hexdigest() != answer.get("contentHash")
            or (blocked_domains and matches(blocked_domains)) or (allowed_domains and not matches(allowed_domains))
            or (site_domains and not matches(site_domains)) or source_intent in {"primary", "official", "official_primary"}):
        return [], []
    metadata = {"sourceKind": "ai_generated_answer", "citationsVerified": False,
                "completion": answer.get("completion"), "contentHash": answer["contentHash"]}
    result = {"title": answer.get("title"), "url": url, "finalUrl": url, "snippet": text[:600],
              "sourceKind": "ai_generated_answer", "readSelectionReason": "captured_browser_answer"}
    read = {"ok": True, "url": url, "finalUrl": url, "title": answer.get("title"), "text": text,
            "sourceRole": "secondary", "sourceKind": "ai_generated_answer",
            "acquisitionState": answer.get("completion"),
            "textPreview": text[:1200], "contentChars": len(text),
            "originalContentChars": max(len(text), int(answer.get("originalContentChars") or len(text))),
            "links": answer.get("references") or [], "metadata": metadata, "omittedChars": 0,
            "extractionQuality": {"status": "partial" if answer.get("completion") == "partial" else "usable"},
            "warnings": payload.get("warnings") or [], "readReuse": "captured_browser_answer"}
    read["omittedChars"] = read["originalContentChars"] - len(text)
    read["textTruncated"] = bool(answer.get("textTruncated") or read["omittedChars"])
    if read["textTruncated"]:
        read["warnings"] = [*read["warnings"], "The browser capture is truncated; this is not the entire answer."]
    read["metadata"].update(textTruncated=read["textTruncated"], referencesTruncated=bool(answer.get("referencesTruncated")))
    return [result], [read]
