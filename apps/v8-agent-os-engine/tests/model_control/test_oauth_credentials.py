from __future__ import annotations

import base64
import json

from core.oauth.credentials import resolve_oauth_reference


def _jwt(payload: dict) -> str:
    encoded = base64.urlsafe_b64encode(json.dumps(payload).encode("utf-8")).decode("ascii").rstrip("=")
    return f"header.{encoded}.signature"


def test_codex_oauth_rejects_api_key_only_auth_file(tmp_path) -> None:
    auth_path = tmp_path / "auth.json"
    auth_path.write_text(json.dumps({"OPENAI_API_KEY": "not-an-oauth-token"}), encoding="utf-8")

    result = resolve_oauth_reference(f"oauth:{auth_path}", provider_id="codex")

    assert result["credential"] == ""
    assert result["oauthFlavor"] == ""
    assert "OPENAI_API_KEY" in result["error"]


def test_codex_oauth_recovers_account_id_from_access_token_claim(tmp_path) -> None:
    access_token = _jwt(
        {
            "https://api.openai.com/auth": {
                "chatgpt_account_id": "account-from-claim",
            }
        }
    )
    auth_path = tmp_path / "auth.json"
    auth_path.write_text(json.dumps({"tokens": {"access_token": access_token}}), encoding="utf-8")

    result = resolve_oauth_reference(f"oauth:{auth_path}", provider_id="codex")

    assert result["credential"] == access_token
    assert result["accessToken"] == access_token
    assert result["accountId"] == "account-from-claim"
    assert result["oauthFlavor"] == "codex"
    assert result["error"] == ""
