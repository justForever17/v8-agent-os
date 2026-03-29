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
