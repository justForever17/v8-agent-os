from core.context_policy import normalize_context_policy
from core.storage import storage


def test_context_config_read_normalizes_without_rewriting_historical_fields(monkeypatch):
    raw = {
        "schema_version": 3,
        "recursion_limit": 100,
        "maxGraphContinuations": 5,
        "compression": {
            "enabled": True,
            "mode": "persistent_baseline",
            "default_context_window_tokens": 32000,
            "trigger_ratio": 0.94,
            "keep_recent_turns": 4,
            "keep_recent_messages": 8,
            "soft_trigger_ratio": 0.90,
            "hard_trigger_ratio": 0.94,
            "use_llm_summary": True,
            "max_summary_input_tokens": 5000,
            "max_summary_input_messages": 60,
            "max_summary_output_tokens": 800,
            "compression_model_safety_ratio": 0.90,
            "noticeable_latency_ms": 800,
        },
        "runtime_adapters": {
            "automation": {"recent_run_limit": 3, "job_memory_limit": 6},
            "plugin_host": {"window_size": 15, "max_summary_items": 8},
        },
    }
    writes = []
    monkeypatch.setattr(storage, "_ensure_legacy_model_bindings_migrated", lambda: None)
    monkeypatch.setattr(storage, "read_json", lambda _filename: raw)
    monkeypatch.setattr(storage, "write_json", lambda filename, payload: writes.append((filename, payload)))

    resolved = storage.get_context_config()

    assert resolved == normalize_context_policy(raw)
    assert "plugin_host" not in resolved["runtime_adapters"]
    assert writes == []


def test_explicit_context_config_save_still_persists_normalized_policy(monkeypatch):
    writes = []
    monkeypatch.setattr(storage, "write_json", lambda filename, payload: writes.append((filename, payload)))

    storage.save_context_config({"recursion_limit": 1})

    assert writes == [("context_config.json", normalize_context_policy({"recursion_limit": 1}))]
