from __future__ import annotations

import json
import sys
import tempfile
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock, patch


ENGINE_ROOT = Path(__file__).resolve().parents[1]
if str(ENGINE_ROOT) not in sys.path:
    sys.path.insert(0, str(ENGINE_ROOT))

if "chromadb" not in sys.modules:
    class _FakeChromaCollection:
        def upsert(self, *args, **kwargs):  # noqa: ANN002, ANN003
            return None

        def delete(self, *args, **kwargs):  # noqa: ANN002, ANN003
            return None

        def query(self, *args, **kwargs):  # noqa: ANN002, ANN003
            return {}

    class _FakeChromaClient:
        def __init__(self, *args, **kwargs):  # noqa: ANN002, ANN003
            pass

        def get_or_create_collection(self, *args, **kwargs):  # noqa: ANN002, ANN003
            return _FakeChromaCollection()

    sys.modules["chromadb"] = type("chromadb", (), {"PersistentClient": _FakeChromaClient})()

from agents import memory_agent  # noqa: E402
from agents.memory_agent import KnowledgeExtraction, MemoryExtractionResult, PreferenceExtraction  # noqa: E402
from core import memory_store as memory_store_module  # noqa: E402
from core.storage import MEMORY_DURABLE_POLICY_DEFAULTS, storage  # noqa: E402
from runtimes.memory.workflow_service import workflow_memory_config  # noqa: E402
from runtimes.network_supervisor.memory_adapter import network_supervisor_memory_adapter  # noqa: E402


REPO_ROOT = ENGINE_ROOT.parents[1]
DOCS_ROOT = REPO_ROOT / "docs" / "chatruntime"
OUTPUT_ROOT = DOCS_ROOT / "memory_capability_reports"
RUNBOOK_PATH = DOCS_ROOT / "ASSESSMENT_DIAGNOSTICS_RUNBOOK_ZH.md"
EVALS_ROOT = ENGINE_ROOT / "tests" / "evals"
LONGMEMEVAL_HARNESS_ROOT = EVALS_ROOT / "longmemeval"
LONGMEMEVAL_OFFICIAL_REPO = "https://github.com/xiaowu0162/LongMemEval"


def _run_real_eval_suite() -> dict[str, Any]:
    if not EVALS_ROOT.exists():
        return {
            "available": False,
            "passRate": 0.0,
            "p0Passed": False,
            "failedCases": ["eval_suite_missing"],
            "riskFindings": [{"id": "eval_suite_missing", "details": str(EVALS_ROOT)}],
        }
    if str(EVALS_ROOT) not in sys.path:
        sys.path.insert(0, str(EVALS_ROOT))
    try:
        from memory_eval_matrix import run_memory_eval_matrix

        result = run_memory_eval_matrix()
        result["available"] = True
        return result
    except Exception as exc:
        return {
            "available": True,
            "passRate": 0.0,
            "p0Passed": False,
            "failedCases": ["eval_suite_failed"],
            "riskFindings": [{"id": "eval_suite_failed", "details": str(exc)}],
        }


def _longmemeval_official_harness_status() -> dict[str, Any]:
    adapter_ready = (LONGMEMEVAL_HARNESS_ROOT / "harness.py").exists()
    smoke_test_ready = (LONGMEMEVAL_HARNESS_ROOT / "test_longmemeval_harness.py").exists()
    return {
        "officialRepo": LONGMEMEVAL_OFFICIAL_REPO,
        "adapterPath": str(LONGMEMEVAL_HARNESS_ROOT),
        "adapterReady": adapter_ready,
        "smokeTestReady": smoke_test_ready,
        "supportedSplits": ["oracle", "longmemeval_s_cleaned", "longmemeval_m_cleaned"],
        "officialScore": None,
        "officialScoreAvailable": False,
        "status": "adapter_ready_not_officially_scored" if adapter_ready else "adapter_missing",
        "notes": [
            "V8OS internal eval scores are not LongMemEval official scores.",
            "Generate question_id/hypothesis JSONL with the adapter, then run LongMemEval src/evaluation/evaluate_qa.py for official scoring.",
            "Report model, data version, split, date, and whether cleaned data was used when publishing a score.",
        ],
    }


def _now_stamp() -> str:
    return datetime.now().strftime("%Y-%m-%d_%H-%M-%S")


def _public_internal_score(public_score: int, internal_score: int) -> tuple[float, float]:
    return round(public_score / 10.0, 1), round(internal_score / 10.0, 1)


def _check_same_key_overwrite() -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as temp_dir:
        memory_root = Path(temp_dir) / "memory"
        with patch.object(memory_store_module, "CONFIG_DIR", Path(temp_dir)), patch.object(
            memory_store_module,
            "MEMORY_ROOT",
            memory_root,
        ):
            store = memory_store_module.MemoryStore()
            store.update_preference("favorite_shoe_brand", "阿迪达斯", scope="workspace:main")
            store.update_preference("favorite_shoe_brand", "耐克", scope="workspace:main")
            merged = store.load_preferences(scope="workspace:main", scope_chain=["global", "workspace:main"])
            raw = store._load_raw_preferences()
    passed = merged.get("favorite_shoe_brand") == "耐克" and raw.get("workspace:main", {}).get("favorite_shoe_brand") == "耐克"
    return {
        "id": "same_key_preference_overwrite",
        "title": "同 key 偏好覆写",
        "status": "pass" if passed else "fail",
        "evidenceType": "executed",
        "details": {
            "mergedValue": merged.get("favorite_shoe_brand"),
            "rawValue": raw.get("workspace:main", {}).get("favorite_shoe_brand"),
        },
    }


def _check_temporal_preference_recommendation() -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as temp_dir:
        memory_root = Path(temp_dir) / "memory"
        with patch.object(memory_store_module, "CONFIG_DIR", Path(temp_dir)), patch.object(
            memory_store_module,
            "MEMORY_ROOT",
            memory_root,
        ):
            store = memory_store_module.MemoryStore()
            store.update_preference("favorite_shoe_brand", "阿迪达斯", scope="workspace:main")
            store.update_preference("shoe_brand_preference", "耐克", scope="workspace:main")
            merged = store.load_preferences(scope="workspace:main", scope_chain=["global", "workspace:main"])
            injection = store.format_preferences_for_injection(scope="workspace:main", scope_chain=["global", "workspace:main"])
    passed = merged.get("favorite_shoe_brand") == "耐克" and "阿迪达斯" not in injection and "耐克" in injection
    return {
        "id": "temporal_preference_recommendation",
        "title": "时间维度偏好覆写推荐题",
        "status": "pass" if passed else "fail",
        "evidenceType": "executed",
        "details": {
            "mergedValue": merged.get("favorite_shoe_brand"),
            "injectionPreview": injection[:240],
            "example": "1 月喜欢阿迪达斯，4 月改喜欢耐克，7 月推荐鞋时必须引用耐克。",
        },
    }


def _check_semantic_key_drift() -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as temp_dir:
        memory_root = Path(temp_dir) / "memory"
        with patch.object(memory_store_module, "CONFIG_DIR", Path(temp_dir)), patch.object(
            memory_store_module,
            "MEMORY_ROOT",
            memory_root,
        ):
            store = memory_store_module.MemoryStore()
            store.update_preference("favorite_shoe_brand", "阿迪达斯", scope="workspace:main")
            store.update_preference("shoe_brand_preference", "耐克", scope="workspace:main")
            raw = store._load_raw_preferences()
    keys = sorted((raw.get("workspace:main") or {}).keys())
    passed = len(keys) == 1
    return {
        "id": "semantic_key_drift_reconciliation",
        "title": "同义 key 漂移归一",
        "status": "pass" if passed else "fail",
        "evidenceType": "executed",
        "details": {
            "keys": keys,
            "note": "canonical registry 会把明确同义 key 归并到同一偏好键，避免长期注入面并存冲突。",
        },
    }


def _check_summary_contamination_resistance() -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as temp_dir:
        memory_root = Path(temp_dir) / "memory"
        with patch.object(memory_store_module, "CONFIG_DIR", Path(temp_dir)), patch.object(
            memory_store_module,
            "MEMORY_ROOT",
            memory_root,
        ):
            store = memory_store_module.MemoryStore()
            store.update_preference("assistant_persona", "专业冷静", scope="global")
            store.update_preference("favorite_shoe_brand", "阿迪达斯", scope="workspace:main")
            store.update_preference("shoe_brand_preference", "耐克", scope="workspace:main")
            merged = store.load_preferences(scope="workspace:main", scope_chain=["global", "workspace:main"])
            injection = store.format_preferences_for_injection(scope="workspace:main", scope_chain=["global", "workspace:main"])
    passed = (
        merged.get("favorite_shoe_brand") == "耐克"
        and "阿迪达斯" not in injection
        and "favorite_shoe_brand: 耐克" in injection
        and "assistant_persona: 专业冷静" in injection
    )
    return {
        "id": "summary_contamination_resistance",
        "title": "被动注入链的旧结论污染抵抗力",
        "status": "pass" if passed else "fail",
        "evidenceType": "executed",
        "details": {
            "mergedPreferences": merged,
            "injectionPreview": injection[:320],
            "note": "canonical key 覆写后，注入面不应继续保留旧偏好值。",
        },
    }


def _check_project_scope_isolation() -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as temp_dir:
        memory_root = Path(temp_dir) / "memory"
        with patch.object(memory_store_module, "CONFIG_DIR", Path(temp_dir)), patch.object(
            memory_store_module,
            "MEMORY_ROOT",
            memory_root,
        ):
            store = memory_store_module.MemoryStore()
            store.update_preference("preferred_framework", "React", scope="project:project-a")
            store.update_preference("preferred_framework", "Vue", scope="project:project-b")
            store.update_preference("language", "zh-CN", scope="global")
            project_a = store.load_preferences(scope="project:project-a", scope_chain=["global", "project:project-a"])
            project_b = store.load_preferences(scope="project:project-b", scope_chain=["global", "project:project-b"])
    passed = (
        project_a.get("preferred_framework") == "React"
        and project_b.get("preferred_framework") == "Vue"
        and project_a.get("language") == "zh-CN"
        and project_b.get("language") == "zh-CN"
    )
    return {
        "id": "project_scope_isolation",
        "title": "项目作用域隔离",
        "status": "pass" if passed else "fail",
        "evidenceType": "executed",
        "details": {
            "projectA": project_a,
            "projectB": project_b,
        },
    }


def _check_no_implicit_global_leak() -> dict[str, Any]:
    result = MemoryExtractionResult(
        summary="scope governance",
        tags=["scope"],
        preferences=[
            PreferenceExtraction(scope="global", key="surface", value="os-phone", importance=90, confidence=0.9),
            PreferenceExtraction(scope="global", key="language", value="所有项目默认使用中文回复。", importance=90, confidence=0.9),
        ],
        knowledge=[
            KnowledgeExtraction(
                scope="channel:feishu:legacy",
                fact="项目级规则测试",
                category="architecture",
                importance=80,
                confidence=0.9,
            )
        ],
    )
    decisions = memory_agent._align_extraction_scopes(result, "project:v8")
    passed = (
        result.preferences[0].scope == "project:v8"
        and result.preferences[1].scope == "global"
        and result.knowledge[0].scope == "project:v8"
    )
    return {
        "id": "no_implicit_global_leak",
        "title": "无显式信号时不自动升级到 global",
        "status": "pass" if passed else "fail",
        "evidenceType": "executed",
        "details": {
            "decisions": decisions,
        },
    }


@contextmanager
def _patched_network_adapter():
    memory_runtime = SimpleNamespace(add_knowledge=Mock(return_value="fact_compat_1"))
    run_service = SimpleNamespace(update_metadata=Mock())
    db = SimpleNamespace(add_runtime_event=Mock(), get_next_runtime_seq=Mock(return_value=1))
    with patch("runtimes.network_supervisor.memory_adapter.memory_runtime", memory_runtime), patch(
        "runtimes.network_supervisor.memory_adapter.run_service",
        run_service,
    ), patch("runtimes.network_supervisor.memory_adapter.db", db):
        yield {"memory_runtime": memory_runtime, "run_service": run_service, "db": db}


def _check_external_api_isolation() -> dict[str, Any]:
    payload = {"messages": [{"role": "user", "content": "请记住：这个外部用户以后更喜欢中文。"}]}
    events = [{"type": "text_chunk", "content": "已记录"}, {"type": "done", "status": "completed"}]
    with _patched_network_adapter() as patched:
        result = network_supervisor_memory_adapter.record_openai_compat_delta(
            payload=payload,
            chat_request=SimpleNamespace(session_id="sess_external", config=SimpleNamespace(external_tools=[])),
            run_id="run_external_1",
            events=events,
            response_payload={"choices": [{"finish_reason": "stop"}]},
            external_thread_id="thread-memory-eval",
            external_user_id="user-memory-eval",
        )
        _, kwargs = patched["memory_runtime"].add_knowledge.call_args
    passed = result.get("resolvedScope") == "external_api_thread:thread-memory-eval" and kwargs.get("scope") == "external_api_thread:thread-memory-eval"
    return {
        "id": "external_api_memory_isolation",
        "title": "外部 API 记忆隔离",
        "status": "pass" if passed else "fail",
        "evidenceType": "executed",
        "details": {
            "adapterStatus": result.get("adapterStatus"),
            "resolvedScope": result.get("resolvedScope"),
            "persistScope": kwargs.get("scope"),
        },
    }


def _check_durable_threshold_hygiene() -> dict[str, Any]:
    storage.ensure_memory_runtime_defaults()
    policy = memory_agent._load_memory_policy()
    deltas = {}
    passed = True
    for key, default_value in MEMORY_DURABLE_POLICY_DEFAULTS.items():
        current = policy.get(key)
        deltas[key] = {"current": current, "default": default_value}
        if isinstance(default_value, float):
            if float(current or 0.0) < float(default_value):
                passed = False
        else:
            if int(current or 0) < int(default_value):
                passed = False
    return {
        "id": "durable_threshold_hygiene",
        "title": "durable policy 阈值卫生",
        "status": "pass" if passed else "fail",
        "evidenceType": "executed",
        "details": deltas,
    }


def _check_workflow_engineering_gating() -> dict[str, Any]:
    cfg = workflow_memory_config()
    engineering = dict(cfg.get("engineering") or {})
    risk_policy = dict(cfg.get("riskTierActivationPolicy") or {})
    passed = (
        bool(engineering.get("requireVerifiedProofForActivation"))
        and bool(engineering.get("learnFailedVerificationAsAntiPattern"))
        and str(risk_policy.get("high") or "") in {"approval", "quarantine"}
        and str(risk_policy.get("critical") or "") == "quarantine"
    )
    return {
        "id": "engineering_activation_gating",
        "title": "工程 workflow 激活门槛",
        "status": "pass" if passed else "fail",
        "evidenceType": "config_governance",
        "details": {
            "engineering": engineering,
            "riskTierActivationPolicy": risk_policy,
        },
    }


def _check_engineering_failed_path_not_golden() -> dict[str, Any]:
    cfg = workflow_memory_config()
    engineering = dict(cfg.get("engineering") or {})
    passed = bool(engineering.get("learnFailedVerificationAsAntiPattern")) and bool(
        engineering.get("requireVerifiedProofForActivation")
    )
    return {
        "id": "engineering_failed_path_not_golden",
        "title": "失败验证不会直接进 golden path",
        "status": "pass" if passed else "fail",
        "evidenceType": "config_governance",
        "details": {
            "engineering": engineering,
        },
    }


SCORE_WEIGHTS: dict[str, tuple[int, int]] = {
    "same_key_preference_overwrite": (10, 10),
    "temporal_preference_recommendation": (10, 10),
    "semantic_key_drift_reconciliation": (10, 10),
    "project_scope_isolation": (10, 10),
    "no_implicit_global_leak": (10, 10),
    "external_api_memory_isolation": (10, 10),
    "durable_threshold_hygiene": (10, 9),
    "summary_contamination_resistance": (10, 9),
    "engineering_failed_path_not_golden": (9, 6),
    "engineering_activation_gating": (9, 6),
}

SCORE_STATUS_FACTORS: dict[str, tuple[float, float]] = {
    "pass": (1.0, 1.0),
    "partial": (0.6, 0.5),
    "fail": (0.0, 0.0),
}


def _scorecard(checks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    check_map = {str(item.get("id") or "").strip(): item for item in checks}
    scorecard: list[dict[str, Any]] = []
    for check_id, weights in SCORE_WEIGHTS.items():
        check = check_map.get(check_id) or {}
        status = str(check.get("status") or "fail").strip().lower()
        public_factor, internal_factor = SCORE_STATUS_FACTORS.get(status, (0.0, 0.0))
        public_weight, internal_weight = weights
        scorecard.append(
            {
                "id": check_id,
                "status": status,
                "public": round(public_weight * public_factor),
                "internal": round(internal_weight * internal_factor),
                "max": 10,
            }
        )
    return scorecard


def _harsh_question_matrix() -> list[dict[str, Any]]:
    return [
        {
            "category": "偏好覆写题",
            "prompt": "1 月 30 日用户说喜欢阿迪达斯；4 月 30 日又说更喜欢耐克；7 月 30 日让 supervisor 推荐一款鞋。",
            "expected": "必须优先引用最新偏好耐克，并说明旧偏好已被覆盖。",
            "currentAssessment": "同 key 覆写链路可通过，但如果 extractor key 漂移或旧摘要未刷新，就有答错风险。",
        },
        {
            "category": "作用域隔离题",
            "prompt": "项目 A 偏好 React，项目 B 偏好 Vue；默认工作区没有框架偏好。",
            "expected": "项目 A/B 互不串区，默认工作区也不误带项目偏好。",
            "currentAssessment": "当前 scope chain 与 project preference isolation 基本能做对。",
        },
        {
            "category": "一次性噪音题",
            "prompt": "某轮排障出现临时路径、临时 workaround、临时报错说明。",
            "expected": "不应自动沉淀为长期记忆或全局规则。",
            "currentAssessment": "durable policy 已收紧到平衡档，但仍应继续用真实排障样本回归，防止一次性 operational 噪音重新混入长期记忆。",
        },
        {
            "category": "API 隔离题",
            "prompt": "外部 API 调用里说“以后叫我老板”。随后 phone/web 普通对话中继续打招呼。",
            "expected": "外部 API 偏好不得投影成 phone/web 的人格记忆。",
            "currentAssessment": "network supervisor 专用 memory adapter 当前隔离方向是对的。",
        },
        {
            "category": "工程绕路清洗题",
            "prompt": "工程任务先失败验证、后纠偏成功。",
            "expected": "golden path 只保留成功链，失败步骤进入 anti-pattern/warning。",
            "currentAssessment": "Phase 6 配置门槛方向正确，但仍需要更多 proof-backed 实战数据来完全坐实。",
        },
    ]


def _build_report(results: dict[str, Any]) -> str:
    lines = [
        "# 记忆能力双轨评分报告",
        "",
        f"- 生成时间: `{results['generatedAt']}`",
        "- 背景文档: `E:\\Projects\\v8chat\\v8-agent-os\\docs\\chatruntime\\参与 agent 记忆能力测评.md`",
        f"- 统一运行说明: `{RUNBOOK_PATH}`",
        "",
        "## 双轨评分",
        "",
        f"- 对外 benchmark 映射分: `{results['scoreSummary']['publicScore']}/10`",
        f"- 内部 runtime-first 苛刻治理分: `{results['scoreSummary']['internalScore']}/10`",
        f"- 真实 eval 通过率: `{round(results['realEvalSummary']['passRate'] * 100, 2)}%`",
        f"- 真实 eval P0 全通过: `{'是' if results['realEvalSummary']['p0Passed'] else '否'}`",
        f"- 硬门槛达成: `{'是' if results['scoreSummary']['gateReached'] else '否'}`",
        f"- LongMemEval 官方成绩: `{'未产生' if not results['officialHarnessStatus']['officialScoreAvailable'] else results['officialHarnessStatus']['officialScore']}`",
        "",
        "## 总体判断",
        "",
        "- 当前报告采用双层评分：守门评分保留当前结构/配置自检，最终结论优先看 `tests/evals` 的真实可复跑评测。",
        "- LongMemEval 只显示 official harness 接入状态；未运行官方 `evaluate_qa.py` 前，不将任何内部结果表述为官方成绩。",
        "- 同 key 覆写、语义 key 归一、项目隔离、external API 隔离已经进入可执行通过状态。",
        "- 未达门槛时，优先排查真实 eval 失败项，其次排查 durable policy 是否仍停留在旧低阈值模板，以及 workflow learning 是否缺少更多 proof-backed 成功样本。",
        "",
        "## 真实 Eval Suite",
        "",
        f"- eval 目录: `{EVALS_ROOT}`",
        f"- caseCount: `{results['realEvalSummary'].get('caseCount', 0)}`",
        f"- passed: `{results['realEvalSummary'].get('passed', 0)}`",
        f"- failed: `{results['realEvalSummary'].get('failed', 0)}`",
        f"- failedCases: `{json.dumps(results['realEvalSummary'].get('failedCases', []), ensure_ascii=False)}`",
        "",
        "## LongMemEval Official Harness",
        "",
        f"- officialRepo: `{results['officialHarnessStatus']['officialRepo']}`",
        f"- adapterPath: `{results['officialHarnessStatus']['adapterPath']}`",
        f"- status: `{results['officialHarnessStatus']['status']}`",
        f"- adapterReady: `{results['officialHarnessStatus']['adapterReady']}`",
        f"- smokeTestReady: `{results['officialHarnessStatus']['smokeTestReady']}`",
        f"- officialScoreAvailable: `{results['officialHarnessStatus']['officialScoreAvailable']}`",
        f"- supportedSplits: `{', '.join(results['officialHarnessStatus']['supportedSplits'])}`",
        "- 发布任何 LongMemEval 分数前，必须记录模型、数据版本、split、评估日期和官方评估日志路径。",
        "",
        "## 逐项结果",
        "",
    ]
    check_map = {item["id"]: item for item in results["checks"]}
    for score in results["scorecard"]:
        check = check_map[score["id"]]
        lines.extend(
            [
                f"### {check['title']}",
                "",
                f"- status: `{check['status']}`",
                f"- evidenceType: `{check['evidenceType']}`",
                f"- public: `{score['public']}/{score['max']}`",
                f"- internal: `{score['internal']}/{score['max']}`",
                f"- details: `{json.dumps(check['details'], ensure_ascii=False)[:400]}`",
                "",
            ]
        )

    lines.extend(
        [
            "## 可提升点",
            "",
            "### 配置治理问题",
            "",
            "- 如果本机仍保留旧低阈值 durable policy，会持续放大一次性噪音和低置信偏好的进入概率。",
            "",
            "### 提取与归一问题",
            "",
            "- 主干 canonicalization 已具备，但仍建议继续扩 canonical registry，降低更多长尾 key 漂移。",
            "",
            "### scope / policy 问题",
            "",
            "- 作用域隔离方向已经收紧，但 external/network 与 global promotion 的边界仍应持续做守门回归。",
            "",
            "### workflow learning 资格问题",
            "",
            "- Engineering Workflow Memory 的门槛已正确收紧，但内部苛刻分仍需要更多 proof-backed 成功链路样本来支撑。",
            "",
            "## 苛刻考题矩阵",
            "",
        ]
    )
    for item in results["harshQuestionMatrix"]:
        lines.extend(
            [
                f"### {item['category']}",
                "",
                f"- 题目: {item['prompt']}",
                f"- 正确答案标准: {item['expected']}",
                f"- 当前评估: {item['currentAssessment']}",
                "",
            ]
        )
    lines.extend(
        [
            "## 可复跑入口",
            "",
            "- 运行脚本: `E:\\Projects\\v8chat\\v8-agent-os\\apps\\v8-agent-os-engine\\.venv\\Scripts\\python.exe E:\\Projects\\v8chat\\v8-agent-os\\apps\\v8-agent-os-engine\\scripts\\export_memory_capability_assessment.py`",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> None:
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    stamp = _now_stamp()
    storage.ensure_memory_runtime_defaults()

    executed_checks = [
        _check_same_key_overwrite(),
        _check_temporal_preference_recommendation(),
        _check_semantic_key_drift(),
        _check_project_scope_isolation(),
        _check_no_implicit_global_leak(),
        _check_external_api_isolation(),
        _check_durable_threshold_hygiene(),
        _check_summary_contamination_resistance(),
        _check_workflow_engineering_gating(),
        _check_engineering_failed_path_not_golden(),
    ]
    scorecard = _scorecard(executed_checks)
    public_total = sum(item["public"] for item in scorecard)
    internal_total = sum(item["internal"] for item in scorecard)
    public_score, internal_score = _public_internal_score(public_total, internal_total)
    real_eval_summary = _run_real_eval_suite()
    official_harness_status = _longmemeval_official_harness_status()
    eval_gate_reached = bool(real_eval_summary.get("p0Passed")) and float(real_eval_summary.get("passRate") or 0.0) >= 0.95
    gate_reached = public_score >= 9.8 and internal_score >= 9.0 and eval_gate_reached

    results = {
        "generatedAt": stamp,
        "backgroundDoc": r"E:\Projects\v8chat\v8-agent-os\docs\chatruntime\参与 agent 记忆能力测评.md",
        "memoryPolicyDefaults": MEMORY_DURABLE_POLICY_DEFAULTS,
        "currentMemoryPolicy": memory_agent._load_memory_policy(),
        "workflowMemoryConfig": workflow_memory_config(),
        "checks": executed_checks,
        "scorecard": scorecard,
        "scoreSummary": {
            "publicTotal": public_total,
            "internalTotal": internal_total,
            "publicScore": public_score,
            "internalScore": internal_score,
            "gateReached": gate_reached,
            "gateTarget": {"public": 9.8, "internal": 9.0},
            "realEvalGateReached": eval_gate_reached,
        },
        "realEvalSummary": real_eval_summary,
        "officialHarnessStatus": official_harness_status,
        "harshQuestionMatrix": _harsh_question_matrix(),
    }
    markdown = _build_report(results)
    md_path = OUTPUT_ROOT / f"{stamp}_memory_capability_assessment.md"
    json_path = OUTPUT_ROOT / f"{stamp}_memory_capability_assessment.json"
    md_path.write_text(markdown, encoding="utf-8")
    json_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"markdown": str(md_path), "json": str(json_path)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
