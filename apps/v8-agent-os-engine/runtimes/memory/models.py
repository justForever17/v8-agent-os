from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class ChannelBinding(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    channel_type: str = Field(alias="channelType")
    remote_id: str = Field(alias="remoteId")
    mode: str = "default"


class WorkflowBinding(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    workflow_id: str = Field(alias="workflowId")
    mode: str = "default"


class ProjectDescriptor(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    project_id: str = Field(alias="id")
    name: str
    description: Optional[str] = None
    workspace_id: Optional[str] = Field(default=None, alias="workspaceId")
    workspace_path: Optional[str] = Field(default=None, alias="workspacePath")
    default_scope: Optional[str] = Field(default=None, alias="defaultScope")
    tags: List[str] = Field(default_factory=list)
    channel_bindings: List[ChannelBinding] = Field(default_factory=list, alias="channelBindings")
    workflow_bindings: List[WorkflowBinding] = Field(default_factory=list, alias="workflowBindings")
    active: bool = True

    def normalized(self) -> "ProjectDescriptor":
        data = self.model_copy(deep=True)
        data.project_id = str(data.project_id or "").strip()
        data.name = str(data.name or "").strip() or data.project_id
        data.description = str(data.description or "").strip() or None
        data.workspace_path = str(data.workspace_path or "").strip() or None
        data.workspace_id = str(data.workspace_id or "").strip() or data.project_id
        data.default_scope = f"project:{data.project_id}"
        data.tags = [str(tag).strip() for tag in list(data.tags or []) if str(tag).strip()]
        return data


class WorkspaceProjectBinding(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    workspace_id: str
    workspace_path: str
    project_id: str
    source: str
    confidence: float = 1.0


class SessionScopeBinding(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    session_id: str
    conversation_id: Optional[str] = None
    thread_id: Optional[str] = None
    user_id: Optional[str] = None
    workspace_id: Optional[str] = None
    workspace_path: Optional[str] = None
    project_id: Optional[str] = None
    workflow_id: Optional[str] = None
    channel_type: Optional[str] = None
    channel_remote_id: Optional[str] = None
    scope_hint: Optional[str] = None
    resolved_scope: str
    scope_source: str
    scope_confidence: float = 1.0
    status: str = "active"
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    def metadata_view(self) -> Dict[str, Any]:
        return {
            "project_id": self.project_id,
            "workspace_id": self.workspace_id,
            "workspace_path": self.workspace_path,
            "workflow_id": self.workflow_id,
            "channel_type": self.channel_type,
            "channel_remote_id": self.channel_remote_id,
            "scope_hint": self.scope_hint,
            "resolved_scope": self.resolved_scope,
            "scope_source": self.scope_source,
            "scope_confidence": self.scope_confidence,
        }


class ScopeResolutionEvent(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    id: str
    session_id: str
    run_id: Optional[str] = None
    requested_scope: Optional[str] = None
    resolved_scope: str
    source: str
    confidence: float = 1.0
    evidence: Dict[str, Any] = Field(default_factory=dict)
    created_at: Optional[str] = None


class ScopeResolutionResult(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    binding: SessionScopeBinding
    requested_scope: Optional[str] = None
    scope_chain: List[str] = Field(default_factory=list)
    evidence: Dict[str, Any] = Field(default_factory=dict)
    reused_existing_binding: bool = False
