import json

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from core.native_file_progress import NativeFileProgress


def write(progress, call_id, path, version, *, owner="worker", ok=True, expected=""):
    call = {"id": call_id, "name": "write_native_file", "args": {"path": path, "expected_version": expected}}
    metadata = {"v8_owner_agent_id": owner}
    progress.observe(AIMessage(content="", tool_calls=[call], additional_kwargs=metadata), [call])
    result = ToolMessage(content=json.dumps({"ok": ok, "contentVersion": version}),
                         name="write_native_file", tool_call_id=call_id, additional_kwargs=metadata)
    progress.observe(result, [])
    return result


def epoch(progress, tool, path):
    return progress.read_epoch({"name": tool, "args": {"path": path}})


@pytest.mark.parametrize("workspace,write_path,target", [
    ("E:/workspace", "src/./app.txt", "e:\\workspace\\src\\APP.txt"),
    ("/workspace", "src/other/../app.txt", "/workspace/src/app.txt"),
    ("", "src/./app.txt", "src/app.txt"),
])
def test_relative_and_absolute_paths_share_progress_without_filesystem_resolution(workspace, write_path, target):
    progress = NativeFileProgress(agent_id="worker", workspace_path=workspace)
    write(progress, "w1", write_path, "sha256:" + "a" * 64)
    assert epoch(progress, "read_native_file", target) == 1


def test_directory_grep_progress_excludes_prefix_siblings_and_posix_case_changes():
    progress = NativeFileProgress(agent_id="worker", workspace_path="/workspace")
    write(progress, "w1", "src-other/a.py", "sha256:" + "a" * 64)
    assert epoch(progress, "grep_search", "src") == 0
    write(progress, "w2", "src/a.py", "sha256:" + "b" * 64)
    assert epoch(progress, "grep_search", "src") == 2
    assert epoch(progress, "grep_search", ".") == 2
    assert epoch(progress, "read_native_file", "src/b.py") == 0
    assert epoch(progress, "read_native_file", "src/A.py") == 0
    assert epoch(progress, "grep_search", "") == 0


def test_failed_noop_foreign_and_replayed_receipts_cannot_manufacture_progress():
    progress = NativeFileProgress(agent_id="worker")
    version_a, version_b = "sha256:" + "a" * 64, "sha256:" + "b" * 64
    old = write(progress, "w1", "app.txt", version_a)
    latest = write(progress, "w2", "app.txt", version_b)
    baseline = epoch(progress, "read_native_file", "app.txt")
    write(progress, "failed", "app.txt", version_a, ok=False)
    write(progress, "noop", "app.txt", version_b)
    write(progress, "foreign", "app.txt", version_a, owner="another-worker")
    for result in (old, latest, old):
        progress.observe(result.model_copy(update={"id": "replayed-" + result.tool_call_id}), [])
    assert epoch(progress, "read_native_file", "app.txt") == baseline
    # A genuinely new successful write may restore older content.
    write(progress, "restore", "app.txt", version_a)
    assert epoch(progress, "read_native_file", "app.txt") > baseline


def test_first_write_matching_expected_version_is_a_noop():
    progress = NativeFileProgress(agent_id="worker")
    version = "sha256:" + "a" * 64
    write(progress, "noop", "app.txt", version, expected=version)
    assert epoch(progress, "read_native_file", "app.txt") == 0


def test_foreign_result_cannot_complete_an_owned_call_or_poison_its_real_receipt():
    progress = NativeFileProgress(agent_id="worker")
    call = {"id": "write", "name": "write_native_file", "args": {"path": "app.txt"}}
    progress.observe(AIMessage(content="", tool_calls=[call]), [call])
    foreign = ToolMessage(content=json.dumps({"ok": True, "contentVersion": "sha256:" + "a" * 64}),
                          name="write_native_file", tool_call_id="write", additional_kwargs={"v8_owner_agent_id": "other"})
    progress.observe(foreign, [])
    assert epoch(progress, "read_native_file", "app.txt") == 0
    progress.observe(foreign.model_copy(update={"additional_kwargs": {"v8_owner_agent_id": "worker"}}), [])
    assert epoch(progress, "read_native_file", "app.txt") == 1
