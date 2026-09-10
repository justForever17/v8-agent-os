from __future__ import annotations

from types import SimpleNamespace

import pytest

from runtimes.computer_use.drivers.windows_uia import WindowsUIADriver
from runtimes.computer_use.runtime import ComputerUseRuntime


def _window(handle, title="Owned Window", cls="OwnedClass", process="owned.exe", visible=True):
    return {"handle": handle, "title": title, "className": cls, "processName": process,
            "processId": handle + 100, "isVisible": visible}


@pytest.fixture
def discovered(monkeypatch):
    driver = WindowsUIADriver()
    snapshot = [_window(42), _window(43, title="Different Window"),
                _window(44, process="other.exe"), _window(45, cls="OtherClass"),
                _window(46, title=""), _window(47, title="Desktop", cls="Progman", process="explorer.exe")]
    reads = []

    def read(backend):
        reads.append(backend)
        return list(snapshot)

    monkeypatch.setattr(driver, "_safe_backend_windows", read)
    monkeypatch.setattr(driver, "_window_dict", lambda data: dict(data))
    return driver, snapshot, reads


def test_batch_retains_each_query_filter_score_order_and_limit(discovered):
    driver, snapshot, reads = discovered
    queries = [
        {"title_filters": ["Owned Window"], "class_names": ["OwnedClass"], "process_names": ["owned.exe"]},
        {"class_names": ["OwnedClass"], "process_names": ["owned.exe"]},
        {"process_names": ["owned.exe"], "limit": 2},
        {"class_names": ["OwnedClass"]},
        {"process_ids": [142]},
        {"process_names": ["missing.exe"]},
    ]
    expected = [driver.list_windows(**query) for query in queries]
    assert [[item["handle"] for item in group] for group in expected] == [[42], [42, 43], [42, 43], [42, 43, 44], [42], []]
    reads.clear()
    result = driver.list_windows_batch(queries)
    assert result == expected
    assert reads == ["uia"]
    assert all("matchScore" not in item for item in snapshot)


def test_runtime_alternative_queries_use_one_snapshot_and_refresh_next_call(discovered):
    driver, snapshot, reads = discovered
    runtime = object.__new__(ComputerUseRuntime)
    runtime.driver = driver
    arguments = dict(expected_titles=["Owned Window"], expected_classes=["OwnedClass"],
                     expected_process_names=["owned.exe"], limit=2)
    result = runtime._collect_window_candidates(**arguments)
    assert [item["handle"] for item in result] == [42, 43]
    # The old runtime enumerated the OS once per title/class/process alternative.
    assert reads == ["uia"]
    snapshot[:] = [_window(99, process="replacement.exe")]
    result = runtime._collect_window_candidates(**arguments)
    assert [item["handle"] for item in result] == [99]
    assert result[0]["processName"] == "replacement.exe"
    assert reads == ["uia", "uia"]
    # Discovery is only a candidate union. Binding/action validation must still
    # reject a mismatched process; never rewrite its identity to fit the query.


def test_posix_queries_and_failed_discovery_keep_existing_evidence_behavior():
    runtime = object.__new__(ComputerUseRuntime)
    queries = []

    def discover(**query):
        queries.append(query)
        if "title_filters" in query:
            raise RuntimeError("one discovery unavailable")
        return [_window(42)]

    runtime.driver = SimpleNamespace(list_windows=discover)
    args = dict(expected_titles=["Owned Window"], expected_classes=["OwnedClass"], expected_process_names=["owned.exe"])
    assert [item["handle"] for item in runtime._collect_window_candidates(**args)] == [42]
    assert len(queries) == 4

    def unavailable(_queries):
        raise RuntimeError("desktop unavailable")

    runtime.driver = SimpleNamespace(list_windows_batch=unavailable,
        list_windows=lambda **_: pytest.fail("Do not repeat the same failed OS scan"))
    assert runtime._collect_window_candidates(**args) == []
    assert runtime._collect_window_candidates(**args, extra_windows=[_window(88)]) == [_window(88)]
