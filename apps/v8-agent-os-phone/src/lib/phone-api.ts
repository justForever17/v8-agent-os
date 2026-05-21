import { buildAdminApiUrl, parseJsonSafe, parseTextSafe, streamNdjson } from "@/src/lib/admin-client";
import { normalizeSessionHistoryItem, normalizeSessionHistoryList } from "@/src/lib/session-history";
import { translateCurrent } from "@/src/lib/locale";
import type {
    ArtifactDetail,
    AuthSessionPayload,
    ChatStreamEvent,
    ChatSubmitResponse,
    CommandPresetSummary,
    ConnectionSummary,
    ContextMentionSummary,
    ConversationDetail,
    ConversationSummary,
    DesktopLiveOfferPayload,
    DesktopLiveSessionPayload,
    DesktopLiveStatus,
    PasswordChangePayload,
    PendingApproval,
    PhoneUser,
    ProfileUpdatePayload,
    ProjectSummary,
    QueuedChatMessage,
    WorkspaceFolderNode,
    WorkspaceFolderTreeResponse,
    ScopeBindingView,
    RegisterInput,
    RealtimeSessionSnapshot,
    RPAAvailability,
    RPADraftSummary,
    RPARobotScriptSummary,
    RPATemplateSummary,
    SkillReferenceSummary,
    SubagentFamilySummary,
    AdminProcessRef,
    MusicTrack,
    UploadedWorkspaceFile,
} from "@/src/types/admin";

type AuthorizedFetch = (path: string, init?: RequestInit) => Promise<Response>;
type AuthorizedRealtimeStream = (
    path: string,
    onEvent: (eventName: string, payload: unknown) => void,
    signal?: AbortSignal,
) => Promise<void>;

type CreateConversationInput = {
    title?: string;
    projectId?: string;
    workspaceId?: string;
    workspacePath?: string;
    threadId?: string;
    scopeHint?: string;
    scopeMode?: string;
};

type CreateProjectInput = {
    name?: string;
    workspacePath?: string;
};

function normalizeArray<T>(value: unknown): T[] {
    return Array.isArray(value) ? (value as T[]) : [];
}

async function readJsonOrThrow<T>(response: Response, fallbackMessage: string): Promise<T> {
    const payload = await parseJsonSafe<T & { error?: string }>(response);
    if (!response.ok) {
        const detail = payload && typeof payload === "object" && "error" in payload
            ? String(payload.error || fallbackMessage)
            : await parseTextSafe(response) || fallbackMessage;
        throw new Error(detail || fallbackMessage);
    }
    return (payload || {}) as T;
}

async function authorizedJson<T>(
    authorizedFetch: AuthorizedFetch,
    path: string,
    fallbackMessage: string,
    init?: RequestInit,
) {
    const response = await authorizedFetch(path, init);
    return readJsonOrThrow<T>(response, fallbackMessage);
}

export async function signUp(adminBaseUrl: string, input: RegisterInput): Promise<AuthSessionPayload> {
    const response = await fetch(buildAdminApiUrl(adminBaseUrl, "/api/client/auth/register"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
            login: input.login,
            password: input.password,
            name: input.name,
            email: input.email,
            image: input.image,
            deviceName: "v8-phone",
        }),
    });
    return readJsonOrThrow<AuthSessionPayload>(response, translateCurrent("src.providers.app_session.text_4"));
}

export async function getConnectionSummary(authorizedFetch: AuthorizedFetch) {
    return authorizedJson<ConnectionSummary>(authorizedFetch, "/api/client/connection", translateCurrent("src.lib.phone_api.text_2"), {
        cache: "no-store",
    });
}

export async function getCurrentProfile(authorizedFetch: AuthorizedFetch) {
    const payload = await authorizedJson<{ user?: PhoneUser }>(
        authorizedFetch,
        "/api/client/auth/profile",
        translateCurrent("src.lib.phone_api.text_3"),
        { cache: "no-store" },
    );
    return payload.user || null;
}

export async function updateProfile(authorizedFetch: AuthorizedFetch, input: ProfileUpdatePayload) {
    const payload = await authorizedJson<{ user?: PhoneUser }>(
        authorizedFetch,
        "/api/client/auth/profile",
        translateCurrent("src.lib.phone_api.text_4"),
        {
            method: "PATCH",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(input),
        },
    );
    return payload.user || null;
}

export async function updatePassword(authorizedFetch: AuthorizedFetch, input: PasswordChangePayload) {
    return authorizedJson<Record<string, unknown>>(
        authorizedFetch,
        "/api/client/auth/password",
        translateCurrent("src.lib.phone_api.text_5"),
        {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                currentPassword: input.currentPassword,
                newPassword: input.nextPassword,
            }),
        },
    );
}

export async function uploadUserAvatar(
    authorizedFetch: AuthorizedFetch,
    file: { uri: string; name?: string; type?: string },
) {
    const form = new FormData();
    form.append("file", {
        uri: file.uri,
        name: file.name || `avatar-${Date.now()}.jpg`,
        type: file.type || "image/jpeg",
    } as unknown as Blob);
    const response = await authorizedFetch("/api/client/user-avatar-upload", {
        method: "POST",
        body: form,
    });
    return readJsonOrThrow<{ url?: string; path?: string }>(response, translateCurrent("src.lib.phone_api.text_6"));
}

export async function uploadAttachment(
    authorizedFetch: AuthorizedFetch,
    file: { uri: string; name?: string; type?: string },
    scope?: { sessionId?: string | null; conversationId?: string | null; workspaceId?: string | null; workspacePath?: string | null; projectId?: string | null },
) {
    try {
        const form = new FormData();
        form.append("file", {
            uri: file.uri,
            name: file.name || `upload-${Date.now()}`,
            type: file.type || "application/octet-stream",
        } as unknown as Blob);
        const sessionId = String(scope?.sessionId || scope?.conversationId || "").trim();
        if (sessionId) {
            form.append("sessionId", sessionId);
            form.append("conversationId", sessionId);
        }
        const workspaceId = String(scope?.workspaceId || "").trim();
        if (workspaceId) form.append("workspaceId", workspaceId);
        const workspacePath = String(scope?.workspacePath || "").trim();
        if (workspacePath) form.append("workspacePath", workspacePath);
        const projectId = String(scope?.projectId || "").trim();
        if (projectId) form.append("projectId", projectId);
        const response = await authorizedFetch("/api/client/upload", {
            method: "POST",
            body: form,
        });
        return readJsonOrThrow<UploadedWorkspaceFile>(response, translateCurrent("src.lib.phone_api.text_7"));
    } catch (error) {
        const message = error instanceof Error ? String(error.message || "").trim() : "";
        const lowered = message.toLowerCase();
        const label = file.name ? `“${file.name}”` : translateCurrent("shared.upload.file_fallback_label");
        const scheme = String(file.uri || "").split(":")[0] || "unknown";
        if (lowered.includes("network request failed")) {
            throw new Error(translateCurrent("shared.upload.request_interrupted", { label }));
        }
        if (lowered.includes("fetch failed") || lowered.includes("failed to fetch")) {
            throw new Error(translateCurrent("shared.upload.transport_failed_with_scheme", { label, scheme }));
        }
        throw error instanceof Error ? error : new Error(translateCurrent("shared.upload.generic_failed", { label }));
    }
}

export async function listProjects(authorizedFetch: AuthorizedFetch) {
    const payload = await authorizedJson<{ projects?: ProjectSummary[] }>(
        authorizedFetch,
        "/api/client/projects",
        translateCurrent("src.lib.phone_api.text_9"),
        { cache: "no-store" },
    );
    return normalizeArray<ProjectSummary>(payload.projects);
}

export async function getProjectsRegistry(authorizedFetch: AuthorizedFetch) {
    const payload = await authorizedJson<{ projects?: ProjectSummary[]; defaultProjectId?: string | null; mainWorkspacePath?: string }>(
        authorizedFetch,
        "/api/client/projects",
        translateCurrent("src.lib.phone_api.text_9"),
        { cache: "no-store" },
    );
    return {
        projects: normalizeArray<ProjectSummary>(payload.projects),
        defaultProjectId: typeof (payload as { defaultProjectId?: unknown }).defaultProjectId === "string"
            ? (payload as { defaultProjectId?: string }).defaultProjectId
            : null,
        mainWorkspacePath: typeof payload.mainWorkspacePath === "string" ? payload.mainWorkspacePath : "",
    };
}

export async function createProject(authorizedFetch: AuthorizedFetch, input: CreateProjectInput) {
    return authorizedJson<ProjectSummary>(
        authorizedFetch,
        "/api/client/projects",
        translateCurrent("src.lib.phone_api.text_11"),
        {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(input),
        },
    );
}

export async function listWorkspaceFolders(
    authorizedFetch: AuthorizedFetch,
    input: { path?: string; maxDepth?: number; maxChildren?: number; cursor?: string } = {},
) {
    const params = new URLSearchParams();
    if (input.path) params.set("path", input.path);
    if (typeof input.maxDepth === "number") params.set("maxDepth", String(input.maxDepth));
    if (typeof input.maxChildren === "number") params.set("maxChildren", String(input.maxChildren));
    if (input.cursor) params.set("cursor", input.cursor);
    const suffix = params.toString() ? `?${params.toString()}` : "";
    return authorizedJson<WorkspaceFolderTreeResponse>(
        authorizedFetch,
        `/api/client/workspace/folders${suffix}`,
        translateCurrent("src.lib.phone_api.workspace_folders"),
        { cache: "no-store" },
    );
}

export async function createWorkspaceFolder(
    authorizedFetch: AuthorizedFetch,
    input: { parentPath: string; folderName: string },
) {
    const payload = await authorizedJson<{ folder?: WorkspaceFolderNode }>(
        authorizedFetch,
        "/api/client/workspace/folders",
        translateCurrent("src.lib.phone_api.workspace_folder_create"),
        {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(input),
        },
    );
    return payload.folder || null;
}

export async function getSessionScope(authorizedFetch: AuthorizedFetch, sessionId: string) {
    const payload = await authorizedJson<{ binding?: ScopeBindingView | null }>(
        authorizedFetch,
        `/api/client/sessions/${encodeURIComponent(sessionId)}/scope`,
        translateCurrent("src.lib.phone_api.scope"),
        { cache: "no-store" },
    );
    return payload.binding || null;
}

export async function updateSessionScope(
    authorizedFetch: AuthorizedFetch,
    sessionId: string,
    input: {
        projectId?: string;
        workspaceId?: string;
        workspacePath?: string;
        threadId?: string;
        scopeHint?: string;
        scopeSource?: string;
        scopeConfidence?: number;
    },
) {
    const payload = await authorizedJson<{ binding?: ScopeBindingView | null }>(
        authorizedFetch,
        `/api/client/sessions/${encodeURIComponent(sessionId)}/scope`,
        translateCurrent("src.lib.phone_api.scope_2"),
        {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(input),
        },
    );
    return payload.binding || null;
}

export async function reresolveSessionScope(
    authorizedFetch: AuthorizedFetch,
    sessionId: string,
    input: {
        userQuery?: string;
        projectId?: string;
        workspaceId?: string;
        workspacePath?: string;
        threadId?: string;
        scopeHint?: string;
        scopeMode?: string;
    },
) {
    const payload = await authorizedJson<{ binding?: ScopeBindingView | null }>(
        authorizedFetch,
        `/api/client/sessions/${encodeURIComponent(sessionId)}/scope/re-resolve`,
        translateCurrent("src.lib.phone_api.scope_3"),
        {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                sessionId,
                ...input,
            }),
        },
    );
    return payload.binding || null;
}

export async function listMusicTracks(authorizedFetch: AuthorizedFetch) {
    const payload = await authorizedJson<{ tracks?: MusicTrack[]; items?: MusicTrack[] }>(
        authorizedFetch,
        "/api/client/music",
        translateCurrent("src.lib.phone_api.text_12"),
        { cache: "no-store" },
    );
    return normalizeArray<MusicTrack>(payload.tracks || payload.items).map((track) => {
        const legacyTrack = track as MusicTrack & { audioUrl?: string };
        return {
            ...track,
            url: track.url || legacyTrack.audioUrl || "",
        };
    });
}

export async function listConversations(authorizedFetch: AuthorizedFetch) {
    const payload = await authorizedJson<ConversationSummary[]>(authorizedFetch, "/api/client/conversations", translateCurrent("src.lib.phone_api.text_13"), {
        cache: "no-store",
    });
    return normalizeSessionHistoryList(Array.isArray(payload) ? payload : []);
}

export async function createConversation(
    authorizedFetch: AuthorizedFetch,
    input?: string | CreateConversationInput,
) {
    const requestBody: Record<string, unknown> = {};
    if (typeof input === "string") {
        requestBody.title = input;
    } else if (input && typeof input === "object") {
        if (typeof input.title === "string") {
            requestBody.title = input.title;
        }
        if (typeof input.projectId === "string" && input.projectId.trim()) {
            requestBody.projectId = input.projectId.trim();
        }
        if (typeof input.workspaceId === "string" && input.workspaceId.trim()) {
            requestBody.workspaceId = input.workspaceId.trim();
        }
        if (typeof input.workspacePath === "string" && input.workspacePath.trim()) {
            requestBody.workspacePath = input.workspacePath.trim();
        }
        if (typeof input.threadId === "string" && input.threadId.trim()) {
            requestBody.threadId = input.threadId.trim();
        }
        if (typeof input.scopeHint === "string" && input.scopeHint.trim()) {
            requestBody.scopeHint = input.scopeHint.trim();
        }
        if (typeof input.scopeMode === "string" && input.scopeMode.trim()) {
            requestBody.scopeMode = input.scopeMode.trim();
        }
    }
    const payload = await authorizedJson<ConversationSummary>(authorizedFetch, "/api/client/conversations", translateCurrent("src.lib.phone_api.text_14"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(requestBody),
    });
    return normalizeSessionHistoryItem(payload);
}

export async function deleteConversation(authorizedFetch: AuthorizedFetch, id: string) {
    return authorizedJson<{ success: boolean }>(
        authorizedFetch,
        `/api/client/conversations/${encodeURIComponent(id)}`,
        translateCurrent("src.lib.phone_api.text_15"),
        { method: "DELETE" },
    );
}

export async function getConversationDetail(authorizedFetch: AuthorizedFetch, id: string) {
    return authorizedJson<ConversationDetail>(
        authorizedFetch,
        `/api/client/conversations/${encodeURIComponent(id)}`,
        translateCurrent("src.lib.phone_api.text_16"),
        { cache: "no-store" },
    );
}

export async function getSessionProcesses(authorizedFetch: AuthorizedFetch, id: string) {
    const payload = await authorizedJson<{
        sessionId?: string;
        currentRunId?: string | null;
        latestSeq?: number;
        processes?: AdminProcessRef[];
    }>(
        authorizedFetch,
        `/api/client/sessions/${encodeURIComponent(id)}/processes`,
        translateCurrent("src.lib.phone_api.text_17"),
        { cache: "no-store" },
    );
    return {
        sessionId: String(payload.sessionId || id),
        currentRunId: typeof payload.currentRunId === "string" ? payload.currentRunId : null,
        latestSeq: Number(payload.latestSeq || 0) || 0,
        processes: normalizeArray<AdminProcessRef>(payload.processes),
    };
}

export async function listCommandPresets(authorizedFetch: AuthorizedFetch) {
    const payload = await authorizedJson<{ items?: CommandPresetSummary[] }>(
        authorizedFetch,
        "/api/client/commands",
        translateCurrent("src.lib.phone_api.text_18"),
        { cache: "no-store" },
    );
    return normalizeArray<CommandPresetSummary>(payload.items);
}

export async function getCommandPreset(authorizedFetch: AuthorizedFetch, name: string) {
    return authorizedJson<Record<string, unknown>>(
        authorizedFetch,
        `/api/client/commands/${encodeURIComponent(name)}`,
        translateCurrent("src.lib.phone_api.text_19"),
        { cache: "no-store" },
    );
}

export async function listSkills(authorizedFetch: AuthorizedFetch) {
    const payload = await authorizedJson<{ skills?: SkillReferenceSummary[] }>(
        authorizedFetch,
        "/api/client/skills/list",
        translateCurrent("src.lib.phone_api.text_20"),
        { cache: "no-store" },
    );
    return normalizeArray<SkillReferenceSummary>(payload.skills);
}

export async function listSkillsAndSubagentFamilies(authorizedFetch: AuthorizedFetch) {
    const payload = await authorizedJson<{ skills?: SkillReferenceSummary[]; subagentFamilies?: SubagentFamilySummary[] }>(
        authorizedFetch,
        "/api/client/skills/list",
        translateCurrent("src.lib.phone_api.text_20"),
        { cache: "no-store" },
    );
    return {
        skills: normalizeArray<SkillReferenceSummary>(payload.skills),
        subagentFamilies: normalizeArray<SubagentFamilySummary>(payload.subagentFamilies)
            .map((family) => ({
                ...family,
                familyId: String(family.familyId || "").trim(),
                displayName: String(family.displayName || family.familyId || "").trim(),
                aliases: Array.isArray(family.aliases) ? family.aliases : [],
                description: String(family.description || "").trim(),
                memberCount: Number(family.memberCount || 0) || 0,
            }))
            .filter((family) => family.familyId),
    };
}

export async function listArtifacts(authorizedFetch: AuthorizedFetch, conversationId?: string | null) {
    const search = conversationId ? `?sessionId=${encodeURIComponent(conversationId)}` : "";
    const payload = await authorizedJson<{ artifacts?: ArtifactDetail[] }>(
        authorizedFetch,
        `/api/client/artifacts${search}`,
        translateCurrent("src.lib.phone_api.text_21"),
        { cache: "no-store" },
    );
    return normalizeArray<ArtifactDetail>(payload.artifacts);
}

export async function getArtifact(authorizedFetch: AuthorizedFetch, id: string) {
    return authorizedJson<ArtifactDetail>(
        authorizedFetch,
        `/api/client/artifacts/${encodeURIComponent(id)}`,
        translateCurrent("src.lib.phone_api.text_22"),
        { cache: "no-store" },
    );
}

export function getArtifactContentUrl(adminBaseUrl: string, id: string) {
    return buildAdminApiUrl(adminBaseUrl, `/api/client/artifacts/${encodeURIComponent(id)}/content`);
}

export function getWorkspaceFileUrl(adminBaseUrl: string, path: string) {
    const normalized = path
        .split("/")
        .filter(Boolean)
        .map((segment) => encodeURIComponent(segment))
        .join("/");
    return buildAdminApiUrl(adminBaseUrl, `/api/client/workspace/files/${normalized}`);
}

export async function fetchArtifactContentResponse(authorizedFetch: AuthorizedFetch, id: string) {
    const response = await authorizedFetch(`/api/client/artifacts/${encodeURIComponent(id)}/content`, {
        cache: "no-store",
    });
    if (!response.ok) {
        const detail = await parseTextSafe(response);
        throw new Error(detail || translateCurrent("src.lib.phone_api.text_23"));
    }
    return response;
}

export async function fetchWorkspaceFileResponse(authorizedFetch: AuthorizedFetch, path: string) {
    const normalized = path
        .split("/")
        .filter(Boolean)
        .map((segment) => encodeURIComponent(segment))
        .join("/");
    const response = await authorizedFetch(`/api/client/workspace/files/${normalized}`, {
        cache: "no-store",
    });
    if (!response.ok) {
        const detail = await parseTextSafe(response);
        throw new Error(detail || translateCurrent("src.lib.phone_api.text_24"));
    }
    return response;
}

export async function deleteMessage(authorizedFetch: AuthorizedFetch, id: string) {
    return authorizedJson<Record<string, unknown>>(
        authorizedFetch,
        `/api/client/messages/${encodeURIComponent(id)}`,
        translateCurrent("src.lib.phone_api.text_25"),
        { method: "DELETE" },
    );
}

export async function listPendingApprovals(authorizedFetch: AuthorizedFetch, conversationId?: string) {
    const suffix = conversationId
        ? `?status=pending&session_id=${encodeURIComponent(conversationId)}`
        : "?status=pending";
    const payload = await authorizedJson<{ approvals?: PendingApproval[] }>(
        authorizedFetch,
        `/api/client/approvals${suffix}`,
        translateCurrent("src.lib.phone_api.text_26"),
        { cache: "no-store" },
    );
    return normalizeArray<PendingApproval>(payload.approvals);
}

export async function approvePendingItem(
    authorizedFetch: AuthorizedFetch,
    approvalId: string,
    answer: string,
    approve = true,
) {
    const path = approve
        ? `/api/client/approvals/${encodeURIComponent(approvalId)}/approve`
        : `/api/client/approvals/${encodeURIComponent(approvalId)}/reject`;
    return authorizedJson<Record<string, unknown>>(authorizedFetch, path, approve ? translateCurrent("src.lib.phone_api.text_27") : translateCurrent("src.lib.phone_api.text_28"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
            response: {
                answer,
                approved: approve,
            },
        }),
    });
}

export async function respondAskUser(
    authorizedFetch: AuthorizedFetch,
    interactionId: string,
    answer: string,
) {
    return authorizedJson<Record<string, unknown>>(
        authorizedFetch,
        `/api/client/ask-user/${encodeURIComponent(interactionId)}/respond`,
        translateCurrent("src.lib.phone_api.text_29"),
        {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ answer }),
        },
    );
}

export async function dispatchRunCommand(
    authorizedFetch: AuthorizedFetch,
    runId: string,
    command: "interrupt" | "retry",
    reason?: string,
) {
    return authorizedJson<Record<string, unknown>>(
        authorizedFetch,
        `/api/client/runs/${encodeURIComponent(runId)}/commands/${encodeURIComponent(command)}`,
        command === "interrupt" ? translateCurrent("src.lib.phone_api.text_30") : translateCurrent("src.lib.phone_api.text_31"),
        {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ reason }),
        },
    );
}

export async function getRealtimeSnapshot(authorizedFetch: AuthorizedFetch, conversationId: string) {
    return authorizedJson<RealtimeSessionSnapshot>(
        authorizedFetch,
        `/api/client/realtime/sessions/${encodeURIComponent(conversationId)}/snapshot`,
        translateCurrent("src.lib.phone_api.text_32"),
        { cache: "no-store" },
    );
}

export async function streamRealtimeSession(
    authorizedRealtimeStream: AuthorizedRealtimeStream,
    conversationId: string,
    onEvent: (eventName: string, payload: unknown) => void,
    signal?: AbortSignal,
) {
    await authorizedRealtimeStream(
        `/api/client/realtime/sessions/${encodeURIComponent(conversationId)}/stream`,
        onEvent,
        signal,
    );
}

export async function submitChatMessage(
    authorizedFetch: AuthorizedFetch,
    userText: string,
    options: SendChatOptions,
) {
    const response = await authorizedFetch("/api/client/chat-submit", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
            clientMessageId: options.clientMessageId || undefined,
            messages: [
                ...options.messages,
                { role: "user", content: userText },
            ],
            data: {
                conversationId: options.conversationId || undefined,
                clientMessageId: options.clientMessageId || undefined,
                commandPreset: options.commandPresetName ? { name: options.commandPresetName } : undefined,
                fileUrls: Array.isArray(options.fileUrls) && options.fileUrls.length > 0 ? options.fileUrls : undefined,
                attachments: Array.isArray(options.attachments) && options.attachments.length > 0 ? options.attachments : undefined,
                plannerMode: options.taskPlanningMode ? "force" : undefined,
                taskPlanningMode: options.taskPlanningMode ? true : undefined,
                taskPlanningSource: options.taskPlanningMode ? "composer" : undefined,
                taskPlanningRequestedByComposer: options.taskPlanningMode ? true : undefined,
                skillReferences: Array.isArray(options.skillReferences) && options.skillReferences.length > 0
                    ? options.skillReferences.map((skill) => ({
                        name: skill.name,
                        description: skill.description,
                        path: skill.path,
                    }))
                    : undefined,
                contextMentions: Array.isArray(options.contextMentions) && options.contextMentions.length > 0
                    ? options.contextMentions
                    : undefined,
            },
        }),
    });

    return readJsonOrThrow<ChatSubmitResponse>(response, translateCurrent("src.lib.phone_api.text_33"));
}

export async function promoteQueuedChatMessage(authorizedFetch: AuthorizedFetch, queueMessageId: string) {
    return authorizedJson<{ ok?: boolean; queuedMessage?: QueuedChatMessage }>(
        authorizedFetch,
        `/api/client/chat-queue/${encodeURIComponent(queueMessageId)}/promote`,
        translateCurrent("src.lib.phone_api.promote_queued_message_failed"),
        { method: "POST" },
    );
}

export async function cancelQueuedChatMessage(authorizedFetch: AuthorizedFetch, queueMessageId: string) {
    return authorizedJson<{ ok?: boolean; queuedMessage?: QueuedChatMessage }>(
        authorizedFetch,
        `/api/client/chat-queue/${encodeURIComponent(queueMessageId)}`,
        translateCurrent("src.lib.phone_api.cancel_queued_message_failed"),
        { method: "DELETE" },
    );
}

export async function updateQueuedChatMessage(authorizedFetch: AuthorizedFetch, queueMessageId: string, content: string) {
    return authorizedJson<{ ok?: boolean; queuedMessage?: QueuedChatMessage }>(
        authorizedFetch,
        `/api/client/chat-queue/${encodeURIComponent(queueMessageId)}`,
        translateCurrent("src.lib.phone_api.update_queued_message_failed"),
        {
            method: "PATCH",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ content }),
        },
    );
}

export async function getDesktopLiveStatus(authorizedFetch: AuthorizedFetch) {
    return authorizedJson<DesktopLiveStatus>(
        authorizedFetch,
        "/api/client/desktop-live/status",
        translateCurrent("src.lib.phone_api.desktop_live"),
        { cache: "no-store" },
    );
}

export async function prepareDesktopLive(authorizedFetch: AuthorizedFetch) {
    return authorizedJson<DesktopLiveStatus>(
        authorizedFetch,
        "/api/client/desktop-live/prepare",
        translateCurrent("src.lib.phone_api.desktop_live_2"),
        { method: "POST" },
    );
}

export async function createDesktopLiveSession(authorizedFetch: AuthorizedFetch) {
    return authorizedJson<DesktopLiveSessionPayload>(
        authorizedFetch,
        "/api/client/desktop-live/session",
        translateCurrent("src.lib.phone_api.desktop_live_3"),
        { method: "POST" },
    );
}

export async function createDesktopLiveOffer(
    authorizedFetch: AuthorizedFetch,
    payload: Record<string, unknown>,
) {
    return authorizedJson<DesktopLiveOfferPayload>(
        authorizedFetch,
        "/api/client/desktop-live/offer",
        translateCurrent("src.lib.phone_api.desktop_live_offer"),
        {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
        },
    );
}

export async function sendDesktopLiveCandidate(
    authorizedFetch: AuthorizedFetch,
    payload: Record<string, unknown>,
) {
    return authorizedJson<Record<string, unknown>>(
        authorizedFetch,
        "/api/client/desktop-live/candidate",
        translateCurrent("src.lib.phone_api.desktop_live_candidate"),
        {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
        },
    );
}

export async function releaseDesktopLiveSession(authorizedFetch: AuthorizedFetch, sessionId: string) {
    return authorizedJson<DesktopLiveSessionPayload>(
        authorizedFetch,
        `/api/client/desktop-live/session/${encodeURIComponent(sessionId)}`,
        translateCurrent("src.lib.phone_api.desktop_live_4"),
        { method: "DELETE" },
    );
}

export function getDesktopLiveStreamUrl(adminBaseUrl: string, sessionId: string) {
    return buildAdminApiUrl(
        adminBaseUrl,
        `/api/client/desktop-live/stream?sessionId=${encodeURIComponent(sessionId)}`,
    );
}

export async function getRpaAvailability(authorizedFetch: AuthorizedFetch) {
    return authorizedJson<RPAAvailability>(
        authorizedFetch,
        "/api/client/rpa/availability",
        translateCurrent("src.lib.phone_api.rpa"),
        { cache: "no-store" },
    );
}

export async function listRpaDrafts(authorizedFetch: AuthorizedFetch, limit = 8) {
    const payload = await authorizedJson<{ drafts?: RPADraftSummary[] }>(
        authorizedFetch,
        `/api/client/rpa/drafts?limit=${encodeURIComponent(String(limit))}`,
        translateCurrent("src.lib.phone_api.rpa_2"),
        { cache: "no-store" },
    );
    return normalizeArray<RPADraftSummary>(payload.drafts);
}

export async function listRpaTemplates(authorizedFetch: AuthorizedFetch, limit = 50, status = "approved") {
    const payload = await authorizedJson<{ templates?: RPATemplateSummary[] }>(
        authorizedFetch,
        `/api/client/rpa/templates?limit=${encodeURIComponent(String(limit))}&status=${encodeURIComponent(status)}`,
        translateCurrent("src.lib.phone_api.rpa_2"),
        { cache: "no-store" },
    );
    return normalizeArray<RPATemplateSummary>(payload.templates);
}

export async function listRpaScripts(authorizedFetch: AuthorizedFetch, limit = 50) {
    const payload = await authorizedJson<{ scripts?: RPARobotScriptSummary[] }>(
        authorizedFetch,
        `/api/client/rpa/scripts?limit=${encodeURIComponent(String(limit))}`,
        translateCurrent("src.lib.phone_api.rpa_6"),
        { cache: "no-store" },
    );
    return normalizeArray<RPARobotScriptSummary>(payload.scripts);
}

export async function runRpaCompile(authorizedFetch: AuthorizedFetch, runIds: string[]) {
    const endpoint = runIds.length === 1
        ? `/api/client/rpa/compile/${encodeURIComponent(runIds[0])}`
        : "/api/client/rpa/compile";
    const body = runIds.length === 1 ? { save: true } : { runIds, save: true };
    return authorizedJson<Record<string, unknown>>(authorizedFetch, endpoint, translateCurrent("src.lib.phone_api.rpa_3"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
    });
}

export async function runRpaDraft(
    authorizedFetch: AuthorizedFetch,
    scriptId: string,
    variables: Record<string, unknown>,
) {
    return authorizedJson<Record<string, unknown>>(
        authorizedFetch,
        `/api/client/rpa/drafts/${encodeURIComponent(scriptId)}/run`,
        translateCurrent("src.lib.phone_api.rpa_5"),
        {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ variables, triggerSource: "rpa_phone", nonChatRun: true }),
        },
    );
}

export async function runExistingRobotFlow(
    authorizedFetch: AuthorizedFetch,
    robotFile: string,
    variables: Record<string, unknown>,
) {
    return authorizedJson<Record<string, unknown>>(
        authorizedFetch,
        "/api/client/rpa/run-existing",
        translateCurrent("src.lib.phone_api.rpa_4"),
        {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ robotFile, variables, triggerSource: "rpa_phone", nonChatRun: true }),
        },
    );
}

export async function speechToText(authorizedFetch: AuthorizedFetch, formData: FormData) {
    const response = await authorizedFetch("/api/client/audio/stt", {
        method: "POST",
        body: formData,
    });
    return readJsonOrThrow<Record<string, unknown>>(response, translateCurrent("src.lib.phone_api.text_34"));
}

export async function requestTextToSpeech(authorizedFetch: AuthorizedFetch, payload: Record<string, unknown>) {
    const response = await authorizedFetch("/api/client/audio/tts", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
    });
    if (!response.ok) {
        const errorPayload = await parseJsonSafe<Record<string, unknown>>(response.clone());
        const detail = String(
            errorPayload?.detail
            || errorPayload?.error
            || await parseTextSafe(response)
            || translateCurrent("src.lib.phone_api.text_35"),
        ).trim();
        throw new Error(detail || translateCurrent("src.lib.phone_api.text_35"));
    }
    return response;
}

type SendChatOptions = {
    messages: Array<{ role: string; content: string }>;
    conversationId?: string | null;
    clientMessageId?: string | null;
    commandPresetName?: string | null;
    skillReferences?: SkillReferenceSummary[];
    contextMentions?: ContextMentionSummary[];
    fileUrls?: string[];
    attachments?: Array<Record<string, unknown>>;
    taskPlanningMode?: boolean;
};

export async function sendChatMessageStream(
    authorizedFetch: AuthorizedFetch,
    userText: string,
    options: SendChatOptions,
    onEvent: (event: ChatStreamEvent) => void,
) {
    const response = await authorizedFetch("/api/client/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
            clientMessageId: options.clientMessageId || undefined,
            messages: [
                ...options.messages,
                { role: "user", content: userText },
            ],
            data: {
                conversationId: options.conversationId || undefined,
                clientMessageId: options.clientMessageId || undefined,
                commandPreset: options.commandPresetName ? { name: options.commandPresetName } : undefined,
                fileUrls: Array.isArray(options.fileUrls) && options.fileUrls.length > 0 ? options.fileUrls : undefined,
                attachments: Array.isArray(options.attachments) && options.attachments.length > 0 ? options.attachments : undefined,
                plannerMode: options.taskPlanningMode ? "force" : undefined,
                taskPlanningMode: options.taskPlanningMode ? true : undefined,
                taskPlanningSource: options.taskPlanningMode ? "composer" : undefined,
                taskPlanningRequestedByComposer: options.taskPlanningMode ? true : undefined,
                skillReferences: Array.isArray(options.skillReferences) && options.skillReferences.length > 0
                    ? options.skillReferences.map((skill) => ({
                        name: skill.name,
                        description: skill.description,
                        path: skill.path,
                    }))
                    : undefined,
                contextMentions: Array.isArray(options.contextMentions) && options.contextMentions.length > 0
                    ? options.contextMentions
                    : undefined,
            },
        }),
    });

    if (!response.ok) {
        const detail = await parseTextSafe(response);
        throw new Error(detail || translateCurrent("src.lib.phone_api.text_37"));
    }

    await streamNdjson(response, onEvent);
}
