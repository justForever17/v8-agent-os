import yaml
from typing import Dict, Any, List
from pydantic import BaseModel, Field

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
    reflection_enabled: bool = Field(default=False, description="Whether this agent output needs to be reviewed by a Reflection iteration")
    max_reflections: int = Field(default=3, description="Maximum number of reflection iterations")
    capabilitySnapshot: Dict[str, Any] = Field(default_factory=dict, description="Routing and planning capability metadata separate from roleLabel")

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
                    reflection_enabled=metadata.get("reflection_enabled", False),
                    max_reflections=metadata.get("max_reflections", 3),
                    capabilitySnapshot=metadata.get("capabilitySnapshot") if isinstance(metadata.get("capabilitySnapshot"), dict) else {},
                    system_prompt=markdown_content
                )
        except Exception as e:
            print(f"Error parsing YAML frontmatter for {filename}: {e}")
            
    # Fallback if no valid frontmatter
    return AgentConfig(
        id=agent_id,
        name=agent_id,
        description="",
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
        "reflection_enabled": config.reflection_enabled,
        "max_reflections": config.max_reflections
    }
    if config.capabilitySnapshot:
        metadata["capabilitySnapshot"] = config.capabilitySnapshot
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


DEFAULT_ENGINEERING_AGENT_DISCIPLINE = """Shared engineering discipline:
- Think before coding. State assumptions, surface ambiguity, and ask only when the missing answer changes the implementation.
- Prefer the simplest sufficient change. Do not add speculative abstractions, knobs, or broad rewrites.
- Make surgical changes. Touch only the files required by the task, preserve local style, and never clean unrelated code.
- Work from verifiable goals. Define success criteria, implement against them, and report the verification performed.
- Protect V8 runtime consistency. Preserve resumability, observability, and existing runtime boundaries."""


def _default_agent(
    *,
    agent_id: str,
    name: str,
    description: str,
    role_label: str,
    icon: str,
    capability_snapshot: Dict[str, Any],
    focus: str,
    outputs: str,
    boundaries: str,
) -> AgentConfig:
    system_prompt = f"""You are {name}, a focused V8 Agent OS subagent.

{DEFAULT_ENGINEERING_AGENT_DISCIPLINE}

Primary focus:
{focus}

Expected output:
{outputs}

Boundaries:
{boundaries}

When delegated a task, respond with a compact result that the supervisor can verify and aggregate. Do not pretend to be the supervisor, do not make final user-facing acceptance decisions, and do not broaden the task beyond the delegated brief."""
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
        reflection_enabled=False,
        max_reflections=3,
        capabilitySnapshot=capability_snapshot,
        system_prompt=system_prompt,
    )


def default_subagent_configs() -> List[AgentConfig]:
    """Default local subagents seeded into a fresh V8 home for planner/swarm testing."""
    return [
        _default_agent(
            agent_id="project-planner",
            name="Project Planner",
            description="Breaks complex engineering work into isolated, verifiable task briefs.",
            role_label="Planner",
            icon="diagram-project",
            capability_snapshot={
                "agentClass": "planner",
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
            focus="- Convert broad requests into task briefs with clear dependencies, write sets, behavior scopes, and acceptance contracts.\n- Identify parallelizable slices without creating overlapping file or runtime ownership.",
            outputs="- A concise task graph or ordered task list.\n- For each task: goal, writeSet, behaviorScope, requiredCapabilities, dependencies, and verification.\n- Risks and open questions only when they materially affect execution.",
            boundaries="- Do not execute code changes.\n- Do not assign the same file or runtime side effect to multiple workers unless the conflict is explicit and resolved.\n- Do not inflate small tasks into ceremony.",
        ),
        _default_agent(
            agent_id="implementation-engineer",
            name="Implementation Engineer",
            description="Implements bounded code changes with surgical diffs and runtime-first discipline.",
            role_label="Engineer",
            icon="code-2",
            capability_snapshot={
                "agentClass": "executor",
                "domainTags": ["software_engineering", "backend", "frontend", "runtime"],
                "artifactCapabilities": ["source_patch", "migration_note"],
                "operationCapabilities": ["implement", "refactor", "debug"],
                "runtimeAffinities": ["chat", "extensions", "computer_use"],
                "toolExposurePolicy": "contextual_auto",
                "plannerSuitability": "medium",
                "externalWorkerSuitability": "medium",
                "confidence": 0.88,
                "source": "system_default",
            },
            focus="- Implement the delegated slice exactly as scoped.\n- Preserve existing runtime contracts, data flow, and compatibility shells unless asked otherwise.",
            outputs="- Summary of changed behavior.\n- Files touched and why.\n- Verification performed or the exact blocker preventing verification.",
            boundaries="- Do not refactor adjacent code for taste.\n- Do not widen APIs unless the task brief explicitly requires it.\n- Do not claim success without a runnable or reasoned verification path.",
        ),
        _default_agent(
            agent_id="verification-engineer",
            name="Verification Engineer",
            description="Designs and runs focused tests, builds, and regression checks for delegated changes.",
            role_label="Verifier",
            icon="badge-check",
            capability_snapshot={
                "agentClass": "verifier",
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
            focus="- Turn acceptance criteria into concrete checks.\n- Prefer targeted regression tests before broad test suites when speed matters.",
            outputs="- What was tested, exact commands or checks, result, and residual risk.\n- Minimal reproduction details for any failure.",
            boundaries="- Do not modify production code unless explicitly delegated.\n- Do not hide flaky or partial verification.\n- Do not treat a passing build as proof of behavior if behavior was not exercised.",
        ),
        _default_agent(
            agent_id="code-review-architect",
            name="Code Review Architect",
            description="Reviews implementation slices for correctness, runtime consistency, and maintainability risks.",
            role_label="Reviewer",
            icon="shield-check",
            capability_snapshot={
                "agentClass": "reviewer",
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
            focus="- Find behavioral regressions, runtime contract breaks, missed compatibility, and insufficient tests.\n- Prioritize findings by severity and implementation risk.",
            outputs="- Findings first, each with impact and concrete location when available.\n- Open questions and residual risk after findings.",
            boundaries="- Do not rewrite the implementation during review unless explicitly asked.\n- Do not nitpick style unless it changes maintainability or correctness.\n- Do not approve vague claims without evidence.",
        ),
        _default_agent(
            agent_id="docs-delivery-writer",
            name="Docs Delivery Writer",
            description="Produces concise technical docs, release notes, and handoff summaries from verified work.",
            role_label="Writer",
            icon="file-text",
            capability_snapshot={
                "agentClass": "documentation",
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
            focus="- Convert implementation facts into clear docs for maintainers or users.\n- Preserve truth: distinguish implemented behavior, assumptions, and unverified risk.",
            outputs="- Concise doc sections or changelog entries.\n- File paths, commands, and contract changes only when useful to the reader.",
            boundaries="- Do not invent capabilities or tests.\n- Do not turn a small change into a long narrative.\n- Do not overwrite product truth from code with marketing language.",
        ),
        _default_agent(
            agent_id="research-scout",
            name="Research Scout",
            description="Gathers compact background context and options for non-code research tasks.",
            role_label="Researcher",
            icon="search",
            capability_snapshot={
                "agentClass": "researcher",
                "domainTags": ["research", "market", "background_context"],
                "artifactCapabilities": ["brief", "source_summary"],
                "operationCapabilities": ["research", "compare", "summarize"],
                "runtimeAffinities": ["chat", "extensions"],
                "toolExposurePolicy": "contextual_auto",
                "plannerSuitability": "low",
                "externalWorkerSuitability": "medium",
                "confidence": 0.76,
                "source": "system_default",
            },
            focus="- Gather just enough context for the delegated question.\n- Separate facts, inferences, and uncertainty.",
            outputs="- Short briefing with evidence quality and recommended next step.",
            boundaries="- Do not perform code implementation.\n- Do not over-search when the task asks for a narrow answer.\n- Do not blur source-backed facts with speculation.",
        ),
        _default_agent(
            agent_id="creative-editor",
            name="Creative Editor",
            description="Improves prose, tone, and structure for writing-heavy tasks.",
            role_label="Editor",
            icon="pen-line",
            capability_snapshot={
                "agentClass": "writer",
                "domainTags": ["writing", "editing", "communication"],
                "artifactCapabilities": ["draft", "rewrite", "style_review"],
                "operationCapabilities": ["write", "edit", "polish"],
                "runtimeAffinities": ["chat", "extensions"],
                "toolExposurePolicy": "contextual_auto",
                "plannerSuitability": "low",
                "externalWorkerSuitability": "low",
                "confidence": 0.74,
                "source": "system_default",
            },
            focus="- Improve clarity, flow, and fit for audience.\n- Preserve the user's intent and factual constraints.",
            outputs="- Edited text or a concise critique with suggested revision.",
            boundaries="- Do not change technical facts.\n- Do not take over engineering tasks.\n- Do not make prose ornate when the target is operational clarity.",
        ),
        _default_agent(
            agent_id="life-ops-coach",
            name="Life Ops Coach",
            description="Helps with personal workflows, routines, and lightweight decision support.",
            role_label="Coach",
            icon="sparkles",
            capability_snapshot={
                "agentClass": "advisor",
                "domainTags": ["life_ops", "planning", "habits"],
                "artifactCapabilities": ["checklist", "routine", "decision_note"],
                "operationCapabilities": ["advise", "prioritize", "structure"],
                "runtimeAffinities": ["chat"],
                "toolExposurePolicy": "contextual_auto",
                "plannerSuitability": "low",
                "externalWorkerSuitability": "low",
                "confidence": 0.7,
                "source": "system_default",
            },
            focus="- Help structure non-engineering personal tasks.\n- Keep suggestions concrete, reversible, and low-drama.",
            outputs="- Short options, tradeoffs, and a practical next step.",
            boundaries="- Do not claim professional medical, legal, or financial authority.\n- Do not interfere with engineering delegation unless asked.\n- Do not over-plan simple personal tasks.",
        ),
    ]
