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
            "note": "当前系统不会自动把语义相近的不同 key 归并成同一偏好。",
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


def _analysis_only_cases() -> list[dict[str, Any]]:
    return [
        {
            "id": "temporal_preference_recommendation",
            "title": "时间维度偏好覆写推荐题",
            "status": "partial",
            "evidenceType": "analysis",
            "details": {
                "note": "同 key 覆写本身可行，但最终能否在数月后稳定答对，仍依赖 extractor key 稳定、summary 不残留旧偏好、以及新事件成功进入 durable memory。",
                "example": "1 月喜欢阿迪达斯，4 月改喜欢耐克，7 月问推荐鞋时必须引用最新偏好。",
            },
        },
        {
            "id": "summary_contamination_resistance",
            "title": "摘要污染抵抗力",
            "status": "partial",
            "evidenceType": "analysis",
            "details": {
                "memoryConfig": storage.get_memory_config(),
                "note": "当前摘要与被动注入链路存在，但 durable 阈值偏低会放大噪音进入长期记忆的概率，因此对苛刻场景只能给 partial。",
            },
        },
    ]


def _scorecard() -> list[dict[str, Any]]:
    return [
        {"id": "same_key_preference_overwrite", "public": 10, "internal": 10, "max": 10},
        {"id": "temporal_preference_recommendation", "public": 6, "internal": 4, "max": 10},
        {"id": "semantic_key_drift_reconciliation", "public": 0, "internal": 0, "max": 10},
        {"id": "project_scope_isolation", "public": 10, "internal": 10, "max": 10},
        {"id": "no_implicit_global_leak", "public": 10, "internal": 10, "max": 10},
        {"id": "external_api_memory_isolation", "public": 10, "internal": 10, "max": 10},
        {"id": "durable_threshold_hygiene", "public": 2, "internal": 0, "max": 10},
        {"id": "summary_contamination_resistance", "public": 5, "internal": 2, "max": 10},
        {"id": "engineering_failed_path_not_golden", "public": 8, "internal": 6, "max": 10},
        {"id": "engineering_activation_gating", "public": 7, "internal": 4, "max": 10},
    ]


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
            "currentAssessment": "durable 阈值当前偏低，这类题是现阶段最容易翻车的点之一。",
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
        "",
        "## 总体判断",
        "",
        "- 当前系统不是“没有记忆”，而是“基础能力有了，但对冲突更新、语义归一、噪音阈值治理还不够稳”。",
        "- 同 key 覆写、项目隔离、external API 隔离这几条已经具备不错基础。",
        "- 真正拖分的点主要是 durable policy 阈值过低、同义 key 漂移、旧摘要/旧结论可能在时间跨度题里压过新事实。",
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
            "- 当前 durable policy 阈值显著低于默认推荐值，容易让一次性噪音或低置信偏好进入长期记忆。",
            "",
            "### 提取与归一问题",
            "",
            "- `favorite_shoe_brand` 与 `shoe_brand_preference` 这类语义同义 key 目前不会自动归一，是时间跨度题的真实风险点。",
            "",
            "### scope / policy 问题",
            "",
            "- 作用域隔离方向基本正确，但任何把 global 当默认写入的 extractor 漂移，都会显著放大串区风险。",
            "",
            "### workflow learning 资格问题",
            "",
            "- Engineering Workflow Memory 的激活门槛方向正确，但要拿高分还需要更多 proof-backed 成功链路样本来证明不会误学失败绕路。",
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

    executed_checks = [
        _check_same_key_overwrite(),
        _check_semantic_key_drift(),
        _check_project_scope_isolation(),
        _check_no_implicit_global_leak(),
        _check_external_api_isolation(),
        _check_durable_threshold_hygiene(),
        _check_workflow_engineering_gating(),
        _check_engineering_failed_path_not_golden(),
    ]
    executed_checks.extend(_analysis_only_cases())
    scorecard = _scorecard()
    public_total = sum(item["public"] for item in scorecard)
    internal_total = sum(item["internal"] for item in scorecard)
    public_score, internal_score = _public_internal_score(public_total, internal_total)

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
        },
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
