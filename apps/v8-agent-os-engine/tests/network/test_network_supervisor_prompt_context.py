import unittest

from agents.runners.supervisor_runner import SupervisorAgentRunner
from graph.supervisor_context import render_network_supervisor_context


class NetworkSupervisorPromptContextTests(unittest.TestCase):
    def test_context_renders_only_for_openai_compat_transport(self):
        content = render_network_supervisor_context({"transport": "network_supervisor_openai"})

        self.assertIn("[NETWORK SUPERVISOR CONTEXT]", content)
        self.assertIn("OpenAI-compatible API via Admin relay", content)
        self.assertIn("third-party application owns history", content)
        self.assertIn("do not tell the caller to inspect V8 internal panels", content)
        self.assertIn("Only use V8OS support tools", content)
        self.assertIn("Do not use V8OS file writes, shell commands, desktop operation, media generation, Spec mode, or subagent collaboration", content)

    def test_context_renders_for_anthropic_compat_transport(self):
        content = render_network_supervisor_context({"transport": "network_supervisor_anthropic"})

        self.assertIn("Anthropic-compatible API via Admin relay", content)
        self.assertIn("third-party application owns history", content)

    def test_context_renders_v8_main_chain_advanced_mode_when_explicit(self):
        content = render_network_supervisor_context(
            {
                "transport": "network_supervisor_openai",
                "current_route_context": {"compatIngressDiagnostics": {"compatContextMode": "v8_main_chain"}},
            }
        )

        self.assertIn("V8OS main-chain enhanced mode", content)
        self.assertIn("You may use broader V8OS context, route suggestions, and governed tools", content)
        self.assertIn("Do not expose internal tool names", content)

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
