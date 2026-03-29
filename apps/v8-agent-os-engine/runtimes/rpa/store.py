from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from core.multimodal_payload_adapter import utc_now_iso
from core.storage import storage
from runtimes.rpa.execution_semantics import outcome_family_for_execution_state
from runtimes.rpa.types import RPAScript


class RPAScriptStore:
    def __init__(self, root_dir: Path | None = None) -> None:
        base_dir = root_dir
        if base_dir is None:
            configured_base = getattr(storage, "base_dir", None)
            if configured_base:
                base_dir = Path(configured_base)
            else:
                base_dir = Path.home() / ".v8-agent-os"
        self.base_dir = Path(base_dir)
        self.rpa_dir = self.base_dir / "rpa"
        self.draft_dir = self.rpa_dir / "drafts"
        self.script_dir = self.rpa_dir / "scripts"
        self.template_dir = self.rpa_dir / "templates"
        self.template_history_dir = self.rpa_dir / "template_history"
        self.trust_metrics_path = self.rpa_dir / "trust_metrics.json"
        self.draft_dir.mkdir(parents=True, exist_ok=True)
        self.script_dir.mkdir(parents=True, exist_ok=True)
        self.template_dir.mkdir(parents=True, exist_ok=True)
        self.template_history_dir.mkdir(parents=True, exist_ok=True)
        if not self.trust_metrics_path.exists():
            self.trust_metrics_path.write_text(json.dumps(self._default_trust_metrics(), ensure_ascii=False, indent=2), encoding="utf-8")

    def _safe_name(self, value: str) -> str:
        normalized = re.sub(r"[^a-zA-Z0-9._-]+", "_", str(value or "").strip())
        return normalized.strip("._") or "rpa_script"

    def _draft_path(self, script_id: str) -> Path:
        return self.draft_dir / f"{self._safe_name(script_id)}.json"

    def _template_path(self, template_id: str) -> Path:
        return self.template_dir / f"{self._safe_name(template_id)}.json"

    def _template_history_root(self, template_id: str) -> Path:
        return self.template_history_dir / self._safe_name(template_id)

    def _template_history_path(self, template_id: str, *, revision: int | None = None) -> Path:
        history_root = self._template_history_root(template_id)
        history_root.mkdir(parents=True, exist_ok=True)
        stamp = utc_now_iso().replace(":", "").replace("-", "").replace(".", "")
        suffix = f"rev_{int(revision)}_" if revision not in (None, "") else ""
        return history_root / f"{suffix}{stamp}.json"

    def _default_trust_metrics(self) -> Dict[str, Any]:
        return {
            "version": 1,
            "updatedAt": utc_now_iso(),
            "apps": {},
            "scripts": {},
            "fingerprints": {},
        }

    def _load_trust_metrics(self) -> Dict[str, Any]:
        try:
            payload = json.loads(self.trust_metrics_path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                payload.setdefault("version", 1)
                payload.setdefault("updatedAt", utc_now_iso())
                payload.setdefault("apps", {})
                payload.setdefault("scripts", {})
                payload.setdefault("fingerprints", {})
                return payload
        except Exception:
            pass
        return self._default_trust_metrics()

    def _save_trust_metrics(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        next_payload = dict(payload or {})
        next_payload["updatedAt"] = utc_now_iso()
        self.trust_metrics_path.write_text(json.dumps(next_payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        return next_payload

    def get_action_calibration(self, *, app_id: str, use: str) -> Dict[str, Any]:
        payload = self._load_trust_metrics()
        action_metrics = (
            payload.get("apps", {})
            .get(str(app_id or "").strip(), {})
            .get("actions", {})
            .get(str(use or "").strip(), {})
        )
        if not isinstance(action_metrics, dict):
            action_metrics = {}
        runs = int(action_metrics.get("runs") or 0)
        successes = int(action_metrics.get("successes") or 0)
        fallbacks = int(action_metrics.get("fallbacks") or 0)
        failures = int(action_metrics.get("failures") or 0)
        fallback_failures = int(action_metrics.get("fallbackFailures") or 0)
        review_required = int(action_metrics.get("reviewRequired") or 0)
        compile_blocked = int(action_metrics.get("compileBlocked") or 0)
        native_runs = int(action_metrics.get("nativeSemanticRuns") or 0)
        native_successes = int(action_metrics.get("nativeSemanticSuccesses") or 0)
        return {
            "runs": runs,
            "successRate": round(successes / runs, 3) if runs else None,
            "fallbackRate": round(fallbacks / runs, 3) if runs else None,
            "failureRate": round(failures / runs, 3) if runs else None,
            "fallbackFailureRate": round(fallback_failures / runs, 3) if runs else None,
            "fallbackHeavyRate": round((fallbacks + fallback_failures) / runs, 3) if runs else None,
            "reviewRequiredRate": round(review_required / runs, 3) if runs else None,
            "compileBlockedRate": round(compile_blocked / runs, 3) if runs else None,
            "nativeSuccessRate": round(native_successes / native_runs, 3) if native_runs else None,
        }

    def _script_metric_view(self, script_metrics: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(script_metrics, dict):
            script_metrics = {}
        runs = int(script_metrics.get("runs") or 0)
        completed = int(script_metrics.get("completed") or 0)
        fallbacks = int(script_metrics.get("fallbacks") or 0)
        fallback_failures = int(script_metrics.get("fallbackFailures") or 0)
        failed = int(script_metrics.get("failed") or 0)
        review_required = int(script_metrics.get("reviewRequired") or 0)
        compile_blocked = int(script_metrics.get("compileBlocked") or 0)
        return {
            "runs": runs,
            "completedRate": round(completed / runs, 3) if runs else None,
            "fallbackRate": round(fallbacks / runs, 3) if runs else None,
            "fallbackFailureRate": round(fallback_failures / runs, 3) if runs else None,
            "failureRate": round(failed / runs, 3) if runs else None,
            "reviewRequiredRate": round(review_required / runs, 3) if runs else None,
            "compileBlockedRate": round(compile_blocked / runs, 3) if runs else None,
            "fallbackHeavyRate": round((fallbacks + fallback_failures) / runs, 3) if runs else None,
            "profileAugmentedRatio": round(
                int(script_metrics.get("profileAugmentedSteps") or 0) / int(script_metrics.get("stepRuns") or 0),
                3,
            ) if int(script_metrics.get("stepRuns") or 0) else None,
            "stepLevelFallbackRate": round(
                int(script_metrics.get("stepLevelFallbacks") or 0) / runs,
                3,
            ) if runs else None,
            "avgRecoveredSteps": round(
                int(script_metrics.get("stepLevelRecoveredSteps") or 0) / int(script_metrics.get("stepLevelFallbacks") or 0),
                3,
            ) if int(script_metrics.get("stepLevelFallbacks") or 0) else None,
            "localRepairRate": round(
                int(script_metrics.get("localRepairs") or 0) / runs,
                3,
            ) if runs else None,
            "avgRepairedSteps": round(
                int(script_metrics.get("localRepairSteps") or 0) / int(script_metrics.get("localRepairs") or 0),
                3,
            ) if int(script_metrics.get("localRepairs") or 0) else None,
            "nativeSuccessRate": round(
                int(script_metrics.get("nativeSemanticSuccesses") or 0) / int(script_metrics.get("nativeSemanticRuns") or 0),
                3,
            ) if int(script_metrics.get("nativeSemanticRuns") or 0) else None,
            "lastOutcomeFamily": script_metrics.get("lastOutcomeFamily"),
            "feedbackSuggestionCounts": dict(script_metrics.get("feedbackSuggestionCounts") or {}),
            "lastFeedbackSuggestions": dict(script_metrics.get("lastFeedbackSuggestions") or {}),
            "lastStatus": script_metrics.get("lastStatus"),
        }

    def get_script_calibration(self, *, script_id: str, fingerprint: str | None = None) -> Dict[str, Any]:
        payload = self._load_trust_metrics()
        script_metrics = payload.get("scripts", {}).get(str(script_id or "").strip(), {})
        fingerprint_metrics = {}
        if fingerprint:
            fingerprint_metrics = payload.get("fingerprints", {}).get(str(fingerprint).strip(), {})
        script_view = self._script_metric_view(script_metrics)
        fingerprint_view = self._script_metric_view(fingerprint_metrics)
        if int(script_view.get("runs") or 0) >= int(fingerprint_view.get("runs") or 0):
            result = dict(script_view)
            result["source"] = "script"
            return result
        result = dict(fingerprint_view)
        result["source"] = "fingerprint"
        return result

    def record_run_feedback(
        self,
        *,
        script: Dict[str, Any],
        execution_state: str,
        outcome_family: str | None = None,
        feedback: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        payload = self._load_trust_metrics()
        normalized_outcome_family = outcome_family or outcome_family_for_execution_state(execution_state)
        script_id = str(script.get("id") or "").strip()
        app_id = str(script.get("appId") or "desktop").strip() or "desktop"
        fingerprint = str(
            (script.get("metadata") or {}).get("fingerprint")
            or (script.get("source") or {}).get("fingerprint")
            or ""
        ).strip()
        scripts = payload.setdefault("scripts", {})
        apps = payload.setdefault("apps", {})
        fingerprints = payload.setdefault("fingerprints", {})
        script_metrics = scripts.setdefault(
            script_id,
            {
                "runs": 0,
                "completed": 0,
                "fallbacks": 0,
                "fallbackFailures": 0,
                "failed": 0,
                "reviewRequired": 0,
                "compileBlocked": 0,
                "stepRuns": 0,
                "profileAugmentedSteps": 0,
                "nativeSemanticRuns": 0,
                "nativeSemanticSuccesses": 0,
                "stepLevelFallbacks": 0,
                "stepLevelRecoveredSteps": 0,
                "localRepairs": 0,
                "localRepairSteps": 0,
                "lastStatus": None,
                "lastOutcomeFamily": None,
            },
        )
        fingerprint_metrics = None
        if fingerprint:
            fingerprint_metrics = fingerprints.setdefault(
                fingerprint,
                {
                    "runs": 0,
                    "completed": 0,
                    "fallbacks": 0,
                    "fallbackFailures": 0,
                    "failed": 0,
                    "reviewRequired": 0,
                    "compileBlocked": 0,
                    "stepRuns": 0,
                    "profileAugmentedSteps": 0,
                    "nativeSemanticRuns": 0,
                    "nativeSemanticSuccesses": 0,
                    "stepLevelFallbacks": 0,
                    "stepLevelRecoveredSteps": 0,
                    "localRepairs": 0,
                    "localRepairSteps": 0,
                    "lastStatus": None,
                    "lastOutcomeFamily": None,
                },
            )
        script_metrics["runs"] += 1
        script_metrics["lastStatus"] = execution_state
        script_metrics["lastOutcomeFamily"] = normalized_outcome_family
        if fingerprint_metrics is not None:
            fingerprint_metrics["runs"] += 1
            fingerprint_metrics["lastStatus"] = execution_state
            fingerprint_metrics["lastOutcomeFamily"] = normalized_outcome_family
        if normalized_outcome_family == "completed":
            script_metrics["completed"] += 1
            if fingerprint_metrics is not None:
                fingerprint_metrics["completed"] += 1
        elif normalized_outcome_family == "completed_with_fallback":
            script_metrics["fallbacks"] += 1
            if fingerprint_metrics is not None:
                fingerprint_metrics["fallbacks"] += 1
        elif execution_state == "failed" and execution_state == "fallback_failed":
            script_metrics["fallbackFailures"] += 1
            script_metrics["failed"] += 1
            if fingerprint_metrics is not None:
                fingerprint_metrics["fallbackFailures"] += 1
                fingerprint_metrics["failed"] += 1
        elif normalized_outcome_family == "review_required":
            script_metrics["reviewRequired"] += 1
            if fingerprint_metrics is not None:
                fingerprint_metrics["reviewRequired"] += 1
        elif normalized_outcome_family == "blocked":
            script_metrics["compileBlocked"] += 1
            if fingerprint_metrics is not None:
                fingerprint_metrics["compileBlocked"] += 1
        elif normalized_outcome_family == "failed":
            script_metrics["failed"] += 1
            if fingerprint_metrics is not None:
                fingerprint_metrics["failed"] += 1

        feedback_payload = dict(feedback or {})
        feedback_suggestion_counts = dict(script_metrics.get("feedbackSuggestionCounts") or {})
        fingerprint_feedback_counts = dict(fingerprint_metrics.get("feedbackSuggestionCounts") or {}) if fingerprint_metrics is not None else {}
        for key in ("selectorMemoryCandidate", "appProfileRecommendation", "playbookRecommendation"):
            if isinstance(feedback_payload.get(key), dict) and feedback_payload.get(key):
                feedback_suggestion_counts[key] = int(feedback_suggestion_counts.get(key) or 0) + 1
                if fingerprint_metrics is not None:
                    fingerprint_feedback_counts[key] = int(fingerprint_feedback_counts.get(key) or 0) + 1
        if list(feedback_payload.get("preflightHints") or []):
            hint_count = len([item for item in list(feedback_payload.get("preflightHints") or []) if isinstance(item, dict)])
            feedback_suggestion_counts["preflightHints"] = int(feedback_suggestion_counts.get("preflightHints") or 0) + len(
                [item for item in list(feedback_payload.get("preflightHints") or []) if isinstance(item, dict)]
            )
            if fingerprint_metrics is not None:
                fingerprint_feedback_counts["preflightHints"] = int(fingerprint_feedback_counts.get("preflightHints") or 0) + hint_count
        if feedback_suggestion_counts:
            script_metrics["feedbackSuggestionCounts"] = feedback_suggestion_counts
            script_metrics["lastFeedbackSuggestions"] = {
                "selectorMemoryCandidate": dict(feedback_payload.get("selectorMemoryCandidate") or {}) if isinstance(feedback_payload.get("selectorMemoryCandidate"), dict) else None,
                "appProfileRecommendation": dict(feedback_payload.get("appProfileRecommendation") or {}) if isinstance(feedback_payload.get("appProfileRecommendation"), dict) else None,
                "playbookRecommendation": dict(feedback_payload.get("playbookRecommendation") or {}) if isinstance(feedback_payload.get("playbookRecommendation"), dict) else None,
                "preflightHints": [dict(item) for item in list(feedback_payload.get("preflightHints") or []) if isinstance(item, dict)],
            }
            if fingerprint_metrics is not None:
                fingerprint_metrics["feedbackSuggestionCounts"] = dict(fingerprint_feedback_counts)
                fingerprint_metrics["lastFeedbackSuggestions"] = dict(script_metrics["lastFeedbackSuggestions"])
        if bool(feedback_payload.get("stepLevelFallback")):
            recovered_steps = max(0, int(feedback_payload.get("recoveredSteps") or 0))
            script_metrics["stepLevelFallbacks"] = int(script_metrics.get("stepLevelFallbacks") or 0) + 1
            script_metrics["stepLevelRecoveredSteps"] = int(script_metrics.get("stepLevelRecoveredSteps") or 0) + recovered_steps
            if fingerprint_metrics is not None:
                fingerprint_metrics["stepLevelFallbacks"] = int(fingerprint_metrics.get("stepLevelFallbacks") or 0) + 1
                fingerprint_metrics["stepLevelRecoveredSteps"] = int(fingerprint_metrics.get("stepLevelRecoveredSteps") or 0) + recovered_steps
        if bool(feedback_payload.get("localRepairApplied")):
            repaired_steps = max(0, int(feedback_payload.get("repairedSteps") or 0))
            script_metrics["localRepairs"] = int(script_metrics.get("localRepairs") or 0) + 1
            script_metrics["localRepairSteps"] = int(script_metrics.get("localRepairSteps") or 0) + repaired_steps
            if fingerprint_metrics is not None:
                fingerprint_metrics["localRepairs"] = int(fingerprint_metrics.get("localRepairs") or 0) + 1
                fingerprint_metrics["localRepairSteps"] = int(fingerprint_metrics.get("localRepairSteps") or 0) + repaired_steps

        step_count = 0
        profile_augmented_steps = 0
        native_ready_steps = 0
        for step in list(script.get("steps") or []):
            if not isinstance(step, dict):
                continue
            step_count += 1
            assessment = step.get("assessment") if isinstance(step.get("assessment"), dict) else {}
            signals = assessment.get("signals") if isinstance(assessment.get("signals"), dict) else {}
            metadata = step.get("metadata") if isinstance(step.get("metadata"), dict) else {}
            if bool(signals.get("profileAugmented") or metadata.get("profileAugmented")):
                profile_augmented_steps += 1
            robot = step.get("robot") if isinstance(step.get("robot"), dict) else {}
            if bool(robot.get("library") and robot.get("keyword")):
                native_ready_steps += 1
        if step_count:
            script_metrics["stepRuns"] = int(script_metrics.get("stepRuns") or 0) + step_count
            script_metrics["profileAugmentedSteps"] = int(script_metrics.get("profileAugmentedSteps") or 0) + profile_augmented_steps
            if fingerprint_metrics is not None:
                fingerprint_metrics["stepRuns"] = int(fingerprint_metrics.get("stepRuns") or 0) + step_count
                fingerprint_metrics["profileAugmentedSteps"] = int(fingerprint_metrics.get("profileAugmentedSteps") or 0) + profile_augmented_steps
        if native_ready_steps:
            script_metrics["nativeSemanticRuns"] = int(script_metrics.get("nativeSemanticRuns") or 0) + native_ready_steps
            if fingerprint_metrics is not None:
                fingerprint_metrics["nativeSemanticRuns"] = int(fingerprint_metrics.get("nativeSemanticRuns") or 0) + native_ready_steps
            if normalized_outcome_family == "completed":
                script_metrics["nativeSemanticSuccesses"] = int(script_metrics.get("nativeSemanticSuccesses") or 0) + native_ready_steps
                if fingerprint_metrics is not None:
                    fingerprint_metrics["nativeSemanticSuccesses"] = int(fingerprint_metrics.get("nativeSemanticSuccesses") or 0) + native_ready_steps

        app_metrics = apps.setdefault(app_id, {"actions": {}})
        actions = app_metrics.setdefault("actions", {})
        for step in list(script.get("steps") or []):
            if not isinstance(step, dict):
                continue
            use = str(step.get("use") or "").strip()
            if not use:
                continue
            action_metrics = actions.setdefault(
                use,
                {
                    "runs": 0,
                    "successes": 0,
                    "fallbacks": 0,
                    "failures": 0,
                    "fallbackFailures": 0,
                    "reviewRequired": 0,
                    "compileBlocked": 0,
                    "nativeSemanticRuns": 0,
                    "nativeSemanticSuccesses": 0,
                },
            )
            action_metrics["runs"] += 1
            if normalized_outcome_family == "completed":
                action_metrics["successes"] += 1
            elif normalized_outcome_family == "completed_with_fallback":
                action_metrics["fallbacks"] += 1
            elif normalized_outcome_family == "failed" and execution_state == "fallback_failed":
                action_metrics["fallbackFailures"] += 1
                action_metrics["failures"] += 1
            elif normalized_outcome_family == "review_required":
                action_metrics["reviewRequired"] += 1
            elif normalized_outcome_family == "blocked":
                action_metrics["compileBlocked"] += 1
            elif normalized_outcome_family == "failed":
                action_metrics["failures"] += 1

            robot = step.get("robot") if isinstance(step.get("robot"), dict) else {}
            native_ready = bool(robot.get("library") and robot.get("keyword"))
            if native_ready:
                action_metrics["nativeSemanticRuns"] += 1
                if normalized_outcome_family == "completed":
                    action_metrics["nativeSemanticSuccesses"] += 1
        return self._save_trust_metrics(payload)

    def save_draft(self, script: RPAScript | Dict[str, Any]) -> Dict[str, Any]:
        payload = script.as_dict() if isinstance(script, RPAScript) else dict(script or {})
        payload["updatedAt"] = utc_now_iso()
        path = self._draft_path(str(payload.get("id") or "rpa_script"))
        if not payload.get("createdAt"):
            payload["createdAt"] = payload["updatedAt"]
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        return payload

    def get_draft(self, script_id: str) -> Optional[Dict[str, Any]]:
        path = self._draft_path(script_id)
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None

    def _append_template_history(
        self,
        *,
        template_id: str,
        template: Dict[str, Any],
        event: str,
        actor: str = "system",
        reason: str | None = None,
    ) -> Dict[str, Any]:
        payload = dict(template or {})
        metadata = dict(payload.get("metadata") or {})
        history_payload = {
            "templateId": template_id,
            "capturedAt": utc_now_iso(),
            "event": str(event or "snapshot"),
            "actor": str(actor or "system"),
            "reason": str(reason or "").strip() or None,
            "revision": int(metadata.get("revision") or 0) if metadata.get("revision") not in (None, "") else None,
            "status": str(metadata.get("templateStatus") or payload.get("status") or "candidate"),
            "template": payload,
        }
        path = self._template_history_path(template_id, revision=history_payload.get("revision"))
        path.write_text(json.dumps(history_payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        history_payload["path"] = str(path)
        return history_payload

    def save_template(
        self,
        template: Dict[str, Any],
        *,
        history_reason: str | None = None,
        history_actor: str = "system",
        write_history: bool = True,
    ) -> Dict[str, Any]:
        payload = dict(template or {})
        payload["updatedAt"] = utc_now_iso()
        template_id = str(payload.get("id") or "rpa_template")
        path = self._template_path(template_id)
        if write_history and path.exists():
            existing = self.get_template(template_id)
            if isinstance(existing, dict):
                self._append_template_history(
                    template_id=template_id,
                    template=existing,
                    event="snapshot",
                    actor=history_actor,
                    reason=history_reason or "save_template",
                )
        if not payload.get("createdAt"):
            payload["createdAt"] = payload["updatedAt"]
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        return payload

    def get_template(self, template_id: str) -> Optional[Dict[str, Any]]:
        path = self._template_path(template_id)
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None

    def list_drafts(self, *, limit: int = 100) -> list[Dict[str, Any]]:
        drafts: list[Dict[str, Any]] = []
        for path in sorted(self.draft_dir.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                payload.setdefault("path", str(path))
                drafts.append(payload)
            except Exception:
                continue
            if len(drafts) >= max(1, int(limit)):
                break
        return drafts

    def list_templates(self, *, limit: int = 100, app_id: str | None = None) -> list[Dict[str, Any]]:
        templates: list[Dict[str, Any]] = []
        normalized_app_id = str(app_id or "").strip().lower()
        for path in sorted(self.template_dir.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            current_app_id = str(payload.get("appId") or "").strip().lower()
            if normalized_app_id and current_app_id != normalized_app_id:
                continue
            payload.setdefault("path", str(path))
            templates.append(payload)
            if len(templates) >= max(1, int(limit)):
                break
        return templates

    def list_template_history(self, template_id: str, *, limit: int = 50) -> list[Dict[str, Any]]:
        history_root = self._template_history_root(template_id)
        if not history_root.exists():
            return []
        items: list[Dict[str, Any]] = []
        for path in sorted(history_root.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            payload.setdefault("path", str(path))
            items.append(payload)
            if len(items) >= max(1, int(limit)):
                break
        return items

    def list_robot_scripts(self, *, limit: int = 100) -> list[Dict[str, Any]]:
        scripts: list[Dict[str, Any]] = []
        for path in sorted(self.script_dir.glob("*.robot"), key=lambda item: item.stat().st_mtime, reverse=True):
            stat = path.stat()
            scripts.append(
                {
                    "name": path.name,
                    "path": str(path),
                    "updatedAt": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat().replace("+00:00", "Z"),
                    "size": int(stat.st_size),
                }
            )
            if len(scripts) >= max(1, int(limit)):
                break
        return scripts


rpa_script_store = RPAScriptStore()
