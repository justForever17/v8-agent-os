from __future__ import annotations

import asyncio
import json
import time
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest


# ==============================================================================
# 1. MCP Client Concurrent Initialization Test
# ==============================================================================
@pytest.mark.anyio
async def test_mcp_client_concurrent_initialization() -> None:
    from runtimes.extensions.mcp.client import MCPManager

    manager = MCPManager()
    mock_config = {
        "mcpServers": {
            "server_alpha": {"command": "echo alpha"},
            "server_beta": {"command": "echo beta"},
        }
    }

    async def mock_start(name: str, srv_config: dict[str, Any]) -> None:
        await asyncio.sleep(0.05)
        manager._set_server_state(name, status="ready", impact="ready")

    with patch("runtimes.extensions.mcp.client.storage.get_mcp_config", return_value=mock_config), \
         patch.object(manager, "_start_server", side_effect=mock_start):
        t0 = time.perf_counter()
        await manager.initialize()
        elapsed = time.perf_counter() - t0

    assert elapsed < 0.09, f"MCPManager.initialize took {elapsed:.3f}s, expected < 0.09s"
    assert manager._server_state.get("server_alpha", {}).get("status") == "ready"
    assert manager._server_state.get("server_beta", {}).get("status") == "ready"


# ==============================================================================
# 2. Engineering Context Pack Concurrent Gathering Test
# ==============================================================================
def test_context_pack_concurrent_probe_gathering(tmp_path: Any) -> None:
    from runtimes.engineering.service import EngineeringLaneService

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "package.json").write_text('{"name": "test-pkg"}', encoding="utf-8")

    service = EngineeringLaneService()

    def slow_rules(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        time.sleep(0.04)
        return {"rules": "active"}

    def slow_workflows(*_args: Any, **_kwargs: Any) -> list[str]:
        time.sleep(0.04)
        return ["wf1.md"]

    with patch("runtimes.engineering.service.workspace_resolution_service.resolve_workspace_descriptor",
               return_value={"workspaceRoot": str(workspace), "workspaceId": "ws-test"}), \
         patch.object(service, "_repo_brief", return_value={"name": "test"}), \
         patch.object(service, "_git_summary", return_value={"clean": True}), \
         patch.object(service, "_manifest_summary", return_value={"manifests": []}), \
         patch.object(service, "_critical_file_candidates", return_value=[]), \
         patch.object(service, "_evidence_graph_digest", return_value={"nodes": []}), \
         patch.object(service, "_coding_execution_contract_preview", return_value={}), \
         patch.object(service, "_workspace_rules_digest", side_effect=slow_rules), \
         patch.object(service, "_ranked_workflow_paths", side_effect=slow_workflows):
        t0 = time.perf_counter()
        pack = service.build_context_pack(
            user_query="test query",
            workspace_path=str(workspace),
        )
        elapsed = time.perf_counter() - t0

    assert elapsed < 0.08, f"build_context_pack took {elapsed:.3f}s, expected < 0.08s"
    assert "contextPack" in pack
    inner = pack["contextPack"]
    assert inner.get("workspaceRulesDigest") == {"rules": "active"}
    assert inner.get("workflowRankedPaths") == ["wf1.md"]


# ==============================================================================
# 3. Memory Store Unified Recall Concurrent Search Test
# ==============================================================================
def test_memory_store_concurrent_vector_and_fts_recall() -> None:
    from core.memory_store import MemoryStore

    store = MemoryStore()
    mock_vector_store = MagicMock()

    def slow_vector(*_args: Any, **_kwargs: Any) -> list[dict[str, Any]]:
        time.sleep(0.04)
        return [{"id": "mem_vec_1", "text": "vector result 1", "relevance_score": 0.9, "score_source": "vector"}]

    def slow_fts(*_args: Any, **_kwargs: Any) -> list[dict[str, Any]]:
        time.sleep(0.04)
        return [{"id": "mem_fts_1", "fact": "fts result 1"}]

    mock_vector_store.similarity_search_with_rerank.side_effect = slow_vector

    with patch("core.runtime.startup_profile.optional_capability_enabled", return_value=True), \
         patch("core.vector_store.get_vector_store", return_value=mock_vector_store), \
         patch("core.knowledge_db.knowledge_db.fts_search", side_effect=slow_fts), \
         patch.object(store, "_is_injectable_knowledge", return_value=True):
        t0 = time.perf_counter()
        results = store._execute_unified_recall(
            query="test recall query",
            scope="global",
            limit=5,
        )
        elapsed = time.perf_counter() - t0

    # If serial: 0.04 + 0.04 = 0.08s. Concurrently in ThreadPoolExecutor: ~0.045s.
    assert elapsed < 0.075, f"_execute_unified_recall took {elapsed:.3f}s, expected < 0.075s"
    items = results.get("items") or []
    result_ids = {r["id"] for r in items}
    assert "mem_vec_1" in result_ids
    assert "mem_fts_1" in result_ids


# ==============================================================================
# 4. Supervisor Context Concurrent Probes Test
# ==============================================================================
def test_supervisor_context_concurrent_probes() -> None:
    from graph.supervisor_context import build_supervisor_system_content

    class _StubMemoryRuntime:
        def build_session_context(self, **_kwargs: Any) -> str:
            time.sleep(0.04)
            return "memory content"

    def slow_alerts(*_args: Any, **_kwargs: Any) -> str:
        time.sleep(0.04)
        return "[HOST ALERT] High IO"

    def slow_load(*_args: Any, **_kwargs: Any) -> str:
        time.sleep(0.04)
        return "[HOST LOAD] Normal"

    with patch("graph.supervisor_context.render_host_alerts_line", side_effect=slow_alerts), \
         patch("graph.supervisor_context.render_host_load_line", side_effect=slow_load), \
         patch("graph.supervisor_context._build_artifact_awareness_context", return_value=("", [])), \
         patch("graph.supervisor_context._render_engineering_context", return_value=("", [])), \
         patch("graph.supervisor_context._build_workspace_rules_context", return_value=("", [])), \
         patch("graph.supervisor_context.build_engineering_kernel_context", return_value=("", [])), \
         patch("graph.supervisor_context.capability_registry.build_supervisor_summary", return_value=""):
        t0 = time.perf_counter()
        result = build_supervisor_system_content(
            state={},
            config=SimpleNamespace(system_prompt="Base prompt."),
            user_query="Check system status",
            current_scope="global",
            scope_chain=["global"],
            session_id="sess_perf_test",
            messages=[],
            loaded_agents=[],
            supervisor_tools=[],
            memory_runtime=_StubMemoryRuntime(),
        )
        elapsed = time.perf_counter() - t0

    # If serial: 0.04 * 3 = 0.12s + base overhead (~0.25s). Concurrent 6-thread pool takes ~0.10s-0.16s.
    assert elapsed < 0.20, f"build_supervisor_system_content took {elapsed:.3f}s, expected < 0.20s"
    assert isinstance(result, dict)


# ==============================================================================
# 5. Delegation External Worker Parallel Dispatch Test
# ==============================================================================
def test_delegation_external_worker_parallel_dispatch() -> None:
    from concurrent.futures import ThreadPoolExecutor

    def mock_dispatch(
        *,
        index: int,
        task_brief: dict[str, Any],
        **_kwargs: Any,
    ) -> tuple[int, dict[str, Any], dict[str, Any] | None]:
        time.sleep(0.05)
        item = {
            "delegationId": f"ext-{index}",
            "lane": "external_worker",
            "branchIndex": index,
            "status": "succeeded",
            "taskBriefId": task_brief.get("taskBriefId"),
        }
        return index, item, task_brief

    tasks = [
        {"taskBriefId": "task-0", "goal": "lint run"},
        {"taskBriefId": "task-1", "goal": "type check"},
    ]

    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=len(tasks)) as executor:
        futures = [
            executor.submit(
                mock_dispatch,
                index=i,
                task_brief=t,
            )
            for i, t in enumerate(tasks)
        ]
        results = [f.result() for f in futures]
    elapsed = time.perf_counter() - t0

    # Serial: 0.05 * 2 = 0.10s. Concurrent: ~0.05s.
    assert elapsed < 0.085, f"Parallel dispatch took {elapsed:.3f}s, expected < 0.085s"
    assert len(results) == 2
    assert results[0][0] == 0
    assert results[1][0] == 1
    assert results[0][1]["taskBriefId"] == "task-0"
    assert results[1][1]["taskBriefId"] == "task-1"


# ==============================================================================
# 6. Research Broker Micro-Batch Concurrency and Early Stop Test
# ==============================================================================
def test_research_micro_batch_concurrency_and_early_stop() -> None:
    from core.tools.research_broker import (
        _ResearchReadAttemptLedger,
        _run_search_shard,
    )

    call_count = 0
    read_urls: list[str] = []

    def mock_source_router_search(**_kwargs: Any) -> str:
        return json.dumps({
            "ok": True,
            "provider": "mock",
            "results": [
                {"url": "https://example.com/page1", "title": "Page 1", "snippet": "Snippet 1"},
                {"url": "https://example.com/page2", "title": "Page 2", "snippet": "Snippet 2"},
                {"url": "https://example.com/page3", "title": "Page 3", "snippet": "Snippet 3"},
                {"url": "https://example.com/page4", "title": "Page 4", "snippet": "Snippet 4"},
            ],
        })

    def mock_source_router_read(**kwargs: Any) -> str:
        nonlocal call_count
        call_count += 1
        url = kwargs.get("url", "")
        read_urls.append(url)
        time.sleep(0.04)
        return json.dumps({
            "ok": True,
            "status": 200,
            "text": "Detailed factual evidence content for query",
            "finalUrl": url,
        })

    shard = {
        "facet": "overview",
        "query": "python async concurrency",
        "searchEngine": "searxng",
    }
    ledger = _ResearchReadAttemptLedger()

    with patch("core.tools.research_broker._source_router_search", side_effect=mock_source_router_search), \
         patch("core.tools.research_broker._source_router_read", side_effect=mock_source_router_read), \
         patch("core.tools.research_broker._source_matches_intent", return_value=True):
        t0 = time.perf_counter()
        res = _run_search_shard(
            shard=shard,
            allowed_domains=[],
            blocked_domains=[],
            source_policy="balanced",
            max_rounds=1,
            use_agent_browser_profile=False,
            tool_call_id="call-mock",
            read_attempt_ledger=ledger,
        )
        elapsed = time.perf_counter() - t0

    # 1. Micro-batch concurrency: Batch of 2 URLs running concurrently in ~0.045s (serial would be 0.08s)
    assert elapsed < 0.075, f"_run_search_shard took {elapsed:.3f}s, expected < 0.075s"

    # 2. Early-stop verification: Since the first batch of 2 satisfied target=2, page3 and page4 must NOT be read!
    assert call_count == 2, f"Expected exactly 2 reads due to early stop, got {call_count}"
    assert "https://example.com/page1" in read_urls
    assert "https://example.com/page2" in read_urls
    assert "https://example.com/page3" not in read_urls
    assert "https://example.com/page4" not in read_urls

    # 3. Fetched results integrity
    fetched = res.get("fetchedTopSources") or []
    assert len(fetched) == 2
    assert all(item.get("ok") is True for item in fetched)
