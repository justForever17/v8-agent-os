from __future__ import annotations

from typing import Any, Dict, Iterable


def built_in_playbook_seeds() -> list[dict[str, Any]]:
    return [
        {
            "id": "github.star_repository",
            "version": 1,
            "status": "seed",
            "runtimeNative": True,
            "domain": "github",
            "operation": "star_repository",
            "goldenCase": {
                "goal": "去 GitHub 给 TuriX-CUA 点星标",
                "repoUrl": "https://github.com/TurixAI/TuriX-CUA",
                "completionEvidence": "Star button state is Starred",
            },
            "intentPatterns": [
                "star github repository",
                "给 GitHub 仓库点星标",
                "给 TuriX / TuriX-CUA 点星标",
            ],
            "factResolution": {
                "required": True,
                "goal": "resolve_repository_url_before_gui_action",
                "goldenExample": {
                    "entity": "TuriX-CUA",
                    "repoUrl": "https://github.com/TurixAI/TuriX-CUA",
                    "targetUrl": "https://github.com/TurixAI/TuriX-CUA",
                },
                "acceptableEvidence": [
                    "exact repository URL",
                    "GitHub search result with owner/repo match",
                    "repository page canonical owner/name",
                ],
            },
            "preferredLane": "browser_cdp_dom",
            "fallbackLane": "visual_locator",
            "requiredState": {
                "auth": "authenticated_or_user_intervention",
                "page": "canonical_repository_page",
            },
            "successState": {
                "buttonState": "Starred",
                "acceptedEvidence": ["DOM button state", "visible Starred text", "repository page canonical owner/name"],
                "notEnough": ["opened_github_home", "opened_search_page", "opened_repo_without_starred_state"],
            },
            "failureModes": [
                "not_logged_in",
                "repo_not_found",
                "ambiguous_repo",
                "page_not_loaded",
                "rate_limited",
                "star_button_not_visible",
            ],
            "safety": {
                "requiresUserInterventionOnLogin": True,
                "doNotUseGitHubTokenByDefault": True,
                "doNotCreateIssuePrOrComment": True,
            },
            "sourceRefs": [
                {
                    "kind": "external_reference",
                    "project": "TuriX-CUA",
                    "path": "skills/github-web-actions.md",
                    "license": "MIT",
                    "usedAs": "experience_pattern_rewritten_as_v8_runtime_native_seed",
                }
            ],
        },
        {
            "id": "web.search_and_open_result",
            "version": 1,
            "status": "catalog",
            "runtimeNative": True,
            "domain": "web",
            "operation": "search_and_open_result",
            "intentPatterns": ["search web and open result", "搜索并打开结果", "查找官网/文档站"],
            "factResolution": {
                "required": True,
                "goal": "resolve canonical URL or search-result evidence before GUI navigation",
            },
            "preferredLane": "browser_cdp_dom",
            "fallbackLane": "visual_locator",
            "requiredState": {"browser": "debug_profile_available_or_user_attention"},
            "successState": {"acceptedEvidence": ["target URL loaded", "page title/domain matches resolved fact"]},
            "failureModes": ["ambiguous_result", "captcha", "network_error", "search_engine_blocker"],
        },
        {
            "id": "browser.login_gate",
            "version": 1,
            "status": "catalog",
            "runtimeNative": True,
            "domain": "browser",
            "operation": "login_gate",
            "intentPatterns": ["登录网站", "sign in", "requires login"],
            "factResolution": {"required": True, "goal": "identify login boundary and target service"},
            "preferredLane": "browser_cdp_dom",
            "fallbackLane": "visual_locator",
            "requiredState": {"auth": "user_intervention_default"},
            "successState": {"acceptedEvidence": ["authenticated account UI visible", "login page no longer blocks task"]},
            "failureModes": ["credential_required", "captcha", "mfa_required", "wrong_account"],
            "safety": {"defaultNextAction": "ask_user", "doNotStorePasswords": True},
        },
        {
            "id": "web.form_submit",
            "version": 1,
            "status": "catalog",
            "runtimeNative": True,
            "domain": "web",
            "operation": "form_submit",
            "intentPatterns": ["填写表单", "submit web form", "提交设置"],
            "factResolution": {"required": False, "goal": "resolve form page when target is ambiguous"},
            "preferredLane": "browser_cdp_dom",
            "fallbackLane": "visual_locator",
            "successState": {"acceptedEvidence": ["validation success", "submitted state", "confirmation text"]},
            "failureModes": ["missing_required_field", "captcha", "destructive_submit_requires_review"],
        },
        {
            "id": "web.file_upload",
            "version": 1,
            "status": "catalog",
            "runtimeNative": True,
            "domain": "web",
            "operation": "file_upload",
            "intentPatterns": ["上传文件", "choose file", "file picker"],
            "factResolution": {"required": True, "goal": "resolve local file and upload target before opening picker"},
            "preferredLane": "browser_cdp_dom",
            "fallbackLane": "visual_locator",
            "successState": {"acceptedEvidence": ["file name appears in page", "upload completed indicator"]},
            "failureModes": ["file_not_found", "unsupported_file_type", "upload_timeout"],
        },
        {
            "id": "download_and_open",
            "version": 1,
            "status": "catalog",
            "runtimeNative": True,
            "domain": "web",
            "operation": "download_and_open",
            "intentPatterns": ["下载并打开", "download and open", "下载安装包"],
            "factResolution": {"required": True, "goal": "resolve official download page and artifact risk before GUI download"},
            "preferredLane": "browser_cdp_dom",
            "fallbackLane": "visual_locator",
            "successState": {"acceptedEvidence": ["download artifact registered", "file opened only after safety review if executable"]},
            "failureModes": ["untrusted_download", "download_after_execute_requires_review", "blocked_file_type"],
            "safety": {"downloadThenExecuteDefault": "review"},
        },
        {
            "id": "settings.toggle_option",
            "version": 1,
            "status": "catalog",
            "runtimeNative": True,
            "domain": "settings",
            "operation": "toggle_option",
            "intentPatterns": ["打开/关闭设置", "toggle option", "启用功能"],
            "factResolution": {"required": False, "goal": "resolve app and setting page if target is ambiguous"},
            "preferredLane": "accessibility",
            "fallbackLane": "visual_locator",
            "successState": {"acceptedEvidence": ["toggle state changed to requested value", "settings page shows target value"]},
            "failureModes": ["wrong_toggle", "requires_restart", "admin_permission_required"],
        },
    ]


def experience_asset_inventory(
    *,
    app_profiles: Iterable[Any] | None = None,
    app_catalog_summary: Dict[str, Any] | None = None,
    selector_stats: Dict[str, Any] | None = None,
) -> dict[str, Any]:
    profiles = []
    for profile in list(app_profiles or []):
        try:
            payload = profile.as_dict()
        except Exception:
            payload = {}
        if not payload:
            continue
        profiles.append(
            {
                "appId": payload.get("appId"),
                "displayName": payload.get("displayName"),
                "controlClass": payload.get("controlClass"),
                "scenarioTags": list(payload.get("scenarioTags") or []),
                "selectorCount": len(dict(payload.get("selectors") or {})),
                "toolbarActionCount": len(dict(payload.get("toolbarActions") or {})),
                "validationUse": "runtime_native_app_profile",
            }
        )
    return {
        "version": 1,
        "policy": "catalog_only_not_prompt_injected",
        "v8RuntimeNative": {
            "appProfiles": profiles,
            "appCatalogSummary": dict(app_catalog_summary or {}),
            "selectorStats": dict(selector_stats or {}),
            "rpaMuscleMemory": {
                "source": "RPA Runtime templates/drafts via computer_use_lookup_muscle_memory",
                "reuseConstraint": "route-first; not injected into supervisor prompt as raw skill text",
            },
        },
        "externalReferences": [
            {
                "project": "TuriX-CUA",
                "asset": "skills/github-web-actions.md",
                "kind": "markdown_playbook_reference",
                "license": "MIT",
                "reuseConstraint": "rewrite into structured runtime-native playbook seed; do not load external skill at runtime",
            },
            {
                "project": "TuriX-CUA",
                "asset": "origin/multi-agent-windows src/windows/actions.py",
                "kind": "platform_action_reference",
                "reuseConstraint": "parity checklist only; do not copy action implementation",
            },
            {
                "project": "TuriX-CUA",
                "asset": "origin/multi-agent-linux src/linux/actions.py",
                "kind": "platform_action_reference",
                "reuseConstraint": "parity checklist only; distinguish X11/Wayland",
            },
            {
                "project": "TuriX-CUA",
                "asset": "origin/mac_mcp src/mac/actions.py",
                "kind": "platform_action_reference",
                "reuseConstraint": "parity checklist only; validate TCC permissions on real host",
            },
            {
                "project": "Mano-P",
                "asset": "README.md",
                "kind": "gui_vla_paradigm_reference",
                "reuseConstraint": "conceptual reference only; no executable source observed locally",
            },
        ],
        "builtInPlaybookSeeds": built_in_playbook_seeds(),
    }
