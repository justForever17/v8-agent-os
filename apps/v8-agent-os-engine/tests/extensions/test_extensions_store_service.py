from __future__ import annotations

import base64
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote

import pytest

from core import extensions_store_service as service


def test_parse_skills_home_items_reads_embedded_popular_payload() -> None:
    html = r'''
    <script>self.__next_f.push([1,"{\"source\":\"mattpocock/skills\",\"skillId\":\"grill-me\",\"name\":\"grill-me\",\"installs\":464265,\"weeklyInstalls\":[1,2,3]}"])</script>
    '''

    items = service.parse_skills_home_items(html)

    assert len(items) == 1
    assert items[0]["id"] == "mattpocock/skills@grill-me"
    assert items[0]["source"] == "mattpocock/skills"
    assert items[0]["skillId"] == "grill-me"
    assert items[0]["installs"] == 464265
    assert items[0]["weeklyInstalls"] == [1, 2, 3]
    assert items[0]["detailUrl"] == "https://skills.sh/mattpocock/skills/grill-me"


def test_parse_skills_search_response_reads_cli_api_shape() -> None:
    payload = {
        "skills": [
            {
                "id": "github/awesome-copilot/typescript-mcp-server-generator",
                "skillId": "typescript-mcp-server-generator",
                "name": "typescript-mcp-server-generator",
                "installs": 10974,
                "source": "github/awesome-copilot",
            }
        ]
    }

    items = service.parse_skills_search_response(payload)

    assert items[0]["id"] == "github/awesome-copilot@typescript-mcp-server-generator"
    assert items[0]["detailUrl"] == "https://skills.sh/github/awesome-copilot/typescript-mcp-server-generator"


def test_parse_skill_download_response_reads_description_and_skill_markdown() -> None:
    payload = {
        "files": [
            {
                "path": "SKILL.md",
                "contents": "---\nname: grill-me\ndescription: A relentless interview to sharpen a plan or design.\n---\n\nRun a `/grilling` session.\n",
            }
        ],
        "hash": "abc",
    }

    detail = service.parse_skill_download_response(payload, source="mattpocock/skills", skill_id="grill-me")

    assert detail["name"] == "grill-me"
    assert detail["description"] == "A relentless interview to sharpen a plan or design."
    assert detail["markdown"] == ""


def test_parse_skill_download_response_ignores_symbol_only_description() -> None:
    payload = {
        "files": [
            {
                "path": "SKILL.md",
                "contents": "---\nname: noisy\ndescription: |\n---\n\n>\n\nUse this skill to prepare a concise project brief.\n",
            }
        ],
    }

    detail = service.parse_skill_download_response(payload, source="example/skills", skill_id="noisy")

    assert detail["description"] == "Use this skill to prepare a concise project brief."
    assert detail["markdown"] == "Use this skill to prepare a concise project brief."


def test_decorate_skill_items_pins_find_skills_and_filters_low_install_items(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(service, "_installed_skill_ids", lambda: set())
    monkeypatch.setattr(service, "_enrich_skill_summary", lambda item: item)
    items = service._dedupe_skill_items(
        [
            {"source": "example/skills", "skillId": "tiny", "name": "tiny", "installs": 199},
            {"source": "example/skills", "skillId": "useful", "name": "useful", "installs": 400},
            {"source": "vercel-labs/skills", "skillId": "find-skills", "name": "find-skills", "installs": 1},
        ]
    )

    decorated = service._decorate_skill_items(items, limit=10)

    assert [item["id"] for item in decorated] == [
        "vercel-labs/skills@find-skills",
        "example/skills@useful",
    ]


def test_skill_list_never_fans_out_to_detail_requests(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(service, "_installed_skill_ids", lambda: set())
    monkeypatch.setattr(service, "_read_cache", lambda *_args, **_kwargs: (None, "miss"))
    monkeypatch.setattr(
        service,
        "get_store_skill_detail",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("list reads must not fetch detail")),
    )
    items = service._dedupe_skill_items(
        [{"source": "example/skills", "skillId": "useful", "name": "useful", "installs": 400}]
    )

    decorated = service._decorate_skill_items(items, limit=10)

    assert decorated[0]["id"] == "example/skills@useful"
    assert decorated[0]["description"] == ""


def test_cache_write_keeps_previous_value_when_atomic_replace_fails(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("V8_AGENT_OS_EXTENSIONS_STORE_CACHE_DIR", str(tmp_path))
    service._write_cache("atomic", {"version": "old"})
    observed_during_replace: list[object] = []

    def fail_replace(source, destination) -> None:
        assert source.parent == destination.parent == tmp_path
        observed_during_replace.append(service._read_cache("atomic", allow_stale=True)[0])
        raise OSError("replace failed")

    monkeypatch.setattr(service.os, "replace", fail_replace)

    with pytest.raises(OSError, match="replace failed"):
        service._write_cache("atomic", {"version": "new"})

    assert observed_during_replace == [{"version": "old"}]
    assert service._read_cache("atomic", allow_stale=True) == ({"version": "old"}, "cached")
    assert list(tmp_path.glob("*.tmp")) == []
    assert list(tmp_path.glob(".*.tmp")) == []


@pytest.mark.parametrize(("refresh", "seed_cache"), [(False, False), (True, True)])
def test_same_cache_key_coalesces_concurrent_loads(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    refresh: bool,
    seed_cache: bool,
) -> None:
    monkeypatch.setenv("V8_AGENT_OS_EXTENSIONS_STORE_CACHE_DIR", str(tmp_path))
    cache_name = f"single-flight-{refresh}"
    if seed_cache:
        service._write_cache(cache_name, {"value": "old"})

    loader_started = threading.Event()
    follower_started = threading.Event()
    follower_waiting = threading.Event()
    release_loader = threading.Event()
    calls_lock = threading.Lock()
    calls = 0

    def loader() -> dict[str, str]:
        nonlocal calls
        with calls_lock:
            calls += 1
        loader_started.set()
        assert release_loader.wait(timeout=2)
        return {"value": "new"}

    def invoke(*, follower: bool = False):
        if follower:
            follower_started.set()
        return service._load_cached_value(
            cache_name,
            refresh=refresh,
            loader=loader,
            accepts=lambda value: isinstance(value, dict),
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        leader = executor.submit(invoke)
        assert loader_started.wait(timeout=2)
        with service._CACHE_FLIGHTS_LOCK:
            flight = service._CACHE_FLIGHTS[cache_name]
            original_event = flight.event

            class ObservedEvent:
                def wait(self) -> None:
                    follower_waiting.set()
                    original_event.wait()

                def set(self) -> None:
                    original_event.set()

            flight.event = ObservedEvent()
        follower = executor.submit(invoke, follower=True)
        assert follower_started.wait(timeout=2)
        assert follower_waiting.wait(timeout=2)
        with calls_lock:
            assert calls == 1
        assert not follower.done()
        release_loader.set()
        results = [leader.result(timeout=2), follower.result(timeout=2)]

    assert [result[0] for result in results] == [{"value": "new"}, {"value": "new"}]
    assert [result[1] for result in results] == ["live", "live"]
    assert all(result[2] is None for result in results)
    assert calls == 1
    assert service._read_cache(cache_name) == ({"value": "new"}, "cached")


def test_different_cache_keys_load_concurrently(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("V8_AGENT_OS_EXTENSIONS_STORE_CACHE_DIR", str(tmp_path))
    loaders_met = threading.Barrier(2)

    def invoke(cache_name: str):
        return service._load_cached_value(
            cache_name,
            refresh=True,
            loader=lambda: {"value": loaders_met.wait(timeout=2)},
            accepts=lambda value: isinstance(value, dict),
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(invoke, ["key-a", "key-b"]))

    assert [result[1] for result in results] == ["live", "live"]


def test_cache_load_preserves_stale_fallback(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("V8_AGENT_OS_EXTENSIONS_STORE_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(service, "_CACHE_TTL_SECONDS", -1)
    service._write_cache("stale", {"value": "previous"})
    upstream_error = RuntimeError("upstream unavailable")

    payload, freshness, fallback_error = service._load_cached_value(
        "stale",
        refresh=False,
        loader=lambda: (_ for _ in ()).throw(upstream_error),
        accepts=lambda value: isinstance(value, dict),
    )

    assert payload == {"value": "previous"}
    assert freshness == "cached"
    assert fallback_error is upstream_error


def test_install_store_skill_compiles_controlled_global_command(monkeypatch: pytest.MonkeyPatch) -> None:
    commands: list[str] = []

    def fake_install(command: str) -> dict:
        commands.append(command)
        return {"status": "success", "installed": [], "warnings": []}

    monkeypatch.setattr(service, "install_skill_from_command", fake_install)

    result = service.install_store_skill({"source": "github/awesome-copilot", "skillId": "typescript-mcp-server-generator"})

    assert commands == ["npx --yes skills add github/awesome-copilot@typescript-mcp-server-generator -g"]
    assert result["store"]["provider"] == "skills.sh"


def test_parse_github_mcp_cards_reads_registry_embedded_json() -> None:
    html = '''
    <a href="/mcp/github/github-mcp-server"></a>
    {"id":"github/github-mcp-server","name":"github/github-mcp-server","display_name":"GitHub","description":"GitHub tools","url":"https://github.com/github/github-mcp-server","created_at":"1.0.0","updated_at":"2026-07-06T00:00:00Z","stargazer_count":123,"owner_avatar_url":"https://avatars.githubusercontent.com/u/1?v=4","opengraph_image_url":"https://opengraph.githubassets.com/demo/github/github-mcp-server","primary_language":"Go","license":"MIT License","topics":["mcp","github"],"repository":{"source":"github","url":"https://github.com/github/github-mcp-server"},"full_name":"io.github.github/github-mcp-server","api_name":"github/github-mcp-server"}
    '''

    cards = service.parse_github_mcp_cards(html)

    assert len(cards) == 1
    assert cards[0]["id"] == "github/github-mcp-server"
    assert cards[0]["title"] == "GitHub"
    assert cards[0]["description"] == "GitHub tools"
    assert cards[0]["repositoryUrl"] == "https://github.com/github/github-mcp-server"
    assert cards[0]["detailUrl"] == "https://github.com/mcp/github/github-mcp-server"
    assert cards[0]["stars"] == 123
    assert cards[0]["avatarUrl"] == "https://avatars.githubusercontent.com/u/1?v=4"
    assert cards[0]["language"] == "Go"
    assert cards[0]["topics"] == ["mcp", "github"]
    assert cards[0]["serverName"] == "github-mcp-server"


def test_parse_github_mcp_cards_falls_back_to_opengraph_avatar() -> None:
    html = '''
    {"id":"microsoft/markitdown","name":"microsoft/markitdown","display_name":"Markitdown","description":"Convert files","url":"https://github.com/microsoft/markitdown","stargazer_count":456,"owner_avatar_url":"","opengraph_image_url":"https://opengraph.githubassets.com/demo/microsoft/markitdown","primary_language":"Python","topics":[],"repository":{"source":"github","url":"https://github.com/microsoft/markitdown"}}
    '''

    cards = service.parse_github_mcp_cards(html)

    assert len(cards) == 1
    assert cards[0]["avatarUrl"] == "https://opengraph.githubassets.com/demo/microsoft/markitdown"


def test_parse_mcp_install_redirect_candidates_extracts_secret_input() -> None:
    config = {
        "servers": {
            "github": {
                "type": "http",
                "url": "https://api.githubcopilot.com/mcp/",
                "headers": {"Authorization": "Bearer ${input:github_pat}"},
            }
        },
        "inputs": [
            {
                "type": "promptString",
                "id": "github_pat",
                "description": "GitHub Personal Access Token",
                "password": True,
            }
        ],
    }
    link = "https://insiders.vscode.dev/redirect/mcp/install?name=github&config=" + quote(json.dumps(config))

    candidates = service.parse_mcp_install_redirect_candidates(link, default_server_name="github-mcp-server")

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate["transport"] == "http"
    assert candidate["serverName"] == "github"
    assert candidate["requirements"] == [
        {
            "key": "header.Authorization.github_pat",
            "target": "header",
            "name": "Authorization",
            "label": "GitHub Personal Access Token",
            "placeholder": "github_pat",
            "required": True,
            "secret": True,
            "valueTemplate": "Bearer ${input:github_pat}",
        }
    ]


def test_parse_mcp_install_redirect_candidates_extracts_cursor_base64_config() -> None:
    encoded = base64.urlsafe_b64encode(json.dumps({"url": "https://mcp.context7.com/mcp"}).encode("utf-8")).decode("utf-8")
    link = f"https://cursor.com/en/install-mcp?name=context7&config={encoded}"

    candidates = service.parse_mcp_install_redirect_candidates(link, default_server_name="context7")

    assert len(candidates) == 1
    assert candidates[0]["serverName"] == "context7"
    assert candidates[0]["transport"] == "http"
    assert candidates[0]["url"] == "https://mcp.context7.com/mcp"


def test_parse_mcp_readme_json_candidates_reads_servers_block() -> None:
    markdown = """
```json
{
  "servers": {
    "demo": {
      "command": "npx",
      "args": ["-y", "demo-mcp"],
      "env": {
        "DEMO_API_KEY": "${input:demo_key}"
      }
    }
  },
  "inputs": [
    {"id": "demo_key", "description": "Demo API key", "password": true}
  ]
}
```
"""

    candidates = service.parse_mcp_readme_json_candidates(markdown, default_server_name="demo")

    assert len(candidates) == 1
    assert candidates[0]["transport"] == "stdio"
    assert candidates[0]["command"] == "npx"
    assert candidates[0]["requirements"][0]["key"] == "env.DEMO_API_KEY.demo_key"
    assert candidates[0]["requirements"][0]["secret"] is True


def test_mcp_candidates_prefer_requirements_and_infer_explicit_remote_header() -> None:
    no_input = service._candidate_from_config(
        server_name="context7",
        config={"type": "http", "url": "https://mcp.context7.com/mcp"},
        source="vscode_install_link",
    )
    with_input = service._candidate_from_config(
        server_name="github",
        config={
            "type": "http",
            "url": "https://api.githubcopilot.com/mcp/",
            "headers": {"Authorization": "Bearer ${input:github_mcp_pat}"},
        },
        source="readme_json",
    )

    inferred = service._augment_mcp_candidates_from_detail_text(
        [no_input],
        "Pass your API key via the CONTEXT7_API_KEY header.",
    )
    ordered = service._dedupe_candidates([no_input, with_input])

    assert inferred[0]["requirements"][0]["target"] == "header"
    assert inferred[0]["requirements"][0]["name"] == "CONTEXT7_API_KEY"
    assert inferred[0]["_serverConfig"]["headers"] == {"CONTEXT7_API_KEY": ""}
    assert ordered[0]["serverName"] == "github"
    assert ordered[0]["requirements"][0]["name"] == "Authorization"


def test_parse_mcp_detail_page_text_reads_markdown_body_description() -> None:
    html = """
    <html><body>
      <div class="McpDetails-module__content__t4MUc">
        <div class="markdown-body">
          <h1>GitHub MCP Server</h1>
          <p>The GitHub MCP Server connects AI tools directly to GitHub's platform.</p>
          <h2>Use Cases</h2>
          <ul><li>Repository Management: Browse and query code.</li></ul>
        </div>
      </div>
    </body></html>
    """

    detail = service.parse_mcp_detail_page_text(html)

    assert detail["description"] == "The GitHub MCP Server connects AI tools directly to GitHub's platform."
    assert "# GitHub MCP Server" in detail["markdown"]
    assert "- Repository Management: Browse and query code." in detail["markdown"]


def test_install_store_mcp_applies_requirements_and_uses_config_service(monkeypatch: pytest.MonkeyPatch) -> None:
    candidate = {
        "id": "abc123",
        "serverName": "github",
        "requirements": [
            {
                "key": "header.Authorization.github_pat",
                "target": "header",
                "name": "Authorization",
                "label": "GitHub PAT",
                "placeholder": "github_pat",
                "required": True,
                "secret": True,
                "valueTemplate": "Bearer ${input:github_pat}",
            }
        ],
        "_serverConfig": {
            "type": "http",
            "url": "https://api.githubcopilot.com/mcp/",
            "headers": {"Authorization": "Bearer ${input:github_pat}"},
        },
    }
    installed_payloads: list[dict] = []

    monkeypatch.setattr(service, "_candidate_by_id", lambda mcp_id, candidate_id: candidate)

    def fake_install(config: dict, *, refresh_reason: str) -> dict:
        installed_payloads.append(config)
        return {"status": "success", "installedServers": ["github"], "replacedServers": [], "refreshRequested": True}

    monkeypatch.setattr(service, "install_mcp_server_config", fake_install)

    result = service.install_store_mcp(
        {
            "id": "github/github-mcp-server",
            "candidateId": "abc123",
            "values": {"header.Authorization.github_pat": "ghp_secret"},
        }
    )

    assert installed_payloads == [
        {
            "mcpServers": {
                "github": {
                    "type": "http",
                    "url": "https://api.githubcopilot.com/mcp/",
                    "headers": {"Authorization": "Bearer ghp_secret"},
                }
            }
        }
    ]
    assert result["store"]["serverName"] == "github"
