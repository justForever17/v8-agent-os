from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import threading
import time
from types import SimpleNamespace

from api import config_registry_routes
from core import supervisor_tool_policy
from core.runtime import startup_profile
from core.storage import storage
from core.tools import research_ledger
import erc.capability_registry as capability_registry_module
from erc.capability_registry import CapabilityRegistry, capability_registry


def _reset_system_base_environment_state() -> None:
    with config_registry_routes._system_base_environment_lock:
        inflight = list(config_registry_routes._system_base_environment_inflight.values())
        assert all(future.done() for future in inflight)
        config_registry_routes._system_base_environment_cache = None
        config_registry_routes._system_base_environment_failures.clear()
        config_registry_routes._system_base_environment_inflight.clear()
        config_registry_routes._system_base_environment_latest_key = ""


def _environment_snapshot(label: str = "fresh") -> dict:
    return {
        "desktopReadiness": {
            "status": "ready",
            "ocrReady": True,
            "imageLocatorReady": True,
            "pointLocatorReady": True,
            "missingItems": [],
        },
        "detectedDesktopTools": {"tesseractPath": f"{label}-tesseract", "tessdataPrefix": ""},
        "dependencyStatus": [{"id": label, "category": "core"}],
        "remoteLinkMeshStatus": {
            "ok": True,
            "providers": [],
            "peerCandidates": [],
            "policy": {},
        },
        "remoteLinkDiagnostics": {
            "readOnly": True,
            "candidateIps": [],
            "vpn": {},
            "warnings": [],
            "info": [],
        },
    }


def _stub_system_base_domain_dependencies(monkeypatch, system_base: dict) -> None:
    monkeypatch.setattr(config_registry_routes.storage, "get_system_base_config", lambda: deepcopy(system_base))
    monkeypatch.setattr(
        config_registry_routes,
        "detect_desktop_tools_readiness",
        lambda: {
            "status": "ready",
            "ocrReady": True,
            "imageLocatorReady": True,
            "pointLocatorReady": True,
            "missingItems": [],
            "detectedDesktopTools": {"tesseractPath": "configured-tesseract", "tessdataPrefix": ""},
        },
    )
    monkeypatch.setattr(
        config_registry_routes,
        "build_link_manifest",
        lambda **kwargs: {
            "transportKind": "manual_url",
            "diagnostics": deepcopy(kwargs.get("diagnostics") or {}),
        },
    )


def test_research_summary_reads_once_and_projects_only_requested_page(monkeypatch):
    payload = {
        "version": 1,
        "evidenceBundles": [
            {"evidenceBundleId": f"bundle-{index}", "scope": "global", "question": f"Question {index}"}
            for index in range(80)
        ],
        "experiencePacks": [
            {
                "experiencePackId": f"pack-{index}",
                "scope": "global",
                "status": "active",
                "createdFromBundleId": f"bundle-{index}",
            }
            for index in range(80)
        ],
    }
    reads = 0
    projected: list[str] = []

    def read_store():
        nonlocal reads
        reads += 1
        return deepcopy(payload)

    def visible_experience(item, *, now=None):
        projected.append(str(item["experiencePackId"]))
        return dict(item)

    monkeypatch.setattr(research_ledger, "_read_store", read_store)
    monkeypatch.setattr(research_ledger, "_visible_experience", visible_experience)
    monkeypatch.setattr(
        research_ledger,
        "_write_store",
        lambda _payload: (_ for _ in ()).throw(AssertionError("unchanged reads must not rewrite the ledger")),
    )

    result = research_ledger.research_ledger_summary(limit=3)

    assert reads == 1
    assert projected == ["pack-0", "pack-1", "pack-2"]
    assert result["counts"] == {"evidenceBundles": 80, "experiencePacks": 80}
    assert len(result["evidenceBundles"]) == 3
    assert len(result["experiencePacks"]) == 3


def test_research_summary_keeps_read_time_corruption_cleanup(monkeypatch):
    payload = {
        "version": 1,
        "evidenceBundles": [],
        "experiencePacks": [{"experiencePackId": "valid", "scope": "global"}, "invalid"],
    }
    writes = []
    monkeypatch.setattr(research_ledger, "_read_store", lambda: deepcopy(payload))
    monkeypatch.setattr(research_ledger, "_write_store", lambda value: writes.append(deepcopy(value)))

    research_ledger.research_ledger_summary()

    assert [item["experiencePackId"] for item in writes[0]["experiencePacks"]] == ["valid"]


def test_capability_snapshot_reuses_one_installation_projection(monkeypatch):
    registry = CapabilityRegistry()
    registry.register({"kind": "engineering", "displayName": "Engineering"})
    installation_calls = 0
    config_calls = {"network": 0, "engineering": 0}

    def installation_snapshot():
        nonlocal installation_calls
        installation_calls += 1
        return {
            "installProfile": "desktop",
            "installPlatform": "windows",
            "installedRuntimeFamilies": ["engineering"],
            "featurePacks": [],
            "featurePackSummary": {},
            "bootstrapManaged": True,
            "lastUpgradeAt": None,
        }

    def network_config():
        config_calls["network"] += 1
        return {"enabled": False}

    def engineering_config():
        config_calls["engineering"] += 1
        return {"enabled": True}

    monkeypatch.setattr(startup_profile, "build_installation_snapshot", installation_snapshot)
    monkeypatch.setattr(storage, "get_network_supervisor_runtime_config", network_config)
    monkeypatch.setattr(storage, "get_engineering_lane_config", engineering_config)
    monkeypatch.setattr(
        startup_profile,
        "runtime_family_installed",
        lambda _kind: (_ for _ in ()).throw(AssertionError("snapshot must use its bound installation projection")),
    )

    result = registry.snapshot()

    assert installation_calls == 1
    assert config_calls == {"network": 1, "engineering": 1}
    engineering = next(item for item in result["runtimes"] if item["kind"] == "engineering")
    assert engineering["availability"] == "installed"
    assert engineering["availabilityReason"] == "installed"


def test_capability_snapshot_keeps_config_failures_isolated(monkeypatch):
    registry = CapabilityRegistry()
    registry.register({"kind": "engineering", "displayName": "Engineering"})
    monkeypatch.setattr(
        startup_profile,
        "build_installation_snapshot",
        lambda: {"installedRuntimeFamilies": ["engineering"]},
    )
    monkeypatch.setattr(
        storage,
        "get_network_supervisor_runtime_config",
        lambda: (_ for _ in ()).throw(RuntimeError("network config unavailable")),
    )
    monkeypatch.setattr(storage, "get_engineering_lane_config", lambda: {"enabled": False})

    result = registry.snapshot()

    engineering = next(item for item in result["runtimes"] if item["kind"] == "engineering")
    assert engineering["availability"] == "disabled_by_config"


def test_supervisor_tool_policy_binds_one_runtime_installation_snapshot(monkeypatch):
    installation_calls = 0
    observed_availability = None

    def installed_families():
        nonlocal installation_calls
        installation_calls += 1
        return ["computer_use"]

    def filter_direct_tools(_tools, *, runtime_availability=None):
        nonlocal observed_availability
        observed_availability = dict(runtime_availability or {})
        return []

    monkeypatch.setattr(startup_profile, "installed_runtime_families", installed_families)
    monkeypatch.setattr(
        supervisor_tool_policy,
        "_ensure_runtime_managed_descriptors_loaded",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(capability_registry, "list", lambda: [])
    monkeypatch.setattr(capability_registry, "filter_direct_tools", filter_direct_tools)

    result = supervisor_tool_policy.build_supervisor_tool_policy_snapshot(None)

    assert installation_calls == 1
    assert observed_availability == {"computer_use": True, "desktop_live": False, "rpa": False}
    assert result == {"allowedTools": None, "lockedNativeTools": [], "runtimeManagedTools": []}


def test_capability_registry_uses_bound_runtime_availability(monkeypatch):
    registry = CapabilityRegistry()
    tool = SimpleNamespace(name="computer_use_click")
    monkeypatch.setattr(
        capability_registry_module,
        "runtime_tool_available",
        lambda _name: (_ for _ in ()).throw(AssertionError("bound truth must avoid a second registry read")),
    )

    result = registry.filter_direct_tools(
        [tool],
        runtime_availability={"computer_use": False, "desktop_live": False, "rpa": False},
    )

    assert result == []


def test_supervisor_domain_reuses_one_models_config(monkeypatch):
    models_config = {"roles": {"default": "provider::default", "supervisor": "provider::supervisor"}}
    config_reads = 0
    resolved_roles = []
    parameter_reads = []

    monkeypatch.setattr(config_registry_routes.storage, "get_supervisor_config", lambda: {"allowed_tools": None})
    monkeypatch.setattr(
        config_registry_routes.storage,
        "get_supervisor_profile",
        lambda: {"name": "Supervisor", "roleLabel": "Owner", "avatar": "avatar.png"},
    )
    monkeypatch.setattr(config_registry_routes.storage, "get_supervisor_prompt", lambda: "prompt")
    monkeypatch.setattr(config_registry_routes.storage, "get_system_identity", lambda: {"name": "V8"})
    monkeypatch.setattr(
        config_registry_routes,
        "enforce_prompt_budget",
        lambda **_kwargs: SimpleNamespace(diagnostic=lambda: {"estimatedTokens": 1}),
    )
    monkeypatch.setattr(config_registry_routes, "render_system_identity_block", lambda identity: identity["name"])
    monkeypatch.setattr(
        config_registry_routes,
        "build_supervisor_tool_policy_snapshot",
        lambda _allowed: {"allowedTools": None, "lockedNativeTools": [], "runtimeManagedTools": []},
    )

    def get_config():
        nonlocal config_reads
        config_reads += 1
        return models_config

    def resolve_model_for_role(role, config=None):
        resolved_roles.append((role, config))
        return {"resolvedModelRef": config["roles"][role]}

    def get_role_parameters(role, config=None):
        parameter_reads.append((role, config))
        return {"temperature": None}

    monkeypatch.setattr(config_registry_routes.model_control_plane, "get_config", get_config)
    monkeypatch.setattr(config_registry_routes.model_control_plane, "resolve_model_for_role", resolve_model_for_role)
    monkeypatch.setattr(config_registry_routes.model_control_plane, "get_role_parameters", get_role_parameters)
    monkeypatch.setattr(
        config_registry_routes.model_control_plane,
        "get_role_model_id",
        lambda _role: (_ for _ in ()).throw(AssertionError("domain must reuse the bound models config")),
    )

    result = config_registry_routes._build_supervisor_domain()

    assert config_reads == 1
    assert resolved_roles == [("supervisor", models_config), ("default", models_config)]
    assert parameter_reads == [("supervisor", models_config), ("subagent", models_config)]
    assert result["data"]["bindings"] == {
        "supervisorModel": "provider::supervisor",
        "defaultReplyModel": "provider::default",
    }


def test_system_base_cold_read_does_not_wait_for_environment_probe(monkeypatch):
    _reset_system_base_environment_state()
    system_base = {
        "bridge": {"adminBaseUrl": "http://127.0.0.1:9528/api", "engineBaseUrl": "http://127.0.0.1:9530/v1"},
        "desktopTools": {"tesseractPath": "configured-tesseract"},
        "remoteLink": {},
    }
    _stub_system_base_domain_dependencies(monkeypatch, system_base)
    started = threading.Event()
    release = threading.Event()

    def probe(_system_base):
        started.set()
        assert release.wait(2.0)
        return _environment_snapshot()

    monkeypatch.setattr(config_registry_routes, "_probe_system_base_environment", probe)
    started_at = time.perf_counter()
    result = config_registry_routes._build_system_base_domain()
    elapsed = time.perf_counter() - started_at

    assert elapsed < 0.25
    assert started.wait(0.5)
    assert result["data"]["bridge"] == system_base["bridge"]
    assert result["data"]["environmentProbe"]["status"] == "refreshing"
    assert result["data"]["dependencyStatus"] == []

    with config_registry_routes._system_base_environment_lock:
        future = config_registry_routes._system_base_environment_inflight[
            config_registry_routes._system_base_environment_input_key(system_base)
        ]
    release.set()
    assert future.result(timeout=2.0)["dependencyStatus"][0]["id"] == "fresh"

    hot = config_registry_routes._build_system_base_domain()
    assert hot["data"]["environmentProbe"]["status"] == "ready"
    assert hot["data"]["dependencyStatus"][0]["id"] == "fresh"
    _reset_system_base_environment_state()


def test_system_base_explicit_refresh_joins_the_inflight_probe(monkeypatch):
    _reset_system_base_environment_state()
    system_base = {
        "bridge": {"adminBaseUrl": "http://127.0.0.1:9528/api", "engineBaseUrl": "http://127.0.0.1:9530/v1"},
        "desktopTools": {},
        "remoteLink": {},
    }
    _stub_system_base_domain_dependencies(monkeypatch, system_base)
    started = threading.Event()
    release = threading.Event()
    probe_calls = 0

    def probe(_system_base):
        nonlocal probe_calls
        probe_calls += 1
        started.set()
        assert release.wait(2.0)
        return _environment_snapshot("joined")

    monkeypatch.setattr(config_registry_routes, "_probe_system_base_environment", probe)
    cold = config_registry_routes._build_system_base_domain()
    assert cold["data"]["environmentProbe"]["status"] == "refreshing"
    assert started.wait(0.5)

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(config_registry_routes._build_system_base_domain, refresh_environment=True)
        second = executor.submit(config_registry_routes._build_system_base_domain, refresh_environment=True)
        time.sleep(0.05)
        assert probe_calls == 1
        release.set()
        first_result = first.result(timeout=2.0)
        second_result = second.result(timeout=2.0)

    assert first_result["data"]["environmentProbe"]["status"] == "ready"
    assert second_result["data"]["dependencyStatus"][0]["id"] == "joined"
    assert probe_calls == 1
    _reset_system_base_environment_state()


def test_system_base_config_change_never_projects_previous_environment_snapshot(monkeypatch):
    _reset_system_base_environment_state()
    previous = {
        "bridge": {"adminBaseUrl": "http://old:9528/api", "engineBaseUrl": "http://old:9530/v1"},
        "desktopTools": {},
        "remoteLink": {},
    }
    current = {
        "bridge": {"adminBaseUrl": "http://new:9528/api", "engineBaseUrl": "http://new:9530/v1"},
        "desktopTools": {},
        "remoteLink": {},
    }
    _stub_system_base_domain_dependencies(monkeypatch, current)
    with config_registry_routes._system_base_environment_lock:
        config_registry_routes._system_base_environment_cache = {
            "inputKey": config_registry_routes._system_base_environment_input_key(previous),
            "checkedAtMonotonic": time.monotonic(),
            "checkedAt": "2026-08-04T00:00:00+00:00",
            "snapshot": _environment_snapshot("previous"),
        }
    release = threading.Event()
    monkeypatch.setattr(
        config_registry_routes,
        "_probe_system_base_environment",
        lambda _system_base: (release.wait(2.0), _environment_snapshot("current"))[1],
    )

    result = config_registry_routes._build_system_base_domain()

    assert result["data"]["bridge"] == current["bridge"]
    assert result["data"]["environmentProbe"]["status"] == "refreshing"
    assert result["data"]["environmentProbe"]["stale"] is False
    assert result["data"]["dependencyStatus"] == []
    with config_registry_routes._system_base_environment_lock:
        future = config_registry_routes._system_base_environment_inflight[
            config_registry_routes._system_base_environment_input_key(current)
        ]
    release.set()
    future.result(timeout=2.0)
    _reset_system_base_environment_state()


def test_system_base_probe_failure_keeps_config_available(monkeypatch):
    _reset_system_base_environment_state()
    system_base = {
        "bridge": {"adminBaseUrl": "http://127.0.0.1:9528/api", "engineBaseUrl": "http://127.0.0.1:9530/v1"},
        "desktopTools": {},
        "remoteLink": {},
    }
    _stub_system_base_domain_dependencies(monkeypatch, system_base)
    probe_calls = 0

    def fail_probe(_system_base):
        nonlocal probe_calls
        probe_calls += 1
        raise RuntimeError("probe unavailable")

    monkeypatch.setattr(config_registry_routes, "_probe_system_base_environment", fail_probe)

    result = config_registry_routes._build_system_base_domain(refresh_environment=True)
    backoff_result = config_registry_routes._build_system_base_domain()

    assert result["data"]["bridge"] == system_base["bridge"]
    assert result["data"]["environmentProbe"]["status"] == "error"
    assert result["warnings"] == ["system_base_environment_probe_failed"]
    assert backoff_result["data"]["environmentProbe"]["status"] == "error"
    assert backoff_result["data"]["environmentProbe"]["retryAfterSeconds"] > 0
    assert probe_calls == 1
    _reset_system_base_environment_state()
