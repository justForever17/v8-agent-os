from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class NetworkTraceContext(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    source_run_id: Optional[str] = Field(default=None, alias="sourceRunId")
    source_session_id: Optional[str] = Field(default=None, alias="sourceSessionId")
    workflow_id: Optional[str] = Field(default=None, alias="workflowId")
    delegation_id: Optional[str] = Field(default=None, alias="delegationId")


class NetworkEnvelope(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    version: str = "1"
    message_id: str = Field(alias="messageId")
    message_type: str = Field(alias="messageType")
    sent_at: str = Field(alias="sentAt")
    expires_at: str = Field(alias="expiresAt")
    from_peer_id: str = Field(alias="fromPeerId")
    to_peer_id: str = Field(alias="toPeerId")
    nonce: str
    signature: str
    trace: NetworkTraceContext = Field(default_factory=NetworkTraceContext)
    payload: Dict[str, Any] = Field(default_factory=dict)


class TrustedPeerConfig(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    peer_id: str = Field(alias="peerId")
    display_name: Optional[str] = Field(default=None, alias="displayName")
    base_url: str = Field(alias="baseUrl")
    ws_url: Optional[str] = Field(default=None, alias="wsUrl")
    public_key: str = Field(alias="publicKey")
    allowed_scopes: List[str] = Field(default_factory=list, alias="allowedScopes")
    allowed_workspaces: List[str] = Field(default_factory=list, alias="allowedWorkspaces")


class NetworkNodeConfig(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    display_name: str = Field(default="V8 Node", alias="displayName")
    peer_id: str = Field(default="", alias="peerId")
    advertised_base_url: str = Field(default="http://127.0.0.1:9530", alias="advertisedBaseUrl")
    advertised_ws_url: str = Field(
        default="ws://127.0.0.1:9530/v1/network-supervisor/peer/ws",
        alias="advertisedWsUrl",
    )


class NetworkDiscoveryConfig(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    lan_enabled: bool = Field(default=False, alias="lanEnabled")
    multicast_group: str = Field(default="239.8.8.8", alias="multicastGroup")
    multicast_port: int = Field(default=19530, alias="multicastPort")
    announce_interval_seconds: int = Field(default=15, alias="announceIntervalSeconds")
    peer_expiry_seconds: int = Field(default=60, alias="peerExpirySeconds")
    wan_bootstrap_peers: List[str] = Field(default_factory=list, alias="wanBootstrapPeers")


class NetworkTrustConfig(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    enrollment_mode: Literal["manual", "open"] = Field(default="manual", alias="enrollmentMode")
    allowed_scopes: List[str] = Field(default_factory=list, alias="allowedScopes")
    trusted_peers: List[TrustedPeerConfig] = Field(default_factory=list, alias="trustedPeers")


class NetworkWakeConfig(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    enabled: bool = True
    ack_timeout_seconds: int = Field(default=10, alias="ackTimeoutSeconds")


class NetworkDelegationConfig(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    enabled: bool = True
    max_concurrent: int = Field(default=2, alias="maxConcurrent")
    default_timeout_seconds: int = Field(default=120, alias="defaultTimeoutSeconds")


class NetworkOpenAICompatConfig(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    enabled: bool = Field(default=False)
    admin_relay_only: bool = Field(default=True, alias="adminRelayOnly")
    allow_workspace_headers: bool = Field(default=True, alias="allowWorkspaceHeaders")
    allow_raw_workspace_path: bool = Field(default=False, alias="allowRawWorkspacePath")
    max_external_tools: int = Field(default=8, alias="maxExternalTools")
    default_scope_mode: str = Field(default="explicit", alias="defaultScopeMode")
    max_external_system_tokens: int = Field(default=1200, alias="maxExternalSystemTokens")
    max_external_message_tokens: int = Field(default=16000, alias="maxExternalMessageTokens")
    max_external_tool_description_tokens: int = Field(default=800, alias="maxExternalToolDescriptionTokens")
    max_external_tool_schema_bytes: int = Field(default=32768, alias="maxExternalToolSchemaBytes")
    max_external_tools_payload_tokens: int = Field(default=6000, alias="maxExternalToolsPayloadTokens")
    max_memory_hint_tokens: int = Field(default=1200, alias="maxMemoryHintTokens")
    max_workflow_hint_tokens: int = Field(default=600, alias="maxWorkflowHintTokens")


class NetworkSupervisorRuntimeConfig(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    enabled: bool = False
    node: NetworkNodeConfig = Field(default_factory=NetworkNodeConfig)
    discovery: NetworkDiscoveryConfig = Field(default_factory=NetworkDiscoveryConfig)
    trust: NetworkTrustConfig = Field(default_factory=NetworkTrustConfig)
    wake: NetworkWakeConfig = Field(default_factory=NetworkWakeConfig)
    delegation: NetworkDelegationConfig = Field(default_factory=NetworkDelegationConfig)
    openai_compat: NetworkOpenAICompatConfig = Field(default_factory=NetworkOpenAICompatConfig, alias="openaiCompat")


class NetworkPeerMutationPayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    peer_id: str = Field(alias="peerId")
    display_name: Optional[str] = Field(default=None, alias="displayName")
    base_url: Optional[str] = Field(default=None, alias="baseUrl")
    ws_url: Optional[str] = Field(default=None, alias="wsUrl")
    public_key: Optional[str] = Field(default=None, alias="publicKey")
    allowed_scopes: Optional[List[str]] = Field(default=None, alias="allowedScopes")
    allowed_workspaces: Optional[List[str]] = Field(default=None, alias="allowedWorkspaces")
    peer_token: Optional[str] = Field(default=None, alias="peerToken")


class NetworkDiagnosticsPayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    peer_id: str = Field(alias="peerId")
    note: Optional[str] = None
    task: Optional[str] = None
    timeout_seconds: Optional[int] = Field(default=None, alias="timeoutSeconds")
    project_id: Optional[str] = Field(default=None, alias="projectId")
    workspace_id: Optional[str] = Field(default=None, alias="workspaceId")
    workspace_path: Optional[str] = Field(default=None, alias="workspacePath")
    scope_hint: Optional[str] = Field(default=None, alias="scopeHint")


class NetworkDelegationRequestPayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    peer_id: str = Field(alias="peerId")
    task: str
    timeout_seconds: Optional[int] = Field(default=None, alias="timeoutSeconds")
    project_id: Optional[str] = Field(default=None, alias="projectId")
    workspace_id: Optional[str] = Field(default=None, alias="workspaceId")
    workspace_path: Optional[str] = Field(default=None, alias="workspacePath")
    scope_hint: Optional[str] = Field(default=None, alias="scopeHint")
