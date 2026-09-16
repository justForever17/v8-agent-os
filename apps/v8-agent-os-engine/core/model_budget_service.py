from __future__ import annotations

from datetime import datetime, timezone
from contextlib import contextmanager, nullcontext
import math
import os
import uuid
from typing import Any, Dict, List

from core.database import db
from core.model_governance_exceptions import ModelGovernanceInterventionRequired

_PROCESS_OWNER = f"{os.getpid()}:{uuid.uuid4().hex}"


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or value == "":
            return default
        return int(value)
    except Exception:
        return default


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def _today_bucket() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


class ModelBudgetService:
    def __init__(self, database=None):
        self._database = database

    @property
    def database(self):
        return self._database if self._database is not None else db

    def _holds(self, conn, *, bucket_date=None, run_id=None, project_id=None):
        # Run totals for reserved invocations live here, independently of the
        # best-effort observability log. Daily/project totals use usage_ledger.
        settled = "state='settled'" if run_id else "(state='settled' AND ledger_accounted=0)"
        query = f"SELECT COALESCE(SUM(CASE WHEN state='settled' THEN COALESCE(actual_tokens,estimated_tokens) ELSE estimated_tokens END),0) tokens, COALESCE(SUM(CASE WHEN state='settled' THEN COALESCE(actual_cost,estimated_cost) ELSE estimated_cost END),0) cost, COUNT(*) invocations, COALESCE(SUM(state='unknown'),0) unknown FROM model_budget_reservations WHERE (state IN ('reserved','in_flight','unknown') OR {settled})"
        values = []
        if bucket_date:
            # Running requests may finish after UTC midnight. Unknown old-day
            # usage remains recorded but does not permanently consume a new day.
            query += " AND (bucket_date=? OR state IN ('reserved','in_flight'))"
            values.append(bucket_date)
        if run_id:
            query += " AND run_id=?"
            values.append(run_id)
        if project_id:
            query += " AND project_id=?"
            values.append(project_id)
        return dict(conn.execute(query, values).fetchone())

    def _budget_config(self, config: Dict[str, Any]) -> Dict[str, Any]:
        governance = dict((config or {}).get("governance") or {})
        return dict(governance.get("budgets") or {})

    def _project_budget(self, config: Dict[str, Any], project_id: str | None) -> Dict[str, Any]:
        budgets = self._budget_config(config)
        overrides = list(budgets.get("projectBudgets") or [])
        if project_id:
            for override in overrides:
                if str(override.get("projectId") or "") == str(project_id):
                    return {
                        "projectId": str(project_id),
                        "dailyCostLimit": _safe_float(override.get("dailyCostLimit")),
                        "dailyTokenLimit": _safe_int(override.get("dailyTokenLimit")),
                        "source": "override",
                    }
        return {
            "projectId": str(project_id or ""),
            "dailyCostLimit": _safe_float(budgets.get("defaultProjectDailyCostLimit")),
            "dailyTokenLimit": _safe_int(budgets.get("defaultProjectDailyTokenLimit")),
            "source": "default",
        }

    def build_budget_summary(self, config: Dict[str, Any]) -> Dict[str, Any]:
        budgets = self._budget_config(config)
        today = _today_bucket()
        global_usage = self.database.get_usage_ledger_totals(bucket_date=today)
        with self.database.get_connection() as conn:
            global_holds = self._holds(conn, bucket_date=today)
        project_budgets: List[Dict[str, Any]] = []
        for override in list(budgets.get("projectBudgets") or []):
            project_id = str(override.get("projectId") or "")
            if not project_id:
                continue
            usage = self.database.get_usage_ledger_totals(
                bucket_date=today,
                scope_type="project",
                scope_id=project_id,
            )
            project_budgets.append(
                {
                    "projectId": project_id,
                    "dailyCostLimit": _safe_float(override.get("dailyCostLimit")),
                    "dailyTokenLimit": _safe_int(override.get("dailyTokenLimit")),
                    "usage": {
                        "costTotal": float(usage.get("cost_total") or 0.0),
                        "totalTokens": int(usage.get("total_tokens") or 0),
                        "invocations": int(usage.get("invocations") or 0),
                    },
                }
            )
            with self.database.get_connection() as conn:
                project_budgets[-1]["reserved"] = self._public_holds(self._holds(
                    conn, bucket_date=today, project_id=project_id,
                ))

        return {
            "enabled": bool(budgets.get("enabled", True)),
            "reservationMode": "estimated",
            "estimatedOutputTokens": max(1, _safe_int(budgets.get("estimatedOutputTokens"), 1024)),
            "today": today,
            "global": {
                "dailyCostLimit": _safe_float(budgets.get("globalDailyCostLimit")),
                "dailyTokenLimit": _safe_int(budgets.get("globalDailyTokenLimit")),
                "reserved": self._public_holds(global_holds),
                "usage": {
                    "costTotal": float(global_usage.get("cost_total") or 0.0),
                    "totalTokens": int(global_usage.get("total_tokens") or 0),
                    "invocations": int(global_usage.get("invocations") or 0),
                },
            },
            "run": {
                "maxCost": _safe_float(budgets.get("runMaxCost")),
                "maxTokens": _safe_int(budgets.get("runMaxTokens")),
            },
            "projectDefaults": {
                "dailyCostLimit": _safe_float(budgets.get("defaultProjectDailyCostLimit")),
                "dailyTokenLimit": _safe_int(budgets.get("defaultProjectDailyTokenLimit")),
            },
            "projectBudgets": project_budgets,
        }

    @staticmethod
    def _public_holds(holds):
        return {"totalTokens": int(holds.get("tokens") or 0),
                "costTotal": float(holds.get("cost") or 0),
                "invocations": int(holds.get("invocations") or 0),
                "unknownUsageInvocations": int(holds.get("unknown") or 0)}

    def _checks(self, config, run_id, project_id):
        budgets = self._budget_config(config)
        today = _today_bucket()
        global_usage = self.database.get_usage_ledger_totals(bucket_date=today)
        run_usage = self.database.get_run_invocation_totals(run_id, unreserved_only=True) if run_id else {}
        project_budget = self._project_budget(config, project_id)
        project_usage = self.database.get_usage_ledger_totals(
            bucket_date=today, scope_type="project", scope_id=str(project_id),
        ) if project_id else {}
        return [
            ("global_daily_cost", _safe_float(budgets.get("globalDailyCostLimit")), float(global_usage.get("cost_total") or 0), "global", "cost"),
            ("global_daily_tokens", _safe_int(budgets.get("globalDailyTokenLimit")), int(global_usage.get("total_tokens") or 0), "global", "tokens"),
            ("run_cost", _safe_float(budgets.get("runMaxCost")) if run_id else 0, float(run_usage.get("cost_total") or 0), "run", "cost"),
            ("run_tokens", _safe_int(budgets.get("runMaxTokens")) if run_id else 0, int(run_usage.get("total_tokens") or 0), "run", "tokens"),
            ("project_daily_cost", _safe_float(project_budget.get("dailyCostLimit")) if project_id else 0, float(project_usage.get("cost_total") or 0), "project", "cost"),
            ("project_daily_tokens", _safe_int(project_budget.get("dailyTokenLimit")) if project_id else 0, int(project_usage.get("total_tokens") or 0), "project", "tokens"),
        ]

    @staticmethod
    def _intervention(code, *, limit=None, usage=None, reserved=None, requested=None, **details):
        question = "当前模型预算不足或无法估算，请调整预算配置，或等待正在执行的调用完成后重试。"
        return ModelGovernanceInterventionRequired(
            question, approval_kind="budget_review", question=question,
            details={"code": code, "limit": limit, "usage": usage, "reserved": reserved,
                     "requested": requested, "reservationMode": "estimated", **details},
        )

    def reserve(self, *, config, run_id=None, project_id=None, provider_id="", model_id="",
                role="", capability_class="", estimated_tokens, estimated_cost=None, reservation_id=None):
        if not self._budget_config(config).get("enabled", True):
            return None
        if not any(check[1] > 0 for check in self._checks(config, run_id, project_id)):
            return None
        tokens = max(1, int(estimated_tokens))
        if estimated_cost is not None and (not math.isfinite(float(estimated_cost)) or float(estimated_cost) < 0):
            raise self._intervention("budget_estimate_invalid")
        key = str(reservation_id or uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        with self.database.get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute("SELECT state FROM model_budget_reservations WHERE id=?", (key,)).fetchone()
            if existing:
                # Reusing an accounting identity must not authorize a second call.
                raise self._intervention("budget_reservation_duplicate")
            holds = {"global": self._holds(conn, bucket_date=_today_bucket()),
                     "run": self._holds(conn, run_id=run_id) if run_id else {},
                     "project": self._holds(conn, bucket_date=_today_bucket(), project_id=project_id) if project_id else {}}
            for code, limit, usage, scope, unit in self._checks(config, run_id, project_id):
                if limit <= 0:
                    continue
                requested = estimated_cost if unit == "cost" else tokens
                if requested is None:
                    raise self._intervention("budget_cost_estimate_unavailable", modelId=model_id, role=role)
                held = holds[scope].get(unit, 0)
                if usage + held + requested > limit:
                    raise self._intervention(code, limit=limit, usage=usage, reserved=held, requested=requested,
                                             runId=run_id, projectId=project_id, modelId=model_id, role=role,
                                             capabilityClass=capability_class)
            conn.execute("""INSERT INTO model_budget_reservations
                (id,owner_id,run_id,project_id,provider_id,model_id,bucket_date,estimated_tokens,estimated_cost,state,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,'reserved',?,?)""",
                (key, _PROCESS_OWNER, run_id, project_id, provider_id, model_id, _today_bucket(), tokens,
                 float(estimated_cost or 0), now, now))
            conn.commit()
        return key

    def mark_dispatched(self, reservation_id):
        if not reservation_id:
            return
        with self.database.get_connection() as conn:
            changed = conn.execute("UPDATE model_budget_reservations SET state='in_flight', updated_at=? WHERE id=? AND state='reserved'",
                                   (datetime.now(timezone.utc).isoformat(), reservation_id)).rowcount
            conn.commit()
            if changed != 1:
                raise self._intervention("budget_reservation_not_dispatchable")

    def mark_ledger_accounted(self, reservation_id, *, connection=None):
        if not reservation_id:
            return
        context = nullcontext(connection) if connection is not None else self.database.get_connection()
        with context as conn:
            conn.execute("UPDATE model_budget_reservations SET ledger_accounted=1,updated_at=? WHERE id=? AND state='settled'",
                         (datetime.now(timezone.utc).isoformat(), reservation_id))
            if connection is None:
                conn.commit()

    def release(self, reservation_id, *, reason="not_dispatched"):
        if not reservation_id:
            return
        with self.database.get_connection() as conn:
            # Cancellation after dispatch has unknown remote consumption; it is
            # not proof that the reserved money/tokens were never consumed.
            conn.execute("""UPDATE model_budget_reservations SET state=CASE WHEN state='reserved' THEN 'released' ELSE 'unknown' END,
                reason=?,updated_at=? WHERE id=? AND state IN ('reserved','in_flight')""",
                (reason, datetime.now(timezone.utc).isoformat(), reservation_id))
            conn.commit()

    @contextmanager
    def settlement(self, reservation_id, *, usage_reported, actual_tokens=0, actual_cost=0):
        if not reservation_id:
            yield None, True
            return
        with self.database.get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT state FROM model_budget_reservations WHERE id=?", (reservation_id,)).fetchone()
            if not row or row["state"] in {"settled", "released"}:
                yield conn, False
                return
            try:
                # The caller writes the canonical usage ledger using this same
                # transaction, so no gap exists between charging and releasing.
                conn.execute("""UPDATE model_budget_reservations SET state=?,actual_tokens=?,actual_cost=?,reason=?,updated_at=? WHERE id=?""",
                    ("settled" if usage_reported else "unknown", int(actual_tokens) if usage_reported else None,
                     float(actual_cost) if usage_reported else None, "usage_reported" if usage_reported else "usage_not_reported",
                     datetime.now(timezone.utc).isoformat(), reservation_id))
                yield conn, True
                conn.commit()
            except BaseException:
                conn.rollback()
                self.release(reservation_id, reason="settlement_failed")
                raise

    def recover_orphans(self):
        """Call only after acquiring the exclusive Engine state-root ownership."""
        with self.database.get_connection() as conn:
            result = conn.execute("""UPDATE model_budget_reservations
                SET state=CASE WHEN state='reserved' THEN 'released' ELSE 'unknown' END,
                    reason='previous_engine_stopped',updated_at=?
                WHERE owner_id<>? AND state IN ('reserved','in_flight')""",
                (datetime.now(timezone.utc).isoformat(), _PROCESS_OWNER)).rowcount
            conn.commit()
        return result

    def enforce_or_raise(
        self,
        *,
        config: Dict[str, Any],
        run_id: str | None,
        project_id: str | None,
        role: str = "",
        capability_class: str = "",
        model_id: str = "",
    ) -> None:
        budgets = self._budget_config(config)
        if not budgets.get("enabled", True):
            return
        for code, limit, usage, scope, unit in self._checks(config, run_id, project_id):
            if limit > 0 and usage >= limit:
                raise self._intervention(code, limit=limit, usage=usage, requested=0,
                                         runId=run_id, projectId=project_id, role=role,
                                         capabilityClass=capability_class, modelId=model_id)



model_budget_service = ModelBudgetService()
