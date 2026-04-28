from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict

from runtimes.computer_use.fact_resolver import classify_goal, resolve_goal_facts
from runtimes.computer_use.playbooks import built_in_playbook_seeds


FactSearch = Callable[[str], Any]


@dataclass(slots=True)
class ComputerUseTaskLoop:
    goal: str
    intent: dict[str, Any]
    factEvidence: list[dict[str, Any]]
    domain: dict[str, Any]
    laneDecision: dict[str, Any]
    plan: dict[str, Any]
    verifier: dict[str, Any]
    recordResume: dict[str, Any]
    status: str
    humanAttentionReason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "version": 1,
            "stages": [
                "intent_normalizer",
                "fact_resolver",
                "domain_resolver",
                "lane_router",
                "planner",
                "actor",
                "verifier",
                "record_resume",
            ],
            "goal": self.goal,
            "intent": self.intent,
            "factEvidence": self.factEvidence,
            "domain": self.domain,
            "laneDecision": self.laneDecision,
            "plan": self.plan,
            "verifier": self.verifier,
            "recordResume": self.recordResume,
            "status": self.status,
            "humanAttentionReason": self.humanAttentionReason,
        }


def normalize_intent(goal: str) -> dict[str, Any]:
    return classify_goal(goal)


def resolve_facts(intent: dict[str, Any], *, web_searcher: FactSearch | None = None) -> list[dict[str, Any]]:
    result = resolve_goal_facts(str(intent.get("rawGoal") or ""), intent=intent, web_searcher=web_searcher)
    return [dict(item) for item in result.evidence]


def select_playbook(intent: dict[str, Any]) -> dict[str, Any] | None:
    for playbook in built_in_playbook_seeds():
        if (
            str(playbook.get("domain")) == str(intent.get("domain"))
            and str(playbook.get("operation")) == str(intent.get("operation"))
        ):
            selected = dict(playbook)
            selected["status"] = "selected"
            return selected
    return None


def route_lane(
    *,
    intent: dict[str, Any],
    playbook: dict[str, Any] | None,
    browser_decision: dict[str, Any] | None = None,
) -> dict[str, Any]:
    preferred_lane = str((playbook or {}).get("preferredLane") or "").strip()
    browser_payload = dict(browser_decision or {})
    if preferred_lane == "browser_cdp_dom":
        if browser_payload.get("available"):
            return {
                "lane": "browser_cdp_dom",
                "status": "selected",
                "reason": browser_payload.get("reason") or "browser_lane_available",
                "browserDecision": browser_payload,
            }
        return {
            "lane": "visual_locator",
            "status": "fallback_or_human_attention",
            "reason": browser_payload.get("reason") or "browser_lane_unavailable",
            "browserDecision": browser_payload,
            "fallbackLane": (playbook or {}).get("fallbackLane"),
        }
    if preferred_lane:
        return {"lane": preferred_lane, "status": "selected", "reason": "playbook_preferred_lane"}
    return {"lane": "visual_locator", "status": "selected", "reason": "no_browser_playbook"}


def build_plan(
    *,
    intent: dict[str, Any],
    playbook: dict[str, Any] | None,
    facts: list[dict[str, Any]],
    lane: dict[str, Any],
) -> dict[str, Any]:
    if intent.get("operation") != "star_repository" or not playbook:
        if not playbook:
            return {"status": "not_applicable", "steps": []}
        required = bool((playbook.get("factResolution") or {}).get("required"))
        target = next((item for item in facts if item.get("url") or item.get("kind") == "login_boundary"), None)
        if required and not target:
            return {
                "status": "blocked_before_gui",
                "reason": "canonical_target_not_resolved",
                "steps": [],
            }
        return {
            "status": "ready",
            "selectedPlaybook": playbook.get("id"),
            "targetUrl": (target or {}).get("url"),
            "failureBudget": 2,
            "steps": [
                {"stage": "resolve", "lane": lane.get("lane"), "target": target or {}},
                {"stage": "act", "action": intent.get("operation")},
                {"stage": "verify", "assert": "playbook specific success state"},
            ],
        }
    repo = next((item for item in facts if item.get("kind") == "canonical_github_repo"), None)
    if not repo:
        return {
            "status": "blocked_before_gui",
            "reason": "canonical_repo_url_not_resolved",
            "steps": [],
        }
    return {
        "status": "ready",
        "selectedPlaybook": playbook.get("id"),
        "targetUrl": repo.get("url"),
        "failureBudget": 2,
        "steps": [
            {"stage": "open", "lane": lane.get("lane"), "url": repo.get("url")},
            {"stage": "precheck", "assert": "repo page canonical owner/name and current star state"},
            {"stage": "act", "if": "not starred and authenticated", "action": "click Star button"},
            {"stage": "verify", "assert": "button state becomes Starred"},
        ],
    }


def build_record_resume(loop_id: str, *, playbook: dict[str, Any] | None, facts: list[dict[str, Any]], lane: dict[str, Any]) -> dict[str, Any]:
    return {
        "loopId": loop_id,
        "selectedPlaybook": (playbook or {}).get("id"),
        "factEvidenceSummary": [
            {key: item.get(key) for key in ("kind", "url", "source", "confidence") if key in item}
            for item in facts
        ],
        "laneDecision": {key: lane.get(key) for key in ("lane", "status", "reason")},
        "resumePolicy": "resume_from_fact_and_lane_summary_not_full_screenshot_history",
    }


def prepare_task_loop(
    goal: str,
    *,
    browser_decision: dict[str, Any] | None = None,
    web_searcher: FactSearch | None = None,
    loop_id: str = "computer_use_task_loop",
) -> ComputerUseTaskLoop:
    intent = normalize_intent(goal)
    facts = resolve_facts(intent, web_searcher=web_searcher)
    playbook = select_playbook(intent)
    lane = route_lane(intent=intent, playbook=playbook, browser_decision=browser_decision)
    plan = build_plan(intent=intent, playbook=playbook, facts=facts, lane=lane)
    if playbook and plan.get("status") == "blocked_before_gui":
        status = "needs_fact_resolution"
        human_attention = str(plan.get("reason") or "canonical_target_not_resolved")
    elif playbook and plan.get("status") == "ready" and lane.get("status") == "selected":
        status = "ready"
        human_attention = None
    elif playbook and plan.get("status") == "ready":
        status = "needs_human_attention"
        human_attention = str(lane.get("reason") or "preferred_lane_unavailable")
    elif playbook:
        status = "blocked"
        human_attention = str(plan.get("reason") or "playbook_not_ready")
    else:
        status = "generic_planner"
        human_attention = None
    verifier = {
        "type": "dom_or_visible_state",
        "successCondition": "Starred",
        "notEnough": ["opened_github_home", "opened_repo_without_starred_state"],
    } if intent.get("operation") == "star_repository" else {"type": "generic_post_action_verify"}
    return ComputerUseTaskLoop(
        goal=str(goal or "").strip(),
        intent=intent,
        factEvidence=facts,
        domain={"selectedPlaybook": playbook.get("id") if playbook else None, "playbook": playbook},
        laneDecision=lane,
        plan=plan,
        verifier=verifier,
        recordResume=build_record_resume(loop_id, playbook=playbook, facts=facts, lane=lane),
        status=status,
        humanAttentionReason=human_attention,
    )


def github_star_dom_probe_script() -> str:
    return (
        "(() => {\n"
        "  const text = document.body ? document.body.innerText : '';\n"
        "  const normalized = (value) => String(value || '').trim();\n"
        "  const buttonLike = Array.from(document.querySelectorAll('button, a, [role=\"button\"]'));\n"
        "  const star = buttonLike.find((el) => {\n"
        "    const hay = [el.textContent, el.getAttribute('aria-label'), el.getAttribute('title'), el.value].map(normalized).join(' ');\n"
        "    return /\\bStarred\\b|\\bUnstar\\b|\\bStar\\b/i.test(hay);\n"
        "  }) || null;\n"
        "  const starLabel = star ? [star.textContent, star.getAttribute('aria-label'), star.getAttribute('title'), star.value].map(normalized).filter(Boolean).join(' ') : '';\n"
        "  const signedInHints = !!document.querySelector('summary[aria-label*=\"View profile\"], meta[name=\"user-login\"], [aria-label*=\"Signed in\"]');\n"
        "  const needsLoginForStar = /must be signed in|sign in to star|sign in to your account/i.test(starLabel + ' ' + text) || /\\/login\\b/i.test(location.pathname);\n"
        "  const loggedOut = needsLoginForStar || (!signedInHints && /Sign in|Sign up|Join GitHub/i.test(text));\n"
        "  const isStarred = /\\bStarred\\b|\\bUnstar\\b/i.test(starLabel);\n"
        "  return { url: location.href, title: document.title, loggedOut, needsLoginForStar, starLabel, isStarred, hasStarTarget: !!star };\n"
        "})()"
    )


def github_star_click_script() -> str:
    return (
        "(() => {\n"
        "  const normalized = (value) => String(value || '').trim();\n"
        "  const buttonLike = Array.from(document.querySelectorAll('button, a, [role=\"button\"]'));\n"
        "  const target = buttonLike.find((el) => {\n"
        "    const hay = [el.textContent, el.getAttribute('aria-label'), el.getAttribute('title'), el.value].map(normalized).join(' ');\n"
        "    return /\\bStar\\b/i.test(hay) && !/\\bStarred\\b|\\bUnstar\\b|must be signed in|sign in to star/i.test(hay);\n"
        "  }) || null;\n"
        "  if (!target) return { ok: false, reason: 'star_button_not_found_or_already_starred' };\n"
        "  target.scrollIntoView({ block: 'center', inline: 'center' });\n"
        "  const rect = target.getBoundingClientRect();\n"
        "  target.click();\n"
        "  return { ok: true, text: normalized(target.textContent || target.getAttribute('aria-label') || target.getAttribute('title')), x: rect.x + rect.width / 2, y: rect.y + rect.height / 2 };\n"
        "})()"
    )
