import hashlib
import json
import re
import yaml
from typing import Dict, Any, List
from pydantic import BaseModel, Field

from core.time_truth import utc_now_iso

DEFAULT_SUBAGENT_TEMPLATE_VERSION = "v8-default-subagents-2026-09-17-professional-personas"
FREELANCERS_SPECIALIST_FAMILY_ID = "freelancers"
DEFAULT_SUBAGENT_IDS = {
    "implementation-engineer",
    "frontend-product-engineer",
    "verification-engineer",
    "code-review-architect",
    "web-research-architect",
    "research-synthesizer",
    "docs-delivery-writer",
    "skill-workflow-curator",
    "creative-media-director",
    "visual-recipe-engineer",
    "psd-layer-compositor",
    "character-continuity-designer",
    "motion-shot-director",
    "audio-post-producer",
}
DEPRECATED_DEFAULT_SUBAGENT_IDS = {
    "project-planner",
    "research-scout",
    "creative-editor",
    "life-ops-coach",
}
DEFAULT_SPECIALIST_FAMILIES = [
    {
        "familyId": FREELANCERS_SPECIALIST_FAMILY_ID,
        "displayName": "Freelancers",
        "aliases": ["通用协作", "general", "generalist", "freelance"],
        "description": "General-purpose collaborators that are not bound to a specialist family yet.",
    },
    {
        "familyId": "engineering",
        "displayName": "Engineering",
        "aliases": ["工程", "coding", "project_coding"],
        "description": "Code, architecture, tests, migration, debugging, and repository implementation work.",
    },
    {
        "familyId": "creative_media",
        "displayName": "Creative Media",
        "aliases": ["创意媒体", "media", "multimedia"],
        "description": "Image, video, voice, music brief, layered PSD/source assets, recipe, asset, and post-production specialist work.",
    },
    {
        "familyId": "writing",
        "displayName": "Writing",
        "aliases": ["写作", "docs", "documentation"],
        "description": "Documentation, research synthesis, handoff, proposals, and narrative delivery.",
    },
    {
        "familyId": "research",
        "displayName": "Research",
        "aliases": ["搜索", "调研", "web_research", "source_quality"],
        "description": "Web research planning, source ranking, evidence bundles, confidence, and citation synthesis.",
    },
]
_SPECIALIST_FAMILY_SORT_ORDER = {
    "engineering": 0,
    "research": 1,
    "creative_media": 2,
    "writing": 3,
    FREELANCERS_SPECIALIST_FAMILY_ID: 4,
}


def normalize_specialist_family_id(value: Any, *, default: str = FREELANCERS_SPECIALIST_FAMILY_ID) -> str:
    normalized = str(value or "").strip().lower()
    normalized = re.sub(r"\s+", "_", normalized)
    normalized = re.sub(r"[^\w.+-]+", "_", normalized, flags=re.UNICODE)
    normalized = re.sub(r"_+", "_", normalized).strip("._-")
    return normalized or default


def normalize_specialist_family_entry(value: Any) -> Dict[str, Any] | None:
    if isinstance(value, str):
        family_id = normalize_specialist_family_id(value)
        return {
            "familyId": family_id,
            "displayName": value.strip() or family_id,
            "aliases": [],
            "description": "",
        }
    if not isinstance(value, dict):
        return None
    raw_id = value.get("familyId") or value.get("id") or value.get("name") or value.get("displayName")
    family_id = normalize_specialist_family_id(raw_id)
    display_name = str(value.get("displayName") or value.get("name") or raw_id or family_id).strip() or family_id
    aliases = []
    for item in list(value.get("aliases") or []):
        text = str(item or "").strip()
        if text and text not in aliases:
            aliases.append(text)
    return {
        "familyId": family_id,
        "displayName": display_name,
        "aliases": aliases,
        "description": str(value.get("description") or "").strip(),
    }


def normalize_specialist_families_config(value: Any) -> List[Dict[str, Any]]:
    entries: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for item in [*DEFAULT_SPECIALIST_FAMILIES, *list(value or [])]:
        entry = normalize_specialist_family_entry(item)
        if not entry:
            continue
        family_id = str(entry.get("familyId") or "").strip()
        if not family_id:
            continue
        if family_id in seen:
            existing = next((candidate for candidate in entries if candidate.get("familyId") == family_id), None)
            if existing is not None:
                existing_aliases = list(existing.get("aliases") or [])
                for alias in list(entry.get("aliases") or []):
                    if alias not in existing_aliases:
                        existing_aliases.append(alias)
                existing["aliases"] = existing_aliases
                if entry.get("description") and not existing.get("description"):
                    existing["description"] = entry.get("description")
                if entry.get("displayName") and existing.get("displayName") == family_id:
                    existing["displayName"] = entry.get("displayName")
            continue
        seen.add(family_id)
        entries.append(entry)
    return entries


def build_specialist_family_registry(agents: List[Dict[str, Any]] | None, specialist_registry: Dict[str, Any] | None = None) -> List[Dict[str, Any]]:
    configured = normalize_specialist_families_config((specialist_registry or {}).get("families") if isinstance(specialist_registry, dict) else None)
    by_id: Dict[str, Dict[str, Any]] = {str(item["familyId"]): dict(item) for item in configured}
    member_counts: Dict[str, int] = {}
    for agent in list(agents or []):
        if not isinstance(agent, dict):
            continue
        snapshot = agent.get("capabilitySnapshot") if isinstance(agent.get("capabilitySnapshot"), dict) else {}
        family_id = normalize_specialist_family_id(
            snapshot.get("specialistFamily")
            or snapshot.get("family")
            or agent.get("specialistFamily")
            or agent.get("family")
        )
        member_counts[family_id] = member_counts.get(family_id, 0) + 1
        by_id.setdefault(
            family_id,
            {
                "familyId": family_id,
                "displayName": family_id.replace("_", " ").title(),
                "aliases": [],
                "description": "",
            },
        )
    result = []
    for family_id in sorted(by_id, key=lambda key: (_SPECIALIST_FAMILY_SORT_ORDER.get(key, 99), key)):
        entry = dict(by_id[family_id])
        entry["memberCount"] = member_counts.get(family_id, 0)
        result.append(entry)
    return result

def ensure_specialist_family(snapshot: Dict[str, Any] | None) -> Dict[str, Any]:
    """Backfill compact supervisor routing metadata for legacy agent files."""
    normalized = dict(snapshot or {})
    family_value = normalized.get("specialistFamily") or normalized.get("family")
    normalized["specialistFamily"] = normalize_specialist_family_id(family_value)
    return normalized


def _stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _snapshot_list(value: Any, *, limit: int = 12) -> list[str]:
    result: list[str] = []
    for item in list(value or []):
        text = str(item or "").strip()
        if text and text not in result:
            result.append(text)
        if len(result) >= limit:
            break
    return result


def _compact_registry_member(agent: Dict[str, Any]) -> Dict[str, Any] | None:
    if not isinstance(agent, dict):
        return None
    agent_id = str(agent.get("id") or "").strip()
    if not agent_id or agent_id == "supervisor":
        return None
    raw_snapshot = agent.get("capabilitySnapshot") if isinstance(agent.get("capabilitySnapshot"), dict) else {}
    snapshot = dict(raw_snapshot)
    if not (snapshot.get("specialistFamily") or snapshot.get("family")):
        legacy_family = agent.get("specialistFamily") or agent.get("family")
        if legacy_family:
            snapshot["specialistFamily"] = legacy_family
    snapshot = ensure_specialist_family(snapshot)
    family_id = normalize_specialist_family_id(snapshot.get("specialistFamily") or snapshot.get("family"))
    member = {
        "id": agent_id,
        "agentId": agent_id,
        "name": str(agent.get("name") or agent_id).strip() or agent_id,
        "description": str(agent.get("description") or "").strip()[:240],
        "family": family_id,
        "globalExposure": bool(agent.get("globalExposure")),
        "isEnabled": agent.get("isEnabled") is not False,
        "model": str(agent.get("model") or agent.get("modelId") or "").strip(),
        "tool_mode": str(agent.get("tool_mode") or agent.get("toolMode") or "").strip(),
        "capabilitySnapshot": {
            "specialistFamily": family_id,
            "agentClass": str(snapshot.get("agentClass") or "").strip(),
            "domainTags": _snapshot_list(snapshot.get("domainTags")),
            "artifactCapabilities": _snapshot_list(snapshot.get("artifactCapabilities")),
            "operationCapabilities": _snapshot_list(snapshot.get("operationCapabilities")),
            "runtimeAffinities": _snapshot_list(snapshot.get("runtimeAffinities")),
            "runtimeBindings": list(snapshot.get("runtimeBindings") or [])[:8] if isinstance(snapshot.get("runtimeBindings"), list) else [],
            "executionSuitability": snapshot.get("executionSuitability"),
        },
    }
    # Cache invalidation must cover the full executable definition, even when
    # the registry's human/model-facing descriptor lists are compact.
    tools = [str(item).strip() for item in list(agent.get("tools") or []) if str(item).strip()]
    if tools:
        member["toolsHash"] = hashlib.sha256(_stable_json(tools).encode("utf-8")).hexdigest()[:16]
    elif "tools" not in agent and agent.get("toolsHash"):
        member["toolsHash"] = str(agent["toolsHash"])
    execution_config = {
        "tools": tools,
        "toolMode": member["tool_mode"],
        "reflectionEnabled": agent.get("reflection_enabled", False),
        "maxReflections": agent.get("max_reflections", 3),
        "capabilitySnapshot": snapshot,
    }
    # A frozen run registry may be merged with one newly registered worker.
    # Its compact members carry the original digest, not the full definition;
    # preserve that receipt instead of hashing the lossy projection again.
    raw_definition_fields = {"tools", "system_prompt", "systemPrompt", "reflection_enabled", "max_reflections"}
    member["executionConfigHash"] = (
        str(agent["executionConfigHash"])
        if agent.get("executionConfigHash") and raw_definition_fields.isdisjoint(agent)
        else hashlib.sha256(_stable_json(execution_config).encode("utf-8")).hexdigest()[:16]
    )
    system_prompt = str(agent.get("system_prompt") or agent.get("systemPrompt") or "")
    if system_prompt:
        member["systemPromptHash"] = hashlib.sha256(system_prompt.encode("utf-8")).hexdigest()[:16]
    elif "system_prompt" not in agent and "systemPrompt" not in agent and agent.get("systemPromptHash"):
        member["systemPromptHash"] = str(agent["systemPromptHash"])
    return member


def build_subagent_registry_snapshot(
    agents: List[Dict[str, Any]] | None,
    specialist_registry: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    members = [
        member
        for member in (_compact_registry_member(agent) for agent in list(agents or []))
        if member and member.get("isEnabled") is not False
    ]
    members.sort(key=lambda item: str(item.get("id") or ""))
    families = build_specialist_family_registry(members, specialist_registry)
    stable_payload = {
        "schemaVersion": "v8.subagent_registry_snapshot.v1",
        "agentIds": [str(member.get("id") or "") for member in members],
        "families": [
            {
                "familyId": item.get("familyId"),
                "displayName": item.get("displayName"),
                "memberCount": item.get("memberCount"),
            }
            for item in families
        ],
        "members": members,
    }
    digest = hashlib.sha256(_stable_json(stable_payload).encode("utf-8")).hexdigest()
    return {
        **stable_payload,
        "version": f"subagents:{digest[:12]}",
        "hash": digest,
        "generatedAt": utc_now_iso(),
    }


def agents_from_subagent_registry_snapshot(snapshot: Dict[str, Any] | None) -> List[Dict[str, Any]]:
    if not isinstance(snapshot, dict):
        return []
    members = snapshot.get("members") if isinstance(snapshot.get("members"), list) else []
    return [dict(item) for item in members if isinstance(item, dict) and str(item.get("id") or item.get("agentId") or "").strip()]

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
    # Known identities keep routing/configuration in the built-in registry. New
    # default files contain professional Markdown only. Explicit older/custom
    # frontmatter still wins; editing prose does not remove execution bindings.
    default = next((item for item in default_subagent_configs() if item.id == agent_id), None)
    
    if content.startswith("---"):
        try:
            # Find the end of the frontmatter
            end_idx = content.find("---", 3)
            if end_idx != -1:
                frontmatter_str = content[3:end_idx].strip()
                markdown_content = content[end_idx+3:].strip()
                
                supplied_metadata = yaml.safe_load(frontmatter_str) or {}
                # Normalize a user's supported legacy alias before adding
                # built-in defaults; otherwise contextual_auto masks explicit.
                if not supplied_metadata.get("tool_mode") and "toolMode" in supplied_metadata:
                    supplied_metadata["tool_mode"] = supplied_metadata["toolMode"]
                metadata = {
                    **(default.model_dump() if default else {}),
                    **supplied_metadata,
                }
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
    if default is not None:
        return default.model_copy(update={"system_prompt": content.strip()})
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

    # Do not export built-in tool, routing or runtime metadata into an editable
    # professional prompt. Preserve explicit custom metadata on round-trip.
    default = next((item for item in default_subagent_configs() if item.id == config.id), None)
    if default is not None:
        defaults = default.model_dump()
        metadata = {key: value for key, value in metadata.items() if value != defaults.get(key)}
    if not metadata:
        return config.system_prompt.strip() + "\n"
    
    frontmatter = yaml.dump(metadata, sort_keys=False, default_flow_style=False)
    
    return f"---\n{frontmatter.strip()}\n---\n\n{config.system_prompt.strip()}\n"


# Exact stock files emitted by f46631d1 (9.16.4). Unknown or edited files are
# never migrated on version/source/phrase markers. Extend this provenance when
# changing shipped seeds; this is content evidence, not executable old prompts.
_RELEASED_SEED_HASHES = {
    "implementation-engineer": {"7f2709f13930186de54988df326a0039655918f47f2dfaf3beacdaf10e0faa12"},
    "frontend-product-engineer": {"22aae8d3b6f662bb730cb99675bf496e3efbdb59498ed09a201e003e7d846fd9"},
    "verification-engineer": {"4a5ea96ecf601fde5da70760834fa4d125519a5cf5c223589d8aee3edced423f"},
    "code-review-architect": {"6f43c64197afbc3d22a60ad547328de63fc439c83791b5f6c462910c200766aa"},
    "web-research-architect": {"e0a736474dc4b6e952cd2010d040eb59fd7eee1da1e872026e39b9a954e9006e"},
    "research-synthesizer": {"1cf27c7e7df26d892066dee4f8df969a4bb55d5d276b636801e06bb898fc36e1"},
    "docs-delivery-writer": {"87c74590d1ad097bf15f7eea571ad86c6b69f319c910b78515cbf522ab219584"},
    "skill-workflow-curator": {"20ad93a3ec63231d3cbebd75840348acc5a3a303df904ebccdb32638d3ea4cd2"},
    "creative-media-director": {"d0ae1239dd476619c1bb47758cf9a51669080ea7c5f5ea2f3706fb35aeb748a8"},
    "visual-recipe-engineer": {"231a04b6b5dd43cb37aa63dfe8db27c4ea2641f82046437d0c071ffc28d2a28b"},
    "psd-layer-compositor": {"20d1ca75388673b1d50eb59f890296c7868e268d8f39fed641f00d62505b6238"},
    "character-continuity-designer": {"620267bf1e98c7b09c42346ef16b7a604469e5eb094116375d5f98bfa8a08037"},
    "motion-shot-director": {"ff2a594e608ef434c90a4404ed5fa9a43f5839112299d0d2542d1f7481c91f15"},
    "audio-post-producer": {"d98b376db52014a6a567dfefd42f73f5cd0e0e7d83aa15393d7a045d0a0a21ea"},
}


def is_unmodified_default_agent_md(content: str, agent_id: str) -> bool:
    normalized = content.replace("\r\n", "\n")
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    if digest in _RELEASED_SEED_HASHES.get(agent_id, set()):
        return True
    default = next((item for item in default_subagent_configs() if item.id == agent_id), None)
    return default is not None and normalized == dump_agent_md(default)


DEFAULT_PROMPT_SOURCE_REFS = [
    "apps/v8-agent-os-engine/core/default_agent_personas.py",
]

CREATIVE_MEDIA_PROMPT_SOURCE_REFS = [
    *DEFAULT_PROMPT_SOURCE_REFS,
    "skill:seedance-prompt-zh",
    "https://huggingface.co/MiniMaxAI/MiniMax-H3/raw/main/docs/VIDEO_PROMPT_WRITING_GUIDE_base_en.md",
    "https://help.aliyun.com/en/model-studio/wan-video-to-video-api-reference",
]

def _default_runtime_bindings_for_snapshot(snapshot: Dict[str, Any]) -> List[Dict[str, Any]]:
    family = normalize_specialist_family_id(snapshot.get("specialistFamily") or snapshot.get("family") or "")
    if family == "research":
        return [
            {
                "runtimeKind": "research",
                "grantGroups": ["research.core"],
                "label": "Research",
                "source": "system_default",
            }
        ]
    if family == "creative_media":
        return [
            {
                "runtimeKind": "creative_media",
                "grantGroups": ["creative_media.core"],
                "label": "Creative Media",
                "source": "system_default",
            }
        ]
    if family == "engineering":
        return [
            {
                "runtimeKind": "engineering",
                "grantGroups": ["engineering.core"],
                "label": "Engineering",
                "source": "system_default",
            }
        ]
    return []


def _default_agent(
    *,
    agent_id: str,
    name: str,
    description: str,
    role_label: str,
    icon: str,
    capability_snapshot: Dict[str, Any],
    prompt_source_refs: List[str] | None = None,
    global_exposure: bool = False,
) -> AgentConfig:
    capability_snapshot = dict(capability_snapshot or {})
    capability_snapshot.setdefault("runtimeBindings", _default_runtime_bindings_for_snapshot(capability_snapshot))
    resolved_prompt_source_refs = list(prompt_source_refs or DEFAULT_PROMPT_SOURCE_REFS)
    from core.default_agent_personas import default_role_prompt

    system_prompt = default_role_prompt(
        agent_id, name, creative=capability_snapshot.get("specialistFamily") == "creative_media"
    )
    template_payload = {
        "agentId": agent_id,
        "description": description,
        "roleLabel": role_label,
        "icon": icon,
        "capabilitySnapshot": capability_snapshot,
        "promptSourceRefs": resolved_prompt_source_refs,
        "globalExposure": global_exposure,
        "systemPrompt": system_prompt,
    }
    template_digest = hashlib.sha256(
        json.dumps(
            template_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()[:16]
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
        globalExposure=global_exposure,
        reflection_enabled=False,
        max_reflections=3,
        capabilitySnapshot=capability_snapshot,
        defaultTemplateVersion=f"{DEFAULT_SUBAGENT_TEMPLATE_VERSION}:{template_digest}",
        promptSourceRefs=resolved_prompt_source_refs,
        system_prompt=system_prompt,
    )


def default_subagent_configs() -> List[AgentConfig]:
    """Default local execution specialists seeded into a fresh V8 home."""
    return [
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
                "executionSuitability": "medium",
                "externalWorkerSuitability": "medium",
                "confidence": 0.88,
                "source": "system_default",
            },
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
                "executionSuitability": "medium",
                "externalWorkerSuitability": "medium",
                "confidence": 0.87,
                "source": "system_default",
            },
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
                "executionSuitability": "medium",
                "externalWorkerSuitability": "medium",
                "confidence": 0.9,
                "source": "system_default",
            },
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
                "executionSuitability": "medium",
                "externalWorkerSuitability": "low",
                "confidence": 0.88,
                "source": "system_default",
            },
        ),
        _default_agent(
            agent_id="web-research-architect",
            name="Web Research Architect",
            description="Provides the configured model identity for source-grounded internal Research Runtime stages.",
            role_label="Research Architect",
            icon="search-check",
            capability_snapshot={
                "agentClass": "research_coordinator",
                "specialistFamily": "research",
                "domainTags": ["web_research", "source_quality", "fact_checking", "provider_docs", "parallel_research"],
                "artifactCapabilities": ["research_plan", "evidence_bundle", "source_matrix", "citation_pack"],
                "operationCapabilities": ["plan_search", "decompose_queries", "rank_sources", "orchestrate_shards", "synthesize_evidence"],
                "runtimeAffinities": ["research", "chat", "extensions"],
                "toolExposurePolicy": "contextual_auto",
                "executionSuitability": "high",
                "externalWorkerSuitability": "low",
                "confidence": 0.9,
                "source": "system_default",
            },
            global_exposure=True,
        ),
        _default_agent(
            agent_id="research-synthesizer",
            name="Research Synthesizer",
            description="Gathers and synthesizes source-backed research into compact briefs for supervisor decisions.",
            role_label="Researcher",
            icon="search-check",
            capability_snapshot={
                "agentClass": "researcher",
                "specialistFamily": "research",
                "domainTags": ["research", "synthesis", "source_quality", "strategy"],
                "artifactCapabilities": ["research_brief", "source_matrix", "option_analysis"],
                "operationCapabilities": ["research", "compare", "summarize", "triangulate"],
                "runtimeAffinities": ["research", "chat", "extensions"],
                "toolExposurePolicy": "contextual_auto",
                "executionSuitability": "low",
                "externalWorkerSuitability": "medium",
                "confidence": 0.82,
                "source": "system_default",
            },
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
                "executionSuitability": "low",
                "externalWorkerSuitability": "low",
                "confidence": 0.84,
                "source": "system_default",
            },
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
                "executionSuitability": "medium",
                "externalWorkerSuitability": "low",
                "confidence": 0.86,
                "source": "system_default",
            },
        ),
        _default_agent(
            agent_id="creative-media-director",
            name="Creative Media Director",
            description="Owns governed creative-media delivery from hard constraints and storyboards through provider execution, editing, QA, and artifact handoff.",
            role_label="Creative Director",
            icon="clapperboard",
            capability_snapshot={
                "agentClass": "creative_director",
                "specialistFamily": "creative_media",
                "domainTags": ["creative_media", "video_generation", "storyboard", "script", "asset_planning"],
                "artifactCapabilities": ["creative_brief", "storyboard", "shot_plan", "media_artifact", "qa_evidence", "acceptance_contract"],
                "operationCapabilities": ["brief", "decompose", "preserve_constraints", "sequence", "execute_media_run", "edit", "quality_gate", "artifact_handoff"],
                "runtimeAffinities": ["chat", "artifact", "extensions", "audio"],
                "toolExposurePolicy": "contextual_auto",
                "executionSuitability": "high",
                "externalWorkerSuitability": "medium",
                "confidence": 0.86,
                "source": "system_default",
            },
            prompt_source_refs=CREATIVE_MEDIA_PROMPT_SOURCE_REFS,
        ),
        _default_agent(
            agent_id="visual-recipe-engineer",
            name="Visual Recipe Engineer",
            description="Compiles image, keyframe, and video prompts into structured recipes while preserving user constraints.",
            role_label="Visual Recipe",
            icon="image-up",
            capability_snapshot={
                "agentClass": "visual_recipe_engineer",
                "specialistFamily": "creative_media",
                "domainTags": ["creative_media", "image_generation", "keyframe", "prompt_engineering", "visual_recipe"],
                "artifactCapabilities": ["prompt_recipe", "image_prompt", "keyframe_prompt", "negative_constraints"],
                "operationCapabilities": ["compile_prompt", "polish", "structure", "adapt_provider", "quality_gate"],
                "runtimeAffinities": ["chat", "artifact", "extensions"],
                "toolExposurePolicy": "contextual_auto",
                "executionSuitability": "medium",
                "externalWorkerSuitability": "medium",
                "confidence": 0.84,
                "source": "system_default",
            },
            prompt_source_refs=CREATIVE_MEDIA_PROMPT_SOURCE_REFS,
        ),
        _default_agent(
            agent_id="psd-layer-compositor",
            name="PSD Layer Compositor",
            description="Designs editable PSD/source assets, layer manifests, alpha cleanup, and preview handoff for Creative Media jobs.",
            role_label="PSD Compositor",
            icon="layers",
            capability_snapshot={
                "agentClass": "psd_layer_compositor",
                "specialistFamily": "creative_media",
                "domainTags": [
                    "creative_media",
                    "psd",
                    "layered_assets",
                    "alpha_quality",
                    "background_removal",
                    "image_generation",
                    "asset_composition",
                ],
                "artifactCapabilities": ["psd_source", "layer_manifest", "preview_png", "alpha_report", "asset_cutout_plan"],
                "operationCapabilities": ["plan_layers", "inspect_alpha", "request_chroma_key_assets", "compose_psd", "export_preview", "handoff_artifact"],
                "runtimeAffinities": ["chat", "artifact", "extensions", "creative_media"],
                "toolExposurePolicy": "contextual_auto",
                "executionSuitability": "medium",
                "externalWorkerSuitability": "medium",
                "confidence": 0.84,
                "source": "system_default",
            },
            prompt_source_refs=CREATIVE_MEDIA_PROMPT_SOURCE_REFS,
        ),
        _default_agent(
            agent_id="character-continuity-designer",
            name="Character Continuity Designer",
            description="Maintains character bibles, reference strategy, and consistency checks for multi-shot media.",
            role_label="Character Continuity",
            icon="user-round-check",
            capability_snapshot={
                "agentClass": "character_continuity",
                "specialistFamily": "creative_media",
                "domainTags": ["creative_media", "character_consistency", "reference_assets", "long_video", "keyframe"],
                "artifactCapabilities": ["character_bible", "reference_plan", "continuity_checklist", "variation_log"],
                "operationCapabilities": ["define_character", "anchor_references", "compare", "repair_plan"],
                "runtimeAffinities": ["chat", "artifact", "extensions"],
                "toolExposurePolicy": "contextual_auto",
                "executionSuitability": "medium",
                "externalWorkerSuitability": "medium",
                "confidence": 0.83,
                "source": "system_default",
            },
            prompt_source_refs=CREATIVE_MEDIA_PROMPT_SOURCE_REFS,
        ),
        _default_agent(
            agent_id="motion-shot-director",
            name="Motion Shot Director",
            description="Designs camera motion, timed shot prompts, and short-clip stitching plans for long videos.",
            role_label="Motion Director",
            icon="video",
            capability_snapshot={
                "agentClass": "motion_director",
                "specialistFamily": "creative_media",
                "domainTags": ["creative_media", "video_generation", "camera_motion", "shot_planning", "clip_stitching"],
                "artifactCapabilities": ["shot_list", "camera_plan", "timed_prompt", "stitch_plan"],
                "operationCapabilities": ["timebox", "direct_camera", "sequence_clips", "plan_transition", "evaluate_motion"],
                "runtimeAffinities": ["chat", "artifact", "extensions"],
                "toolExposurePolicy": "contextual_auto",
                "executionSuitability": "medium",
                "externalWorkerSuitability": "medium",
                "confidence": 0.84,
                "source": "system_default",
            },
            prompt_source_refs=CREATIVE_MEDIA_PROMPT_SOURCE_REFS,
        ),
        _default_agent(
            agent_id="audio-post-producer",
            name="Audio Post Producer",
            description="Plans voiceover, music, sound effects, subtitles, and final media assembly through existing V8 artifacts.",
            role_label="Audio Post",
            icon="captions",
            capability_snapshot={
                "agentClass": "audio_post",
                "specialistFamily": "creative_media",
                "domainTags": ["creative_media", "audio", "tts", "music", "subtitle", "editing", "artifact_delivery"],
                "artifactCapabilities": ["voiceover_script", "subtitle_plan", "audio_cue_sheet", "edit_decision_list"],
                "operationCapabilities": ["plan_voiceover", "sync_audio", "write_subtitles", "assemble", "deliver_artifact"],
                "runtimeAffinities": ["chat", "audio", "artifact", "extensions"],
                "toolExposurePolicy": "contextual_auto",
                "executionSuitability": "medium",
                "externalWorkerSuitability": "medium",
                "confidence": 0.82,
                "source": "system_default",
            },
            prompt_source_refs=CREATIVE_MEDIA_PROMPT_SOURCE_REFS,
        ),
    ]
