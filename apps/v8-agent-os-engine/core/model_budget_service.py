from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List

from core.database import db
from core.model_governance_exceptions import ModelGovernanceInterventionRequired


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
    return datetime.utcnow().strftime("%Y-%m-%d")


class ModelBudgetService:
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
        global_usage = db.get_usage_ledger_totals(bucket_date=today)
        project_budgets: List[Dict[str, Any]] = []
        for override in list(budgets.get("projectBudgets") or []):
            project_id = str(override.get("projectId") or "")
            if not project_id:
                continue
            usage = db.get_usage_ledger_totals(
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

        return {
            "enabled": bool(budgets.get("enabled", True)),
            "today": today,
            "global": {
                "dailyCostLimit": _safe_float(budgets.get("globalDailyCostLimit")),
                "dailyTokenLimit": _safe_int(budgets.get("globalDailyTokenLimit")),
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

        today = _today_bucket()
        global_usage = db.get_usage_ledger_totals(bucket_date=today)
        run_usage = db.get_run_invocation_totals(run_id) if run_id else {}
        project_budget = self._project_budget(config, project_id)
        project_usage = (
            db.get_usage_ledger_totals(bucket_date=today, scope_type="project", scope_id=str(project_id))
            if project_id
            else {}
        )

        checks = [
            (
                "global_daily_cost",
                _safe_float(budgets.get("globalDailyCostLimit")),
                float(global_usage.get("cost_total") or 0.0),
                "今天的全局模型成本预算已经达到上限。",
            ),
            (
                "global_daily_tokens",
                _safe_int(budgets.get("globalDailyTokenLimit")),
                int(global_usage.get("total_tokens") or 0),
                "今天的全局 token 预算已经达到上限。",
            ),
            (
                "run_cost",
                _safe_float(budgets.get("runMaxCost")),
                float(run_usage.get("cost_total") or 0.0),
                "当前 run 的模型成本已经达到上限。",
            ),
            (
                "run_tokens",
                _safe_int(budgets.get("runMaxTokens")),
                int(run_usage.get("total_tokens") or 0),
                "当前 run 的 token 用量已经达到上限。",
            ),
            (
                "project_daily_cost",
                _safe_float(project_budget.get("dailyCostLimit")),
                float(project_usage.get("cost_total") or 0.0),
                f"项目 {project_id} 今天的模型成本预算已经达到上限。" if project_id else "",
            ),
            (
                "project_daily_tokens",
                _safe_int(project_budget.get("dailyTokenLimit")),
                int(project_usage.get("total_tokens") or 0),
                f"项目 {project_id} 今天的 token 预算已经达到上限。" if project_id else "",
            ),
        ]

        for code, limit, usage, question in checks:
            if not limit or limit <= 0:
                continue
            if usage < limit:
                continue
            raise ModelGovernanceInterventionRequired(
                question,
                approval_kind="budget_review",
                question=question + " 是否允许你手动放宽预算或稍后重试？",
                details={
                    "code": code,
                    "limit": limit,
                    "usage": usage,
                    "projectId": project_id,
                    "runId": run_id,
                    "role": role,
                    "capabilityClass": capability_class,
                    "modelId": model_id,
                },
            )


model_budget_service = ModelBudgetService()
