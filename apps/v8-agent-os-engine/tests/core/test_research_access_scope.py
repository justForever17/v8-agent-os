from copy import deepcopy
import json

import pytest

from core.tools import research_broker as broker, research_ledger as ledger
from core.tool_surface import apply_tool_surface_budget
from langchain_core.messages import ToolMessage
from erc.runtime_context import bind_runtime_context
from runtimes.research.access_scope import ResearchAccessScope
from runtimes.research.tool_access import saved_research_reader
from tests.core.research_scope_fixture import research_sessions
from tests.core.test_research_answer_read import accepted_bundle


@pytest.fixture
def world(monkeypatch, tmp_path):
    monkeypatch.setenv("V8_RESEARCH_LEDGER_PATH", str(tmp_path / "research.json"))
    database, create, trusts = research_sessions(monkeypatch, tmp_path)
    contexts = {
        "origin": create("origin"), "peer": create("peer"),
        "other_user": create("other_user", user="bob"),
        "other_workspace": create("other_workspace", workspace="beta"),
    }
    bundle, _, _ = accepted_bundle(3200, True)
    with bind_runtime_context(**contexts["origin"]):
        stored = broker._store_evidence(bundle, state={})
    return database, create, trusts, contexts, stored


def read(context, **args):
    with bind_runtime_context(**context):
        return json.loads(saved_research_reader.func(**args))


@pytest.mark.parametrize("role,depth", [("supervisor", 0), ("direct_subagent", 1), ("grandchild", 2)])
def test_same_user_trusted_workspace_cross_session_public_reader(world, role, depth):
    _, _, _, contexts, stored = world
    context = {**contexts["peer"], "actor_role": role, "delegation_depth": depth}
    pack_id = stored["experienceUpdate"]["experiencePackId"]
    assert read(context, mode="get_experience", experiencePackId=pack_id)["ok"]
    page = read(context, mode="get_evidence", evidenceBundleId=stored["evidenceBundleId"], readAnswer=True)
    assert page["ok"] and "保留换行" in json.dumps(page, ensure_ascii=False)
    source = read(context, mode="get_evidence", evidenceBundleId=stored["evidenceBundleId"], sourceKey="S1")
    assert source["ok"] and source["originalReadBindings"][0]["claimId"] == "read_S1:R1"
    assert read(context, mode="observe")["counts"]["evidenceBundles"] == 1
    assert read(context, mode="search_experience", query="原始格式")["items"][0]["experiencePackId"] == pack_id


@pytest.mark.parametrize("identity", ["other_user", "other_workspace", "unknown"])
@pytest.mark.parametrize("mode", ["get_experience", "get_evidence"])
def test_id_and_forged_parent_cannot_open_other_scope_or_surface(world, identity, mode, monkeypatch):
    _, _, _, contexts, stored = world
    context = contexts.get(identity, {"session_id": "missing", "runtime_kind": "chat"})
    context = {**context, "parent_session_id": "origin", "sourceContext": stored["sourceContext"]}
    args = {"experiencePackId": stored["experienceUpdate"]["experiencePackId"]} if mode == "get_experience" else {"evidenceBundleId": stored["evidenceBundleId"]}
    result = read(context, mode=mode, **args)
    assert result["error"] == "research_scope_unauthorized"
    assert not any(key in result for key in ("item", "answer", "evidenceBundleId", "experiencePackId", "detailTool"))
    assert stored["evidenceBundleId"] not in json.dumps(result)
    monkeypatch.setattr(ledger, "get_evidence_bundle", lambda *_a, **_k: pytest.fail("denial must not reread through renderer"))
    surface = apply_tool_surface_budget(ToolMessage(content=json.dumps(result), name="research_broker", tool_call_id="denied"), {}, tool_name="research_broker")
    assert "保留换行" not in surface.content


def test_scope_filter_applies_before_limit_counts_and_global_candidates(world):
    _, _, _, contexts, stored = world
    foreign = deepcopy(stored)
    foreign.update(evidenceBundleId="foreign", scope="global", sourceContext={})
    ledger.store_evidence_bundle(foreign, ttl_seconds=60, scope="global")
    result = read(contexts["peer"], mode="observe", limit=1)
    assert result["counts"] == {"evidenceBundles": 1, "experiencePacks": 1}
    assert result["items"][0]["evidenceBundleId"] == stored["evidenceBundleId"]
    for context in (contexts["other_user"], contexts["other_workspace"]):
        assert read(context, mode="observe")["items"] == []
        assert read(context, mode="search_experience", query="原始格式")["items"] == []


@pytest.mark.parametrize("mode", ["archive_experience", "restore_experience", "delete_experience", "promote_experience", "run"])
def test_id_mutations_and_revision_do_not_cross_scope_or_start_model(world, mode, monkeypatch):
    _, _, _, contexts, stored = world
    before = ledger._ledger_path().read_bytes()
    monkeypatch.setattr(broker, "_run_agent_owned_research", lambda **_kw: pytest.fail("unauthorized revision must not run model"))
    with bind_runtime_context(**contexts["other_user"]):
        result = json.loads(broker.research_broker.func(mode=mode, question="update", confirm=True,
            experiencePackId=stored["experienceUpdate"]["experiencePackId"], evidenceBundleId=stored["evidenceBundleId"]))
    assert result["kind"] == "research_access_denied"
    assert ledger._ledger_path().read_bytes() == before


def test_reader_projection_rejects_mutation_even_when_called_as_python(world):
    _, _, _, contexts, stored = world
    assert read(contexts["origin"], mode="delete_experience", experiencePackId=stored["experienceUpdate"]["experiencePackId"])["ok"] is False
    assert ledger.get_experience_pack(stored["experienceUpdate"]["experiencePackId"])


def test_runtime_context_wins_over_injected_state_and_nested_session_is_supported(world):
    _, _, _, contexts, stored = world
    with bind_runtime_context(**contexts["other_user"]):
        result = json.loads(saved_research_reader.func(mode="get_evidence", evidenceBundleId=stored["evidenceBundleId"], state=contexts["origin"]))
    assert result["ok"] is False
    access = ResearchAccessScope({"route_context": contexts["origin"]})
    assert access.valid and access.allows(stored)
    with bind_runtime_context(sessionId="other_user", userId="bob"):
        assert not ResearchAccessScope(contexts["origin"]).allows(stored)
    with bind_runtime_context(**contexts["origin"], episode_id="not-a-real-parent-episode"):
        assert not ResearchAccessScope().allows(stored)


def test_revoked_trust_changed_scope_and_deleted_origin(world):
    database, _, trusts, contexts, stored = world
    trusts["alpha"] = "restricted"
    assert read(contexts["peer"], mode="get_evidence", evidenceBundleId=stored["evidenceBundleId"])["ok"] is False
    assert read(contexts["origin"], mode="get_evidence", evidenceBundleId=stored["evidenceBundleId"])["ok"] is True
    trusts["alpha"] = "trusted"
    # Saved server-owned provenance keeps accepted answers recoverable after a
    # source conversation is deleted, without trusting a caller's parent hint.
    database.delete_session("origin")
    assert read(contexts["peer"], mode="get_evidence", evidenceBundleId=stored["evidenceBundleId"])["ok"] is True
    assert read(contexts["other_user"], mode="get_evidence", evidenceBundleId=stored["evidenceBundleId"])["ok"] is False


def test_legacy_session_scope_recovers_but_ownerless_global_does_not(world):
    database, _, _, contexts, stored = world
    legacy = {key: value for key, value in stored.items() if key != "sourceContext"}
    assert ResearchAccessScope(contexts["peer"]).allows(legacy)
    assert not ResearchAccessScope(contexts["peer"]).allows({**legacy, "scope": "global"})
    with database.get_connection() as connection:
        connection.execute("UPDATE sessions SET user_id = ? WHERE id = ?", ("bob", "origin"))
        connection.commit()
    assert not ResearchAccessScope(contexts["peer"]).allows(stored)


def test_same_workspace_supervisor_archive_restore_and_child_no_mutation(world):
    _, _, _, contexts, stored = world
    pack_id = stored["experienceUpdate"]["experiencePackId"]
    with bind_runtime_context(**contexts["peer"]):
        assert json.loads(broker.research_broker.func(mode="archive_experience", experiencePackId=pack_id))["ok"]
    assert read(contexts["peer"], mode="get_experience", experiencePackId=pack_id)["ok"] is False
    assert read(contexts["peer"], mode="get_experience", experiencePackId=pack_id, includeArchived=True)["ok"]
    with bind_runtime_context(**contexts["peer"], actor_role="direct_subagent"):
        assert json.loads(broker.research_broker.func(mode="restore_experience", experiencePackId=pack_id))["ok"] is False
    with bind_runtime_context(**contexts["peer"]):
        assert json.loads(broker.research_broker.func(mode="restore_experience", experiencePackId=pack_id))["ok"]


def test_store_cannot_overwrite_foreign_id_or_revision_under_ledger_lock(world):
    _, _, _, contexts, stored = world
    access = ResearchAccessScope(contexts["other_user"])
    before = ledger._ledger_path().read_bytes()
    with pytest.raises(PermissionError, match="research_scope_unauthorized"):
        ledger.store_evidence_bundle(stored, ttl_seconds=60, scope="other_user", access_check=access.allows)
    with pytest.raises(PermissionError, match="research_scope_unauthorized"):
        ledger.store_evidence_bundle({**stored, "evidenceBundleId": "new", "supersedesExperiencePackId": stored["experienceUpdate"]["experiencePackId"]},
                                    ttl_seconds=60, scope="other_user", access_check=access.allows)
    assert ledger._ledger_path().read_bytes() == before


def test_personal_chat_without_workspace_can_read_own_answer_only(world):
    database, _, _, _, stored = world
    database.create_or_update_session("personal", "Personal research", user_id="alice")
    database.create_or_update_session("personal-other", "Other conversation", user_id="alice")
    context = {"session_id": "personal", "runtime_kind": "chat", "user_id": "alice"}
    with bind_runtime_context(**context):
        bundle = broker._store_evidence({**stored, "evidenceBundleId": "personal-evidence"}, state={})
    assert bundle["sourceContext"]["workspaceScope"] == ""
    assert read(context, mode="get_evidence", evidenceBundleId="personal-evidence")["ok"]
    assert read({**context, "session_id": "personal-other"}, mode="get_evidence", evidenceBundleId="personal-evidence")["ok"] is False


def test_project_alias_rename_does_not_override_physical_workspace_identity(world):
    database, _, trusts, contexts, stored = world
    binding = database.get_session_scope_binding("peer")
    binding["project_id"] = "alpha-renamed"
    database.upsert_session_scope_binding(binding)
    trusts["alpha-renamed"] = "trusted"
    assert read(contexts["peer"], mode="get_evidence", evidenceBundleId=stored["evidenceBundleId"])["ok"]


def test_trust_is_resolved_once_per_call_and_revocation_checked_next_call(world, monkeypatch):
    from runtimes.research import access_scope

    _, _, trusts, contexts, stored = world
    calls = []
    resolve = access_scope.workspace_authority_service.resolve

    def counted(**kwargs):
        calls.append(kwargs)
        return resolve(**kwargs)

    monkeypatch.setattr(access_scope.workspace_authority_service, "resolve", counted)
    access = ResearchAccessScope(contexts["peer"])
    for _ in range(5):
        assert access.allows(stored)
    assert len(calls) == 1
    trusts["alpha"] = "restricted"
    assert not ResearchAccessScope(contexts["peer"]).allows(stored)
    assert len(calls) == 2


def test_id_mutation_rechecks_inside_ledger_lock_before_side_effect(world):
    _, _, _, contexts, stored = world
    access = ResearchAccessScope(contexts["peer"])
    pack_id = stored["experienceUpdate"]["experiencePackId"]
    assert ledger.get_experience_pack(pack_id, access_check=access.allows)
    before = ledger._ledger_path().read_bytes()

    def revoked(_item):
        assert ledger._LOCK._is_owned()
        return False

    assert ledger.archive_experience_pack(pack_id, access_check=revoked) is None
    assert ledger.restore_experience_pack(pack_id, access_check=revoked) is None
    assert not ledger.delete_experience_pack(pack_id, confirm=True, access_check=revoked)
    assert ledger.promote_experience_pack(stored["evidenceBundleId"], access_check=revoked) is None
    assert ledger._ledger_path().read_bytes() == before
