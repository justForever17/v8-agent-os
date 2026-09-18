from __future__ import annotations

import json
import re
from typing import Any

from .formatting import (
    _content_excerpt,
    _short_text,
    _source_line,
    _status_counts_line,
    _surface_ref_lines,
    _yes_no,
)

def _render_computer_use_surface(tool_name: str, payload: dict[str, Any], raw_ref: str) -> str | None:
    control = payload.get("control")
    if (payload.get("kind") == "computer_use_controlled" and payload.get("ok") is False
            and payload.get("status") in {"cancelled", "paused", "interrupted", "blocked"}
            and isinstance(control, dict) and control.get("status") == payload.get("status")):
        lines = ["Computer Use control", f"Status: {payload['status']}", _short_text(payload.get("summary"), 700),
                 "This is a control receipt, not proof of task completion or absence of earlier side effects."]
        if payload.get("recommendedNextAction"):
            lines.append(f"Next: {_short_text(payload['recommendedNextAction'], 100)}")
        lines.extend(_surface_ref_lines(raw_ref, include_raw=True))
        return "\n".join(line for line in lines if line).strip()
    if tool_name == "computer_use_resolve_execution_route":
        lines = ["Computer Use route"]
        route = payload.get("recommendedMode") or payload.get("executionReadyMode")
        tool = payload.get("recommendedTool")
        action = payload.get("recommendedAction")
        if route or tool:
            lines.append(f"Recommended: {_short_text(route, 60)} -> {_short_text(tool, 80)}")
        if action:
            lines.append(f"Action: {_short_text(action, 100)}")
        match = payload.get("recommendedMatch")
        if isinstance(match, dict):
            lines.append(
                "Best match: "
                + " | ".join(
                    part
                    for part in (
                        _short_text(match.get("id"), 90),
                        _short_text(match.get("name"), 90),
                        f"score={match.get('score')}" if match.get("score") not in (None, "") else "",
                        f"confidence={match.get('confidence')}" if match.get("confidence") not in (None, "") else "",
                    )
                    if part
                )
            )
        missing = payload.get("missingVariables") or payload.get("missingRequiredVariables")
        if isinstance(missing, list) and missing:
            lines.append("Missing variables: " + ", ".join(_short_text(item, 50) for item in missing[:8]))
        else:
            lines.append("Missing variables: none")
        summary = payload.get("summary")
        if isinstance(summary, dict):
            bits = []
            for key in ("templateCount", "draftCount", "bestScore", "bestConfidence", "requiresLearning"):
                if summary.get(key) not in (None, ""):
                    bits.append(f"{key}={summary.get(key)}")
            if bits:
                lines.append("Signals: " + " | ".join(bits))
        next_action = payload.get("recommendedToolSummary") or payload.get("recommendedNextAction")
        if next_action:
            lines.append(f"Next: {_short_text(next_action, 180)}")
        lines.extend(_surface_ref_lines(raw_ref, payload.get("detailTool"), include_raw=True))
        return "\n".join(line for line in lines if line).strip()

    if tool_name == "computer_use_list_apps":
        apps = payload.get("apps")
        if not isinstance(apps, list):
            return None
        lines = [f"Computer Use apps (showing {min(len(apps), 6)} of {payload.get('count') or len(apps)})"]
        lines.append("Search matches and inferred app classes, not an exhaustive installed-app inventory. Unverified profiles and helper components are not proof of an installed browser.")
        lines.append("Running state observes app windows only. No observed window does not mean no browser process; Agent Browser/headless sessions are described by browser_capabilities.")
        for app in apps[:6]:
            if not isinstance(app, dict):
                continue
            aliases = app.get("aliases") if isinstance(app.get("aliases"), list) else []
            alias_text = ", ".join(_short_text(alias, 28) for alias in aliases[:2])
            state = []
            if app.get("discoveryState"):
                state.append(str(app["discoveryState"]))
            if app.get("isRunning"):
                if "running" not in state:
                    state.append("running")
            if app.get("launchable"):
                state.append("launch candidate")
            title = app.get("topWindowTitle") or app.get("displayName")
            suffix = f" | aliases: {alias_text}" if alias_text else ""
            if app.get("controlClass"):
                suffix += f" | controlClass: {app['controlClass']}"
            lines.append(f"- {_short_text(app.get('appId'), 48)}: {_short_text(title, 100)} ({', '.join(state) or 'unknown'}){suffix}")
        if len(apps) > 6:
            lines.append(f"- … {len(apps) - 6} more; use detail for full windows/aliases")
        lines.extend(_surface_ref_lines(raw_ref, payload.get("detailTool"), include_raw=True))
        return "\n".join(line for line in lines if line).strip()

    if tool_name in {"computer_use_list_muscle_memories", "computer_use_lookup_muscle_memory"}:
        memories = payload.get("memories") or payload.get("matches") or payload.get("items") or []
        if not isinstance(memories, list):
            memories = []
        lines = ["Computer Use muscle memory"]
        summary = payload.get("summary")
        if isinstance(summary, dict):
            bits = [f"{key}={summary.get(key)}" for key in ("count", "bestScore", "bestConfidence") if summary.get(key) not in (None, "")]
            if bits:
                lines.append("Signals: " + " | ".join(bits))
        if memories:
            lines.append("Matches:")
            for item in memories[:5]:
                if not isinstance(item, dict):
                    continue
                lines.append(
                    "- "
                    + " | ".join(
                        part
                        for part in (
                            _short_text(item.get("id") or item.get("memoryId"), 80),
                            _short_text(item.get("name") or item.get("goal"), 100),
                            _short_text(item.get("routeAction") or item.get("action"), 60),
                            f"confidence={item.get('confidence')}" if item.get("confidence") not in (None, "") else "",
                        )
                        if part
                    )
                )
        else:
            lines.append("Matches: none")
        next_action = payload.get("recommendedNextAction") or payload.get("recommendedAction")
        if next_action:
            lines.append(f"Next: {_short_text(next_action, 180)}")
        lines.extend(_surface_ref_lines(raw_ref, payload.get("detailTool"), include_raw=True))
        return "\n".join(line for line in lines if line).strip()

    if tool_name == "computer_use_desktop_capabilities":
        lines = ["Desktop capability snapshot"]
        host = payload.get("currentHost")
        if isinstance(host, dict):
            counts = _status_counts_line(host.get("statusCounts"))
            lines.append(f"Host: {_short_text(host.get('platform'), 50)}" + (f" | {counts}" if counts else ""))
        driver = payload.get("driverHealth")
        if isinstance(driver, dict):
            available = [key for key, value in driver.items() if isinstance(value, dict) and value.get("available")]
            missing = [key for key, value in driver.items() if isinstance(value, dict) and value.get("available") is False]
            if available:
                lines.append("Available: " + ", ".join(_short_text(item, 28) for item in available[:10]))
            if missing:
                lines.append("Blocking gaps: " + ", ".join(_short_text(item, 28) for item in missing[:8]))
        browser = payload.get("browser")
        if isinstance(browser, dict):
            lines.append(f"Browser: enabled={_yes_no(browser.get('enabled'))}; provider={_short_text(browser.get('provider'), 60)}")
        gaps = payload.get("knownGaps")
        if isinstance(gaps, list) and gaps:
            lines.append("Known gaps:")
            for gap in gaps[:3]:
                if isinstance(gap, dict):
                    lines.append(f"- {_short_text(gap.get('code'), 60)}: {_short_text(gap.get('summary'), 130)}")
        next_action = payload.get("recommendedNextAction")
        if next_action:
            lines.append(f"Next: {_short_text(next_action, 180)}")
        lines.extend(_surface_ref_lines(raw_ref, payload.get("detailTool"), include_raw=True))
        return "\n".join(line for line in lines if line).strip()

    if tool_name == "computer_use_list_primitives":
        lines = ["Computer Use primitives"]
        summary = payload.get("summary")
        if isinstance(summary, dict):
            bits = []
            for key in ("primitiveCount", "categoryCount", "promotionEligibleCount"):
                if summary.get(key) not in (None, ""):
                    bits.append(f"{key}={summary.get(key)}")
            if bits:
                lines.append("Summary: " + " | ".join(bits))
            categories = summary.get("categories")
            if isinstance(categories, list) and categories:
                lines.append("Categories: " + ", ".join(_short_text(item, 24) for item in categories[:8] if not isinstance(item, dict)))
        primitives = payload.get("primitives") or payload.get("items") or []
        if isinstance(primitives, list) and primitives:
            lines.append("Actions:")
            for item in primitives[:8]:
                if isinstance(item, dict):
                    name = item.get("name") or item.get("primitive") or item.get("action")
                    required = item.get("required") or item.get("requiredArgs") or item.get("parameters")
                    req = ""
                    if isinstance(required, list) and required:
                        req = " required=" + ",".join(_short_text(part, 20) for part in required[:4])
                    lines.append(f"- {_short_text(name, 80)}{req}")
        next_action = payload.get("recommendedNextAction") or payload.get("detailTool")
        if next_action:
            lines.append(f"Next: {_short_text(next_action, 180)}")
        lines.extend(_surface_ref_lines(raw_ref, payload.get("detailTool"), include_raw=True))
        return "\n".join(line for line in lines if line).strip()

    if tool_name in {"computer_use_observe", "computer_use_observe_scene", "computer_use_list_windows", "computer_use_find_element"}:
        lines = [f"Computer Use observation: {tool_name.replace('computer_use_', '')}"]
        window = payload.get("window") or {}
        if isinstance(window, dict) and window:
            lines.append(f"Window: {_short_text(window.get('title'), 140)} | handle={window.get('handle')}")
        for screenshot in list(payload.get("screenshot") or []):
            if isinstance(screenshot, dict):
                path = screenshot.get("filePath") or screenshot.get("sourcePath")
                if path:
                    lines.append(f"Screenshot for vision_media_analyzer: {path}")
        if payload.get("observedAt"):
            lines.append(f"Observed: {payload['observedAt']}")
        for key in ("summary", "status", "state", "error"):
            if payload.get(key):
                lines.append(f"{key}: {_short_text(payload.get(key), 180)}")
        candidates = payload.get("candidates") or payload.get("elements") or payload.get("windows") or []
        if isinstance(candidates, list) and candidates:
            candidate_lines: list[str] = []
            # observe_scene has already applied the caller's bounded element limit.
            # A second top-five/name-only projection hid Edit/Button controls and
            # made identically named controls impossible to address precisely.
            candidate_limit = 120 if tool_name == "computer_use_observe_scene" else 5
            for item in candidates[:candidate_limit]:
                if isinstance(item, dict):
                    role_label = item.get("role")
                    if isinstance(role_label, str) and role_label.strip().lower() in {
                        "pane",
                        "group",
                        "window",
                        "document",
                        "section",
                        "generic",
                    }:
                        role_label = None
                    label = (
                        item.get("name")
                        or item.get("title")
                        or item.get("text")
                        or item.get("label")
                        or role_label
                        or item.get("className")
                        or item.get("id")
                        or item.get("elementId")
                    )
                    if label in (None, "", [], {}):
                        continue
                    confidence = item.get("confidence") or item.get("score")
                    suffix = f" confidence={confidence}" if confidence not in (None, "") else ""
                    if tool_name == "computer_use_observe_scene":
                        suffix += f"; control_type={item.get('role') or 'unknown'}"
                        if item.get("automationId"):
                            suffix += f"; automation_id={item['automationId']}"
                        if item.get("elementId"):
                            suffix += f"; elementId={item['elementId']}"
                    candidate_lines.append(f"- {_short_text(label, 120)}{suffix}")
                else:
                    rendered = _short_text(item, 140)
                    if rendered:
                        candidate_lines.append(f"- {rendered}")
            if candidate_lines:
                lines.append(f"Top candidates ({min(candidate_limit, len(candidates))}/{len(candidates)}):")
                lines.extend(candidate_lines)
                if len(candidates) > candidate_limit or payload.get("omittedElementCount"):
                    lines.append("Narrow with element_query or read the observation detail for other controls.")
            else:
                lines.append("Top candidates: none with actionable labels; use detail/rawRef if visual context is required.")
        next_action = payload.get("recommendedNextAction") or payload.get("nextAction")
        if next_action:
            lines.append(f"Next: {_short_text(next_action, 180)}")
        lines.extend(_surface_ref_lines(raw_ref, payload.get("detailTool"), include_raw=True))
        return "\n".join(line for line in lines if line).strip()
    return None
def _focused_creative_media_contract(payload: dict[str, Any]) -> dict[str, Any] | None:
    """Only the exact current local action schema gets lossless rendering."""
    if payload.get("ok") is not True or payload.get("facade") != "capabilities" or payload.get("action") != "describe":
        return None
    focus = payload.get("contractFocus")
    if not isinstance(focus, dict) or not isinstance(focus.get("facade"), str) or not isinstance(focus.get("action"), str):
        return None
    from core.tools.native.creative_media_facade import creative_media_action_contract

    expected = creative_media_action_contract().get(focus["facade"], {}).get(focus["action"])
    return expected if expected is not None and payload.get("contract") == expected else None
def _render_creative_media_surface(tool_name: str, payload: dict[str, Any], raw_ref: str, *, budget: int = 4000) -> str | None:
    if tool_name in {
        "creative_media_capabilities",
        "creative_media_plan",
        "creative_media_assets",
        "creative_media_jobs",
        "creative_media_edit",
        "creative_media_quality",
    }:
        facade = _short_text(payload.get("facade") or tool_name.removeprefix("creative_media_"), 50)
        action = _short_text(payload.get("action"), 60)
        status = _short_text(payload.get("status"), 60)
        lines = [f"Creative Media {facade}{f'.{action}' if action else ''}"]
        if status:
            lines.append(f"Status: {status}")
        summary = _short_text(payload.get("summary"), 500)
        if summary:
            lines.append(f"Summary: {summary}")
        contract = _focused_creative_media_contract(payload) if tool_name == "creative_media_capabilities" else None
        if contract is not None:
            focus = payload["contractFocus"]
            lines.append(f"Tool: creative_media_{focus['facade']}(action='{focus['action']}', request={{...}})")
            lines.append("Required: " + (", ".join(contract["requiredFields"]) or "none"))
            for group in contract["anyOfFields"]:
                lines.append("Require at least one: " + ", ".join(group))
            lines.append("Allowed request fields: " + ", ".join(contract["allowedFields"]))
            for kind in ("string", "array", "object", "boolean", "integer", "number"):
                fields = [name for name, value in contract["fieldTypes"].items() if value == kind]
                if fields:
                    lines.append(f"{kind}: " + ", ".join(fields))
            if contract["defaults"]:
                lines.append("Defaults: " + json.dumps(contract["defaults"], ensure_ascii=False))
            lines.append(f"Mutating: {contract['mutating']}; output: {contract['outputKind']}. Scope comes from runtime; do not supply session/run/workspace ids.")
            if contract.get("facade") != "capabilities":
                lines.append("If this tool is not currently visible, Supervisor calls runtime_broker(mode='grant', tool_group='creative_media.core') first. Loading tools is neither a configuration change nor delegation; earlier turns' grants have expired.")
            if contract.get("requiresPluginGrantWhen"):
                lines.append("Plugin grant required when " + contract["requiresPluginGrantWhen"])
            detail_ref = _short_text(payload.get("detailRef"), 220)
            detail_tool = f"tool_observation_detail(raw_ref='{detail_ref}')" if detail_ref else None
            lines.extend(_surface_ref_lines(raw_ref, detail_tool, include_raw=True))
            # Never let unrelated handler content borrow the local-schema exception.
            return "\n".join(lines).strip()
        elif isinstance(payload.get("facades"), dict):
            for name, actions in payload["facades"].items():
                if isinstance(actions, list):
                    if len(payload["facades"]) > 1:
                        lines.append(f"- creative_media_{name}: {len(actions)} actions; describe request={{'facade':'{name}'}}")
                    else:
                        lines.append(f"- creative_media_{name}: " + ", ".join(str(item) for item in actions))
            lines.append("Next: creative_media_capabilities(action='describe', request={'facade':'jobs','action':'create'})")
        candidates = payload.get("modelCandidates")
        if isinstance(candidates, list):
            lines.append("Configured candidates (readiness is configuration/adapter evidence, not live health or a grant):")
            shown = 0
            for item in candidates:
                if not isinstance(item, dict):
                    continue
                ready = item.get("readiness") if isinstance(item.get("readiness"), dict) else {}
                def state(value: Any) -> str:
                    return "true" if value is True else "false" if value is False else "unknown"
                block = (f"- modelRef: {item.get('modelRef') or '(not provided)'}; "
                         f"operation: {item.get('operationKind') or 'unknown'}; modality: {item.get('modality') or 'unknown'}\n"
                         f"  enabled={state(item.get('enabled'))}; available={state(item.get('available'))}; "
                         f"executable={state(ready.get('executable'))}; briefOnly={state(item.get('briefOnly'))}")
                if ready.get("reasonCodes"):
                    block += "; reasons=" + ", ".join(str(code) for code in ready["reasonCodes"])
                if len("\n".join(lines)) + len(block) > max(0, budget - 650):
                    break
                lines.append(block)
                shown += 1
            total = payload.get("candidateCount", len(candidates))
            lines.append(f"Showing {shown} of {total} configured candidates.")
            if shown < len(candidates) or payload.get("hasMoreCandidates"):
                lines.append("More candidates available: narrow modality/operationKind with rank_models, or read detailRef; candidate identities were not shortened.")
            if not candidates:
                lines.append("No matching configured candidate; catalog entries do not authorize generation.")
        if payload.get("presetAuthority"):
            lines.append("Resolution presets are convenience defaults, not model capability limits.")
            for kind in ("image", "video"):
                presets = payload.get(kind + "Presets")
                if isinstance(presets, dict):
                    for name, dimensions in presets.items():
                        lines.append(f"- {kind} {name}: " + json.dumps(dimensions, ensure_ascii=False))
        content = payload.get("content")
        if isinstance(content, str) and contract is None:
            lines.append(content)
        artifacts = payload.get("artifacts")
        if isinstance(artifacts, list):
            for artifact in artifacts:
                if not isinstance(artifact, dict):
                    continue
                lines.append(f"Artifact: {artifact.get('artifactId')}; {artifact.get('mimeType') or artifact.get('kind')}")
                if artifact.get("sourcePath"):
                    lines.append(f"Readable file_path: {artifact['sourcePath']}")
                elif artifact.get("contentUrl"):
                    lines.append(f"Content: {artifact['contentUrl']}")
            lines.append(f"Showing {len(artifacts)} of {payload.get('artifactCount', len(artifacts))} artifacts; additional refs are in detail.")
        refs = payload.get("refs")
        if isinstance(refs, list) and refs:
            lines.append("Refs:")
            lines.extend(f"- {_short_text(ref, 180)}" for ref in refs[:8] if _short_text(ref, 180))
            if len(refs) > 8:
                lines.append(f"- … {len(refs) - 8} more")
        error = payload.get("error")
        if isinstance(error, dict) and error:
            code = _short_text(error.get("code"), 80)
            message = _short_text(error.get("message"), 300)
            lines.append(f"Error: {code}{f' - {message}' if message else ''}")
        elif error:
            lines.append(f"Error: {_short_text(error, 300)}")
        next_action = _short_text(payload.get("nextAction"), 300)
        if next_action:
            lines.append(f"Next: {next_action}")
        detail_ref = _short_text(payload.get("detailRef"), 220)
        detail_tool = f"tool_observation_detail(raw_ref='{detail_ref}')" if detail_ref else None
        lines.extend(_surface_ref_lines(raw_ref, detail_tool, include_raw=True))
        return "\n".join(line for line in lines if line).strip()

    return None
def _render_rpa_surface(tool_name: str, payload: dict[str, Any], raw_ref: str) -> str | None:
    if tool_name != "rpa_list_robot_scripts":
        return None
    scripts = payload.get("scripts")
    if not isinstance(scripts, list):
        scripts = []
    lines = [f"RPA robot scripts (showing {min(len(scripts), 5)} of {payload.get('count') or len(scripts)})"]
    for script in scripts[:5]:
        if not isinstance(script, dict):
            continue
        bits = [
            _short_text(script.get("name"), 90),
            f"size={script.get('size')}" if script.get("size") not in (None, "") else "",
            f"updated={_short_text(script.get('updatedAt'), 40)}" if script.get("updatedAt") else "",
        ]
        lines.append("- " + " | ".join(bit for bit in bits if bit))
    if not scripts:
        lines.append("No robot scripts found.")
    lines.extend(_surface_ref_lines(raw_ref, payload.get("detailTool"), include_raw=True))
    return "\n".join(line for line in lines if line).strip()
def _render_native_json_surface(tool_name: str, payload: dict[str, Any], raw_ref: str) -> str | None:
    if tool_name not in {"read_native_file", "grep_search"}:
        return None
    if payload.get("ok") is not False and not payload.get("error"):
        return None
    label = "Read native file" if tool_name == "read_native_file" else "Grep search"
    lines = [label]
    if payload.get("summary"):
        lines.append(f"Summary: {_short_text(payload.get('summary'), 220)}")
    if payload.get("error"):
        lines.append(f"Error: {_short_text(payload.get('error'), 100)}")
    for key in ("inputPath", "resolvedPath", "path"):
        if payload.get(key):
            lines.append(f"{key}: {_short_text(payload.get(key), 180)}")
    next_action = payload.get("recommendedNextAction") or payload.get("nextAction")
    if next_action:
        lines.append(f"Next: {_short_text(next_action, 220)}")
    lines.extend(_surface_ref_lines(raw_ref, payload.get("detailTool"), include_raw=True))
    return "\n".join(line for line in lines if line).strip()
def _render_memory_surface(tool_name: str, payload: dict[str, Any], raw_ref: str) -> str | None:
    if tool_name == "memory_broker":
        mode = str(payload.get("mode") or "").strip() or "recall"
        items = payload.get("items")
        packs = payload.get("evidencePacks")
        summary = str(payload.get("summary") or "").strip().lower()
        no_match = (
            payload.get("ok") is not False
            and not items
            and not packs
            and any(marker in summary for marker in ("no matching prior memory", "no relevant memory found"))
        )
        if no_match:
            return "Memory: no matching prior evidence.\nContinue with the current request."
        lines = [f"Memory broker: {mode}"]
        if payload.get("summary"):
            lines.append(f"Summary: {_short_text(payload.get('summary'), 220)}")
        if payload.get("query"):
            lines.append(f"Query: {_short_text(payload.get('query'), 160)}")
        if payload.get("scope"):
            lines.append(f"Scope: {_short_text(payload.get('scope'), 80)}")

        if isinstance(packs, list) and packs:
            lines.append("Evidence packs:")
            for pack in packs[:3]:
                if not isinstance(pack, dict):
                    continue
                domain = pack.get("sourceDomain") or pack.get("domain")
                confidence = pack.get("confidence")
                header_bits = [_short_text(domain, 80)] if domain else ["unknown"]
                if confidence not in (None, "", [], {}):
                    header_bits.append(f"confidence={_short_text(confidence, 60)}")
                lines.append(f"- {' | '.join(bit for bit in header_bits if bit)}")
                why = pack.get("whySelected")
                if why:
                    lines.append(f"  Why: {_short_text(why, 240)}")
                selected = pack.get("selectedEvidence")
                if isinstance(selected, list) and selected:
                    lines.append("  Selected evidence:")
                    for item in selected[:3]:
                        if not isinstance(item, dict):
                            lines.append(f"  - {_content_excerpt(item, 300)}")
                            continue
                        title = item.get("title") or item.get("id") or item.get("memoryRef") or item.get("sourceRef")
                        answer = (
                            item.get("answer")
                            or item.get("researchResult")
                            or item.get("text")
                            or item.get("summary")
                            or item.get("fact")
                            or item.get("claim")
                        )
                        prefix = f"{_short_text(title, 90)}: " if title and title != answer else ""
                        line = _content_excerpt(answer, 420) if answer else _source_line(item)
                        lines.append(f"  - {prefix}{line}")
                        claims = item.get("claimDigest") or item.get("claims")
                        if isinstance(claims, list) and claims:
                            for claim in claims[:2]:
                                claim_text = claim.get("claim") if isinstance(claim, dict) else claim
                                if claim_text:
                                    lines.append(f"    claim: {_content_excerpt(claim_text, 240)}")
                        sources = item.get("sources") or item.get("sourceRefs") or item.get("sourceUrls")
                        if isinstance(sources, list) and sources:
                            rendered_sources = []
                            for source in sources[:3]:
                                if isinstance(source, dict):
                                    url = source.get("url") or source.get("sourceUrl")
                                    title_text = source.get("title") or source.get("host") or url
                                    if url:
                                        rendered_sources.append(f"{_short_text(title_text, 80)}: {_short_text(url, 140)}")
                                elif str(source or "").strip():
                                    rendered_sources.append(_short_text(source, 140))
                            if rendered_sources:
                                lines.append("    sources: " + "; ".join(rendered_sources))
                rejected = pack.get("rejectedEvidence")
                if isinstance(rejected, list) and rejected:
                    lines.append("  Rejected evidence:")
                    for item in rejected[:2]:
                        if isinstance(item, dict):
                            label = item.get("title") or item.get("id") or item.get("memoryRef") or item.get("sourceRef")
                            reason = item.get("reason") or item.get("rejectedReason") or item.get("whyRejected")
                            lines.append(f"  - {_short_text(label, 110)}: {_short_text(reason, 160)}")
                        else:
                            lines.append(f"  - {_short_text(item, 220)}")
                stale = pack.get("missingOrStaleReasons")
                if isinstance(stale, list) and stale:
                    lines.append("  Missing/stale: " + "; ".join(_short_text(item, 120) for item in stale[:3]))
                pack_next = pack.get("recommendedNextAction")
                if pack_next:
                    lines.append(f"  Next: {_short_text(pack_next, 220)}")
            if len(packs) > 3:
                lines.append(f"- … {len(packs) - 3} more packs")

        if isinstance(items, list) and items:
            lines.append("Items:")
            for item in items[:5]:
                if not isinstance(item, dict):
                    continue
                label = item.get("text") or item.get("name") or item.get("memoryRef") or item.get("id")
                prefix = item.get("id") or item.get("memoryRef") or item.get("name")
                extras = []
                if item.get("scope"):
                    extras.append(str(item.get("scope")))
                if item.get("category") or item.get("type"):
                    extras.append(str(item.get("category") or item.get("type")))
                if item.get("confidence") not in (None, ""):
                    extras.append(f"confidence={item.get('confidence')}")
                suffix = f" | {'; '.join(extras)}" if extras else ""
                if prefix and prefix != label:
                    lines.append(f"- {_short_text(prefix, 80)}: {_short_text(label, 240)}{suffix}")
                else:
                    lines.append(f"- {_short_text(label, 240)}{suffix}")
            if len(items) > 5:
                lines.append(f"- … {len(items) - 5} more")

        relations = payload.get("relations")
        if isinstance(relations, list) and relations:
            lines.append("Relations:")
            for item in relations[:6]:
                if not isinstance(item, dict):
                    continue
                triple = " ".join(
                    _short_text(item.get(key), 80)
                    for key in ("subject", "predicate", "object")
                    if item.get(key)
                )
                if triple:
                    hop = f"hop={item.get('hop')} | " if item.get("hop") else ""
                    lines.append(f"- {hop}{triple}")
            if len(relations) > 6:
                lines.append(f"- … {len(relations) - 6} more")

        if payload.get("preview"):
            lines.append("Preview:")
            lines.append(_short_text(payload.get("preview"), 1200))
        if payload.get("omittedChars"):
            lines.append(f"Omitted chars: {payload.get('omittedChars')}")
        next_action = payload.get("nextAction") or payload.get("recommendedNextAction")
        if next_action:
            lines.append(f"Next: {_short_text(next_action, 220)}")
        if payload.get("ok") is False and payload.get("failureClass"):
            lines.append(f"Failure: {_short_text(payload.get('failureClass'), 80)}")
        lines.extend(_surface_ref_lines(raw_ref, payload.get("detailTool"), include_raw=True))
        return "\n".join(line for line in lines if line).strip()

    if tool_name != "memory_map":
        return None
    lines = ["Memory map"]
    if payload.get("anchorDate"):
        lines.append(f"Anchor: {_short_text(payload.get('anchorDate'), 40)}")
    refs = payload.get("currentRefs")
    if isinstance(refs, dict) and refs:
        lines.append("Current refs: " + ", ".join(f"{key}={_short_text(value, 60)}" for key, value in list(refs.items())[:4]))
    items = payload.get("items")
    if isinstance(items, list) and items:
        lines.append("Items:")
        for item in items[:5]:
            if not isinstance(item, dict):
                continue
            summary_state = item.get("summaryState")
            day_count = item.get("dayCount")
            latest = item.get("latestDay")
            line = f"- {_short_text(item.get('memoryRef'), 80)} | {_short_text(item.get('kind'), 24)} | {_short_text(item.get('label'), 40)}"
            extras = []
            if summary_state:
                extras.append(f"summary={summary_state}")
            if day_count not in (None, ""):
                extras.append(f"days={day_count}")
            if latest:
                extras.append(f"latest={latest}")
            if extras:
                line += " | " + " ".join(extras)
            lines.append(line)
        if len(items) > 5:
            lines.append(f"- … {len(items) - 5} more")
    lines.extend(_surface_ref_lines(raw_ref, payload.get("detailTool"), include_raw=True))
    return "\n".join(line for line in lines if line).strip()
