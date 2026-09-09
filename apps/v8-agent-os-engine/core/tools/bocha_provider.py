"""Small adapter for Bocha's official Web Search API.

The source router owns credentials, provider ordering, and deadlines.  This
module only performs one bounded request and converts the documented
``webPages.value`` records into V8's common search result shape.
"""
from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

import requests


BOCHA_WEB_SEARCH_ENDPOINT = "https://api.bochaai.com/v1/web-search"


def _text(value: Any, limit: int) -> str:
    return str(value or "").strip()[:limit]


def _status_failure(code: int, *, http_status: int) -> dict[str, Any]:
    category = {401: "credential_rejected", 402: "provider_quota_exhausted", 403: "provider_access_denied", 429: "provider_rate_limited"}.get(code)
    category = category or ("provider_server_error" if code >= 500 else "provider_http_error")
    return {"ok": False, "failureClass": category, "reason": f"bocha_status_{code}",
            "retryable": code == 429 or code >= 500, "statusCode": http_status, "providerCode": code}


def bocha_search(query: str, *, api_key: str, limit: int, timeout_seconds: float) -> dict[str, Any]:
    if not api_key:
        return {"ok": False, "failureClass": "credential_missing", "reason": "missing_BOCHA_API_KEY", "retryable": False}
    count = max(1, min(int(limit or 5), 50))
    try:
        response = requests.post(
            BOCHA_WEB_SEARCH_ENDPOINT,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Accept": "application/json",
                "Content-Type": "application/json",
                "User-Agent": "V8 Agent OS Source Router/1.0",
            },
            json={"query": query, "freshness": "noLimit", "summary": True, "count": count},
            timeout=max(1.0, float(timeout_seconds)),
            allow_redirects=False,
        )
    except requests.Timeout:
        return {"ok": False, "failureClass": "network_timeout", "reason": "bocha_request_timeout", "retryable": True}
    except requests.RequestException:
        return {"ok": False, "failureClass": "provider_network_error", "reason": "bocha_request_failed", "retryable": True}
    if not 200 <= response.status_code < 300:
        return _status_failure(response.status_code, http_status=response.status_code)
    try:
        payload = response.json()
    except ValueError:
        return {"ok": False, "failureClass": "provider_format_unavailable", "reason": "bocha_response_not_json", "retryable": False, "statusCode": response.status_code}
    # The official SDK reads data.webPages.value. The landing-page example
    # shows the unwrapped SearchResponse; neither shape permits an error code
    # to masquerade as successful results.
    if isinstance(payload, dict) and "code" in payload and payload["code"] != 200:
        code = payload["code"]
        if type(code) is int:
            return _status_failure(code, http_status=response.status_code)
        return {"ok": False, "failureClass": "provider_format_unavailable", "reason": "bocha_invalid_response_code", "retryable": False}
    data = payload.get("data", payload) if isinstance(payload, dict) else None
    web_pages = data.get("webPages") if isinstance(data, dict) else None
    raw_results = web_pages.get("value") if isinstance(web_pages, dict) else None
    if not isinstance(raw_results, list):
        return {"ok": False, "failureClass": "provider_format_unavailable", "reason": "bocha_response_missing_webpages_value", "retryable": False, "statusCode": response.status_code}
    results: list[dict[str, str]] = []
    for index, item in enumerate(raw_results[:count], start=1):
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "").strip()
        try:
            parsed = urlparse(url)
            valid_url = parsed.scheme in {"http", "https"} and bool(parsed.hostname) and not parsed.username and not parsed.password
        except ValueError:
            valid_url = False
        title = _text(item.get("name") or item.get("title") or url, 300)
        snippet = _text(item.get("summary") or item.get("snippet") or "", 900)
        if not valid_url:
            continue
        result = {"title": title, "url": url, "snippet": snippet, "source": "bocha", "rank": str(index)}
        date = _text(item.get("datePublished"), 80)
        site = _text(item.get("siteName"), 200)
        if date:
            result["datePublished"] = date
        if site:
            result["siteName"] = site
        results.append(result)
    return {"ok": True, "results": results, "statusCode": response.status_code, "totalEstimatedMatches": web_pages.get("totalEstimatedMatches")}
