"""Run focused behavioral oracles against isolated in-memory old mechanisms.

Source files are never changed. The subprocess installs a pytest-only monkeypatch
after isolated test state is configured by conftest.py. A killed mutant must make
its normal (unmodified) acceptance test fail.
"""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


class Mutant:
    def pytest_collection_modifyitems(self, session, config, items):
        if os.environ.get("V8_CONVERSATION_TEST_MUTANT") == "checkpoint":
            from agents.runners.supervisor_runner import SupervisorAgentRunner
            def old_thread(self, session_id):
                return {"configurable": {"thread_id": session_id, "context_epoch": 0}, "recursion_limit": 100}
            SupervisorAgentRunner.build_graph_config = old_thread
        elif os.environ.get("V8_CONVERSATION_TEST_MUTANT") == "snapshot":
            import inspect
            import textwrap
            import erc.snapshot_service as module
            source = textwrap.dedent(inspect.getsource(module.SnapshotService.ensure_chat_projection_row))
            source = source.replace('(snapshot_row.get("snapshot") or {}).get("transcriptRevision") != transcript_revision',
                                    'int((snapshot_row.get("snapshot") or {}).get("canonicalVersion") or 0) < latest_canonical_version')
            namespace = {}
            exec(compile(source, "<isolated-old-max-version-mutant>", "exec"), module.__dict__, namespace)
            module.SnapshotService.ensure_chat_projection_row = namespace["ensure_chat_projection_row"]


def main() -> int:
    engine = Path(__file__).resolve().parents[2]
    cases = {
        "checkpoint": "tests/chat_runtime/test_conversation_recovery.py::test_rotated_sqlite_checkpoint_and_outbound_capture_exclude_old_context",
        "snapshot": "tests/chat_runtime/test_chat_canonical_transcript_contract.py::ChatCanonicalTranscriptContractTests::test_snapshot_refresh_uses_transcript_revision_without_new_runtime_seq_or_max_version",
    }
    if len(sys.argv) > 1 and sys.argv[1] in cases:
        import pytest
        return pytest.main([cases[sys.argv[1]], "-q", "--disable-warnings"], plugins=[Mutant()])
    outcomes = []
    for mutation in cases:
        result = subprocess.run([sys.executable, str(Path(__file__).resolve()), mutation], cwd=engine,
                                env={**os.environ, "V8_CONVERSATION_TEST_MUTANT": mutation}, text=True, capture_output=True)
        killed = result.returncode == 1 and "1 failed" in result.stdout and "AssertionError" in result.stdout
        print(f"{mutation}: {'killed' if killed else 'NOT KILLED'}")
        if not killed:
            print(result.stdout[-3000:])
            print(result.stderr[-1000:])
        outcomes.append(killed)
    return 0 if all(outcomes) else 1


if __name__ == "__main__":
    raise SystemExit(main())
