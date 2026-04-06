import type {
    AdminProcessRef,
    AdminResourceRef,
    AuthoritativeSessionHistoryRecord,
    ContextReferenceItem,
    SessionHistoryControls,
} from "@v8/session-realtime";

export type PhoneUser = {
    id: string;
    email: string;
    login: string;
    name?: string | null;
    image?: string;
    role: string;
    mustChangePassword: boolean;
};

export type AuthSessionPayload = {
    accessToken: string;
    refreshToken: string;
    user: PhoneUser;
};

export type RegisterInput = {
    adminBaseUrl: string;
    login: string;
    password: string;
    name: string;
    email?: string;
    image?: string;
};

export type ConnectionSummary = {
    connection?: {
        adminBaseUrl?: string;
        adminApiBaseUrl?: string;
        configuredAdminApiBaseUrl?: string;
        bridgeMode?: string;
        reachable?: boolean;
        engineBaseUrl?: string;
        desktopLiveBridgeBaseUrl?: string;
    };
    user?: PhoneUser;
};

export type ProjectSummary = {
    id?: string;
    name?: string;
    slug?: string;
    summary?: string;
    status?: string;
    updatedAt?: string;
};

export type MusicTrack = {
    id?: string;
    title?: string;
    artist?: string;
    album?: string;
    coverUrl?: string;
    url?: string;
    durationMs?: number;
};

export type ConversationSummary = AuthoritativeSessionHistoryRecord;

export type CommandPresetSummary = {
    name: string;
    summary?: string;
    filename?: string;
};

export type SkillReferenceSummary = {
    name: string;
    description?: string;
    path?: string;
};

export type ChatArtifact = {
    id?: string;
    artifactId?: string;
    title?: string;
    displayLabel?: string;
    displaySubtitle?: string;
    kind?: string;
    previewUrl?: string;
    externalUrl?: string;
    sourcePath?: string;
    workspacePath?: string;
    mimeType?: string;
    resourceRef?: AdminResourceRef | null;
};

export type PhoneUiTimelineNodeBase = {
    id: string;
    kind: "narrative" | "execution" | "governance" | "artifact" | "system";
    timestamp: number;
    agentName?: string;
    agentAvatar?: string;
    agentRoleLabel?: string;
    agentType?: "supervisor" | "agent" | "user";
};

export type PhoneUiNarrativeNode = PhoneUiTimelineNodeBase & {
    kind: "narrative";
    role: "user" | "assistant" | "system";
    content: string;
};

export type PhoneUiExecutionNode = PhoneUiTimelineNodeBase & {
    kind: "execution";
    executionType: "reasoning" | "tool_call" | "tool_result" | "runtime_progress" | "agent_start";
    content?: string;
    time?: number;
    startTime?: number;
    toolCallId?: string;
    toolName?: string;
    args?: unknown;
    result?: unknown;
    topic?: string;
    label?: string;
    data?: Record<string, unknown>;
};

export type PhoneUiGovernanceNode = PhoneUiTimelineNodeBase & {
    kind: "governance";
    governanceType:
        | "approval_request"
        | "approval_resolved"
        | "run_controlled"
        | "safety_blocked"
        | "context_governance"
        | "lane_updated";
    approvalId?: string;
    approvalKind?: string;
    question?: string;
    toolCallId?: string;
    requestInfo?: unknown;
    topic?: string;
    status?: string;
    reason?: string;
};

export type PhoneUiArtifactNode = PhoneUiTimelineNodeBase & {
    kind: "artifact";
    artifact: ArtifactDetail;
};

export type PhoneUiTimelineNode =
    | PhoneUiNarrativeNode
    | PhoneUiExecutionNode
    | PhoneUiGovernanceNode
    | PhoneUiArtifactNode;

export type ArtifactDetail = {
    id: string;
    artifactId?: string;
    kind?: string;
    mimeType?: string;
    title?: string;
    displayLabel?: string;
    displaySubtitle?: string;
    sessionId?: string;
    runId?: string;
    messageId?: string;
    sourcePath?: string;
    workspacePath?: string;
    externalUrl?: string;
    previewUrl?: string;
    resourceRef?: AdminResourceRef | null;
    createdAt?: string;
    metadata?: Record<string, unknown>;
};

export type UploadedWorkspaceFile = {
    id?: string;
    name?: string;
    url?: string;
    publicUrl?: string;
    path?: string;
    workspacePath?: string;
    type?: string;
    size?: number;
    createdAt?: string;
};

export type ChatMessage = {
    id: string;
    role: "user" | "assistant" | "system" | "tool";
    content: string;
    timestamp?: number;
    runId?: string;
    renderKey?: string;
    agentName?: string;
    agentAvatar?: string;
    agentRoleLabel?: string;
    agentType?: "supervisor" | "agent" | "user";
    nodes?: PhoneUiTimelineNode[];
    images?: string[];
    artifacts?: ChatArtifact[];
    metadata?: {
        commandPreset?: { name?: string };
        skillReferences?: SkillReferenceSummary[];
        taskPlanningMode?: boolean;
        [key: string]: unknown;
    };
    uiEphemeral?: boolean;
    uiStreamPhase?: "placeholder" | "agent_started" | "streaming" | "settling" | "error";
};

export type PendingApproval = {
    id?: string;
    approval_id?: string;
    run_id?: string;
    session_id?: string;
    approval_kind?: string;
    created_at?: string;
    request?: {
        question?: string;
        prompt?: string;
        toolCallId?: string;
        interactionKind?: string;
        [key: string]: unknown;
    };
};

export type ConversationDetail = {
    id: string;
    messages: ChatMessage[];
    latestSeq?: number;
    approvals?: PendingApproval[];
    controls?: SessionHistoryControls;
    recoverable?: boolean | null;
    workflow?: Record<string, unknown> | null;
    workflowProjection?: Record<string, unknown> | null;
    projection?: RealtimeSessionSnapshot & Record<string, unknown>;
    summary?: { title?: string };
};

export type SessionTodoItem = {
    id?: string;
    content?: string;
    status?: string;
};

export type SessionTodoSnapshot = {
    items?: SessionTodoItem[];
    allCompleted?: boolean;
};

export type RealtimeRunSnapshot = {
    id?: string;
    session_id?: string;
    status?: string;
    started_at?: string;
    finished_at?: string;
    trigger_source?: string;
    metadata?: Record<string, unknown>;
};

export type RealtimeSessionSnapshot = {
    sessionId?: string;
    latestSeq?: number;
    messages?: ChatMessage[];
    approvals?: PendingApproval[];
    todos?: SessionTodoSnapshot;
    runtimeEvents?: Array<Record<string, unknown>>;
    currentRun?: RealtimeRunSnapshot | null;
    runtimeStatus?: string;
    processes?: AdminProcessRef[];
    contextReferences?: ContextReferenceItem[];
    projection?: Record<string, unknown>;
    workflowProjection?: Record<string, unknown>;
    summary?: Record<string, unknown>;
    snapshot?: {
        messages?: ChatMessage[];
        artifacts?: ChatArtifact[];
        [key: string]: unknown;
    };
};

export type OperationsSummary = {
    pendingApprovals?: number;
    recentRuns?: number;
    runningCount?: number;
    recoverableCount?: number;
    activeRuns?: number;
    health?: {
        memory?: {
            mode?: string;
        };
    };
};

export type DesktopLiveStatus = {
    available?: boolean;
    reason?: string | null;
    phase?: "idle" | "warming" | "ready" | "degraded";
    bridgeReady?: boolean;
    bridgeStartable?: boolean;
    bridgeWarming?: boolean;
    bridgeReachable?: boolean;
    activeSessionId?: string | null;
    viewerCount?: number;
    warmingStartedAt?: string;
    lastErrorStage?: "spawn" | "port" | "status" | "session" | "offer" | "candidate" | "track";
    retryAllowed?: boolean;
    bridgePid?: number;
    config?: {
        enabled?: boolean;
        maxWidth?: number;
        maxHeight?: number;
        targetFps?: number;
    };
};

export type DesktopLiveSessionPayload = {
    sessionId?: string;
    session_id?: string;
    streamUrl?: string;
    viewerUrl?: string;
    released?: boolean;
    available?: boolean;
    reason?: string | null;
};

export type DesktopLiveOfferPayload = {
    sessionId?: string;
    session_id?: string;
    type?: string;
    sdp?: string;
    error?: string;
};

export type RPAAvailability = {
    robotFramework?: boolean;
    rpaFramework?: boolean;
    libraries?: Record<string, boolean>;
    robotFrameworkDetail?: Record<string, unknown>;
    rpaFrameworkDetail?: Record<string, unknown>;
};

export type RPADraftSummary = {
    id?: string;
    script_id?: string;
    title?: string;
    status?: string;
    created_at?: string;
    updated_at?: string;
};

export type ProfileUpdatePayload = {
    name?: string;
    image?: string;
    email?: string;
};

export type PasswordChangePayload = {
    currentPassword: string;
    nextPassword: string;
};

export type ChatStreamEvent = {
    type: string;
    name?: string;
    content?: string;
    data?: Record<string, unknown>;
    run_id?: string;
    error?: string;
};
