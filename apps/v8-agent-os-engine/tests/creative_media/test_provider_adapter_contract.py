from __future__ import annotations

from runtimes.creative_media.provider_adapter import (
    ProviderHttpError,
    normalize_async_status,
    normalize_remote_status,
)


def test_cancelled_state_is_not_collapsed_into_failed() -> None:
    assert normalize_remote_status("canceled") == "cancelled"
    assert normalize_remote_status("terminated") == "cancelled"
    assert normalize_async_status("cancelled") == "cancelled"


def test_business_or_http_codes_are_not_global_task_states() -> None:
    assert normalize_remote_status(200) == "unknown"
    assert normalize_remote_status("2") == "unknown"
    assert normalize_remote_status("-1") == "unknown"


def test_provider_http_error_is_structured_and_redacted() -> None:
    error = ProviderHttpError(
        method="post",
        url="https://user:secret@provider.example.test/tasks?token=secret",
        status_code=503,
        response_excerpt=(
            '{"api_key":"supersecret","authorization":"Bearer xyz","message":"busy"}'
        ),
    )

    assert error.status_code == 503
    assert error.method == "POST"
    projection = str(error)
    assert error.url == "https://provider.example.test/tasks"
    assert "user" not in projection
    assert "secret" not in projection
    assert "supersecret" not in projection
    assert "Bearer xyz" not in projection
    assert "token=secret" not in projection
    assert "api_key" in projection
    assert "provider_http_error" in str(error)
