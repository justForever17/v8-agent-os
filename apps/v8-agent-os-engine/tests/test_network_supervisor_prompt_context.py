import unittest

from agents.runners.supervisor_runner import SupervisorAgentRunner
from graph.supervisor_context import render_network_supervisor_context


class NetworkSupervisorPromptContextTests(unittest.TestCase):
    def test_context_renders_only_for_openai_compat_transport(self):
        content = render_network_supervisor_context({"transport": "network_supervisor_openai"})

        self.assertIn("[NETWORK SUPERVISOR CONTEXT]", content)
        self.assertIn("OpenAI-compatible API via Admin relay", content)
        self.assertIn("ask_user interaction cards", content)
        self.assertIn("artifact cards", content)
        self.assertIn("Prefer network_* tools first", content)
        self.assertIn("fall back to V8OS native tools", content)

    def test_context_omitted_for_regular_chat_transport(self):
        self.assertEqual(render_network_supervisor_context({"transport": "websocket"}), "")
        self.assertEqual(render_network_supervisor_context({"transport": "http"}), "")
        self.assertEqual(render_network_supervisor_context({}), "")

    def test_supervisor_runner_carries_transport_into_initial_state(self):
        runner = SupervisorAgentRunner()

        state = runner.create_state([], transport="network_supervisor_openai")

        self.assertEqual(state.get("transport"), "network_supervisor_openai")


if __name__ == "__main__":
    unittest.main()
