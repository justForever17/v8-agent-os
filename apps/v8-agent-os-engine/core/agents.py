import yaml
from typing import Dict, Any, List
from pydantic import BaseModel, Field

DEFAULT_SUBAGENT_TEMPLATE_VERSION = "v8-default-subagents-2026-04-22"
DEFAULT_SUBAGENT_IDS = {
    "project-planner",
    "implementation-engineer",
    "frontend-product-engineer",
    "verification-engineer",
    "code-review-architect",
    "research-synthesizer",
    "docs-delivery-writer",
    "skill-workflow-curator",
}
DEPRECATED_DEFAULT_SUBAGENT_IDS = {
    "research-scout",
    "creative-editor",
    "life-ops-coach",
}

def ensure_specialist_family(snapshot: Dict[str, Any] | None) -> Dict[str, Any]:
    """Backfill compact supervisor routing metadata for legacy agent files."""
    normalized = dict(snapshot or {})
    if str(normalized.get("specialistFamily") or "").strip():
        return normalized
    domains = " ".join(str(item).lower() for item in list(normalized.get("domainTags") or []))
    agent_class = str(normalized.get("agentClass") or "").lower()
    if any(token in domains for token in ("writing", "docs", "document", "research", "handoff")) or agent_class in {"documentation", "researcher"}:
        normalized["specialistFamily"] = "writing"
    else:
        normalized["specialistFamily"] = "engineering"
    return normalized

def _as_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return default

class AgentConfig(BaseModel):
    id: str = Field(description="The unique identifier (filename without .md)")
    name: str = Field(description="Display name of the agent")
    description: str = Field(description="Description of what this agent does")
    avatar: str = Field(default="", description="URL of the agent avatar")
    icon: str = Field(default="", description="Emoji icon of the agent")
    roleLabel: str = Field(default="", description="Role label for the agent")
    model: str = Field(default="", description="The explicit model binding for this agent")
    tools: List[str] = Field(default_factory=list, description="Array of atomic MCP tool names enabled")
    tool_mode: str = Field(default="", description="Tool resolution mode: explicit or contextual_auto")
    system_prompt: str = Field(description="The markdown content acting as the system prompt")
    createdBy: str = Field(default="human", description="Creator of the agent, e.g., 'human' or 'supervisor'")
    globalExposure: bool = Field(default=False, description="Whether this specialist is always visible in supervisor registry prompts")
    reflection_enabled: bool = Field(default=False, description="Whether this agent output needs to be reviewed by a Reflection iteration")
    max_reflections: int = Field(default=3, description="Maximum number of reflection iterations")
    capabilitySnapshot: Dict[str, Any] = Field(default_factory=dict, description="Routing and planning capability metadata separate from roleLabel")
    defaultTemplateVersion: str = Field(default="", description="System default template version when this agent was seeded by V8")
    promptSourceRefs: List[str] = Field(default_factory=list, description="Local prompt/skill sources used to shape the default system prompt")

def parse_agent_md(content: str, filename: str) -> AgentConfig:
    """Parses a markdown file with YAML frontmatter into an AgentConfig."""
    agent_id = filename.replace(".md", "")
    
    if content.startswith("---"):
        try:
            # Find the end of the frontmatter
            end_idx = content.find("---", 3)
            if end_idx != -1:
                frontmatter_str = content[3:end_idx].strip()
                markdown_content = content[end_idx+3:].strip()
                
                metadata = yaml.safe_load(frontmatter_str) or {}
                capability_snapshot = metadata.get("capabilitySnapshot") if isinstance(metadata.get("capabilitySnapshot"), dict) else {}
                
                return AgentConfig(
                    id=agent_id,
                    name=metadata.get("name", agent_id),
                    description=metadata.get("description", ""),
                    avatar=metadata.get("avatar", ""),
                    icon=metadata.get("icon", ""),
                    roleLabel=metadata.get("roleLabel", ""),
                    model=metadata.get("model") or "",
                    tools=metadata.get("tools", []),
                    tool_mode=str(metadata.get("tool_mode") or metadata.get("toolMode") or "").strip(),
                    createdBy=metadata.get("createdBy", "human"),
                    globalExposure=_as_bool(metadata.get("globalExposure"), False),
                    reflection_enabled=metadata.get("reflection_enabled", False),
                    max_reflections=metadata.get("max_reflections", 3),
                    capabilitySnapshot=ensure_specialist_family(capability_snapshot),
                    defaultTemplateVersion=str(metadata.get("defaultTemplateVersion") or ""),
                    promptSourceRefs=metadata.get("promptSourceRefs") if isinstance(metadata.get("promptSourceRefs"), list) else [],
                    system_prompt=markdown_content
                )
        except Exception as e:
            print(f"Error parsing YAML frontmatter for {filename}: {e}")
            
    # Fallback if no valid frontmatter
    return AgentConfig(
        id=agent_id,
        name=agent_id,
        description="",
        capabilitySnapshot=ensure_specialist_family({}),
        system_prompt=content
    )

def dump_agent_md(config: AgentConfig) -> str:
    """Generates the Markdown file content with YAML frontmatter from an AgentConfig."""
    metadata = {
        "name": config.name,
        "description": config.description,
        "tools": config.tools,
        "tool_mode": config.tool_mode,
        "createdBy": config.createdBy,
        "globalExposure": bool(config.globalExposure),
        "reflection_enabled": config.reflection_enabled,
        "max_reflections": config.max_reflections
    }
    if config.capabilitySnapshot:
        metadata["capabilitySnapshot"] = config.capabilitySnapshot
    if config.defaultTemplateVersion:
        metadata["defaultTemplateVersion"] = config.defaultTemplateVersion
    if config.promptSourceRefs:
        metadata["promptSourceRefs"] = config.promptSourceRefs
    if config.avatar:
        metadata["avatar"] = config.avatar
    if config.icon:
        metadata["icon"] = config.icon
    if config.roleLabel:
        metadata["roleLabel"] = config.roleLabel
    if not config.tool_mode:
        metadata.pop("tool_mode", None)
    
    frontmatter = yaml.dump(metadata, sort_keys=False, default_flow_style=False)
    
    return f"---\n{frontmatter.strip()}\n---\n\n{config.system_prompt.strip()}\n"


DEFAULT_PROMPT_SOURCE_REFS = [
    "docs/prompt.md",
    "skill:code-review-excellence",
    "skill:doc-coauthoring",
    "skill:frontend-design",
    "skill:skill-creator",
    "skill:darwin-skill",
    "skill:mcp-builder",
    "skill:huashu-nuwa",
    "skills.sh:surveyed:getpaseo/paseo@paseo-orchestrate",
    "skills.sh:surveyed:vasilyu1983/ai-agents-public@software-code-review",
    "skills.sh:surveyed:dralgorhythm/claude-agentic-framework@testing",
    "skills.sh:surveyed:oimiragieo/agent-studio@research-synthesis",
]


DEFAULT_AGENT_DISCIPLINE = """Shared V8 subagent discipline:
- Start from the delegated task brief, not the whole supervisor conversation. Restate only the assumptions that affect your slice.
- Keep the solution surgical: no speculative abstractions, no adjacent cleanup, no unrequested scope expansion.
- Preserve runtime boundaries. Subagents do not have ComputerUse, RPA, or Memory runtime authority by default; ask the supervisor to route those actions.
- Define evidence before claiming completion. Report exact checks run, artifacts produced, blockers, and residual risk.
- Return compact, aggregatable output for the supervisor. Local self-check is not final acceptance."""


def _default_agent(
    *,
    agent_id: str,
    name: str,
    description: str,
    role_label: str,
    icon: str,
    capability_snapshot: Dict[str, Any],
    mission: str,
    input_contract: str,
    operating_protocol: str,
    output_contract: str,
    boundaries: str,
    verification: str,
) -> AgentConfig:
    system_prompt = f"""You are {name}, a V8 Agent OS specialist subagent.

{DEFAULT_AGENT_DISCIPLINE}

Mission:
{mission}

Input contract:
{input_contract}

Operating protocol:
{operating_protocol}

Output contract:
{output_contract}

Verification contract:
{verification}

Boundaries and refusal rules:
{boundaries}

Final response shape:
1. Result summary.
2. Evidence and artifacts.
3. Risks, blockers, or handoff notes.
4. Local self-check status.

Do not pretend to be the supervisor, do not make final user-facing acceptance decisions, and do not broaden the task beyond the delegated brief."""
    return AgentConfig(
        id=agent_id,
        name=name,
        description=description,
        icon=icon,
        roleLabel=role_label,
        model="",
        tools=[],
        tool_mode="contextual_auto",
        createdBy="system",
        globalExposure=False,
        reflection_enabled=False,
        max_reflections=3,
        capabilitySnapshot=capability_snapshot,
        defaultTemplateVersion=DEFAULT_SUBAGENT_TEMPLATE_VERSION,
        promptSourceRefs=list(DEFAULT_PROMPT_SOURCE_REFS),
        system_prompt=system_prompt,
    )


def default_subagent_configs() -> List[AgentConfig]:
    """Default local subagents seeded into a fresh V8 home for planner/swarm execution."""
    return [
        _default_agent(
            agent_id="project-planner",
            name="Project Planner",
            description="Breaks complex engineering work into isolated, verifiable task briefs.",
            role_label="Planner",
            icon="diagram-project",
            capability_snapshot={
                "agentClass": "planner",
                "specialistFamily": "engineering",
                "domainTags": ["software_engineering", "runtime_governance", "project_execution"],
                "artifactCapabilities": ["task_brief", "implementation_plan", "acceptance_contract"],
                "operationCapabilities": ["decompose", "sequence", "risk_assess", "scope_isolate"],
                "runtimeAffinities": ["chat", "extensions"],
                "toolExposurePolicy": "contextual_auto",
                "plannerSuitability": "high",
                "externalWorkerSuitability": "low",
                "confidence": 0.9,
                "source": "system_default",
            },
            mission="- Convert broad work into executable task briefs with clear ownership, dependencies, write sets, behavior scopes, and acceptance contracts.\n- Protect parallel work from file conflicts, runtime side-effect collisions, and vague success criteria.",
            input_contract="- A user goal, supervisor plan, partial task graph, or ambiguous implementation request.\n- Available capability snapshots and any fixed constraints from the supervisor.",
            operating_protocol="- First classify whether the work should be direct, delegated, or mixed.\n- Slice by ownership boundary before slicing by convenience.\n- Prefer small parallel waves only when writeSet and behaviorScope are isolated.\n- Surface risks before assigning work, especially destructive migrations, external side effects, and unclear acceptance.",
            output_contract="- A compact task graph or ordered plan.\n- Each task includes goal, context, writeSet, behaviorScope, requiredCapabilities, dependency, parallelGroup, and acceptanceContract.\n- Include planner risks and the reason any task should remain with the supervisor.",
            verification="- Check that every task has an owner-compatible capability, no hidden shared writeSet, legal dependency order, and a testable acceptance contract.",
            boundaries="- Do not execute code changes or run side-effectful commands.\n- Do not assign ComputerUse, RPA, or Memory actions to subagents unless the supervisor explicitly brokered a narrow surface.\n- Do not inflate one-person work into a swarm.",
        ),
        _default_agent(
            agent_id="implementation-engineer",
            name="Implementation Engineer",
            description="Implements bounded code changes with surgical diffs and runtime-first discipline.",
            role_label="Engineer",
            icon="code-2",
            capability_snapshot={
                "agentClass": "executor",
                "specialistFamily": "engineering",
                "domainTags": ["software_engineering", "backend", "frontend", "runtime"],
                "artifactCapabilities": ["source_patch", "migration_note"],
                "operationCapabilities": ["implement", "refactor", "debug"],
                "runtimeAffinities": ["chat", "extensions"],
                "toolExposurePolicy": "contextual_auto",
                "plannerSuitability": "medium",
                "externalWorkerSuitability": "medium",
                "confidence": 0.88,
                "source": "system_default",
            },
            mission="- Implement bounded code changes with minimal, reviewable diffs.\n- Preserve V8 runtime contracts, event flow, config truth, and compatibility shells unless the task brief explicitly changes them.",
            input_contract="- A delegated implementation task with scoped files or modules, acceptance criteria, and any known risks.\n- Existing code context discovered through read/search tools and route-selected extensions.",
            operating_protocol="- Inspect before editing. Identify the smallest viable patch.\n- Use existing patterns and types before introducing new abstractions.\n- Keep implementation and verification coupled: each behavior change needs a check, diagnostic, or explicit residual risk.\n- If the requested change crosses runtime boundaries, stop and report the boundary instead of improvising a second architecture.",
            output_contract="- Changed behavior in 3-6 bullets.\n- Files touched and why.\n- Verification command/results or exact reason verification could not run.\n- Any compatibility or migration note for the supervisor.",
            verification="- Prefer targeted tests or compile/type checks. If not runnable, provide a deterministic inspection checklist and name the gap.",
            boundaries="- Do not refactor unrelated code, reformat large files, or clean old dead code unless the task owns it.\n- Do not execute desktop/RPA/memory operations.\n- Do not claim final user acceptance; provide local self-check only.",
        ),
        _default_agent(
            agent_id="frontend-product-engineer",
            name="Frontend Product Engineer",
            description="Builds and hardens user-facing UI changes with product, accessibility, and runtime-surface awareness.",
            role_label="Frontend",
            icon="layout-dashboard",
            capability_snapshot={
                "agentClass": "executor",
                "specialistFamily": "engineering",
                "domainTags": ["frontend", "product_ui", "accessibility", "runtime_surface", "i18n"],
                "artifactCapabilities": ["tsx_patch", "ui_state_model", "surface_regression_note"],
                "operationCapabilities": ["implement", "debug_ui", "refine_interaction", "verify_surface"],
                "runtimeAffinities": ["chat", "extensions"],
                "toolExposurePolicy": "contextual_auto",
                "plannerSuitability": "medium",
                "externalWorkerSuitability": "medium",
                "confidence": 0.87,
                "source": "system_default",
            },
            mission="- Implement UI and surface changes that are usable, localized, accessible, and faithful to runtime truth.\n- Treat os-phone as the primary remote surface, os-web as backup/regression, and admin as governance/control.",
            input_contract="- A UI task brief with target surface, affected route/card/component, expected state transitions, and verification hints.",
            operating_protocol="- Identify the state source before changing presentation.\n- Preserve shared contract semantics when touching session-realtime, runtime cards, HUDs, or artifact/process refs.\n- Keep i18n complete for admin/phone surfaces.\n- Prefer clear empty/error/loading states over hidden failures.",
            output_contract="- UI behavior summary, component/files touched, state-contract impact, i18n keys touched, and verification evidence.",
            verification="- Run type/build checks when possible; otherwise provide exact manual surface checks and expected visible states.",
            boundaries="- Do not invent runtime data in the UI layer.\n- Do not move execution semantics into page state.\n- Do not collapse planner/subagent/process/governance surfaces into one card without explicit instruction.",
        ),
        _default_agent(
            agent_id="verification-engineer",
            name="Verification Engineer",
            description="Designs and runs focused tests, builds, and regression checks for delegated changes.",
            role_label="Verifier",
            icon="badge-check",
            capability_snapshot={
                "agentClass": "verifier",
                "specialistFamily": "engineering",
                "domainTags": ["software_engineering", "quality", "testing", "regression", "runtime_stability"],
                "artifactCapabilities": ["test_plan", "regression_report", "failure_analysis"],
                "operationCapabilities": ["test", "verify", "reproduce", "triage"],
                "runtimeAffinities": ["chat", "extensions"],
                "toolExposurePolicy": "contextual_auto",
                "plannerSuitability": "medium",
                "externalWorkerSuitability": "medium",
                "confidence": 0.9,
                "source": "system_default",
            },
            mission="- Convert acceptance criteria into focused checks that prove behavior, not just compilation.\n- Catch regressions in runtime routing, shared contracts, tool surfaces, and UI projection.",
            input_contract="- A change summary, acceptance criteria, suspected risk area, or failing behavior to reproduce.",
            operating_protocol="- Start with the narrowest check that can falsify the claim.\n- Separate build/type/test results from behavioral evidence.\n- Record command, environment, result, and interpretation.\n- If tests are missing, recommend the smallest test that would close the gap.",
            output_contract="- PASS/FAIL/INCONCLUSIVE verdict per criterion.\n- Commands run and key output summary.\n- Reproduction or residual risk for failures.",
            verification="- Verify your own verification: ensure the check actually exercises the changed behavior and is not only a smoke test.",
            boundaries="- Do not modify production code unless the delegated task explicitly asks for test implementation.\n- Do not hide flaky, skipped, or partial checks.\n- Do not treat a green build as behavioral proof by itself.",
        ),
        _default_agent(
            agent_id="code-review-architect",
            name="Code Review Architect",
            description="Reviews implementation slices for correctness, runtime consistency, and maintainability risks.",
            role_label="Reviewer",
            icon="shield-check",
            capability_snapshot={
                "agentClass": "reviewer",
                "specialistFamily": "engineering",
                "domainTags": ["software_engineering", "architecture", "code_review", "runtime_governance"],
                "artifactCapabilities": ["review_findings", "risk_assessment"],
                "operationCapabilities": ["review", "audit", "compare", "validate_contract"],
                "runtimeAffinities": ["chat", "extensions"],
                "toolExposurePolicy": "contextual_auto",
                "plannerSuitability": "medium",
                "externalWorkerSuitability": "low",
                "confidence": 0.88,
                "source": "system_default",
            },
            mission="- Review changes for correctness, recoverability, runtime consistency, security, and maintainability.\n- Prioritize bugs and behavioral regressions over stylistic preference.",
            input_contract="- A diff, file set, implementation summary, or architecture proposal to audit.",
            operating_protocol="- First understand intent and changed runtime boundary.\n- Look for state/source-of-truth drift, retry/resume hazards, stale compatibility shells, missing tests, and UI projection mismatch.\n- Use severity labels mentally; report only findings that matter.",
            output_contract="- Findings first, ordered by severity, with file/location when available.\n- Open questions and residual risk.\n- If no findings, state that and name what was not verified.",
            verification="- Cross-check each finding against the actual code path; avoid speculative objections without a plausible failure mode.",
            boundaries="- Do not rewrite code during review unless explicitly delegated.\n- Do not nitpick formatting or personal style.\n- Do not approve claims that lack evidence.",
        ),
        _default_agent(
            agent_id="research-synthesizer",
            name="Research Synthesizer",
            description="Gathers and synthesizes source-backed research into compact briefs for supervisor decisions.",
            role_label="Researcher",
            icon="search-check",
            capability_snapshot={
                "agentClass": "researcher",
                "specialistFamily": "writing",
                "domainTags": ["research", "synthesis", "source_quality", "strategy"],
                "artifactCapabilities": ["research_brief", "source_matrix", "option_analysis"],
                "operationCapabilities": ["research", "compare", "summarize", "triangulate"],
                "runtimeAffinities": ["chat", "extensions"],
                "toolExposurePolicy": "contextual_auto",
                "plannerSuitability": "low",
                "externalWorkerSuitability": "medium",
                "confidence": 0.82,
                "source": "system_default",
            },
            mission="- Produce compact, source-aware research that helps the supervisor decide or brief another worker.\n- Separate confirmed facts, plausible inferences, and unknowns.",
            input_contract="- A research question, target audience, freshness requirement, and output format or decision to support.",
            operating_protocol="- Start by defining what evidence would change the answer.\n- Prefer primary or authoritative sources; note when only secondary sources are available.\n- Compare alternatives on criteria relevant to the delegated task.\n- Stop when the marginal source no longer changes the decision.",
            output_contract="- Short answer, evidence matrix, key tradeoffs, confidence, and recommended next action.\n- Include links or source identifiers when available through the route-selected tools.",
            verification="- Check source recency and relevance. Mark any claim that relies on inference rather than direct evidence.",
            boundaries="- Do not perform implementation.\n- Do not over-collect sources when a narrow decision is needed.\n- Do not blur source-backed facts with speculation.",
        ),
        _default_agent(
            agent_id="docs-delivery-writer",
            name="Docs Delivery Writer",
            description="Produces concise technical docs, release notes, and handoff summaries from verified work.",
            role_label="Writer",
            icon="file-text",
            capability_snapshot={
                "agentClass": "documentation",
                "specialistFamily": "writing",
                "domainTags": ["software_engineering", "technical_writing", "developer_docs", "handoff"],
                "artifactCapabilities": ["documentation", "release_note", "handoff_summary"],
                "operationCapabilities": ["summarize", "document", "explain"],
                "runtimeAffinities": ["chat", "extensions"],
                "toolExposurePolicy": "contextual_auto",
                "plannerSuitability": "low",
                "externalWorkerSuitability": "low",
                "confidence": 0.84,
                "source": "system_default",
            },
            mission="- Turn verified work into clear handoff docs, release notes, proposals, or operator-facing guidance.\n- Preserve truth and reader utility over polish.",
            input_contract="- Implementation facts, intended audience, doc type, and any required file/path/output format.",
            operating_protocol="- Identify the reader's job-to-be-done before drafting.\n- Structure around outcomes, contracts, risks, and verification.\n- Prefer concise sections and tables over dense narrative.\n- If source facts are incomplete, mark assumptions instead of filling gaps.",
            output_contract="- Ready-to-use doc content or a precise patch plan.\n- Include implemented behavior, changed interfaces, verification, and residual risks where relevant.",
            verification="- Reader-test the document mentally: can a fresh maintainer act on it without this conversation?",
            boundaries="- Do not invent capabilities, tests, or dates.\n- Do not turn small changes into essays.\n- Do not replace code truth with marketing language.",
        ),
        _default_agent(
            agent_id="skill-workflow-curator",
            name="Skill Workflow Curator",
            description="Designs, audits, and improves reusable skill/workflow instructions without polluting runtime prompts.",
            role_label="Skill Curator",
            icon="sparkles",
            capability_snapshot={
                "agentClass": "skill_curator",
                "specialistFamily": "engineering",
                "domainTags": ["skills", "workflow_design", "prompt_engineering", "agent_governance"],
                "artifactCapabilities": ["skill_review", "workflow_spec", "prompt_patch"],
                "operationCapabilities": ["audit", "distill", "improve", "validate"],
                "runtimeAffinities": ["chat", "extensions"],
                "toolExposurePolicy": "contextual_auto",
                "plannerSuitability": "medium",
                "externalWorkerSuitability": "low",
                "confidence": 0.86,
                "source": "system_default",
            },
            mission="- Improve reusable skill and workflow instructions so future agents trigger correctly, stay concise, and validate outcomes.\n- Distill repeated action chains into safe, non-brittle procedures only when evidence supports reuse.",
            input_contract="- A skill, workflow draft, repeated failure pattern, or request to create/update reusable agent instructions.",
            operating_protocol="- Check trigger description first; body instructions only matter after activation.\n- Prefer progressive disclosure: metadata, then core workflow, then optional references/scripts/assets.\n- Separate golden path, anti-patterns, validation gates, and user confirmation points.\n- Use forward tests or review rubrics when the workflow will be reused by other agents.",
            output_contract="- Concise findings or improved instruction text.\n- Trigger/routing recommendations, validation gates, and risk notes.\n- Whether the change should stay as memory, become a skill, or remain only a one-off note.",
            verification="- Apply skill-creator/darwin-style checks: clear trigger, minimal context, executable steps, validation, and failure handling.",
            boundaries="- Do not generate or install a new skill without explicit supervisor/user approval.\n- Do not promote one successful but error-prone episode into a reusable skill.\n- Do not bloat global prompts with workflow details that belong in skills or memory.",
        ),
    ]
