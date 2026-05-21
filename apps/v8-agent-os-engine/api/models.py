from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field


class ChatToolFunction(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str = Field(default="", description="Function/tool name")
    arguments: str = Field(default="{}", description="JSON-encoded function arguments")


class ChatToolCall(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: Optional[str] = Field(default=None, description="Tool call id")
    type: str = Field(default="function", description="Tool call type")
    function: ChatToolFunction = Field(default_factory=ChatToolFunction)


class ExternalToolFunctionSpec(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str = Field(default="", description="Original wire-visible external function name")
    description: Optional[str] = Field(default=None, description="External function description")
    visible_description: Optional[str] = Field(
        default=None,
        alias="visibleDescription",
        description="Model-visible description excerpt. Full original remains in rawSchemaRef.",
    )
    parameters: Dict[str, Any] = Field(default_factory=dict, description="JSON schema parameters")
    internal_alias_name: Optional[str] = Field(default=None, alias="internalAliasName")
    tool_kind: Optional[str] = Field(default=None, alias="toolKind", description="Inferred external tool kind")
    side_effect: Optional[str] = Field(default=None, alias="sideEffect", description="Inferred side-effect class")
    preconditions: List[str] = Field(default_factory=list, description="Important client-owned tool preconditions")
    recovery_hints: List[str] = Field(default_factory=list, alias="recoveryHints", description="Failure recovery hints for the supervisor")
    client_owned_workspace: bool = Field(default=False, alias="clientOwnedWorkspace", description="Whether the tool operates in the external client's workspace")
    raw_schema_ref: Optional[str] = Field(default=None, alias="rawSchemaRef", description="Raw evidence ref for the original external tool schema")
    reservoir_mode: bool = Field(default=False, alias="reservoirMode", description="Whether model-visible schema/description was compacted into reservoir mode")
    description_omitted_chars: int = Field(default=0, alias="descriptionOmittedChars")
    schema_omission_reason: Optional[str] = Field(default=None, alias="schemaOmissionReason")


class ExternalToolSpec(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    type: str = Field(default="function", description="External tool type")
    function: ExternalToolFunctionSpec = Field(default_factory=ExternalToolFunctionSpec)


class ChatMessage(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    role: str = Field(description="Role of the message sender (user, assistant, system, tool)")
    content: str = Field(description="Content of the message")
    name: Optional[str] = Field(default=None, description="Name of the tool or user")
    tool_call_id: Optional[str] = Field(default=None, description="ID of the tool call if this is a tool response")
    tool_calls: Optional[List[ChatToolCall]] = Field(default=None, alias="toolCalls", description="Assistant tool calls")


class ToolOutput(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    tool_call_id: str = Field(description="ID of the tool call this output answers")
    output: str = Field(description="User or system provided tool output")
    name: Optional[str] = Field(default=None, description="Optional tool name for compatibility")

class EngineConfig(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    provider: str = Field(default="openai", description="The provider name (openai, anthropic, google/gemini)")
    model_name: str = Field(default="gpt-4o", description="The LLM model to use")
    api_key: Optional[str] = Field(default=None)
    base_url: Optional[str] = Field(default=None)
    system_prompt: Optional[str] = Field(default=None, description="The final system prompt to inject")
    allowed_tools: Optional[List[str]] = Field(default=None, description="List of explicitly allowed agent tools")
    external_tools: Optional[List[ExternalToolSpec]] = Field(default=None, alias="externalTools", description="Per-request external tools exposed via the OpenAI compat branch")


class CommandPresetSelection(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str = Field(description="Command preset display name / file stem")


class SkillReferenceSelection(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: Optional[str] = Field(default=None, description="Stable skill id", alias="id")
    name: Optional[str] = Field(default=None, description="Skill display name")
    description: Optional[str] = Field(default=None, description="Short skill description")
    path: Optional[str] = Field(default=None, description="Absolute local path to the skill root or SKILL.md")
    source_type: Optional[str] = Field(default=None, alias="sourceType")
    workspace_path: Optional[str] = Field(default=None, alias="workspacePath")
    workspace_id: Optional[str] = Field(default=None, alias="workspaceId")
    project_id: Optional[str] = Field(default=None, alias="projectId")


class ModelReasoningRepairPayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    model_id: Optional[str] = Field(default=None, alias="modelId")
    model_ref: Optional[str] = Field(default=None, alias="modelRef")
    provider_id: Optional[str] = Field(default=None, alias="providerId")


class ContextMentionSelection(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    kind: str = Field(default="", description="Mention kind, e.g. skill or subagent_family")
    id: Optional[str] = Field(default=None)
    name: Optional[str] = Field(default=None)
    label: Optional[str] = Field(default=None)
    description: Optional[str] = None
    path: Optional[str] = None
    family_id: Optional[str] = Field(default=None, alias="familyId")
    source_type: Optional[str] = Field(default=None, alias="sourceType")


class ChatAttachment(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: Optional[str] = None
    name: Optional[str] = None
    url: Optional[str] = None
    public_url: Optional[str] = Field(default=None, alias="publicUrl")
    workspace_path: Optional[str] = Field(default=None, alias="workspacePath")
    workspace_relative_path: Optional[str] = Field(default=None, alias="workspaceRelativePath")
    mime_type: Optional[str] = Field(default=None, alias="mimeType")
    size: Optional[int] = None
    source: Optional[str] = None
    resource_ref: Optional[Dict[str, Any]] = Field(default=None, alias="resourceRef")


class ChatRequestData(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    client_message_id: Optional[str] = Field(default=None, alias="clientMessageId")
    command_preset: Optional[CommandPresetSelection] = Field(default=None, alias="commandPreset")
    task_planning_mode: Optional[bool] = Field(default=None, alias="taskPlanningMode")
    planner_mode: Optional[str] = Field(default=None, alias="plannerMode")
    planner_dispatch_mode: Optional[str] = Field(default=None, alias="plannerDispatchMode")
    engineering_mode: Optional[str] = Field(default=None, alias="engineeringMode")
    skill_references: Optional[List[SkillReferenceSelection]] = Field(default=None, alias="skillReferences")
    context_mentions: Optional[List[ContextMentionSelection]] = Field(default=None, alias="contextMentions")
    fileUrls: Optional[List[str]] = Field(default=None, description="Compatibility uploaded file URL list")
    attachments: Optional[List[ChatAttachment]] = Field(default=None, description="Structured uploaded attachments")
    disable_extensions_prefilter: Optional[bool] = Field(default=None, alias="disableExtensionsPrefilter")
    compat_ingress_diagnostics: Optional[Dict[str, Any]] = Field(default=None, alias="compatIngressDiagnostics")


class ChatRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    messages: List[ChatMessage]
    config: EngineConfig = Field(default_factory=EngineConfig, description="Configuration for the execution")
    stream: Optional[bool] = Field(default=True, description="Whether to stream the response via SSE")
    session_id: Optional[str] = Field(default=None, description="Unique identifier for the session/conversation")
    conversation_id: Optional[str] = Field(default=None, alias="conversationId")
    client_message_id: Optional[str] = Field(default=None, alias="clientMessageId")
    user_id: Optional[str] = Field(default="anonymous")
    fileUrls: Optional[List[str]] = Field(default=None, description="Local paths or URLs to uploaded files")
    attachments: Optional[List[ChatAttachment]] = Field(default=None, description="Structured uploaded attachments")
    tool_outputs: Optional[List[ToolOutput]] = Field(default=None, description="Structured tool outputs for HITL resume")
    project_id: Optional[str] = Field(default=None, alias="projectId")
    workspace_id: Optional[str] = Field(default=None, alias="workspaceId")
    workspace_path: Optional[str] = Field(default=None, alias="workspacePath")
    thread_id: Optional[str] = Field(default=None, alias="threadId")
    scope_hint: Optional[str] = Field(default=None, alias="scopeHint")
    scope_mode: Optional[str] = Field(default="explicit", alias="scopeMode")
    resume_run_id: Optional[str] = Field(default=None, alias="resumeRunId")
    resume_value: Optional[Dict[str, Any]] = Field(default=None, alias="resumeValue")
    data: Optional[ChatRequestData] = Field(default=None, description="Structured command preset / task mode data")

class StreamEvent(BaseModel):
    event: str = Field(description="Event type: e.g., 'message', 'tool_call', 'agent_step', 'error'")
    data: Any = Field(description="Event payload context")


class ProjectDescriptorPayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: Optional[str] = None
    name: Optional[str] = None
    description: Optional[str] = None
    workspace_id: Optional[str] = Field(default=None, alias="workspaceId")
    workspace_path: Optional[str] = Field(default=None, alias="workspacePath")
    default_scope: Optional[str] = Field(default=None, alias="defaultScope")
    tags: List[str] = Field(default_factory=list)
    channel_bindings: List[Dict[str, Any]] = Field(default_factory=list, alias="channelBindings")
    workflow_bindings: List[Dict[str, Any]] = Field(default_factory=list, alias="workflowBindings")
    active: bool = True


class WorkspaceBindingPayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    workspace_id: str = Field(alias="workspaceId")
    workspace_path: str = Field(alias="workspacePath")
    source: Optional[str] = "admin_selected"
    confidence: Optional[float] = 1.0


class ChannelBindingPayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    channel_type: str = Field(alias="channelType")
    remote_id: str = Field(alias="remoteId")
    mode: Optional[str] = "default"


class WorkflowBindingPayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    workflow_id: str = Field(alias="workflowId")
    mode: Optional[str] = "default"


class SessionScopeBindingPayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    project_id: Optional[str] = Field(default=None, alias="projectId")
    workspace_id: Optional[str] = Field(default=None, alias="workspaceId")
    workspace_path: Optional[str] = Field(default=None, alias="workspacePath")
    workflow_id: Optional[str] = Field(default=None, alias="workflowId")
    channel_type: Optional[str] = Field(default=None, alias="channelType")
    channel_remote_id: Optional[str] = Field(default=None, alias="channelRemoteId")
    scope_hint: Optional[str] = Field(default=None, alias="scopeHint")
    scope_source: Optional[str] = Field(default="admin_selected", alias="scopeSource")
    scope_confidence: Optional[float] = Field(default=1.0, alias="scopeConfidence")
    user_id: Optional[str] = Field(default=None, alias="userId")
    conversation_id: Optional[str] = Field(default=None, alias="conversationId")
    thread_id: Optional[str] = Field(default=None, alias="threadId")


class ScopeResolvePayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    session_id: str = Field(alias="sessionId")
    user_query: Optional[str] = Field(default="", alias="userQuery")
    project_id: Optional[str] = Field(default=None, alias="projectId")
    workspace_id: Optional[str] = Field(default=None, alias="workspaceId")
    workspace_path: Optional[str] = Field(default=None, alias="workspacePath")
    workflow_id: Optional[str] = Field(default=None, alias="workflowId")
    channel_type: Optional[str] = Field(default=None, alias="channelType")
    channel_remote_id: Optional[str] = Field(default=None, alias="channelRemoteId")
    thread_id: Optional[str] = Field(default=None, alias="threadId")
    scope_hint: Optional[str] = Field(default=None, alias="scopeHint")
    scope_mode: Optional[str] = Field(default="explicit", alias="scopeMode")


class PreferenceMutationPayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    scope: str = "global"
    key: str
    value: Optional[str] = None


class PreferenceQuarantineMutationPayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    record_id: str = Field(alias="recordId")


class GraphEntityPayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str
    entity_type: str = Field(default="concept", alias="entityType")
    maintainer_source: Optional[str] = Field(default=None, alias="maintainerSource")
    confidence: Optional[float] = 1.0


class GraphRelationPayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    subject: str
    predicate: str
    object_name: str = Field(alias="object")
    confidence: Optional[float] = 1.0
    maintainer_source: Optional[str] = Field(default=None, alias="maintainerSource")


class RunCommandPayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    reason: Optional[str] = None
    response: Optional[Dict[str, Any]] = None
    payload: Optional[Dict[str, Any]] = None


class ModelConnectionTestPayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    model_id: str = Field(alias="modelId")
    model_ref: Optional[str] = Field(default=None, alias="modelRef")
    provider_id: Optional[str] = Field(default=None, alias="providerId")


class ComputerUseSessionPayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    session_id: Optional[str] = Field(default=None, alias="sessionId")
    run_id: Optional[str] = Field(default=None, alias="runId")
    user_id: Optional[str] = Field(default="anonymous", alias="userId")
    project_id: Optional[str] = Field(default=None, alias="projectId")
    workspace_id: Optional[str] = Field(default=None, alias="workspaceId")
    workspace_path: Optional[str] = Field(default=None, alias="workspacePath")
    goal: Optional[str] = None


class ComputerUseWindowQueryPayload(ComputerUseSessionPayload):
    model_config = ConfigDict(populate_by_name=True)

    title_filter: Optional[str] = Field(default=None, alias="titleFilter")
    limit: int = 20


class ComputerUseAppQueryPayload(ComputerUseSessionPayload):
    model_config = ConfigDict(populate_by_name=True)

    query: Optional[str] = None
    limit: int = 20
    include_running: bool = Field(default=True, alias="includeRunning")
    force_refresh: bool = Field(default=False, alias="forceRefresh")
    include_learned: bool = Field(default=True, alias="includeLearned")


class ComputerUseAgentBrowserOpenPayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    browser_kind: Optional[str] = Field(default="chrome", alias="browserKind")
    url: Optional[str] = Field(default="about:blank")


class ComputerUseObservePayload(ComputerUseSessionPayload):
    model_config = ConfigDict(populate_by_name=True)

    window_title: Optional[str] = Field(default=None, alias="windowTitle")
    window_handle: Optional[int] = Field(default=None, alias="windowHandle")
    depth_limit: int = Field(default=4, alias="depthLimit")
    element_limit: int = Field(default=80, alias="elementLimit")
    include_screenshot: bool = Field(default=True, alias="includeScreenshot")


class ComputerUseElementQueryPayload(ComputerUseSessionPayload):
    model_config = ConfigDict(populate_by_name=True)

    element_id: Optional[str] = Field(default=None, alias="elementId")
    window_title: Optional[str] = Field(default=None, alias="windowTitle")
    window_handle: Optional[int] = Field(default=None, alias="windowHandle")
    name: Optional[str] = None
    name_contains: Optional[str] = Field(default=None, alias="nameContains")
    automation_id: Optional[str] = Field(default=None, alias="automationId")
    control_type: Optional[str] = Field(default=None, alias="controlType")
    class_name: Optional[str] = Field(default=None, alias="className")
    depth_limit: int = Field(default=6, alias="depthLimit")
    limit: int = 20


class ComputerUseClickPayload(ComputerUseElementQueryPayload):
    model_config = ConfigDict(populate_by_name=True)

    double: bool = False


class ComputerUseTypePayload(ComputerUseElementQueryPayload):
    model_config = ConfigDict(populate_by_name=True)

    text: str
    clear_first: bool = Field(default=False, alias="clearFirst")
    press_enter: bool = Field(default=False, alias="pressEnter")


class ComputerUseHotkeyPayload(ComputerUseSessionPayload):
    model_config = ConfigDict(populate_by_name=True)

    sequence: str
    window_title: Optional[str] = Field(default=None, alias="windowTitle")
    window_handle: Optional[int] = Field(default=None, alias="windowHandle")


class ComputerUseScrollPayload(ComputerUseSessionPayload):
    model_config = ConfigDict(populate_by_name=True)

    amount: int
    element_id: Optional[str] = Field(default=None, alias="elementId")
    window_title: Optional[str] = Field(default=None, alias="windowTitle")
    window_handle: Optional[int] = Field(default=None, alias="windowHandle")


class ComputerUseWaitPayload(ComputerUseElementQueryPayload):
    model_config = ConfigDict(populate_by_name=True)

    timeout_ms: int = Field(default=10000, alias="timeoutMs")
    poll_ms: int = Field(default=300, alias="pollMs")


class ComputerUseScreenshotPayload(ComputerUseSessionPayload):
    model_config = ConfigDict(populate_by_name=True)

    element_id: Optional[str] = Field(default=None, alias="elementId")
    window_title: Optional[str] = Field(default=None, alias="windowTitle")
    window_handle: Optional[int] = Field(default=None, alias="windowHandle")


class RPARuntimeBasePayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    session_id: Optional[str] = Field(default=None, alias="sessionId")
    run_id: Optional[str] = Field(default=None, alias="runId")
    user_id: Optional[str] = Field(default="anonymous", alias="userId")
    project_id: Optional[str] = Field(default=None, alias="projectId")
    workspace_id: Optional[str] = Field(default=None, alias="workspaceId")
    workspace_path: Optional[str] = Field(default=None, alias="workspacePath")
    trigger_source: Optional[str] = Field(default="manual", alias="triggerSource")
    non_chat_run: bool = Field(default=False, alias="nonChatRun")
    cwd: Optional[str] = None
    output_dir: Optional[str] = Field(default=None, alias="outputDir")


class RPACompileTracePayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    save: bool = True
    run_ids: List[str] = Field(default_factory=list, alias="runIds")


class RPADraftPreparePayload(RPARuntimeBasePayload):
    model_config = ConfigDict(populate_by_name=True)

    variables: Dict[str, Any] = Field(default_factory=dict)


class RPADraftPatchPayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: Optional[str] = None
    goal: Optional[str] = None
    app_id: Optional[str] = Field(default=None, alias="appId")
    steps: Optional[List[Dict[str, Any]]] = None
    variables: Optional[List[Dict[str, Any]]] = None
    metadata_patch: Dict[str, Any] = Field(default_factory=dict, alias="metadataPatch")


class RPADraftCreatePayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: Optional[str] = None
    goal: Optional[str] = None
    app_id: Optional[str] = Field(default=None, alias="appId")
    steps: List[Dict[str, Any]] = Field(default_factory=list)
    variables: List[Dict[str, Any]] = Field(default_factory=list)
    object_library: List[Dict[str, Any]] = Field(default_factory=list, alias="objectLibrary")
    metadata: Dict[str, Any] = Field(default_factory=dict)


class RPADraftStepValidationPayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    step: Dict[str, Any] = Field(default_factory=dict)
    index: Optional[int] = None
    mode: str = "dry_run"
    variables: Dict[str, Any] = Field(default_factory=dict)


class RPADraftRunPayload(RPADraftPreparePayload):
    model_config = ConfigDict(populate_by_name=True)

    timeout_ms: int = Field(default=600000, alias="timeoutMs")


class RPAExistingFlowPayload(RPARuntimeBasePayload):
    model_config = ConfigDict(populate_by_name=True)

    robot_file: str = Field(alias="robotFile")
    variables: Dict[str, Any] = Field(default_factory=dict)
    timeout_ms: int = Field(default=600000, alias="timeoutMs")


class RPARecordingStartPayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    session_id: Optional[str] = Field(default=None, alias="sessionId")
    user_id: Optional[str] = Field(default="admin_ui", alias="userId")
    name: Optional[str] = None
    goal: Optional[str] = None
    target_mode: str = Field(default="agent_browser", alias="targetMode")
    browser_kind: Optional[str] = Field(default=None, alias="browserKind")
    browser_profile_id: Optional[str] = Field(default=None, alias="browserProfileId")
    app_id: Optional[str] = Field(default=None, alias="appId")
    window_handle: Optional[Any] = Field(default=None, alias="windowHandle")
    active_app: Dict[str, Any] = Field(default_factory=dict, alias="activeApp")
    capture_options: Dict[str, Any] = Field(default_factory=dict, alias="captureOptions")


class RPARecordingEventPayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="allow")

    event_id: Optional[str] = Field(default=None, alias="eventId")
    step_id: Optional[str] = Field(default=None, alias="stepId")
    action: str
    intent: Optional[str] = None
    params: Dict[str, Any] = Field(default_factory=dict)
    target: Dict[str, Any] = Field(default_factory=dict)
    coordinate: Dict[str, Any] = Field(default_factory=dict)
    viewport: Dict[str, Any] = Field(default_factory=dict)
    screen: Dict[str, Any] = Field(default_factory=dict)
    selector_candidates: List[Dict[str, Any]] = Field(default_factory=list, alias="selectorCandidates")
    sensitive_input: bool = Field(default=False, alias="sensitiveInput")
    variable_name: Optional[str] = Field(default=None, alias="variableName")
    verification: Dict[str, Any] = Field(default_factory=dict)
    artifacts: List[Dict[str, Any]] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class RPARecordingDesktopSamplePayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="allow")

    event: Dict[str, Any] = Field(default_factory=dict)
    coordinate: Dict[str, Any] = Field(default_factory=dict)
    target: Dict[str, Any] = Field(default_factory=dict)
    params: Dict[str, Any] = Field(default_factory=dict)
    viewport_mapping: Dict[str, Any] = Field(default_factory=dict, alias="viewportMapping")
    forward_action: bool = Field(default=False, alias="forwardAction")


class RPARecordingBrowserCapturePayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="allow")

    target_id: Optional[str] = Field(default=None, alias="targetId")
    target_url: Optional[str] = Field(default=None, alias="targetUrl")
    window_title: Optional[str] = Field(default=None, alias="windowTitle")
    app_id: Optional[str] = Field(default=None, alias="appId")
    max_events: int = Field(default=50, alias="maxEvents")


class RPARecordingCaptureAssistantPayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="allow")

    action: str = "click"
    backend: Optional[str] = None
    target: Dict[str, Any] = Field(default_factory=dict)
    params: Dict[str, Any] = Field(default_factory=dict)
    target_lock: Dict[str, Any] = Field(default_factory=dict, alias="targetLock")
    record_and_forward: bool = Field(default=False, alias="recordAndForward")
    engine_base_url: Optional[str] = Field(default=None, alias="engineBaseUrl")
    hotkey: Optional[str] = None
    cancel_hotkey: Optional[str] = Field(default=None, alias="cancelHotkey")
    mode: str = "capture_only"
    persistent: bool = False


class RPARecordingStopPayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    compile_draft: bool = Field(default=True, alias="compileDraft")
    save: bool = True


class RPATemplateDecisionPayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    reviewer: Optional[str] = "system"
    notes: Optional[str] = None
    metadata_patch: Dict[str, Any] = Field(default_factory=dict, alias="metadataPatch")


class RPATemplateReviewPayload(RPATemplateDecisionPayload):
    model_config = ConfigDict(populate_by_name=True)

    decision: str


class RPATemplateRollbackPayload(RPATemplateDecisionPayload):
    model_config = ConfigDict(populate_by_name=True)

    revision: Optional[int] = None
    history_path: Optional[str] = Field(default=None, alias="historyPath")


class RuntimeCapabilityPolicyPayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    enabled: Optional[bool] = True
    auto_route: Optional[bool] = Field(default=True, alias="autoRoute")
    expose_direct_tools: Optional[bool] = Field(default=True, alias="exposeDirectTools")
    priority: Optional[int] = 100
    notes: Optional[str] = ""


class RuntimeStabilityConfigPayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    strict_supervisor_durability: Optional[bool] = Field(default=True, alias="strictSupervisorDurability")
    session_lane_policy: Optional[str] = Field(default="queue", alias="sessionLanePolicy")
