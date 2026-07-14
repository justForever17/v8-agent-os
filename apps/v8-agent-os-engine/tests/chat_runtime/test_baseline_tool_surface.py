from __future__ import annotations

import sys
import unittest
from pathlib import Path



from core.system_tools.baseline import (  # noqa: E402
    BASELINE_SYSTEM_TOOL_NAME_ORDER,
    build_baseline_system_tool_descriptors,
)


class BaselineToolSurfaceTests(unittest.TestCase):
    def test_baseline_tool_descriptors_follow_canonical_order_and_hide_legacy_names(self):
        descriptors = build_baseline_system_tool_descriptors()
        names = [item["name"] for item in descriptors]

        self.assertEqual(names, list(BASELINE_SYSTEM_TOOL_NAME_ORDER))
        self.assertIn("delegation_broker", names)
        self.assertIn("command_session_broker", names)
        self.assertIn("web_broker", names)
        self.assertIn("s3_broker", names)
        self.assertNotIn("share_workspace_file", names)
        self.assertNotIn("read_background_output", names)
        self.assertNotIn("web_fetch", names)
        self.assertNotIn("s3_upload_file", names)


if __name__ == "__main__":
    unittest.main()

