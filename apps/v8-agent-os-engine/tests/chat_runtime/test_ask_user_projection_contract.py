import unittest

from erc.ask_user_tool_result import resolve_ask_user_tool_result_interaction
from core.runtime_projection import project_ask_user_interactions, project_pending_approvals


class AskUserProjectionContractTest(unittest.TestCase):
    def test_ask_user_rows_are_not_projected_as_governance_approvals(self):
        rows = [
            {
                "id": "approval_safe",
                "session_id": "session_1",
                "run_id": "run_1",
                "approval_kind": "safety_review",
                "status": "pending",
                "request": {"question": "Allow this command?"},
            },
            {
                "id": "approval_legacy_ask",
                "session_id": "session_1",
                "run_id": "run_1",
                "approval_kind": "ask_user",
                "status": "pending",
                "request": {"interactionKind": "ask_user", "question": "What should I do next?"},
            },
        ]

        projected = project_pending_approvals(rows)

        self.assertEqual([item["id"] for item in projected], ["approval_safe"])
        self.assertEqual(projected[0]["approvalKind"], "safety_review")

    def test_ask_user_interaction_projection_uses_interaction_contract(self):
        projected = project_ask_user_interactions(
            [
                {
                    "id": "ask_1",
                    "session_id": "session_1",
                    "run_id": "run_1",
                    "assistant_message_id": "assistant_1",
                    "tool_call_id": "tool_1",
                    "question": "What should I do next?",
                    "prompt": "Need input",
                    "request": {"toolCallId": "tool_1"},
                    "answer_text": None,
                    "status": "pending",
                    "created_at": "2026-04-16T00:00:00Z",
                    "resolved_at": None,
                }
            ]
        )

        self.assertEqual(len(projected), 1)
        self.assertEqual(projected[0]["interactionId"], "ask_1")
        self.assertEqual(projected[0]["interactionKind"], "ask_user")
        self.assertEqual(projected[0]["toolCallId"], "tool_1")
        self.assertNotIn("approvalId", projected[0])

    def test_resolved_ask_user_result_reuses_original_tool_call_id(self):
        interaction = resolve_ask_user_tool_result_interaction(
            [
                {
                    "id": "ask_1",
                    "tool_call_id": "call_original",
                    "answer_text": "不错哦",
                    "status": "resolved",
                }
            ],
            candidate_tool_call_id="019d96be-resume-event-id",
            output_text="不错哦",
        )

        self.assertIsNotNone(interaction)
        self.assertEqual(interaction["tool_call_id"], "call_original")

    def test_pending_ask_user_interaction_wins_over_resume_event_id(self):
        interaction = resolve_ask_user_tool_result_interaction(
            [
                {
                    "id": "ask_old",
                    "tool_call_id": "call_old",
                    "answer_text": "旧回答",
                    "status": "resolved",
                },
                {
                    "id": "ask_current",
                    "tool_call_id": "call_current",
                    "answer_text": "当前回答",
                    "status": "resolved",
                },
            ],
            pending_interaction_id="ask_current",
            candidate_tool_call_id="019d96be-resume-event-id",
            output_text="旧回答",
        )

        self.assertIsNotNone(interaction)
        self.assertEqual(interaction["tool_call_id"], "call_current")


if __name__ == "__main__":
    unittest.main()
