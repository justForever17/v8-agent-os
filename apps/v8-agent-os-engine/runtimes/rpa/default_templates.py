from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict

from core.multimodal_payload_adapter import utc_now_iso
from runtimes.rpa.store import RPAScriptStore, rpa_script_store


GITHUB_STAR_TEMPLATE_ID = "system.github.star_repository"
GITHUB_STAR_TEMPLATE_SEED_VERSION = 2


def github_star_repository_template() -> Dict[str, Any]:
    now = utc_now_iso()
    return {
        "id": GITHUB_STAR_TEMPLATE_ID,
        "name": "GitHub Star Repository",
        "version": "1.0.0",
        "kind": "rpa_template_candidate",
        "appId": "browser_checkout",
        "goal": "给 GitHub 仓库点星标或取消星标 / star or unstar a GitHub repository / github star repo / TuriX 点星标 / TuriX 消星",
        "variables": [
            {
                "name": "repo_owner",
                "type": "string",
                "required": False,
                "placeholder": "{{repo_owner}}",
                "source": "fact_resolver",
                "exampleValue": "TurixAI",
            },
            {
                "name": "repo_name",
                "type": "string",
                "required": False,
                "placeholder": "{{repo_name}}",
                "source": "fact_resolver",
                "exampleValue": "TuriX-CUA",
            },
            {
                "name": "repo_url",
                "type": "url",
                "required": False,
                "placeholder": "{{repo_url}}",
                "source": "fact_resolver",
                "exampleValue": "https://github.com/TurixAI/TuriX-CUA",
            },
            {
                "name": "desired_state",
                "type": "string",
                "required": False,
                "placeholder": "{{desired_state}}",
                "source": "template_default",
                "exampleValue": "starred",
                "enum": ["starred", "unstarred"],
                "description": "目标状态：starred 表示点星标，unstarred 表示取消星标。",
            },
        ],
        "steps": [
            {
                "stepId": "delegate_github_star_repository",
                "use": "computer_use_playbook",
                "intent": "Delegate GitHub repository star/unstar state changes to ComputerUse runtime-native playbook.",
                "params": {
                    "selectedPlaybook": "github.star_repository",
                    "repoOwner": "{{repo_owner}}",
                    "repoName": "{{repo_name}}",
                    "repoUrl": "{{repo_url}}",
                    "desiredState": "{{desired_state}}",
                    "allowRealClick": True,
                    "loginBoundary": "ask_user",
                },
                "target": {
                    "domain": "github.com",
                    "repoUrl": "{{repo_url}}",
                    "desiredState": "{{desired_state}}",
                },
                "verification": {
                    "successState": "desired_state matched by strict GitHub Star/Unstar DOM control",
                    "evidence": ["browser_dom", "visible_text", "button_state"],
                    "mustNotTreatAsSuccess": ["opened_github_home", "opened_repo_without_desired_star_state"],
                },
                "recovery": {
                    "needsLogin": "Return recommendedNextAction=ask_user and preserve browser profile.",
                    "ambiguousRepo": "Use ComputerUse Fact Resolver before entering GUI.",
                },
                "risk": {
                    "level": "medium",
                    "mutation": "github_star",
                    "credentials": "never_store",
                },
                "metadata": {
                    "delegateRuntime": "computer_use",
                    "runtimeAccess": ["computer_use.control"],
                    "selectedPlaybook": "github.star_repository",
                },
            }
        ],
        "profile": {
            "appId": "browser_checkout",
            "displayName": "V8 dedicated browser profile",
            "scenarioTags": ["browser", "github", "star_repository", "computer_use_playbook"],
            "highRiskActions": ["comment", "issue", "pull_request", "token_oauth"],
        },
        "source": {
            "type": "system_seed",
            "runtime": "computer_use",
            "playbookId": "github.star_repository",
            "license": "V8OS internal seed",
        },
        "metadata": {
            "systemSeed": True,
            "seedTemplateId": GITHUB_STAR_TEMPLATE_ID,
            "seedVersion": GITHUB_STAR_TEMPLATE_SEED_VERSION,
            "templateStatus": "candidate",
            "sourceTraceCount": 1,
            "templateRolloutMode": "computer_use_first",
            "targetStrategyKeys": ["browser_cdp_dom", "computer_use_playbook", "fact_resolver"],
            "playbookRecommendations": [
                {
                    "playbookId": "github.star_repository",
                    "delegateRuntime": "computer_use",
                    "reason": "Browser state, login boundary and Starred verification stay inside ComputerUseRuntime.",
                }
            ],
            "preflightHints": [
                {
                    "kind": "login_boundary",
                    "summary": "If GitHub asks for login, request human login in the dedicated browser profile.",
                }
            ],
            "createdBy": "v8_system_seed",
        },
        "assessment": {
            "score": 0.78,
            "status": "accepted",
            "band": "medium",
            "reasons": [
                "委派到 ComputerUse runtime-native playbook，不保存账号、token 或密码。",
                "必须验证目标 Star/Unstar DOM 状态，不能把打开 GitHub 当作完成。",
            ],
            "reviewRequired": False,
            "excluded": False,
            "signals": {
                "bindingSummary": {"lowConfidenceRatio": 0.0},
                "preflightSummary": {"blockerDetectedSteps": 0},
                "delegateRuntime": "computer_use",
            },
        },
        "robot": {
            "tags": ["system_seed", "computer_use_playbook", "github"],
            "libraries": [],
            "metadata": {
                "executionAdapter": "computer_use_playbook",
                "selectedPlaybook": "github.star_repository",
            },
        },
        "createdAt": now,
        "updatedAt": now,
    }


def system_rpa_seed_templates() -> list[Dict[str, Any]]:
    return [github_star_repository_template()]


def ensure_system_rpa_seed_templates(script_store: RPAScriptStore = rpa_script_store) -> list[Dict[str, Any]]:
    saved: list[Dict[str, Any]] = []
    for template in system_rpa_seed_templates():
        template_id = str(template.get("id") or "").strip()
        if not template_id:
            continue
        existing = script_store.get_template(template_id)
        if isinstance(existing, dict):
            metadata = dict(existing.get("metadata") or {})
            existing_seed_version = int(metadata.get("seedVersion") or 0)
            if metadata.get("systemSeed") is True and existing_seed_version < GITHUB_STAR_TEMPLATE_SEED_VERSION:
                next_template = deepcopy(template)
                next_template["createdAt"] = existing.get("createdAt") or next_template.get("createdAt")
                saved.append(
                    script_store.save_template(
                        next_template,
                        history_reason="system_seed_upgrade",
                        history_actor="system",
                        write_history=True,
                    )
                )
            else:
                saved.append(existing)
            continue
        saved.append(
            script_store.save_template(
                deepcopy(template),
                history_reason="system_seed_install",
                history_actor="system",
                write_history=False,
            )
        )
    return saved
