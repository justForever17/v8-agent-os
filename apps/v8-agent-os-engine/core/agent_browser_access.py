"""Bounded, credential-free projection of the existing Agent Browser owner.

This is an observation cache, not an authentication or browser registry. Cookie
presence only offers a profile read attempt; the last read is separate evidence.
"""
from __future__ import annotations

import threading
import time
from copy import deepcopy
from urllib.parse import urlparse

_lock = threading.Lock()
_ready = threading.Event()
_snapshot: dict = {"available": False, "sites": [], "reason": "not_observed"}
_updated = 0.0
_refreshing = False
_reads: dict[str, dict] = {}


def _refresh(*, launch: bool = False) -> None:
    global _snapshot, _updated, _refreshing
    try:
        from core.storage import storage
        from runtimes.computer_use.browser_automation import agent_browser_automation

        agent_browser_automation.configure(dict(storage.get_computer_use_config() or {}))
        raw = agent_browser_automation.profile_access_summary(launch=launch)
        sites = []
        for item in raw.get("sites") or []:
            host = str(item.get("host") or "").lower().strip(".")
            if not host or any(ch in host for ch in "/:@ "):
                continue
            sites.append({"host": host, "sessionPresent": bool(item.get("sessionPresent")),
                          "openPage": bool(item.get("openPage")), "access": "unverified"})
        result = {"available": bool(raw.get("available")), "sites": sites,
                  "reason": raw.get("reason"), "observedAt": int(time.time() * 1000)}
    except Exception:
        result = {"available": False, "sites": [], "reason": "browser_access_observation_unavailable"}
    with _lock:
        _snapshot = result
        _updated = time.monotonic()
        _refreshing = False
        _ready.set()


def access_snapshot(*, refresh: bool = False, launch: bool = False) -> dict:
    """Prompt reads never wait; discovery waits two seconds, source-start ten."""
    global _refreshing
    with _lock:
        if not _refreshing and (refresh or time.monotonic() - _updated > 20):
            _refreshing = True
            _ready.clear()
            threading.Thread(target=_refresh, kwargs={"launch": launch}, name="browser-access-observation", daemon=True).start()
    if refresh:
        _ready.wait(10 if launch else 2)
    with _lock:
        result = deepcopy(_snapshot)
        result["refreshPending"] = _refreshing
        for site in result["sites"]:
            previous = _reads.get(site["host"].removeprefix("www."))
            if previous:
                site["lastRead"] = dict(previous)
    return result


def observed_profile_host(url: str) -> str | None:
    host = (urlparse(url).hostname or "").lower()
    # Exact observed host/cookie domain, never an arbitrary suffix grant.
    snapshot = access_snapshot()
    if not snapshot.get("available"):
        snapshot = access_snapshot(refresh=True)
        if not snapshot.get("available") and not snapshot.get("refreshPending"):
            # A real source read can resume the existing managed profile;
            # ordinary prompt discovery never starts a browser.
            snapshot = access_snapshot(refresh=True, launch=True)
    for site in snapshot.get("sites") or []:
        domain = site["host"]
        if site["sessionPresent"] and (host == domain or host == "www." + domain):
            return domain
    return None


def record_profile_read(url: str, status: str) -> None:
    host = (urlparse(url).hostname or "").lower()
    if not host or status not in {"readable", "needs_login", "challenge", "failed"}:
        return
    with _lock:
        # Navigation/profile authority already treats root and www as the same
        # site. Do not merge unrelated sibling subdomains into this history.
        _reads[host.removeprefix("www.")] = {"status": status, "observedAt": int(time.time() * 1000)}
        while len(_reads) > 128:
            _reads.pop(next(iter(_reads)))


def render_access_context(*, discovery_tool: bool = True) -> str:
    snapshot = access_snapshot()
    sites = snapshot.get("sites") or []
    candidates = sorted((site for site in sites if site.get("sessionPresent")),
                        key=lambda site: (not site.get("openPage"), site["host"]))
    lines = ["[Agent browser access]"]
    if discovery_tool:
        lines.append("browser_capabilities lists current profile/session candidates without credentials. "
                     "For interactive web pages the Supervisor can load browser.control; use browser_broker if bound. "
                     "Native application controls use computer_use.direct. For source reading use web_broker.")
    else:
        lines.append("Your source read/search tools reuse the eligible Agent Browser session automatically. Do not assume a failed public fetch means its authenticated browser is inaccessible.")
    if candidates:
        lines.append("Session candidates (not verified login): " + ", ".join(site["host"] for site in candidates[:12]))
        if len(candidates) > 12:
            lines.append(f"{len(candidates) - 12} more domains are available through browser_capabilities.")
    lines.append("Reuse follows the current setting and observed domain. A cookie, open tab or past successful read does not prove access to every page. Login expiry/challenges must be reported; never ask for/export cookies.")
    return "\n".join(lines)
