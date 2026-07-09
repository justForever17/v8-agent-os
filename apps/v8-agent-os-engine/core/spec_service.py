from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SPEC_ROOT_PARTS = (".v8", "specs")
SPEC_DOCS = {
    "requirements": "requirements.md",
    "bugfix": "bugfix.md",
    "design": "design.md",
    "tasks": "tasks.md",
}
SPEC_CHECKLISTS = {
    "requirements": "checklists/requirements.md",
}
SPEC_ANNEX_DOCS = {
    "research": "annex/research.md",
    "contracts": "annex/contracts.md",
    "quickstart": "annex/quickstart.md",
}
SPEC_STAGE_ORDER = ("requirements", "bugfix", "design", "tasks")
SPEC_LIFECYCLE_ACTIVE = "active"
SPEC_LIFECYCLE_ARCHIVED = "archived"
SPEC_LIFECYCLE_DELIVERED = "delivered"
_INACTIVE_SPEC_LIFECYCLES = {SPEC_LIFECYCLE_ARCHIVED, SPEC_LIFECYCLE_DELIVERED}


def _stage_order(kind: str) -> tuple[str, ...]:
    return ("bugfix", "design", "tasks") if str(kind or "") == "bugfix" else ("requirements", "design", "tasks")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _safe_text(value: Any, *, limit: int = 4000) -> str:
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if len(text) > limit:
        return text[: limit - 1].rstrip() + "…"
    return text


def _slugify(value: str) -> str:
    text = _safe_text(value, limit=120).lower()
    text = re.sub(r"[`~!@#$%^&*()+={}\[\]|\\:;\"'<>,.?/，。！？、：；（）【】《》]+", " ", text)
    text = re.sub(r"\s+", "-", text).strip("-")
    text = re.sub(r"[^a-z0-9\u4e00-\u9fff_-]+", "", text)
    if len(text) > 48:
        text = text[:48].rstrip("-_")
    digest = hashlib.sha1(_safe_text(value, limit=400).encode("utf-8", errors="ignore")).hexdigest()[:8]
    return f"{text or 'spec'}-{digest}"


_ID_PREFIX_ALIASES = {
    "REQ": ("REQ", "FR", "NFR"),
    "TASK": ("TASK", "TSK", "T"),
}


def _canonical_id(raw_id: str, prefix: str) -> str:
    item = str(raw_id or "").upper()
    canonical_prefix = prefix.upper()
    if prefix == "TASK":
        match = re.match(r"^(?:TASK|TSK|T)-(\d+)$", item)
        if match:
            return f"TASK-{int(match.group(1)):03d}"
    if canonical_prefix in {"REQ", "DES", "BFIX"}:
        aliases = _ID_PREFIX_ALIASES.get(canonical_prefix, (canonical_prefix,))
        prefix_pattern = "|".join(re.escape(alias) for alias in aliases)
        match = re.match(rf"^(?:{prefix_pattern})-(\d+)$", item, re.IGNORECASE)
        if match:
            raw_prefix = item.split("-", 1)[0].upper()
            if canonical_prefix == "REQ" and raw_prefix in {"FR", "NFR"}:
                return f"{raw_prefix}-{int(match.group(1)):03d}"
            return f"{canonical_prefix}-{int(match.group(1)):03d}"
    return item


def _extract_ids(markdown: str, prefix: str) -> list[str]:
    aliases = _ID_PREFIX_ALIASES.get(prefix.upper(), (prefix.upper(),))
    prefix_pattern = "|".join(re.escape(item) for item in aliases)
    pattern = re.compile(rf"\b(({prefix_pattern})-[0-9]+)\b", re.IGNORECASE)
    seen: set[str] = set()
    ids: list[str] = []
    for match in pattern.finditer(markdown or ""):
        item = _canonical_id(match.group(1), prefix.upper())
        if item not in seen:
            seen.add(item)
            ids.append(item)
    return ids


def _normalize_stage_markdown(stage: str, content: str) -> str:
    """Normalize common loose Spec IDs while preserving document wording."""

    normalized_stage = str(stage or "").strip().lower()
    prefixes: tuple[str, ...]
    if normalized_stage == "tasks":
        prefixes = ("TASK", "TSK", "T", "REQ", "FR", "NFR", "BFIX", "DES")
    elif normalized_stage == "design":
        prefixes = ("DES", "REQ", "FR", "NFR", "BFIX")
    elif normalized_stage == "bugfix":
        prefixes = ("BFIX", "AC-BFIX")
    else:
        prefixes = ("REQ", "FR", "NFR", "AC-REQ")
    prefix_pattern = "|".join(re.escape(item) for item in prefixes)

    def replace(match: re.Match[str]) -> str:
        raw_prefix = match.group(1).upper()
        raw_number = int(match.group(2))
        canonical_prefix = {
            "TSK": "TASK",
            "T": "TASK",
            "FR": "FR",
            "NFR": "NFR",
            "AC-REQ": "AC-REQ",
            "AC-BFIX": "AC-BFIX",
        }.get(raw_prefix, raw_prefix)
        return f"{canonical_prefix}-{raw_number:03d}"

    return re.sub(rf"\b({prefix_pattern})-(\d+)\b", replace, str(content or ""), flags=re.IGNORECASE)


def _stage_ids_from_text(stage: str, content: str) -> list[str]:
    normalized_stage = str(stage or "").strip().lower()
    if normalized_stage == "tasks":
        ids = _extract_ids(content, "TASK")
        for item in _task_slices(content, {}, []):
            _add_unique(ids, str(item.get("taskId") or ""))
        return ids
    if normalized_stage == "design":
        ids = _extract_ids(content, "DES")
        if ids:
            return ids
        return [str(item.get("id") or "") for item in _design_fragments(content)[0] if item.get("id")]
    if normalized_stage == "bugfix":
        ids = _extract_ids(content, "BFIX")
        if ids:
            return ids
        return list(_requirement_fragments(content).keys())
    ids = _extract_ids(content, "REQ")
    for item in _requirement_fragments(content):
        _add_unique(ids, item)
    return ids


def _assign_missing_stage_ids(stage: str, content: str) -> tuple[str, dict[str, Any]]:
    """Allocate stable IDs when a draft is written without asking the agent to mint them."""

    normalized_stage = str(stage or "").strip().lower()
    normalized = _normalize_stage_markdown(normalized_stage, content)
    before_ids = _stage_ids_from_text(normalized_stage, normalized)
    if before_ids:
        return normalized, {"allocatedIds": [], "existingIds": before_ids}

    prefix = _stage_prefix(normalized_stage)
    lines = normalized.splitlines()
    allocated: list[str] = []
    next_number = 1
    next_lines: list[str] = []
    changed = False

    def alloc_id() -> str:
        nonlocal next_number
        item = f"{prefix}-{next_number:03d}"
        next_number += 1
        allocated.append(item)
        return item

    if normalized_stage == "tasks":
        for line in lines:
            heading = re.match(r"^(\s*#{2,6}\s+)(?!TASK-|TSK-|T-)(.+?)\s*$", line, re.IGNORECASE)
            checkbox = re.match(r"^(\s*-\s+\[[ xX]\]\s+)(?!TASK-|TSK-|T-)(.+?)\s*$", line, re.IGNORECASE)
            if heading:
                next_lines.append(f"{heading.group(1)}{alloc_id()}: {heading.group(2).strip()}")
                changed = True
            elif checkbox:
                next_lines.append(f"{checkbox.group(1)}{alloc_id()}: {checkbox.group(2).strip()}")
                changed = True
            else:
                next_lines.append(line)
        if not changed:
            title = _compact_snippet(normalized.splitlines()[0] if normalized.splitlines() else "Execute approved work", limit=140)
            next_lines.extend(
                [
                    "",
                    "## Task Pipeline",
                    "",
                    f"### {alloc_id()}: {title}",
                    "",
                    "- runtimeLane: Engineering",
                    "- dependsOn: []",
                    "- specRefs: REQ-001, DES-001",
                    "- expectedOutput: typed runtime handoff",
                    "- acceptance: handoff cites this task and verification result",
                    "- proofRequired: touched files/artifacts or degraded blocker",
                    "- mvpSlice: smallest independently useful slice named by this task",
                    "- independentAcceptance: reviewer can verify the proof without trusting the worker summary",
                ]
            )
            changed = True
    else:
        for line in lines:
            bullet = re.match(r"^(\s*[-*]\s+)(?!REQ-|FR-|NFR-|BFIX-|DES-)(.+?)\s*$", line, re.IGNORECASE)
            if bullet and bullet.group(2).strip():
                next_lines.append(f"{bullet.group(1)}{alloc_id()}: {bullet.group(2).strip()}")
                changed = True
            else:
                next_lines.append(line)
        if not changed:
            title = _compact_snippet(normalized.splitlines()[0] if normalized.splitlines() else "Spec item", limit=160)
            heading = "## Traceability Items"
            next_lines.extend(["", heading, "", f"- {alloc_id()}: {title}"])
            changed = True

    result = "\n".join(next_lines).rstrip() + "\n"
    result = _normalize_stage_markdown(normalized_stage, result)
    return result, {"allocatedIds": allocated, "existingIds": []}


def _stage_dependency(stage: str, kind: str) -> str | None:
    if stage == "design":
        return "bugfix" if kind == "bugfix" else "requirements"
    if stage == "tasks":
        return "design"
    return None


def _stage_prefix(stage: str) -> str:
    if stage == "tasks":
        return "TASK"
    if stage == "design":
        return "DES"
    if stage == "bugfix":
        return "BFIX"
    return "REQ"


def _template_requirements(title: str, user_request: str) -> str:
    return f"""# Requirements: {title}

Source request:
> {_safe_text(user_request, limit=1200)}

## Goals

- REQ-001: Capture the user-visible outcome and delivery format.

## User Stories

- As a user, I want the requested capability to be delivered clearly, so that I can verify it without guessing hidden runtime behavior.

## Acceptance Criteria

- AC-REQ-001: WHEN the implementation is complete, THEN the delivered result SHALL satisfy REQ-001 and include verification evidence.

## Boundaries

- Out of scope items must be called out before runtime execution.
- Runtime execution must cite this spec by `specId` and requirement IDs.
"""


def _template_bugfix(title: str, user_request: str) -> str:
    return f"""# Bugfix Spec: {title}

Source report:
> {_safe_text(user_request, limit=1200)}

## Current Behavior

- BFIX-001: Describe the observed failure, error, or regression.

## Expected Behavior

- BFIX-002: Describe the expected behavior after the fix.

## Unchanged Behavior

- BFIX-003: List behavior that must remain unchanged.

## Root Cause Analysis

- BFIX-004: Record confirmed root cause or mark as `pending evidence`.

## Acceptance Criteria

- AC-BFIX-001: WHEN the fix is complete, THEN the failing behavior SHALL be reproduced or explained and the verification result SHALL be linked to BFIX IDs.
"""


def _template_design(title: str, kind: str, approved_ref: str) -> str:
    source_label = "Bugfix" if kind == "bugfix" else "Requirements"
    return f"""# Design: {title}

Source: {source_label} `{approved_ref}`.

## Architecture

- DES-001: Explain the smallest viable technical path that satisfies the approved spec.

## Runtime Plan

- DES-002: Identify Research / Engineering / Creative / Subagent runtime needs and why they are required.

## Files and Interfaces

- DES-003: List expected files, interfaces, config, and public contracts that may change.

## Verification Strategy

- DES-004: Define unit, integration, live, rollback, and proof expectations.

## Risks

- DES-005: Record security, side effect, compatibility, and recovery risks.
"""


def _template_tasks(title: str) -> str:
    return f"""# Tasks: {title}

Task documents are the execution contract for runtime dispatch. Each task must
carry a stable ID, runtime lane, dependencies, Spec refs, expected output, and
acceptance/proof expectations.

## Pipeline Contract

- Execution truth lives in runtime episodes, typed handoffs, artifacts, and proof ledgers.
- Supervisor todos are only orchestration milestones and must not replace this task contract.
- Runtime lanes should be one of: Research, Engineering, Creative Media, Delegation/Subagent, Memory, or Governance.

## Task Pipeline

| Task ID | Runtime lane | Goal | Depends on | Spec refs | Expected output | Acceptance / proof | MVP slice | Independent acceptance |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| TASK-001 | Governance | Prepare runtime route with approved Spec refs. | - | REQ-001/BFIX-001, DES-001 | Runtime need payload with specId and detailRefs. | Route cites approved Spec refs. | route payload can be inspected before execution | Reviewer can confirm the route cites approved detailRefs. |
| TASK-002 | Engineering/Research/Delegation | Execute the scoped change through the appropriate runtime. | TASK-001 | REQ-001/BFIX-001, DES-002, DES-003 | Runtime handoff, artifact, or degraded handoff. | Handoff/proof links task and Spec IDs. | smallest runnable or readable change slice | Reviewer can verify touched artifacts/proof without trusting the worker summary. |
| TASK-003 | Governance | Verify proof and reconcile final delivery. | TASK-002 | AC-REQ-001/AC-BFIX-001, DES-004 | User-facing completion summary. | Verification result is linked to proof/artifact refs. | proof review can be completed before final wording polish | Reviewer can inspect proof/artifact refs and reproduce the pass/degraded/fail claim. |

## Task Details

### TASK-001: Prepare runtime route

- runtimeLane: Governance
- dependsOn: []
- specRefs: REQ-001/BFIX-001, DES-001
- inputRefs: approved requirements/bugfix, design, tasks
- expectedOutput: runtime need payload
- acceptance: runtime route contains specId and approved detailRefs
- proofRequired: route/episode ledger entry
- mvpSlice: route payload can be inspected before execution
- independentAcceptance: Reviewer can confirm the route cites approved detailRefs.

### TASK-002: Execute scoped change

- runtimeLane: Engineering/Research/Delegation
- dependsOn: [TASK-001]
- specRefs: REQ-001/BFIX-001, DES-002, DES-003
- inputRefs: runtime need payload
- expectedOutput: runtime handoff, artifact, or degraded handoff
- acceptance: output links task ID and Spec IDs
- proofRequired: runtime handoff/proof/artifact refs
- mvpSlice: smallest runnable or readable change slice
- independentAcceptance: Reviewer can verify touched artifacts/proof without trusting the worker summary.

### TASK-003: Verify and reconcile

- runtimeLane: Governance
- dependsOn: [TASK-002]
- specRefs: AC-REQ-001/AC-BFIX-001, DES-004
- inputRefs: runtime handoff/proof/artifact refs
- expectedOutput: final delivery summary
- acceptance: verification result explains pass/degraded/fail
- proofRequired: proof ledger or explicit degraded reason
- mvpSlice: proof review can be completed before final wording polish
- independentAcceptance: Reviewer can inspect proof/artifact refs and reproduce the pass/degraded/fail claim.
"""


_TASKS_PIPELINE_REQUIREMENTS = {
    "runtimeLane": ("runtime lane", "runtimelane", "runtime泳道", "lane", "执行泳道", "执行通道", "执行频道", "执行方"),
    "dependsOn": ("depends on", "dependson", "dependsOn", "depends", "依赖"),
    "specRefs": ("spec refs", "specrefs", "specrefs", "refs", "specId", "spec ids", "需求", "设计", "引用"),
    "expectedOutput": ("expected output", "expectedoutput", "output", "输出", "产物"),
    "acceptanceProof": ("acceptance", "proof", "验收", "证明", "proofRequired", "proof refs"),
}


def _tasks_pipeline_diagnostics(markdown: str) -> dict[str, Any]:
    text = str(markdown or "")
    normalized = re.sub(r"\s+", " ", text).strip().lower()
    task_ids = _extract_ids(text, "TASK")
    inline_requirement_refs = [
        ref
        for ref in _extract_requirement_ref_ids(text)
        if not str(ref).upper().startswith(("TASK-", "TSK-", "T-"))
    ]
    diagnostic_requirement_index = {
        ref: {"id": ref, "summary": "", "detailRef": f"spec://requirements#{ref}"}
        for ref in inline_requirement_refs
    }
    kiro_task_slices = _task_slices(text, diagnostic_requirement_index, [])
    detailed_kiro_task_slices = [
        item
        for item in kiro_task_slices
        if list(item.get("requirementRefs") or [])
        and len(str(item.get("taskExcerpt") or "")) > max(48, len(str(item.get("title") or "")) + 24)
    ]
    if not task_ids and kiro_task_slices:
        task_ids = [str(item.get("taskId") or "") for item in kiro_task_slices if item.get("taskId")]
    missing: list[str] = []
    approval_blocking: list[str] = []
    for field, markers in _TASKS_PIPELINE_REQUIREMENTS.items():
        if not any(str(marker).lower() in normalized for marker in markers):
            missing.append(field)
    has_pipeline_table = bool(re.search(r"(?im)^\|\s*task id\s*\|", text)) or "## task pipeline" in normalized
    has_task_details = bool(re.search(r"(?im)^#{2,6}\s+(?:TASK|TSK|T)-\d{2,}\b", text))
    has_assignable_tasks = bool(
        (task_ids and (has_pipeline_table or has_task_details))
        or detailed_kiro_task_slices
    )
    has_task_refs = bool(
        re.search(r"(?im)_?\s*(?:需求|requirements?|specRefs?|refs?)\s*[:：]", text)
        or inline_requirement_refs
        or _extract_ids(text, "REQ")
        or _extract_ids(text, "BFIX")
    )
    if not has_assignable_tasks:
        approval_blocking.append("taskIds")
    if has_assignable_tasks and not has_task_refs:
        approval_blocking.append("specRefs")
    return {
        "valid": has_assignable_tasks and has_task_refs and not approval_blocking,
        "taskCount": len(task_ids),
        "taskIds": task_ids,
        "missingFields": missing,
        "approvalBlocking": approval_blocking,
        "hasPipelineTable": has_pipeline_table,
        "hasTaskDetails": has_task_details,
        "hasKiroTaskList": bool(kiro_task_slices),
        "assignableKiroTaskCount": len(detailed_kiro_task_slices),
        "hasTaskRefs": has_task_refs,
        "recommendedFormat": "Task Pipeline table plus TASK detail cards is preferred. Kiro-style checkbox tasks are accepted when they are assignable and carry requirement/spec refs.",
    }


def _task_is_large(task: dict[str, Any]) -> bool:
    excerpt = str(task.get("taskExcerpt") or "")
    expected = str(task.get("expectedOutput") or "")
    lane_blob = " ".join(
        str(task.get(key) or "")
        for key in ("runtimeLane", "title", "taskExcerpt")
    ).lower()
    output_count = len(re.findall(r"(?:^|\n)\s*[-*]\s+|`[^`]+`|[A-Za-z]:\\|/[\w.-]+", expected))
    return (
        len(excerpt) > 1100
        or output_count >= 2
        or any(token in lane_blob for token in ("subagent", "sub-agent", "worker", "parallel", "fanout", "子agent", "孙agent", "并行"))
    )


def _tasks_quality_diagnostics(tasks: list[dict[str, Any]]) -> dict[str, Any]:
    blockers: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    large_count = 0
    for task in tasks:
        task_id = str(task.get("taskId") or "").strip()
        large = _task_is_large(task)
        if large:
            large_count += 1
        if not list(task.get("requirementRefs") or []):
            blockers.append({"code": "task_missing_spec_refs", "taskId": task_id, "message": "Task has no requirement/spec refs."})
        if not str(task.get("acceptance") or "").strip():
            warnings.append({"code": "task_missing_acceptance", "taskId": task_id, "message": "Task acceptance is not explicit."})
        if not str(task.get("proofRequired") or "").strip():
            if large:
                blockers.append({"code": "large_task_missing_proof", "taskId": task_id, "message": "Large task requires explicit proofRequired."})
            else:
                warnings.append({"code": "task_missing_proof", "taskId": task_id, "message": "Task proofRequired is not explicit."})
        if large and not str(task.get("mvpSlice") or "").strip():
            blockers.append({"code": "large_task_missing_mvp_slice", "taskId": task_id, "message": "Large task requires an MVP slice."})
        if large and not str(task.get("independentAcceptance") or "").strip():
            blockers.append({"code": "large_task_missing_independent_acceptance", "taskId": task_id, "message": "Large task requires independent acceptance."})
    return {
        "taskCount": len(tasks),
        "largeTaskCount": large_count,
        "hardBlockers": blockers,
        "warnings": warnings,
    }


_REQUIREMENT_REF_RE = re.compile(r"(?<![\d.])(\d{1,2})\.(\d{1,2})(?![\d.])")
_REQUIREMENT_RANGE_RE = re.compile(r"(?<![\d.])(\d{1,2})\.(\d{1,2})\s*[-~–—]\s*(?:(\d{1,2})\.)?(\d{1,2})(?![\d.])")
_EXPLICIT_SPEC_REF_RE = re.compile(r"\b((?:REQ|FR|NFR|BFIX|DES|TASK|TSK|T|AC-REQ|AC-BFIX)-\d{2,})\b", re.IGNORECASE)


def _compact_snippet(value: str, *, limit: int = 520) -> str:
    text = re.sub(r"\s+", " ", str(value or "").replace("|", " ").strip())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def _extract_task_field(block: str, markers: tuple[str, ...]) -> str:
    marker_pattern = "|".join(re.escape(item) for item in markers)
    patterns = [
        rf"(?im)^[ \t]*(?:[-*][ \t]*)?\*\*[ \t]*(?:{marker_pattern})[ \t]*[:：][ \t]*\*\*[ \t]*(.+?)[ \t]*$",
        rf"(?im)^[ \t]*(?:[-*][ \t]*)?(?:\*\*)?[ \t]*(?:{marker_pattern})[ \t]*(?:\*\*)?[ \t]*[:：][ \t]*(.+?)[ \t]*$",
        rf"(?im)^\|[ \t]*(?:\*\*)?[ \t]*(?:{marker_pattern})[ \t]*(?:\*\*)?[ \t]*\|[ \t]*(.+?)[ \t]*\|",
    ]
    for pattern in patterns:
        match = re.search(pattern, block or "")
        if match:
            return _compact_snippet(re.sub(r"[*`]+", "", match.group(1)).strip(), limit=700)
    label_only = re.compile(
        rf"(?i)^[ \t]*(?:[-*][ \t]*)?(?:\*\*)?[ \t]*(?:{marker_pattern})[ \t]*(?:\*\*)?[ \t]*[:：][ \t]*(?:\*\*)?[ \t]*$"
    )
    next_label = re.compile(
        r"^\s*(?:[-*]\s*)?\*\*[^*\n:：]{1,80}(?:\*\*\s*[:：]|[:：]\s*\*\*)"
    )
    lines = str(block or "").splitlines()
    for index, line in enumerate(lines):
        if not label_only.match(line):
            continue
        collected: list[str] = []
        for candidate in lines[index + 1 :]:
            stripped = candidate.strip()
            if not stripped:
                if collected:
                    break
                continue
            if stripped.startswith("---") or re.match(r"^#{2,6}\s+", stripped) or next_label.match(stripped):
                break
            collected.append(stripped)
        if collected:
            return _compact_snippet(re.sub(r"[*`]+", "", "\n".join(collected)).strip(), limit=700)
    return ""


def _split_task_refs(value: str) -> list[str]:
    refs: list[str] = []
    for raw in re.split(r"[,，、\s]+", str(value or "")):
        cleaned = raw.strip().strip("[]()`")
        if re.match(r"^(?:TASK|TSK|T)-\d+", cleaned, re.IGNORECASE):
            _add_unique(refs, _canonical_id(cleaned, "TASK"))
    return refs


def _add_unique(items: list[str], value: str) -> None:
    item = str(value or "").strip()
    if item and item not in items:
        items.append(item)


def _extract_requirement_ref_ids(text: str) -> list[str]:
    """Extract requirement refs from V8-style IDs and Kiro-style `6.1` refs."""

    source = str(text or "")
    refs: list[str] = []
    for match in _EXPLICIT_SPEC_REF_RE.finditer(source):
        _add_unique(refs, _canonical_id(match.group(1), "TASK") if match.group(1).upper().startswith(("TASK-", "TSK-", "T-")) else match.group(1).upper())
    consumed_spans: list[tuple[int, int]] = []
    for match in _REQUIREMENT_RANGE_RE.finditer(source):
        major = int(match.group(1))
        start = int(match.group(2))
        end_major = int(match.group(3) or major)
        end = int(match.group(4))
        consumed_spans.append(match.span())
        if end_major != major or start < 1 or end < start or end - start > 40:
            continue
        for item in range(start, end + 1):
            _add_unique(refs, f"{major}.{item}")
    for match in _REQUIREMENT_REF_RE.finditer(source):
        span = match.span()
        if any(start <= span[0] and span[1] <= end for start, end in consumed_spans):
            continue
        major = int(match.group(1))
        minor = int(match.group(2))
        if minor < 1:
            continue
        _add_unique(refs, f"{major}.{minor}")
    return refs


def _heading_sections(markdown: str) -> list[dict[str, Any]]:
    text = str(markdown or "").replace("\r\n", "\n").replace("\r", "\n")
    matches = list(re.finditer(r"(?m)^(#{2,5})\s+(.+?)\s*$", text))
    sections: list[dict[str, Any]] = []
    for index, match in enumerate(matches):
        start = match.start()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        title = match.group(2).strip()
        sections.append(
            {
                "title": title,
                "level": len(match.group(1)),
                "content": text[start:end].strip(),
                "anchor": re.sub(r"[^a-z0-9\u4e00-\u9fff.-]+", "-", title.lower()).strip("-")[:80] or f"section-{index + 1}",
            }
        )
    return sections


def _requirement_fragments(markdown: str) -> dict[str, dict[str, Any]]:
    text = str(markdown or "")
    fragments: dict[str, dict[str, Any]] = {}
    for line in text.splitlines():
        ids = [item for item in _extract_ids(line, "REQ") + _extract_ids(line, "BFIX") if item]
        ids.extend(item for item in _extract_requirement_ref_ids(line) if re.match(r"^(?:REQ|FR|NFR|BFIX|AC-REQ|AC-BFIX)-", item))
        for item in ids:
            fragments.setdefault(
                item,
                {
                    "id": item,
                    "summary": _compact_snippet(line),
                    "detailRef": f"#{item}",
                    "source": "explicit_id",
                },
            )

    req_heading_re = re.compile(r"(?im)^###\s+(?:需求|Requirement)\s+(\d{1,2})\s*[:：-]?\s*(.*?)\s*$")
    matches = list(req_heading_re.finditer(text))
    for index, match in enumerate(matches):
        major = int(match.group(1))
        title = match.group(2).strip()
        start = match.start()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        section = text[start:end]
        for criterion in re.finditer(r"(?m)^\s*(\d{1,2})\.\s+(.+?)\s*$", section):
            req_id = f"{major}.{int(criterion.group(1))}"
            fragments[req_id] = {
                "id": req_id,
                "requirement": f"需求 {major}" + (f"：{title}" if title else ""),
                "summary": _compact_snippet(criterion.group(2)),
                "detailRef": f"spec://requirements#{req_id}",
                "source": "kiro_acceptance",
            }
    return fragments


_FRAMEWORK_TERMS = (
    "framework",
    "architecture",
    "runtime",
    "技术栈",
    "框架",
    "架构",
    "语言",
    "平台",
    "小程序",
    "uni-app",
    "react",
    "vue",
    "typescript",
    "javascript",
    "python",
    "node",
    "canvas",
)


def _design_fragments(markdown: str) -> tuple[list[dict[str, Any]], str]:
    sections = _heading_sections(markdown)
    fragments: list[dict[str, Any]] = []
    framework_lines: list[str] = []
    for index, section in enumerate(sections):
        content = str(section.get("content") or "")
        title = str(section.get("title") or "")
        refs = _extract_requirement_ref_ids(content)
        explicit_design_ids = _extract_ids(content, "DES")
        lower_blob = f"{title}\n{content}".lower()
        is_framework = any(term.lower() in lower_blob for term in _FRAMEWORK_TERMS)
        if is_framework:
            for line in content.splitlines()[:18]:
                stripped = line.strip()
                if stripped and (any(term.lower() in stripped.lower() for term in _FRAMEWORK_TERMS) or len(framework_lines) < 4):
                    framework_lines.append(stripped)
        if refs or explicit_design_ids or is_framework:
            section_id = explicit_design_ids[0] if explicit_design_ids else f"DES-SECTION-{index + 1:02d}"
            fragments.append(
                {
                    "id": section_id,
                    "title": title,
                    "requirementRefs": refs[:24],
                    "detailRef": f"spec://design#{section.get('anchor')}",
                    "summary": _compact_snippet(content, limit=700),
                    "framework": bool(is_framework),
                }
            )
    framework_digest = "\n".join(dict.fromkeys(framework_lines))[:1400]
    if not framework_digest and markdown:
        framework_digest = _compact_snippet(markdown, limit=900)
    return fragments[:18], framework_digest


def _task_slices(markdown: str, requirement_index: dict[str, dict[str, Any]], design_fragments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    text = str(markdown or "").replace("\r\n", "\n").replace("\r", "\n")
    task_re = re.compile(r"(?m)^(\s*)-\s+\[[ xX]\]\s+((?:TASK|TSK|T)-\d{2,}|\d+(?:\.\d+)*)\.?\s*(.*)$", re.IGNORECASE)
    matches = list(task_re.finditer(text))
    task_style = "checkbox"
    if not matches:
        task_re = re.compile(
            r"(?m)^#{2,6}\s+((?:TASK|TSK|T)-\d{2,})\s*[:：.-]?\s*(.*?)\s*$",
            re.IGNORECASE,
        )
        matches = list(task_re.finditer(text))
        task_style = "heading"
    slices: list[dict[str, Any]] = []
    for index, match in enumerate(matches):
        raw_id = match.group(2 if task_style == "checkbox" else 1).strip()
        title = match.group(3 if task_style == "checkbox" else 2).strip()
        start = match.start()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        block = text[start:end].strip()
        body_for_refs = "\n".join(block.splitlines()[1:]).strip()
        if re.match(r"^(?:TASK|TSK|T)-", raw_id, re.IGNORECASE):
            task_id = _canonical_id(raw_id, "TASK")
        else:
            task_id = f"TASK-{raw_id}"
        runtime_lane = _extract_task_field(
            block,
            ("runtimeLane", "runtime lane", "lane", "Runtime", "执行通道", "执行泳道", "执行层级", "执行角色", "执行方"),
        )
        depends_on = _split_task_refs(_extract_task_field(block, ("dependsOn", "depends on", "depends", "依赖", "依赖关系")))
        expected_output = _extract_task_field(
            block,
            ("expectedOutput", "expected output", "output", "输出", "输出文件", "预期输出", "预期输出路径", "产物"),
        )
        acceptance = _extract_task_field(block, ("acceptance", "acceptanceProof", "验收", "验收标准", "验收检查", "验收方式"))
        proof_required = _extract_task_field(block, ("proofRequired", "proof required", "proof", "证明", "证明方式", "证明材料"))
        mvp_slice = _extract_task_field(block, ("mvpSlice", "mvp slice", "MVP", "MVP 切片", "最小切片", "最小可验收切片"))
        independent_acceptance = _extract_task_field(
            block,
            ("independentAcceptance", "independent acceptance", "独立验收", "独立验收方式", "独立可验收"),
        )
        req_refs = [
            item
            for item in _extract_requirement_ref_ids(body_for_refs)
            if item in requirement_index or re.match(r"^(?:REQ|FR|NFR|BFIX|AC-REQ|AC-BFIX)-", item)
        ]
        direct_design_refs = _extract_ids(body_for_refs, "DES")

        def _matches_design_ref(fragment: dict[str, Any]) -> bool:
            fragment_id = str(fragment.get("id") or "").strip().upper()
            if not fragment_id:
                return False
            if fragment_id in direct_design_refs:
                return True
            fragment_number = re.search(r"(\d+)$", fragment_id)
            if not fragment_number:
                return False
            return any(
                (match := re.search(r"(\d+)$", ref)) is not None
                and int(match.group(1)) == int(fragment_number.group(1))
                for ref in direct_design_refs
            )

        design_matches = [
            item
            for item in design_fragments
            if _matches_design_ref(item)
            or set(req_refs).intersection(set(item.get("requirementRefs") or []))
            or (item.get("framework") and req_refs)
        ]
        slices.append(
            {
                "taskId": task_id,
                "title": _compact_snippet(title, limit=220),
                "requirementRefs": req_refs[:18],
                "requirementSnippets": [
                    {
                        "id": ref,
                        "summary": requirement_index.get(ref, {}).get("summary", ""),
                        "detailRef": requirement_index.get(ref, {}).get("detailRef", f"spec://requirements#{ref}"),
                    }
                    for ref in req_refs[:8]
                ],
                "designRefs": [str(item.get("id") or "") for item in design_matches[:6] if item.get("id")],
                "designSnippets": [
                    {
                        "id": item.get("id"),
                        "title": item.get("title"),
                        "summary": item.get("summary"),
                        "detailRef": item.get("detailRef"),
                    }
                    for item in design_matches[:3]
                ],
                "taskExcerpt": _compact_snippet(block, limit=900),
                "detailRef": f"spec://tasks#{task_id}",
                "runtimeLane": runtime_lane,
                "dependsOn": depends_on,
                "expectedOutput": expected_output,
                "acceptance": acceptance,
                "proofRequired": proof_required,
                "mvpSlice": mvp_slice,
                "independentAcceptance": independent_acceptance,
            }
        )
    return slices[:30]


def _stage_format_diagnostics(stage: str, content: str) -> dict[str, Any]:
    normalized_stage = str(stage or "").strip().lower()
    text = str(content or "")
    missing: list[str] = []
    warnings: list[str] = []
    approval_blocking: list[str] = []
    if normalized_stage in {"requirements", "bugfix"}:
        fragments = _requirement_fragments(text)
        has_acceptance = bool(
            re.search(r"(?i)\bWHEN\b.+\bTHEN\b|\bSHALL\b|验收标准|Acceptance Criteria|AC-REQ-|AC-BFIX-", text)
        )
        if not fragments:
            missing.append("requirementIds")
        if not has_acceptance:
            missing.append("acceptanceCriteria")
            warnings.append("acceptanceCriteria")
        return {
            "stage": normalized_stage,
            "valid": not approval_blocking,
            "ids": list(fragments.keys())[:80],
            "idCount": len(fragments),
            "missingFields": missing,
            "warnings": warnings,
            "approvalBlocking": approval_blocking,
            "recommendedContractRef": "stageContract",
            "recommendedFormat": (
                "Prefer stable REQ-001/BFIX-001 or Kiro-style numbered requirements with acceptance criteria. "
                "Loose requirements may still be approved, but tasks.md must later provide traceable task IDs and refs."
            ),
        }
    if normalized_stage == "design":
        design_fragments, framework_digest = _design_fragments(text)
        design_ids = _extract_ids(text, "DES")
        if not design_fragments and not design_ids:
            missing.append("designSections")
        if not framework_digest.strip():
            missing.append("frameworkDigest")
            warnings.append("frameworkDigest")
        return {
            "stage": normalized_stage,
            "valid": not approval_blocking,
            "ids": design_ids or [str(item.get("id") or "") for item in design_fragments if item.get("id")],
            "idCount": len(design_ids or design_fragments),
            "frameworkDigestPresent": bool(framework_digest.strip()),
            "missingFields": missing,
            "warnings": warnings,
            "approvalBlocking": approval_blocking,
            "recommendedContractRef": "stageContract",
            "recommendedFormat": (
                "Prefer DES-001 style design sections or clear architecture/framework headings. "
                "Loose design may still be approved, but tasks.md must later bind executable work to requirement/design refs."
            ),
        }
    if normalized_stage == "tasks":
        pipeline = _tasks_pipeline_diagnostics(text)
        requirement_index = _requirement_fragments("")
        task_slices = _task_slices(text, requirement_index, [])
        if not pipeline.get("taskIds") and not task_slices:
            missing.append("taskIds")
            approval_blocking.append("taskIds")
        if pipeline.get("missingFields"):
            missing.extend(str(item) for item in pipeline.get("missingFields") or [])
        if pipeline.get("approvalBlocking"):
            approval_blocking.extend(str(item) for item in pipeline.get("approvalBlocking") or [])
        return {
            "stage": normalized_stage,
            "valid": not approval_blocking and bool(pipeline.get("valid")),
            "ids": list(pipeline.get("taskIds") or [item.get("taskId") for item in task_slices if item.get("taskId")]),
            "idCount": int(pipeline.get("taskCount") or len(task_slices)),
            "missingFields": missing,
            "warnings": warnings,
            "approvalBlocking": list(dict.fromkeys(approval_blocking)),
            "pipelineDiagnostics": pipeline,
            "recommendedContractRef": "stageContract",
            "recommendedFormat": pipeline.get("recommendedFormat"),
        }
    return {
        "stage": normalized_stage,
        "valid": True,
        "ids": [],
        "idCount": 0,
        "missingFields": [],
        "warnings": [],
        "approvalBlocking": [],
        "recommendedContractRef": "stageContract",
    }


@dataclass(slots=True)
class SpecPaths:
    workspace: Path
    root: Path
    slug: str
    spec_dir: Path
    manifest: Path


class SpecService:
    """Controlled writer/reader for workspace-local V8OS Spec artifacts."""

    def resolve_paths(self, workspace_path: str, *, feature_name: str | None = None, spec_id: str | None = None) -> SpecPaths:
        workspace = Path(workspace_path).expanduser().resolve()
        if not workspace.exists() or not workspace.is_dir():
            raise ValueError(f"workspace_not_found:{workspace}")
        root = workspace.joinpath(*SPEC_ROOT_PARTS)
        slug_source = feature_name or spec_id or "spec"
        slug = _slugify(slug_source)
        if spec_id:
            existing = self._find_by_spec_id(root, spec_id)
            if existing:
                slug = existing.name
        return SpecPaths(workspace=workspace, root=root, slug=slug, spec_dir=root / slug, manifest=root / slug / "spec.json")

    def _new_spec_paths(self, workspace_path: str, *, feature_name: str) -> SpecPaths:
        base = self.resolve_paths(workspace_path, feature_name=feature_name)
        if not base.manifest.exists():
            return base
        for _ in range(20):
            suffix = uuid.uuid4().hex[:8]
            slug = _slugify(f"{feature_name}-{suffix}")
            candidate = SpecPaths(
                workspace=base.workspace,
                root=base.root,
                slug=slug,
                spec_dir=base.root / slug,
                manifest=base.root / slug / "spec.json",
            )
            if not candidate.manifest.exists():
                return candidate
        raise RuntimeError("spec_unique_slug_exhausted")

    def _find_by_spec_id(self, root: Path, spec_id: str) -> Path | None:
        if not root.exists():
            return None
        for manifest in root.glob("*/spec.json"):
            try:
                payload = json.loads(manifest.read_text(encoding="utf-8"))
            except Exception:
                continue
            if str(payload.get("specId") or "") == str(spec_id or ""):
                return manifest.parent
        return None

    def _load_manifest(self, paths: SpecPaths) -> dict[str, Any]:
        if paths.manifest.exists():
            try:
                return json.loads(paths.manifest.read_text(encoding="utf-8"))
            except Exception:
                return {}
        return {}

    def _manifest_summary(self, paths: SpecPaths, manifest: dict[str, Any]) -> dict[str, Any]:
        docs = dict(manifest.get("documents") or {})
        return {
            "specId": manifest.get("specId"),
            "featureName": manifest.get("featureName"),
            "slug": manifest.get("slug") or paths.slug,
            "specKind": manifest.get("kind"),
            "lifecycle": manifest.get("lifecycle") or SPEC_LIFECYCLE_ACTIVE,
            "currentStage": manifest.get("currentStage"),
            "workspacePath": manifest.get("workspacePath") or str(paths.workspace),
            "specDir": str(paths.spec_dir),
            "createdAt": manifest.get("createdAt"),
            "updatedAt": manifest.get("updatedAt"),
            "pipelineControl": self._pipeline_control(manifest),
            "linkedSections": self._linked_sections(manifest),
            "qualityEvidence": manifest.get("qualityEvidence") or {"checklists": {}},
            "annexDocuments": manifest.get("annexDocuments") or {},
            "clarificationSummary": self._clarification_summary(manifest),
            "documents": {
                stage: {
                    "relativePath": value.get("relativePath"),
                    "ids": list(value.get("ids") or []),
                    "detailRef": f"spec://{manifest.get('specId')}/{stage}",
                    "version": value.get("version"),
                    "status": value.get("status"),
                    "updatedAt": value.get("updatedAt"),
                    "approvedAt": value.get("approvedAt"),
                    **(
                        {"pipelineDiagnostics": value.get("pipelineDiagnostics")}
                        if stage == "tasks" and isinstance(value.get("pipelineDiagnostics"), dict)
                        else {}
                    ),
                    **(
                        {"formatDiagnostics": value.get("formatDiagnostics")}
                        if isinstance(value.get("formatDiagnostics"), dict)
                        else {}
                    ),
                }
                for stage, value in docs.items()
                if isinstance(value, dict)
            },
            "openComments": [
                item
                for item in list(manifest.get("comments") or [])
                if isinstance(item, dict) and str(item.get("status") or "open") == "open"
            ][:12],
        }

    def list_specs(self, *, workspace_path: str, include_archived: bool = False, limit: int = 100) -> dict[str, Any]:
        workspace = Path(workspace_path).expanduser().resolve()
        if not workspace.exists() or not workspace.is_dir():
            raise ValueError(f"workspace_not_found:{workspace}")
        root = workspace.joinpath(*SPEC_ROOT_PARTS)
        specs: list[dict[str, Any]] = []
        if root.exists():
            for manifest_path in root.glob("*/spec.json"):
                paths = SpecPaths(
                    workspace=workspace,
                    root=root,
                    slug=manifest_path.parent.name,
                    spec_dir=manifest_path.parent,
                    manifest=manifest_path,
                )
                manifest = self._load_manifest(paths)
                if not manifest:
                    continue
                lifecycle = str(manifest.get("lifecycle") or SPEC_LIFECYCLE_ACTIVE)
                if lifecycle in _INACTIVE_SPEC_LIFECYCLES and not include_archived:
                    continue
                specs.append(self._manifest_summary(paths, manifest))
        specs.sort(key=lambda item: str(item.get("updatedAt") or item.get("createdAt") or ""), reverse=True)
        capped_limit = max(1, min(int(limit or 100), 300))
        return {
            "ok": True,
            "kind": "spec_list",
            "workspacePath": str(workspace),
            "count": len(specs[:capped_limit]),
            "specs": specs[:capped_limit],
        }

    def read_spec(self, *, workspace_path: str, spec_id: str, max_chars: int = 60000) -> dict[str, Any]:
        paths = self.resolve_paths(workspace_path, spec_id=spec_id)
        manifest = self._load_manifest(paths)
        if not manifest:
            raise ValueError(f"spec_not_found:{spec_id}")
        capped_chars = max(1000, min(int(max_chars or 60000), 200000))
        stages: dict[str, dict[str, Any]] = {}
        for stage, filename in SPEC_DOCS.items():
            doc_path = paths.spec_dir / filename
            if not doc_path.exists():
                continue
            content = doc_path.read_text(encoding="utf-8", errors="ignore")
            stages[stage] = {
                "stage": stage,
                "documentRef": f"spec://{spec_id}/{stage}",
                "content": _safe_text(content, limit=capped_chars),
                "truncated": len(content) > capped_chars,
                "ids": self._document_ids(stage, content),
                "relativePath": str(doc_path.relative_to(paths.workspace)).replace("\\", "/"),
            }
        return {
            "ok": True,
            "kind": "spec_detail",
            "spec": self._manifest_summary(paths, manifest),
            "stages": stages,
            "analysis": self.analyze_spec(workspace_path=workspace_path, spec_id=spec_id),
            "specBrief": self.build_brief(workspace_path=workspace_path, spec_id=spec_id),
        }

    def _write_manifest(self, paths: SpecPaths, manifest: dict[str, Any]) -> None:
        paths.spec_dir.mkdir(parents=True, exist_ok=True)
        manifest["schemaVersion"] = max(2, int(manifest.get("schemaVersion") or 1))
        manifest["updatedAt"] = _now_iso()
        paths.manifest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    def _stage_quality_items(self, paths: SpecPaths, manifest: dict[str, Any]) -> list[dict[str, Any]]:
        docs = dict(manifest.get("documents") or {})
        kind = str(manifest.get("kind") or "feature")
        primary_stage = "bugfix" if kind == "bugfix" else "requirements"
        primary_text = self._read_stage_content(paths, manifest, primary_stage)
        design_text = self._read_stage_content(paths, manifest, "design")
        tasks_text = self._read_stage_content(paths, manifest, "tasks")
        primary_diag = _stage_format_diagnostics(primary_stage, primary_text) if primary_text else {}
        design_diag = _stage_format_diagnostics("design", design_text) if design_text else {}
        tasks_diag = _tasks_pipeline_diagnostics(tasks_text) if tasks_text else {}
        clarifications = [
            item
            for item in list(manifest.get("clarifications") or [])
            if isinstance(item, dict) and str(item.get("status") or "resolved") in {"resolved", "answered"}
        ]
        traceability = self._traceability_index(paths, manifest)
        distribution = dict(traceability.get("distributionChecks") or {})

        def item(code: str, label: str, done: bool, evidence: str = "") -> dict[str, Any]:
            return {
                "code": code,
                "label": label,
                "done": bool(done),
                **({"evidence": _safe_text(evidence, limit=500)} if evidence else {}),
            }

        return [
            item("source_request_captured", "Source request captured", bool(str(manifest.get("sourceRequest") or "").strip())),
            item("human_clarification_recorded", "Spec clarification recorded via ask_user", bool(clarifications), f"{len(clarifications)} clarification(s)"),
            item("primary_ids_present", f"{primary_stage} has stable IDs", bool(primary_diag.get("ids")), ", ".join(list(primary_diag.get("ids") or [])[:8])),
            item("primary_acceptance_present", f"{primary_stage} has acceptance criteria", "acceptanceCriteria" not in set(primary_diag.get("missingFields") or [])),
            item("design_trace_present", "Design has traceable sections", bool(design_diag.get("ids")) if design_text else "design" not in docs),
            item("tasks_trace_present", "Tasks have assignable IDs and spec refs", bool(tasks_diag.get("valid")) if tasks_text else "tasks" not in docs),
            item("tasks_requirements_linked", "Runtime tasks link requirement refs", bool(distribution.get("allTasksHaveRequirementRefs")) if tasks_text else "tasks" not in docs),
            item("tasks_design_linked", "Runtime tasks carry design/framework context", bool(distribution.get("allTasksHaveDesignRefs")) if tasks_text else "tasks" not in docs),
        ]

    def _update_quality_checklist(self, paths: SpecPaths, manifest: dict[str, Any]) -> dict[str, Any]:
        items = self._stage_quality_items(paths, manifest)
        unresolved = [item for item in items if not bool(item.get("done"))]
        rel_path = SPEC_CHECKLISTS["requirements"]
        checklist_path = paths.spec_dir / rel_path
        checklist_path.parent.mkdir(parents=True, exist_ok=True)
        lines = [
            f"# Spec Quality Checklist: {manifest.get('featureName') or manifest.get('specId') or paths.slug}",
            "",
            "This checklist is approval evidence only. It does not replace human Spec approval.",
            "",
        ]
        for item in items:
            marker = "x" if item.get("done") else " "
            evidence = f" — {item.get('evidence')}" if item.get("evidence") else ""
            lines.append(f"- [{marker}] {item.get('label')}{evidence}")
        checklist_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
        entry = {
            "kind": "checklist",
            "stage": "requirements",
            "title": "Requirements Quality Checklist",
            "relativePath": str(checklist_path.relative_to(paths.workspace)).replace("\\", "/"),
            "status": "complete" if not unresolved else "open",
            "unresolvedCount": len(unresolved),
            "items": items,
            "detailRef": f"spec://{manifest.get('specId')}/checklists/requirements",
            "updatedAt": _now_iso(),
        }
        quality = manifest.setdefault("qualityEvidence", {})
        quality.setdefault("checklists", {})["requirements"] = entry
        return entry

    def _detect_annex_needs(self, manifest: dict[str, Any], *texts: str) -> set[str]:
        blob = "\n".join([str(manifest.get("sourceRequest") or ""), *(str(text or "") for text in texts)]).lower()
        needs: set[str] = set()
        if re.search(r"\b(api|http|sse|webhook|schema|contract|openapi|provider|sdk)\b|接口|契约|协议|供应商|模型", blob):
            needs.add("contracts")
        if re.search(r"\b(research|unknown|compare|benchmark|source|doc|docs)\b|调研|未知|资料|证据|参考文档|对比", blob):
            needs.add("research")
        if re.search(r"\b(quickstart|smoke|e2e|acceptance|setup|install|run)\b|验收|烟测|启动|安装|运行|复现", blob):
            needs.add("quickstart")
        if re.search(r"subagent|sub-agent|worker|fanout|parallel|跨\s*runtime|子\s*agent|孙\s*agent|并行", blob):
            needs.update({"research", "quickstart"})
        return needs

    def _ensure_annex_documents(self, paths: SpecPaths, manifest: dict[str, Any]) -> dict[str, Any]:
        docs = dict(manifest.get("documents") or {})
        texts = [self._read_stage_content(paths, manifest, stage) for stage in ("requirements", "bugfix", "design", "tasks")]
        needs = self._detect_annex_needs(manifest, *texts)
        annex_root = manifest.setdefault("annexDocuments", {})
        for annex_name in sorted(needs):
            rel_path = SPEC_ANNEX_DOCS[annex_name]
            annex_path = paths.spec_dir / rel_path
            if not annex_path.exists():
                annex_path.parent.mkdir(parents=True, exist_ok=True)
                title = {
                    "research": "Research Notes",
                    "contracts": "Contracts",
                    "quickstart": "Quickstart / Acceptance",
                }.get(annex_name, annex_name.title())
                annex_path.write_text(
                    "\n".join(
                        [
                            f"# {title}: {manifest.get('featureName') or manifest.get('specId') or paths.slug}",
                            "",
                            "Status: draft evidence annex generated by Spec Mode.",
                            "",
                            "## Purpose",
                            "",
                            "- Capture supporting context that should travel with the approved Spec execution packet.",
                            "",
                            "## Notes",
                            "",
                            "- Supervisor should keep this concise and source-backed when the feature needs it.",
                        ]
                    )
                    + "\n",
                    encoding="utf-8",
                )
            annex_root[annex_name] = {
                "kind": "annex",
                "title": {
                    "research": "Research Notes",
                    "contracts": "Contracts",
                    "quickstart": "Quickstart / Acceptance",
                }.get(annex_name, annex_name.title()),
                "relativePath": str(annex_path.relative_to(paths.workspace)).replace("\\", "/"),
                "status": "draft",
                "detailRef": f"spec://{manifest.get('specId')}/annex/{annex_name}",
                "updatedAt": _now_iso(),
            }
        return annex_root

    def _refresh_quality_artifacts(self, paths: SpecPaths, manifest: dict[str, Any]) -> None:
        self._update_quality_checklist(paths, manifest)
        self._ensure_annex_documents(paths, manifest)

    def _version_dir(self, paths: SpecPaths, stage: str) -> Path:
        return paths.spec_dir / ".versions" / stage

    def _record_version(
        self,
        paths: SpecPaths,
        manifest: dict[str, Any],
        *,
        stage: str,
        previous_content: str,
        action: str,
        reason: str = "",
    ) -> dict[str, Any] | None:
        if not previous_content:
            return None
        version_id = f"ver_{uuid.uuid4().hex[:12]}"
        created_at = _now_iso()
        version_dir = self._version_dir(paths, stage)
        version_dir.mkdir(parents=True, exist_ok=True)
        version_path = version_dir / f"{created_at.replace(':', '').replace('-', '').replace('Z', '')}_{version_id}.md"
        version_path.write_text(previous_content, encoding="utf-8")
        entry = {
            "versionId": version_id,
            "stage": stage,
            "action": _safe_text(action, limit=80),
            "reason": _safe_text(reason, limit=500),
            "createdAt": created_at,
            "relativePath": str(version_path.relative_to(paths.workspace)).replace("\\", "/"),
            "sha256": hashlib.sha256(previous_content.encode("utf-8", errors="ignore")).hexdigest(),
        }
        manifest.setdefault("versionHistory", []).append(entry)
        return entry

    def _downstream_stages(self, manifest: dict[str, Any], stage: str) -> list[str]:
        order = _stage_order(str(manifest.get("kind") or "feature"))
        try:
            index = order.index(stage)
        except ValueError:
            return []
        return list(order[index + 1 :])

    def _mark_stage_changed(
        self,
        manifest: dict[str, Any],
        *,
        stage: str,
        reason: str,
        revoke_current_approval: bool = True,
    ) -> None:
        approvals = manifest.setdefault("approvals", {})
        now = _now_iso()
        if revoke_current_approval and isinstance(approvals.get(stage), dict):
            approvals[stage]["approved"] = False
            approvals[stage]["revokedAt"] = now
            approvals[stage]["revocationReason"] = _safe_text(reason, limit=500)
        stale = manifest.setdefault("staleStages", {})
        for downstream in self._downstream_stages(manifest, stage):
            stale[downstream] = {
                "stale": True,
                "reason": _safe_text(reason or f"{stage}_changed", limit=500),
                "sourceStage": stage,
                "markedAt": now,
            }
            if isinstance(approvals.get(downstream), dict):
                approvals[downstream]["approved"] = False
                approvals[downstream]["revokedAt"] = now
                approvals[downstream]["revocationReason"] = "stale_downstream"

    def _clear_stage_stale(self, manifest: dict[str, Any], stage: str) -> None:
        stale = manifest.setdefault("staleStages", {})
        if isinstance(stale.get(stage), dict):
            stale[stage]["stale"] = False
            stale[stage]["clearedAt"] = _now_iso()

    def _stage_is_approved(self, manifest: dict[str, Any], stage: str) -> bool:
        approval = dict(manifest.get("approvals") or {}).get(stage)
        return isinstance(approval, dict) and bool(approval.get("approved"))

    def _clarification_summary(self, manifest: dict[str, Any]) -> dict[str, Any]:
        rows = [item for item in list(manifest.get("clarifications") or []) if isinstance(item, dict)]
        by_stage: dict[str, int] = {}
        latest: list[dict[str, Any]] = []
        for item in rows:
            stage = str(item.get("stage") or "unknown").strip().lower() or "unknown"
            by_stage[stage] = by_stage.get(stage, 0) + 1
        for item in rows[-5:]:
            latest.append(
                {
                    "stage": item.get("stage"),
                    "question": _safe_text(item.get("question"), limit=240),
                    "answer": _safe_text(item.get("answer"), limit=360),
                    "createdAt": item.get("createdAt"),
                    "sourceRunId": item.get("sourceRunId"),
                }
            )
        return {"count": len(rows), "byStage": by_stage, "latest": latest}

    def _approved_stage_locked_payload(self, manifest: dict[str, Any], *, workspace_path: str, stage: str, action: str) -> dict[str, Any]:
        spec_id = str(manifest.get("specId") or "")
        next_stage = self.next_stage(manifest, stage)
        return {
            "ok": False,
            "kind": "spec_stage_locked",
            "stage": stage,
            "action": action,
            "specId": spec_id,
            "nextStage": next_stage,
            "summary": (
                f"Spec stage '{stage}' is already approved and locked. "
                "Do not rewrite or edit an approved stage in this run; continue to the next stage."
            ),
            "recommendedNextAction": (
                f"Use spec_broker(mode='write_stage', stage='{next_stage}', spec_id='{spec_id}', content='...') "
                "when nextStage is a document stage, or route runtime execution when nextStage is runtime_execution."
            ),
            "pipelineControl": self._pipeline_control(manifest),
            "specBrief": self.build_brief(workspace_path=workspace_path, spec_id=spec_id),
        }

    def _ensure_manifest(self, paths: SpecPaths, *, feature_name: str, kind: str, user_request: str) -> dict[str, Any]:
        manifest = self._load_manifest(paths)
        if not manifest:
            manifest = {
                "schemaVersion": 2,
                "specId": f"spec_{uuid.uuid4().hex[:16]}",
                "featureName": feature_name,
                "slug": paths.slug,
                "kind": kind,
                "workspacePath": str(paths.workspace),
                "createdAt": _now_iso(),
                "updatedAt": _now_iso(),
                "sourceRequest": _safe_text(user_request, limit=4000),
                "approvals": {},
                "comments": [],
                "documents": {},
                "clarifications": [],
                "qualityEvidence": {"checklists": {}},
                "annexDocuments": {},
                "versionHistory": [],
                "staleStages": {},
                "lifecycle": SPEC_LIFECYCLE_ACTIVE,
                "currentStage": "bugfix" if kind == "bugfix" else "requirements",
            }
        else:
            manifest.setdefault("approvals", {})
            manifest.setdefault("comments", [])
            manifest.setdefault("documents", {})
            manifest.setdefault("clarifications", [])
            manifest.setdefault("qualityEvidence", {"checklists": {}})
            manifest.setdefault("annexDocuments", {})
            manifest.setdefault("versionHistory", [])
            manifest.setdefault("staleStages", {})
            manifest.setdefault("lifecycle", SPEC_LIFECYCLE_ACTIVE)
            manifest.setdefault("workspacePath", str(paths.workspace))
        return manifest

    def create_stage(
        self,
        *,
        workspace_path: str,
        user_request: str,
        feature_name: str | None = None,
        spec_id: str | None = None,
        stage: str | None = None,
        kind: str | None = None,
        overwrite: bool = False,
    ) -> dict[str, Any]:
        request_text = _safe_text(user_request)
        inferred_kind = str(kind or "").strip().lower()
        if inferred_kind not in {"feature", "bugfix"}:
            lowered = request_text.lower()
            inferred_kind = "bugfix" if any(token in lowered for token in ("bug", "报错", "修复", "debug", "失败", "异常")) else "feature"
        initial_stage = "bugfix" if inferred_kind == "bugfix" else "requirements"
        normalized_stage = str(stage or initial_stage).strip().lower()
        if normalized_stage not in SPEC_DOCS:
            raise ValueError(f"unsupported_spec_stage:{normalized_stage}")
        title_source = feature_name or (request_text.splitlines()[0] if request_text else "V8OS Spec")
        title = _safe_text(title_source, limit=80)
        if not spec_id and normalized_stage == initial_stage:
            paths = self._new_spec_paths(workspace_path, feature_name=title)
        else:
            paths = self.resolve_paths(workspace_path, feature_name=title, spec_id=spec_id)
            if spec_id and str(spec_id).startswith("spec_") and not paths.manifest.exists():
                raise ValueError(f"spec_not_found:{spec_id}")
        manifest = self._ensure_manifest(paths, feature_name=title, kind=inferred_kind, user_request=request_text)
        kind_value = str(manifest.get("kind") or inferred_kind)
        dependency = _stage_dependency(normalized_stage, kind_value)
        if dependency and not (manifest.get("approvals") or {}).get(dependency, {}).get("approved"):
            return {
                "ok": False,
                "kind": "spec_stage_blocked",
                "stage": normalized_stage,
                "requiredApproval": dependency,
                "summary": f"Spec stage '{normalized_stage}' is blocked until '{dependency}' is approved.",
                "pipelineControl": self._pipeline_control(manifest),
                "specBrief": self.build_brief(workspace_path=workspace_path, spec_id=str(manifest.get("specId") or "")),
            }
        if self._stage_is_approved(manifest, normalized_stage):
            return self._approved_stage_locked_payload(
                manifest,
                workspace_path=workspace_path,
                stage=normalized_stage,
                action="create_stage" if not overwrite else "overwrite_stage",
            )
        filename = SPEC_DOCS[normalized_stage]
        doc_path = paths.spec_dir / filename
        previous_content = doc_path.read_text(encoding="utf-8", errors="ignore") if doc_path.exists() else ""
        if doc_path.exists() and not overwrite:
            content = previous_content
        else:
            if normalized_stage == "requirements":
                content = _template_requirements(title, request_text)
            elif normalized_stage == "bugfix":
                content = _template_bugfix(title, request_text)
            elif normalized_stage == "design":
                content = _template_design(title, kind_value, dependency or initial_stage)
            else:
                content = _template_tasks(title)
            content, id_allocation = _assign_missing_stage_ids(normalized_stage, content)
            paths.spec_dir.mkdir(parents=True, exist_ok=True)
            if previous_content:
                self._record_version(
                    paths,
                    manifest,
                    stage=normalized_stage,
                    previous_content=previous_content,
                    action="rewrite_stage",
                    reason="create_stage_overwrite" if overwrite else "create_stage",
                )
            doc_path.write_text(content, encoding="utf-8")
            if previous_content != content:
                self._mark_stage_changed(
                    manifest,
                    stage=normalized_stage,
                    reason="stage_rewritten",
                    revoke_current_approval=True,
                )
        if doc_path.exists() and not overwrite:
            content, id_allocation = _assign_missing_stage_ids(normalized_stage, content)
            if content != previous_content:
                self._record_version(
                    paths,
                    manifest,
                    stage=normalized_stage,
                    previous_content=previous_content,
                    action="normalize_stage_ids",
                    reason="server_id_allocation",
                )
                doc_path.write_text(content, encoding="utf-8")
                self._mark_stage_changed(
                    manifest,
                    stage=normalized_stage,
                    reason="stage_ids_allocated",
                    revoke_current_approval=True,
                )
        else:
            id_allocation = locals().get("id_allocation", {"allocatedIds": [], "existingIds": self._document_ids(normalized_stage, content)})
        self._clear_stage_stale(manifest, normalized_stage)
        manifest["currentStage"] = normalized_stage
        format_diagnostics = _stage_format_diagnostics(normalized_stage, content)
        doc_version = int((manifest.get("documents") or {}).get(normalized_stage, {}).get("version") or 0) + (
            1 if previous_content and previous_content != content else 0
        )
        manifest.setdefault("documents", {})[normalized_stage] = {
            "path": str(doc_path),
            "relativePath": str(doc_path.relative_to(paths.workspace)).replace("\\", "/"),
            "updatedAt": _now_iso(),
            "ids": self._document_ids(normalized_stage, content),
            "version": max(1, doc_version),
            "status": "draft",
            "formatDiagnostics": format_diagnostics,
            "idAllocation": id_allocation,
        }
        if normalized_stage == "tasks":
            manifest["documents"][normalized_stage]["pipelineDiagnostics"] = _tasks_pipeline_diagnostics(content)
        self._refresh_quality_artifacts(paths, manifest)
        self._write_manifest(paths, manifest)
        tasks_pipeline = (
            dict(manifest["documents"][normalized_stage].get("pipelineDiagnostics") or {})
            if normalized_stage == "tasks"
            else None
        )
        return {
            "ok": True,
            "kind": "spec_stage_ready",
            "stage": normalized_stage,
            "specId": manifest.get("specId"),
            "specDir": str(paths.spec_dir),
            "document": manifest["documents"][normalized_stage],
            "pipelineControl": self._pipeline_control(manifest),
            "linkedSections": self._linked_sections(manifest),
            "summary": f"Spec stage '{normalized_stage}' is ready for review.",
            "formatDiagnostics": format_diagnostics,
            "idAllocation": id_allocation,
            "specBrief": self.build_brief(workspace_path=workspace_path, spec_id=str(manifest.get("specId") or "")),
            **({"tasksPipeline": tasks_pipeline} if tasks_pipeline is not None else {}),
        }

    def _document_ids(self, stage: str, content: str) -> list[str]:
        return _stage_ids_from_text(stage, content)

    def _read_stage_content(self, paths: SpecPaths, manifest: dict[str, Any], stage: str) -> str:
        doc_meta = dict((manifest.get("documents") or {}).get(stage) or {})
        rel = str(doc_meta.get("relativePath") or "").strip()
        filename = SPEC_DOCS.get(stage)
        candidates: list[Path] = []
        if rel:
            candidates.append(paths.workspace / rel)
        if filename:
            candidates.append(paths.spec_dir / filename)
        for candidate in candidates:
            try:
                path = candidate.resolve()
                path.relative_to(paths.workspace)
                if path.exists() and path.is_file():
                    return path.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
        return ""

    def _traceability_index(self, paths: SpecPaths, manifest: dict[str, Any]) -> dict[str, Any]:
        requirements_text = self._read_stage_content(paths, manifest, "requirements")
        if not requirements_text:
            requirements_text = self._read_stage_content(paths, manifest, "bugfix")
        design_text = self._read_stage_content(paths, manifest, "design")
        tasks_text = self._read_stage_content(paths, manifest, "tasks")
        requirement_index = _requirement_fragments(requirements_text)
        design_index, framework_digest = _design_fragments(design_text)
        task_slices = _task_slices(tasks_text, requirement_index, design_index)
        known_req_ids = set(requirement_index.keys())
        missing_refs: list[dict[str, Any]] = []
        for task in task_slices:
            for ref in list(task.get("requirementRefs") or []):
                if ref not in known_req_ids and not re.match(r"^(?:REQ|FR|NFR|BFIX|AC-REQ|AC-BFIX)-", ref):
                    missing_refs.append({"taskId": task.get("taskId"), "requirementRef": ref, "reason": "requirement_fragment_missing"})
        tasks_with_requirements = sum(1 for task in task_slices if task.get("requirementRefs"))
        tasks_with_design = sum(1 for task in task_slices if task.get("designRefs") or task.get("designSnippets"))
        return {
            "kind": "SpecTraceabilityIndex",
            "frameworkDigest": _safe_text(framework_digest, limit=1400),
            "requirements": {
                "count": len(requirement_index),
                "samples": list(requirement_index.values())[:12],
            },
            "design": {
                "count": len(design_index),
                "frameworkSections": [
                    item
                    for item in design_index
                    if item.get("framework")
                ][:6],
            },
            "tasks": task_slices[:16],
            "missingRefs": missing_refs[:20],
            "distributionChecks": {
                "taskCount": len(task_slices),
                "tasksWithRequirementRefs": tasks_with_requirements,
                "tasksWithDesignRefs": tasks_with_design,
                "hasFrameworkDigest": bool(framework_digest.strip()),
                "allTasksHaveRequirementRefs": bool(task_slices) and tasks_with_requirements == len(task_slices),
                "allTasksHaveDesignRefs": bool(task_slices) and tasks_with_design == len(task_slices),
                "missingRefCount": len(missing_refs),
            },
            "recommendedExecutionRule": (
                "For approved Spec execution, each runtime/subagent/child-agent task must receive the task slice, "
                "its requirement snippets, relevant design/framework snippets, task detailRef, and specId. "
                "If a referenced slice is missing, read it with spec_broker(read_section) before execution."
            ),
        }

    def _linked_sections(self, manifest: dict[str, Any]) -> list[dict[str, Any]]:
        spec_id = str(manifest.get("specId") or "")
        documents = dict(manifest.get("documents") or {})
        links: list[dict[str, Any]] = []
        for stage, value in documents.items():
            if not isinstance(value, dict):
                continue
            ids = [str(item) for item in list(value.get("ids") or []) if str(item).strip()]
            links.append(
                {
                    "stage": stage,
                    "detailRef": f"spec://{spec_id}/{stage}",
                    "ids": ids,
                    "relativePath": value.get("relativePath"),
                }
            )
        checklists = dict((manifest.get("qualityEvidence") or {}).get("checklists") or {})
        for name, value in checklists.items():
            if not isinstance(value, dict):
                continue
            links.append(
                {
                    "kind": "checklist",
                    "stage": str(value.get("stage") or name),
                    "title": value.get("title") or "Quality Checklist",
                    "detailRef": value.get("detailRef") or f"spec://{spec_id}/checklists/{name}",
                    "relativePath": value.get("relativePath"),
                    "status": value.get("status"),
                    "unresolvedCount": value.get("unresolvedCount"),
                }
            )
        for name, value in dict(manifest.get("annexDocuments") or {}).items():
            if not isinstance(value, dict):
                continue
            links.append(
                {
                    "kind": "annex",
                    "stage": str(name),
                    "title": value.get("title") or str(name).title(),
                    "detailRef": value.get("detailRef") or f"spec://{spec_id}/annex/{name}",
                    "relativePath": value.get("relativePath"),
                    "status": value.get("status"),
                }
            )
        return links

    def _pipeline_control(self, manifest: dict[str, Any]) -> dict[str, Any]:
        current = str(manifest.get("currentStage") or "").strip().lower()
        approvals = {
            stage: bool(payload.get("approved"))
            for stage, payload in dict(manifest.get("approvals") or {}).items()
            if isinstance(payload, dict)
        }
        docs = dict(manifest.get("documents") or {})
        stale_stages = {
            stage: payload
            for stage, payload in dict(manifest.get("staleStages") or {}).items()
            if isinstance(payload, dict)
            and payload.get("stale")
            and (stage in docs or stage in approvals)
        }
        next_stage = self.next_stage(manifest, current) if current else None
        runtime_allowed = next_stage == "runtime_execution" and approvals.get("tasks") is True and not stale_stages
        blocked_by = None
        blocked_reason = ""
        current_doc = docs.get(current) if current else None
        current_diagnostics = (
            current_doc.get("formatDiagnostics")
            if isinstance(current_doc, dict) and isinstance(current_doc.get("formatDiagnostics"), dict)
            else {}
        )
        current_format_blocking = bool(current_diagnostics.get("approvalBlocking"))
        if current and current in docs and approvals.get(current) is not True:
            if current_format_blocking:
                blocked_reason = "stage_format_invalid"
            else:
                blocked_by = current
                blocked_reason = "approval_required"
        elif next_stage and next_stage != "runtime_execution":
            blocked_by = current if approvals.get(current) is not True else None
            blocked_reason = "approval_required" if blocked_by else ""
        return {
            "currentStage": current,
            "nextStage": next_stage,
            "runtimeExecutionAllowed": bool(runtime_allowed),
            "blockedByApproval": blocked_by,
            "blockedReason": blocked_reason,
            "approvedStages": [stage for stage, is_approved in approvals.items() if is_approved],
            "staleStages": sorted(stale_stages),
            "lifecycle": manifest.get("lifecycle") or SPEC_LIFECYCLE_ACTIVE,
        }

    def has_stage_clarification(self, *, workspace_path: str, spec_id: str, stage: str) -> bool:
        try:
            paths = self.resolve_paths(workspace_path, spec_id=spec_id)
            manifest = self._load_manifest(paths)
        except Exception:
            return False
        normalized_stage = str(stage or "").strip().lower()
        return any(
            isinstance(item, dict)
            and str(item.get("stage") or "").strip().lower() == normalized_stage
            and str(item.get("status") or "resolved").strip().lower() in {"resolved", "answered"}
            for item in list(manifest.get("clarifications") or [])
        )

    def record_clarification(
        self,
        *,
        workspace_path: str,
        spec_id: str,
        stage: str,
        question: str,
        answer: str,
        source_run_id: str = "",
        tool_call_id: str = "",
        interaction_id: str = "",
        feature_name: str = "",
    ) -> dict[str, Any]:
        paths = self.resolve_paths(workspace_path, spec_id=spec_id)
        manifest = self._load_manifest(paths)
        if not manifest:
            raise ValueError(f"spec_not_found:{spec_id}")
        normalized_stage = str(stage or manifest.get("currentStage") or "").strip().lower()
        if normalized_stage not in SPEC_DOCS:
            raise ValueError(f"unsupported_spec_stage:{normalized_stage}")
        existing = manifest.setdefault("clarifications", [])
        dedupe = str(interaction_id or tool_call_id or "").strip()
        if dedupe:
            for item in existing:
                if isinstance(item, dict) and dedupe in {str(item.get("interactionId") or ""), str(item.get("toolCallId") or "")}:
                    item.update(
                        {
                            "answer": _safe_text(answer, limit=2000),
                            "status": "resolved",
                            "updatedAt": _now_iso(),
                        }
                    )
                    self._refresh_quality_artifacts(paths, manifest)
                    self._write_manifest(paths, manifest)
                    return {"ok": True, "kind": "spec_clarification_recorded", "specId": spec_id, "stage": normalized_stage}
        existing.append(
            {
                "id": f"clar_{uuid.uuid4().hex[:12]}",
                "stage": normalized_stage,
                "featureName": _safe_text(feature_name or manifest.get("featureName"), limit=160),
                "question": _safe_text(question, limit=2000),
                "answer": _safe_text(answer, limit=2000),
                "sourceRunId": _safe_text(source_run_id, limit=160),
                "toolCallId": _safe_text(tool_call_id, limit=160),
                "interactionId": _safe_text(interaction_id, limit=160),
                "status": "resolved",
                "createdAt": _now_iso(),
            }
        )
        self._refresh_quality_artifacts(paths, manifest)
        self._write_manifest(paths, manifest)
        return {"ok": True, "kind": "spec_clarification_recorded", "specId": spec_id, "stage": normalized_stage}

    def analyze_spec(self, *, workspace_path: str, spec_id: str) -> dict[str, Any]:
        paths = self.resolve_paths(workspace_path, spec_id=spec_id)
        manifest = self._load_manifest(paths)
        if not manifest:
            raise ValueError(f"spec_not_found:{spec_id}")
        traceability = self._traceability_index(paths, manifest)
        tasks = [dict(item) for item in list(traceability.get("tasks") or []) if isinstance(item, dict)]
        task_quality = _tasks_quality_diagnostics(tasks)
        hard_blockers: list[dict[str, Any]] = []
        warnings: list[dict[str, Any]] = []
        for stage, filename in SPEC_DOCS.items():
            doc_path = paths.spec_dir / filename
            if not doc_path.exists():
                continue
            content = doc_path.read_text(encoding="utf-8", errors="ignore")
            diagnostics = _stage_format_diagnostics(stage, content)
            for code in list(diagnostics.get("approvalBlocking") or []):
                hard_blockers.append({"code": f"{stage}_{code}", "stage": stage, "message": f"{stage} is missing {code}."})
            for code in list(diagnostics.get("warnings") or []):
                warnings.append({"code": f"{stage}_{code}", "stage": stage, "message": f"{stage} warning: {code}."})
        hard_blockers.extend(task_quality.get("hardBlockers") or [])
        warnings.extend(task_quality.get("warnings") or [])
        distribution = dict(traceability.get("distributionChecks") or {})
        if tasks and not bool(distribution.get("allTasksHaveRequirementRefs")):
            hard_blockers.append({"code": "tasks_missing_requirement_refs", "stage": "tasks", "message": "At least one task lacks requirement refs."})
        if tasks and not bool(distribution.get("allTasksHaveDesignRefs")):
            warnings.append({"code": "tasks_missing_design_refs", "stage": "tasks", "message": "At least one task lacks design/framework refs."})
        checklist = dict((manifest.get("qualityEvidence") or {}).get("checklists", {}).get("requirements") or {})
        if checklist and int(checklist.get("unresolvedCount") or 0) > 0:
            warnings.append(
                {
                    "code": "quality_checklist_open_items",
                    "stage": "requirements",
                    "message": f"Quality checklist has {int(checklist.get('unresolvedCount') or 0)} unfinished item(s).",
                }
            )
        terms = self._terminology_drift_warnings(paths, manifest)
        warnings.extend(terms)
        return {
            "ok": True,
            "kind": "spec_analysis",
            "specId": spec_id,
            "featureName": manifest.get("featureName"),
            "workspacePath": str(paths.workspace),
            "summary": (
                f"{len(hard_blockers)} blocker(s), {len(warnings)} warning(s)."
                if hard_blockers or warnings
                else "Spec analysis found no approval blockers."
            ),
            "hardBlockers": hard_blockers[:40],
            "warnings": warnings[:60],
            "taskQuality": task_quality,
            "traceabilityChecks": distribution,
            "checklist": checklist,
            "annexDocuments": manifest.get("annexDocuments") or {},
            "clarificationSummary": self._clarification_summary(manifest),
        }

    def _terminology_drift_warnings(self, paths: SpecPaths, manifest: dict[str, Any]) -> list[dict[str, Any]]:
        feature = str(manifest.get("featureName") or "").strip().lower()
        if not feature:
            return []
        warnings: list[dict[str, Any]] = []
        important_terms = [
            token
            for token in re.findall(r"[A-Za-z][A-Za-z0-9_-]{3,}|[\u4e00-\u9fff]{2,}", feature)
            if token.lower() not in {"spec", "mode", "feature"}
        ][:6]
        if not important_terms:
            return []
        for stage in ("requirements", "bugfix", "design", "tasks"):
            text = self._read_stage_content(paths, manifest, stage).lower()
            if text and not any(term.lower() in text for term in important_terms):
                warnings.append(
                    {
                        "code": "terminology_drift",
                        "stage": stage,
                        "message": f"{stage} does not mention the feature name or its key terms.",
                    }
                )
        return warnings[:8]

    def approve_stage(self, *, workspace_path: str, spec_id: str, stage: str, approver: str = "user", comment: str = "") -> dict[str, Any]:
        paths = self.resolve_paths(workspace_path, spec_id=spec_id)
        manifest = self._load_manifest(paths)
        if not manifest:
            raise ValueError(f"spec_not_found:{spec_id}")
        normalized_stage = str(stage or manifest.get("currentStage") or "").strip().lower()
        if normalized_stage not in SPEC_DOCS:
            raise ValueError(f"unsupported_spec_stage:{normalized_stage}")
        if normalized_stage not in dict(manifest.get("documents") or {}):
            raise ValueError(f"spec_document_not_found:{normalized_stage}")
        doc_meta = dict((manifest.get("documents") or {}).get(normalized_stage) or {})
        doc_path = paths.spec_dir / SPEC_DOCS[normalized_stage]
        content = doc_path.read_text(encoding="utf-8", errors="ignore") if doc_path.exists() else ""
        diagnostics = _stage_format_diagnostics(normalized_stage, content)
        doc_meta["formatDiagnostics"] = diagnostics
        manifest.setdefault("documents", {})[normalized_stage] = doc_meta
        if diagnostics.get("approvalBlocking"):
            self._refresh_quality_artifacts(paths, manifest)
            self._write_manifest(paths, manifest)
            return {
                "ok": False,
                "kind": "spec_stage_format_invalid",
                "stage": normalized_stage,
                "specId": spec_id,
                "summary": f"Spec stage '{normalized_stage}' cannot be approved because required traceability fields are missing.",
                "formatDiagnostics": diagnostics,
                "pipelineControl": self._pipeline_control(manifest),
                "recommendedNextAction": (
                    "Rewrite or edit this stage with stable requirement/design/task IDs before requesting approval again."
                ),
                "specBrief": self.build_brief(workspace_path=workspace_path, spec_id=spec_id),
            }
        analysis: dict[str, Any] | None = None
        if normalized_stage == "tasks":
            self._refresh_quality_artifacts(paths, manifest)
            self._write_manifest(paths, manifest)
            analysis = self.analyze_spec(workspace_path=workspace_path, spec_id=spec_id)
            if list(analysis.get("hardBlockers") or []):
                return {
                    "ok": False,
                    "kind": "spec_stage_analysis_blocked",
                    "stage": normalized_stage,
                    "specId": spec_id,
                    "summary": "Spec tasks cannot be approved until cross-stage blockers are resolved.",
                    "analysis": analysis,
                    "formatDiagnostics": diagnostics,
                    "pipelineControl": self._pipeline_control(manifest),
                    "recommendedNextAction": "Edit tasks.md so every executable task has readable refs, proof expectations, and large-task MVP/independent acceptance.",
                    "specBrief": self.build_brief(workspace_path=workspace_path, spec_id=spec_id),
                }
        manifest.setdefault("approvals", {})[normalized_stage] = {
            "approved": True,
            "approver": _safe_text(approver, limit=80) or "user",
            "approvedAt": _now_iso(),
            "comment": _safe_text(comment, limit=1000),
        }
        self._clear_stage_stale(manifest, normalized_stage)
        docs = manifest.setdefault("documents", {})
        if isinstance(docs.get(normalized_stage), dict):
            docs[normalized_stage]["status"] = "approved"
            docs[normalized_stage]["approvedAt"] = _now_iso()
            if analysis:
                docs[normalized_stage]["analysis"] = {
                    "summary": analysis.get("summary"),
                    "hardBlockerCount": len(list(analysis.get("hardBlockers") or [])),
                    "warningCount": len(list(analysis.get("warnings") or [])),
                }
        self._refresh_quality_artifacts(paths, manifest)
        self._write_manifest(paths, manifest)
        return {
            "ok": True,
            "kind": "spec_stage_approved",
            "stage": normalized_stage,
            "specId": spec_id,
            "nextStage": self.next_stage(manifest, normalized_stage),
            "pipelineControl": self._pipeline_control(manifest),
            **({"analysis": analysis} if analysis else {}),
            "specBrief": self.build_brief(workspace_path=workspace_path, spec_id=spec_id),
        }

    def request_revision(self, *, workspace_path: str, spec_id: str, stage: str, comment: str, section_ref: str = "") -> dict[str, Any]:
        paths = self.resolve_paths(workspace_path, spec_id=spec_id)
        manifest = self._load_manifest(paths)
        if not manifest:
            raise ValueError(f"spec_not_found:{spec_id}")
        normalized_stage = str(stage or manifest.get("currentStage") or "").strip().lower()
        manifest.setdefault("comments", []).append(
            {
                "stage": normalized_stage,
                "sectionRef": _safe_text(section_ref, limit=160),
                "comment": _safe_text(comment, limit=2000),
                "createdAt": _now_iso(),
                "status": "open",
            }
        )
        if normalized_stage in manifest.get("approvals", {}):
            manifest["approvals"][normalized_stage]["approved"] = False
            manifest["approvals"][normalized_stage]["revokedAt"] = _now_iso()
            manifest["approvals"][normalized_stage]["revocationReason"] = "revision_requested"
        self._mark_stage_changed(
            manifest,
            stage=normalized_stage,
            reason="revision_requested",
            revoke_current_approval=False,
        )
        self._write_manifest(paths, manifest)
        return {
            "ok": True,
            "kind": "spec_revision_requested",
            "stage": normalized_stage,
            "specId": spec_id,
            "summary": "Revision comment recorded; downstream stages remain blocked until approval.",
            "pipelineControl": self._pipeline_control(manifest),
            "specBrief": self.build_brief(workspace_path=workspace_path, spec_id=spec_id),
        }

    def mark_delivered(
        self,
        *,
        workspace_path: str,
        spec_id: str,
        reason: str = "runtime_delivery_completed",
        run_id: str = "",
        session_id: str = "",
    ) -> dict[str, Any]:
        paths = self.resolve_paths(workspace_path, spec_id=spec_id)
        manifest = self._load_manifest(paths)
        if not manifest:
            raise ValueError(f"spec_not_found:{spec_id}")
        now = _now_iso()
        manifest["lifecycle"] = SPEC_LIFECYCLE_DELIVERED
        manifest["deliveredAt"] = now
        manifest["deliveryReason"] = _safe_text(reason, limit=500)
        if run_id:
            manifest["deliveryRunId"] = _safe_text(run_id, limit=160)
        if session_id:
            manifest["deliverySessionId"] = _safe_text(session_id, limit=160)
        self._write_manifest(paths, manifest)
        return {
            "ok": True,
            "kind": "spec_delivered",
            "specId": spec_id,
            "lifecycle": SPEC_LIFECYCLE_DELIVERED,
            "deliveredAt": now,
            "pipelineControl": self._pipeline_control(manifest),
            "specBrief": self.build_brief(workspace_path=workspace_path, spec_id=spec_id),
        }

    def edit_stage(
        self,
        *,
        workspace_path: str,
        spec_id: str,
        stage: str,
        action: str,
        content: str,
        section_ref: str = "",
        reason: str = "",
    ) -> dict[str, Any]:
        paths = self.resolve_paths(workspace_path, spec_id=spec_id)
        manifest = self._load_manifest(paths)
        if not manifest:
            raise ValueError(f"spec_not_found:{spec_id}")
        normalized_stage = str(stage or manifest.get("currentStage") or "").strip().lower()
        filename = SPEC_DOCS.get(normalized_stage)
        if not filename:
            raise ValueError(f"unsupported_spec_stage:{normalized_stage}")
        if self._stage_is_approved(manifest, normalized_stage):
            return self._approved_stage_locked_payload(
                manifest,
                workspace_path=workspace_path,
                stage=normalized_stage,
                action=str(action or "edit_stage"),
            )
        doc_path = paths.spec_dir / filename
        if not doc_path.exists():
            raise ValueError(f"spec_document_not_found:{normalized_stage}")
        edit_action = str(action or "").strip().lower()
        if edit_action not in {"replace_section", "append_section", "rewrite_stage"}:
            raise ValueError(f"unsupported_spec_edit_action:{edit_action}")
        previous_content = doc_path.read_text(encoding="utf-8", errors="ignore")
        new_text = _safe_text(content, limit=20000)
        if not new_text:
            raise ValueError("spec_edit_content_required")
        if edit_action == "rewrite_stage":
            new_text, edit_id_allocation = _assign_missing_stage_ids(normalized_stage, new_text)
            next_content = new_text
        else:
            new_text = _normalize_stage_markdown(normalized_stage, new_text)
            edit_id_allocation = {"allocatedIds": [], "existingIds": []}
        if edit_action == "append_section":
            next_content = previous_content.rstrip() + "\n\n" + new_text.strip() + "\n"
        elif edit_action == "replace_section":
            span = self._section_span(previous_content, section_ref)
            if not span:
                raise ValueError(f"spec_section_not_found:{section_ref}")
            start, end = span
            replacement = new_text.strip()
            next_content = previous_content[:start].rstrip() + "\n" + replacement + "\n" + previous_content[end:].lstrip("\n")
        next_content, final_id_allocation = _assign_missing_stage_ids(normalized_stage, next_content)
        allocated_ids = list(dict.fromkeys(list(edit_id_allocation.get("allocatedIds") or []) + list(final_id_allocation.get("allocatedIds") or [])))
        id_allocation = {
            "allocatedIds": allocated_ids,
            "existingIds": final_id_allocation.get("existingIds") or self._document_ids(normalized_stage, next_content),
        }
        self._record_version(
            paths,
            manifest,
            stage=normalized_stage,
            previous_content=previous_content,
            action=edit_action,
            reason=reason or "spec_stage_edited",
        )
        paths.spec_dir.mkdir(parents=True, exist_ok=True)
        doc_path.write_text(next_content, encoding="utf-8")
        self._mark_stage_changed(
            manifest,
            stage=normalized_stage,
            reason=reason or edit_action,
            revoke_current_approval=True,
        )
        self._clear_stage_stale(manifest, normalized_stage)
        manifest["currentStage"] = normalized_stage
        format_diagnostics = _stage_format_diagnostics(normalized_stage, next_content)
        doc_meta = dict((manifest.get("documents") or {}).get(normalized_stage) or {})
        doc_meta.update(
            {
                "path": str(doc_path),
                "relativePath": str(doc_path.relative_to(paths.workspace)).replace("\\", "/"),
                "updatedAt": _now_iso(),
                "ids": self._document_ids(normalized_stage, next_content),
                "version": int(doc_meta.get("version") or 1) + 1,
                "status": "draft",
                "lastEditAction": edit_action,
                "formatDiagnostics": format_diagnostics,
                "idAllocation": id_allocation,
            }
        )
        if normalized_stage == "tasks":
            doc_meta["pipelineDiagnostics"] = _tasks_pipeline_diagnostics(next_content)
        manifest.setdefault("documents", {})[normalized_stage] = doc_meta
        self._refresh_quality_artifacts(paths, manifest)
        self._write_manifest(paths, manifest)
        tasks_pipeline = dict(doc_meta.get("pipelineDiagnostics") or {}) if normalized_stage == "tasks" else None
        return {
            "ok": True,
            "kind": "spec_stage_edited",
            "stage": normalized_stage,
            "action": edit_action,
            "specId": spec_id,
            "document": doc_meta,
            "pipelineControl": self._pipeline_control(manifest),
            "linkedSections": self._linked_sections(manifest),
            "summary": f"Spec stage '{normalized_stage}' edited with {edit_action}; approval is required again.",
            "formatDiagnostics": format_diagnostics,
            "idAllocation": id_allocation,
            "specBrief": self.build_brief(workspace_path=workspace_path, spec_id=spec_id),
            **({"tasksPipeline": tasks_pipeline} if tasks_pipeline is not None else {}),
        }

    def next_stage(self, manifest: dict[str, Any], stage: str) -> str | None:
        order = _stage_order(str(manifest.get("kind") or "feature"))
        try:
            index = order.index(stage)
        except ValueError:
            return order[0]
        return order[index + 1] if index + 1 < len(order) else "runtime_execution"

    def read_section(
        self,
        *,
        workspace_path: str,
        spec_id: str,
        stage: str,
        section_ref: str | None = None,
        max_chars: int = 4000,
    ) -> dict[str, Any]:
        paths = self.resolve_paths(workspace_path, spec_id=spec_id)
        manifest = self._load_manifest(paths)
        if not manifest:
            raise ValueError(f"spec_not_found:{spec_id}")
        normalized_stage = str(stage or manifest.get("currentStage") or "").strip().lower()
        filename = SPEC_DOCS.get(normalized_stage)
        if not filename:
            raise ValueError(f"unsupported_spec_stage:{normalized_stage}")
        doc_path = paths.spec_dir / filename
        if not doc_path.exists():
            raise ValueError(f"spec_document_not_found:{normalized_stage}")
        content = doc_path.read_text(encoding="utf-8", errors="ignore")
        selected = self._select_section(content, section_ref)
        return {
            "ok": True,
            "kind": "spec_section",
            "specId": spec_id,
            "stage": normalized_stage,
            "sectionRef": section_ref or "",
            "content": _safe_text(selected, limit=max(200, max_chars)),
            "documentRef": f"spec://{spec_id}/{normalized_stage}{('#' + section_ref) if section_ref else ''}",
            "linkedSections": self._linked_sections(manifest),
            "specBrief": self.build_brief(workspace_path=workspace_path, spec_id=spec_id),
        }

    def _select_section(self, content: str, section_ref: str | None) -> str:
        ref = _safe_text(section_ref, limit=120)
        if not ref:
            return content
        span = self._section_span(content, ref)
        if not span:
            return content
        return content[span[0] : span[1]].strip()

    def _section_span(self, content: str, section_ref: str | None) -> tuple[int, int] | None:
        ref = _safe_text(section_ref, limit=120)
        if not ref:
            return (0, len(content))
        refs = [ref]
        if ref.upper().startswith("TASK-"):
            number = ref.split("-", 1)[1]
            refs.append("TSK-" + number)
            refs.append("T-" + number)
            try:
                refs.append(f"T-{int(number):02d}")
            except Exception:
                pass
        match = None
        for candidate_ref in refs:
            match = re.search(rf"(?im)^.*\b{re.escape(candidate_ref)}\b.*$", content)
            if match:
                break
        if not match:
            return None
        line_start = content.rfind("\n", 0, match.start()) + 1
        line_end = content.find("\n", match.end())
        if line_end == -1:
            line_end = len(content)
        # IDs usually live on list items. In that case replace the smallest
        # paragraph/list item rather than the entire markdown heading section.
        line = content[line_start:line_end]
        if re.match(r"\s*[-*]\s+", line):
            next_item = re.search(r"(?m)^\s*[-*]\s+(?:REQ|FR|NFR|BFIX|DES|TASK|TSK|T|AC)-\d{2,}\b", content[line_end:])
            next_heading = re.search(r"(?m)^##+\s+", content[line_end:])
            candidates = [len(content)]
            if next_item:
                candidates.append(line_end + next_item.start())
            if next_heading:
                candidates.append(line_end + next_heading.start())
            return (line_start, min(candidates))
        start = content.rfind("\n##", 0, match.start())
        if start == -1:
            start = 0
        end = content.find("\n##", match.end())
        if end == -1:
            end = len(content)
        return (start, end)

    def build_brief(self, *, workspace_path: str, spec_id: str) -> dict[str, Any]:
        paths = self.resolve_paths(workspace_path, spec_id=spec_id)
        manifest = self._load_manifest(paths)
        if not manifest:
            return {"specId": spec_id, "status": "missing"}
        docs = dict(manifest.get("documents") or {})
        approved = {
            stage: bool(payload.get("approved"))
            for stage, payload in dict(manifest.get("approvals") or {}).items()
            if isinstance(payload, dict)
        }
        return {
            "kind": "SpecBrief",
            "specId": manifest.get("specId"),
            "featureName": manifest.get("featureName"),
            "specKind": manifest.get("kind"),
            "lifecycle": manifest.get("lifecycle") or SPEC_LIFECYCLE_ACTIVE,
            "currentStage": manifest.get("currentStage"),
            "approvedStages": [stage for stage, is_approved in approved.items() if is_approved],
            "approvalState": approved,
            "pipelineControl": self._pipeline_control(manifest),
            "workspacePath": manifest.get("workspacePath"),
            "specDir": str(paths.spec_dir),
            "linkedSections": self._linked_sections(manifest),
            "qualityEvidence": manifest.get("qualityEvidence") or {"checklists": {}},
            "annexDocuments": manifest.get("annexDocuments") or {},
            "clarificationSummary": self._clarification_summary(manifest),
            "documents": {
                stage: {
                    "relativePath": value.get("relativePath"),
                    "ids": list(value.get("ids") or []),
                    "detailRef": f"spec://{manifest.get('specId')}/{stage}",
                    "version": value.get("version"),
                    "status": value.get("status"),
                    **(
                        {"pipelineDiagnostics": value.get("pipelineDiagnostics")}
                        if stage == "tasks" and isinstance(value.get("pipelineDiagnostics"), dict)
                        else {}
                    ),
                    **(
                        {"formatDiagnostics": value.get("formatDiagnostics")}
                        if isinstance(value.get("formatDiagnostics"), dict)
                        else {}
                    ),
                }
                for stage, value in docs.items()
                if isinstance(value, dict)
            },
            "staleStages": {
                stage: payload
                for stage, payload in dict(manifest.get("staleStages") or {}).items()
                if isinstance(payload, dict) and payload.get("stale")
            },
            "versionHistory": list(manifest.get("versionHistory") or [])[-8:],
            "openComments": [
                item
                for item in list(manifest.get("comments") or [])
                if isinstance(item, dict) and str(item.get("status") or "open") == "open"
            ][:8],
            "traceability": self._traceability_index(paths, manifest),
        }


spec_service = SpecService()
