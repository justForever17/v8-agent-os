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
        "desiredState": intent.get("desiredState") or "starred",
        "failureBudget": 2,
        "steps": [
            {"stage": "open", "lane": lane.get("lane"), "url": repo.get("url")},
            {"stage": "precheck", "assert": "repo page canonical owner/name and current star state"},
            {"stage": "act", "if": "state differs from desired_state and authenticated", "action": "click strict Star/Unstar button"},
            {"stage": "verify", "assert": "strict DOM button state equals desired_state"},
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
    desired_state = str(intent.get("desiredState") or "starred")
    verifier = {
        "type": "dom_or_visible_state",
        "successCondition": "Starred" if desired_state == "starred" else "Not Starred",
        "desiredState": desired_state,
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
        "  const normalized = (value) => String(value || '').trim();\n"
        "  const ownText = (el) => [el.getAttribute('aria-label'), el.getAttribute('title'), el.value, el.textContent].map(normalized).filter(Boolean).join(' ');\n"
        "  const visible = (el) => { const rect = el.getBoundingClientRect(); const style = getComputedStyle(el); return rect.width > 1 && rect.height > 1 && style.visibility !== 'hidden' && style.display !== 'none' && !el.closest('[hidden],[aria-hidden=\"true\"]'); };\n"
        "  const controlLike = Array.from(document.querySelectorAll('button, a, [role=\"button\"], input[type=\"submit\"], input[type=\"button\"]'));\n"
        "  const controls = controlLike.map((el) => {\n"
        "    const form = el.closest('form');\n"
        "    const action = form ? normalized(form.getAttribute('action')) : '';\n"
        "    const label = ownText(el);\n"
        "    const lower = `${label} ${action}`.toLowerCase();\n"
        "    const isRepoStar = /\\bstar\\b|\\bstarred\\b|\\bunstar\\b/.test(lower) && !/fork|watch|sponsor|starred by|stargazer/.test(lower);\n"
        "    return { el, tag: el.tagName, role: el.getAttribute('role'), label, action, isRepoStar, visible: visible(el) };\n"
        "  }).filter((item) => item.isRepoStar);\n"
        "  const visibleControls = controls.filter((item) => item.visible);\n"
        "  const pool = visibleControls.length ? visibleControls : controls;\n"
        "  const star = pool.find((item) => /\\bunstar\\b|\\bstarred\\b/i.test(item.label + ' ' + item.action)) || pool.find((item) => /\\bstar\\b/i.test(item.label + ' ' + item.action)) || null;\n"
        "  const text = document.body ? document.body.innerText : '';\n"
        "  const signedInHints = !!document.querySelector('summary[aria-label*=\"View profile\"], meta[name=\"user-login\"], [aria-label*=\"Signed in\"]');\n"
        "  const starLabel = star ? star.label : '';\n"
        "  const starAction = star ? star.action : '';\n"
        "  const controlHay = `${starLabel} ${starAction}`;\n"
        "  const needsLoginForStar = /must be signed in|sign in to star|sign in to your account/i.test(controlHay + ' ' + text) || /\\/login\\b/i.test(location.pathname);\n"
        "  const loggedOut = needsLoginForStar || (!signedInHints && /Sign in|Sign up|Join GitHub/i.test(text));\n"
        "  let strictDomState = 'unknown';\n"
        "  if (star) {\n"
        "    if (/\\bunstar\\b|\\bstarred\\b/i.test(controlHay)) strictDomState = 'starred';\n"
        "    else if (/\\bstar\\b/i.test(controlHay)) strictDomState = 'not_starred';\n"
        "  }\n"
        "  const isStarred = strictDomState === 'starred';\n"
        "  const repoPath = location.pathname.split('/').filter(Boolean).slice(0, 2).join('/');\n"
        "  return {\n"
        "    url: location.href,\n"
        "    title: document.title,\n"
        "    repoPath,\n"
        "    loggedOut,\n"
        "    needsLoginForStar,\n"
        "    starLabel,\n"
        "    starAction,\n"
        "    strictDomState,\n"
        "    isStarred,\n"
        "    hasStarTarget: !!star,\n"
        "    starControlVisible: star ? !!star.visible : false,\n"
        "    starControlTag: star ? star.tag : null,\n"
        "    starControlRole: star ? star.role : null,\n"
        "    starControlEvidence: star ? { label: star.label, action: star.action } : null\n"
        "  };\n"
        "})()"
    )


def github_star_click_script(*, desired_state: str = "starred") -> str:
    target_state = "unstarred" if str(desired_state or "").strip().lower() in {"unstarred", "not_starred", "not-starred", "unstar", "remove_star", "remove-star"} else "starred"
    mode_json = "unstarred" if target_state == "unstarred" else "starred"
    return (
        "(() => {\n"
        f"  const desiredState = {mode_json!r};\n"
        "  const normalized = (value) => String(value || '').trim();\n"
        "  const ownText = (el) => [el.getAttribute('aria-label'), el.getAttribute('title'), el.value, el.textContent].map(normalized).filter(Boolean).join(' ');\n"
        "  const visible = (el) => { const rect = el.getBoundingClientRect(); const style = getComputedStyle(el); return rect.width > 1 && rect.height > 1 && style.visibility !== 'hidden' && style.display !== 'none' && !el.closest('[hidden],[aria-hidden=\"true\"]'); };\n"
        "  const controlLike = Array.from(document.querySelectorAll('button, a, [role=\"button\"], input[type=\"submit\"], input[type=\"button\"]'));\n"
        "  const candidates = controlLike.map((el) => {\n"
        "    const form = el.closest('form');\n"
        "    const action = form ? normalized(form.getAttribute('action')) : '';\n"
        "    const label = ownText(el);\n"
        "    return { el, form, label, action, hay: `${label} ${action}`, visible: visible(el) };\n"
        "  }).filter((item) => item.visible);\n"
        "  const target = desiredState === 'unstarred'\n"
        "    ? candidates.find((item) => /\\bunstar\\b|\\bstarred\\b/i.test(item.hay) && !/must be signed in|sign in to star|fork|watch|sponsor|stargazer/i.test(item.hay)) || null\n"
        "    : candidates.find((item) => /\\bstar\\b/i.test(item.hay) && !/\\bstarred\\b|\\bunstar\\b|must be signed in|sign in to star|fork|watch|sponsor|stargazer/i.test(item.hay)) || null;\n"
        "  if (!target) return { ok: false, reason: desiredState === 'unstarred' ? 'unstar_button_not_found_or_not_strictly_clickable' : 'star_button_not_found_or_not_strictly_clickable' };\n"
        "  target.el.scrollIntoView({ block: 'center', inline: 'center' });\n"
        "  const rect = target.el.getBoundingClientRect();\n"
        "  target.el.click();\n"
        "  if (target.form && typeof target.form.requestSubmit === 'function') setTimeout(() => { try { target.form.requestSubmit(target.el); } catch (_) {} }, 0);\n"
        "  return { ok: true, desiredState, text: target.label, action: target.action, visible: target.visible, x: rect.x + rect.width / 2, y: rect.y + rect.height / 2 };\n"
        "})()"
    )
