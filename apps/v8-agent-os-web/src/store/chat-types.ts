import type { RuntimeArtifact } from '@/lib/artifacts';
import type { SessionToolResultStatus } from '@v8/session-realtime';

// --- Base Node ---
export interface UiTimelineNodeBase {
    id: string;
    kind: 'narrative' | 'execution' | 'governance' | 'artifact' | 'system';
    timestamp: number;
    eventSeq?: number;
    eventId?: string;
    runId?: string;
    agentName?: string;
    agentAvatar?: string;
    agentRoleLabel?: string;
    agentType?: 'supervisor' | 'agent' | 'user';
    ownerRuntimeId?: string;
    ownerAgentKind?: string;
    ownerAgentId?: string;
    ownerStreamKey?: string;
    displayInMessage?: boolean;
}

// --- Narrative (User text, Supervisor replies) ---
export interface UiNarrativeNode extends UiTimelineNodeBase {
    kind: 'narrative';
    role: 'user' | 'assistant' | 'system';
    content: string; // Markdown text
    finalized?: boolean;
    partial?: boolean;
}

// --- Execution (Tool calls, reasoning, runtime steps) ---
export interface UiExecutionNode extends UiTimelineNodeBase {
    kind: 'execution';
    executionType: 'reasoning' | 'tool_call' | 'tool_result' | 'runtime_progress' | 'agent_start';
    
    // For reasoning
    content?: string;
    time?: number;
    startTime?: number;
    reasoningKind?: string;
    reasoningSurface?: Record<string, unknown>;
    
    // For tools
    toolCallId?: string;
    toolName?: string;
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    args?: any;
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    result?: any;
    resultStatus?: SessionToolResultStatus;
    resultReasonCode?: string;
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    agentVisibleResult?: any;
    agentVisibleChars?: number;
    mcpApp?: {
        appInstanceId: string;
        serverName?: string;
        resourceUri: string;
        toolInvocationId?: string;
        initialToolResultRef?: string | null;
        csp?: Record<string, unknown>;
        permissions?: Record<string, unknown>;
        status?: string;
        renderer?: string;
        title?: string;
        externalUrl?: string;
        thumbnailUrl?: string;
        fileKey?: string;
        nodeId?: string;
        presentation?: { web?: "inline" | "edge_to_edge"; phone?: "inline" | "modal" };
        allowedFrameOrigins?: string[];
    };
    
    // For runtime progress
    topic?: string;
    label?: string;
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    data?: any;
}

// --- Governance (Approvals, Interrupts) ---
export interface UiGovernanceNode extends UiTimelineNodeBase {
    kind: 'governance';
    governanceType:
        | 'ask_user'
        | 'approval_request'
        | 'approval_resolved'
        | 'run_controlled'
        | 'safety_blocked'
        | 'context_governance'
        | 'lane_updated'
        | 'human_guidance'
        | 'session_coordination';
    
    // Approval
    approvalId?: string;
    approvalKind?: string;
    interactionKind?: string;
    question?: string;
    toolCallId?: string;
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    requestInfo?: any;

    // Control
    topic?: string;
    status?: string;
    reason?: string;
}

// --- Artifact ---
export interface UiArtifactNode extends UiTimelineNodeBase {
    kind: 'artifact';
    artifact: RuntimeArtifact;
}

export type UiTimelineNode = UiNarrativeNode | UiExecutionNode | UiGovernanceNode | UiArtifactNode;

// --- Main Chat Session/Run Model ---
export interface Message {
    id: string;
    renderKey?: string;
    role: 'user' | 'assistant' | 'system' | 'tool';
    runId?: string;
    ordinal?: number;
    turnId?: string;
    turnPosition?: number;
    
    // The textual content for primary narrative
    content: string;
    
    // The new timeline representation for this run/block
    nodes: UiTimelineNode[];
    
    timestamp: number;
    
    // Active identity for the block (Usually Supervisor)
    agentName?: string;
    agentAvatar?: string;
    agentType?: 'supervisor' | 'agent' | 'user';
    agentRoleLabel?: string;
    
    images?: string[];
    artifacts?: RuntimeArtifact[];
    metadata?: Record<string, unknown>;
    
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    toolInvocations?: any;
}
