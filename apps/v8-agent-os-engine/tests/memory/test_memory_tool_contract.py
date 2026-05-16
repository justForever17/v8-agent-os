from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock


import core.native_tools as native_tools


class _FakeMemoryRuntime:
    def __init__(self) -> None:
        self.updated: list[tuple[str, str]] = []
        self.deleted: list[str] = []

    def update_knowledge(self, *, fact_id: str, new_fact: str) -> bool:
        self.updated.append((fact_id, new_fact))
        return True

    def delete_knowledge(self, *, fact_id: str) -> bool:
        self.deleted.append(fact_id)
        return True


class MemoryToolContractTests(unittest.TestCase):
    def test_mem_update_supports_update_and_delete_modes(self):
        runtime = _FakeMemoryRuntime()
        with mock.patch.object(native_tools, "_get_memory_runtime", return_value=runtime):
            update_result = native_tools.mem_update.invoke(
                {"fact_id": "fact-1", "mode": "update", "new_content": "corrected content"}
            )
            delete_result = native_tools.mem_update.invoke({"fact_id": "fact-2", "mode": "delete"})

        self.assertIn("Updated 'fact-1'", update_result)
        self.assertIn("Deleted 'fact-2'", delete_result)
        self.assertEqual(runtime.updated, [("fact-1", "corrected content")])
        self.assertEqual(runtime.deleted, ["fact-2"])

    def test_mem_update_requires_content_for_update_mode(self):
        runtime = _FakeMemoryRuntime()
        with mock.patch.object(native_tools, "_get_memory_runtime", return_value=runtime):
            result = native_tools.mem_update.invoke({"fact_id": "fact-1", "mode": "update"})

        self.assertEqual(result, "Error: new_content is required when mode='update'.")
        self.assertEqual(runtime.updated, [])
        self.assertEqual(runtime.deleted, [])


if __name__ == "__main__":
    unittest.main()

