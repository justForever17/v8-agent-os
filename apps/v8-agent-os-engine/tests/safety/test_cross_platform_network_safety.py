from __future__ import annotations

import unittest

from erc.safety_guardian import safety_guardian


RUNTIME_CONTEXT = {"workspace_path": r"E:\Projects\v8chat\v8-agent-os", "runtime_kind": "chat"}


class CrossPlatformAndNetworkSafetyTests(unittest.TestCase):
    def setUp(self):
        safety_guardian._recent_downloads = []

    def test_linux_auth_store_mutation_is_blocked(self):
        decision = safety_guardian.assess_system_command(
            "sudo rm -rf /etc/shadow",
            runtime_context=RUNTIME_CONTEXT,
        )
        self.assertEqual(decision.verdict, "block")
        self.assertEqual(decision.risk_code, "linux_auth_store_mutation")

    def test_sudo_non_core_command_requires_review(self):
        decision = safety_guardian.assess_system_command(
            "sudo systemctl restart nginx",
            runtime_context=RUNTIME_CONTEXT,
        )
        self.assertEqual(decision.verdict, "review")
        self.assertIn(decision.risk_code, {"cross_platform_persistence_mutation", "privilege_elevation_review"})
        self.assertTrue(decision.details["eventSummary"]["riskCode"])

    def test_firewall_mutation_requires_review(self):
        decision = safety_guardian.assess_system_command(
            "ufw allow 443/tcp",
            runtime_context=RUNTIME_CONTEXT,
        )
        self.assertEqual(decision.verdict, "review")
        self.assertEqual(decision.risk_code, "cross_platform_firewall_mutation")

    def test_sensitive_read_requires_review(self):
        decision = safety_guardian.assess_system_command(
            "cat /etc/shadow",
            runtime_context=RUNTIME_CONTEXT,
        )
        self.assertEqual(decision.verdict, "review")
        self.assertEqual(decision.risk_code, "linux_sensitive_read")

    def test_workspace_chmod_is_allowed(self):
        decision = safety_guardian.assess_system_command(
            r'chmod 644 "E:\Projects\v8chat\v8-agent-os\README.md"',
            runtime_context=RUNTIME_CONTEXT,
        )
        self.assertEqual(decision.verdict, "allow")

    def test_trusted_ai_api_with_bearer_token_is_not_exfiltration(self):
        decision = safety_guardian.assess_http_request(
            "POST",
            "https://api.openai.com/v1/responses",
            headers={"Authorization": "Bearer sk-test1234567890"},
            body='{"model":"gpt-test","input":"hello"}',
            runtime_context=RUNTIME_CONTEXT,
        )
        self.assertIn(decision.verdict, {"allow", "audit"})
        self.assertEqual(decision.risk_code, "trusted_provider_api_http")
        self.assertEqual(decision.details["trustedNetwork"]["providerId"], "openai")

    def test_trusted_video_api_with_bearer_token_is_not_exfiltration(self):
        decision = safety_guardian.assess_http_request(
            "POST",
            "https://api.vidu.com/ent/v2/start-end2video",
            headers={"Authorization": "Bearer sk-test1234567890"},
            body='{"model":"viduq3-pro"}',
            runtime_context=RUNTIME_CONTEXT,
        )
        self.assertIn(decision.verdict, {"allow", "audit"})
        self.assertEqual(decision.risk_code, "trusted_provider_api_http")
        self.assertEqual(decision.details["trustedNetwork"]["providerId"], "vidu")

    def test_trusted_china_collaboration_api_with_token_is_not_exfiltration(self):
        decision = safety_guardian.assess_http_request(
            "POST",
            "https://open.feishu.cn/open-apis/im/v1/messages",
            headers={"Authorization": "Bearer t-test1234567890"},
            body='{"receive_id":"ou_xxx"}',
            runtime_context=RUNTIME_CONTEXT,
        )
        self.assertIn(decision.verdict, {"allow", "audit"})
        self.assertEqual(decision.risk_code, "trusted_provider_api_http")
        self.assertEqual(decision.details["trustedNetwork"]["providerId"], "lark")

    def test_unknown_credential_host_requires_review(self):
        decision = safety_guardian.assess_http_request(
            "POST",
            "https://example.com/hook",
            headers={"Authorization": "Bearer sk-test1234567890"},
            body='{"ok":true}',
            runtime_context=RUNTIME_CONTEXT,
        )
        self.assertEqual(decision.verdict, "review")
        self.assertEqual(decision.risk_code, "unknown_credential_host_http")

    def test_normal_web_get_is_allowed(self):
        decision = safety_guardian.assess_http_request(
            "GET",
            "https://example.com/docs",
            runtime_context=RUNTIME_CONTEXT,
        )
        self.assertEqual(decision.verdict, "allow")
        self.assertEqual(decision.risk_code, "http_allowed")


if __name__ == "__main__":
    unittest.main()
