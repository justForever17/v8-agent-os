from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from langchain_core.messages import AIMessage

import core.tools.research_broker as research_module


_AS_OF = "2026-07-29T03:00:00Z"


class _Candidate:
    def __init__(
        self,
        *,
        max_tokens: int,
        model_ref: str,
        supports_no_think: bool = False,
    ) -> None:
        self._meta = {
            "model_ref": model_ref,
            "global_max_tokens": max_tokens,
            "global_context_window": 1_000_000,
            "thinking_control": {"supportsNoThink": supports_no_think},
        }


class _DualInvocationCandidate(_Candidate):
    def __init__(self) -> None:
        super().__init__(max_tokens=4096, model_ref="fixture::dual")
        self.lock = threading.Lock()
        self.sync_calls = 0
        self.async_calls = 0

    def invoke(self, _messages, **_kwargs):  # noqa: ANN001
        with self.lock:
            self.sync_calls += 1
        return AIMessage(content="sync")

    async def ainvoke(self, _messages, **_kwargs):  # noqa: ANN001
        with self.lock:
            self.async_calls += 1
        return AIMessage(content="async")


def test_architect_parallel_workers_use_sync_client_instead_of_cross_loop_async_client() -> None:
    llm = _DualInvocationCandidate()
    candidate = (llm, "fixture::dual", "summary")

    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(
            executor.map(
                lambda _index: research_module._invoke_architect_candidate_with_deadline(
                    candidate,
                    [],
                    seconds=2,
                    max_tokens=128,
                ),
                range(2),
            )
        )

    assert [response.content for response in responses] == ["sync", "sync"]
    assert llm.sync_calls == 2
    assert llm.async_calls == 0


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("configured,requested,expected", [(4096, 12000, 4096), (4096, 1800, 1800), (None, 1800, 1800)])
def test_research_stage_budget_respects_real_model_configuration(asynchronous, configured, requested, expected):
    seen = []
    class Model:
        _meta = {"global_max_tokens": configured}
        def invoke(self, _messages, **kwargs):
            seen.append(kwargs)
            return "ok"
    llm = Model()
    if asynchronous:
        async def ainvoke(messages, **kwargs):
            return llm.invoke(messages, **kwargs)
        llm.ainvoke = ainvoke
    assert research_module._invoke_architect_candidate_with_deadline(
        (llm, "configured::model", "research"), [], seconds=2, max_tokens=requested,
    ) == "ok"
    assert seen[0]["max_tokens"] == expected
    assert llm._meta == {"global_max_tokens": configured}
