import { buildAdminApiUrl, parseJsonSafe, parseTextSafe, streamNdjson } from "@/src/lib/admin-client";
import { normalizeSessionHistoryItem, normalizeSessionHistoryList } from "@/src/lib/session-history";
import type {
    ArtifactDetail,
    AuthSessionPayload,
    ChatStreamEvent,
    ChatSubmitResponse,
    CommandPresetSummary,
    ConnectionSummary,
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
    ScopeBindingView,
    RegisterInput,
    RealtimeSessionSnapshot,
    RPAAvailability,
    RPADraftSummary,
    SkillReferenceSummary,
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
    return readJsonOrThrow<AuthSessionPayload>(response, "注册失败");
}

export async function getConnectionSummary(authorizedFetch: AuthorizedFetch) {
    return authorizedJson<ConnectionSummary>(authorizedFetch, "/api/client/connection", "读取连接摘要失败", {
        cache: "no-store",
    });
}

export async function getCurrentProfile(authorizedFetch: AuthorizedFetch) {
    const payload = await authorizedJson<{ user?: PhoneUser }>(
        authorizedFetch,
        "/api/client/auth/profile",
        "读取个人信息失败",
        { cache: "no-store" },
    );
    return payload.user || null;
}

export async function updateProfile(authorizedFetch: AuthorizedFetch, input: ProfileUpdatePayload) {
    const payload = await authorizedJson<{ user?: PhoneUser }>(
        authorizedFetch,
        "/api/client/auth/profile",
        "更新个人信息失败",
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
        "修改密码失败",
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
    return readJsonOrThrow<{ url?: string; path?: string }>(response, "头像上传失败");
}

export async function uploadAttachment(
    authorizedFetch: AuthorizedFetch,
    file: { uri: string; name?: string; type?: string },
) {
    const form = new FormData();
    form.append("file", {
        uri: file.uri,
        name: file.name || `upload-${Date.now()}`,
        type: file.type || "application/octet-stream",
    } as unknown as Blob);
    const response = await authorizedFetch("/api/client/upload", {
        method: "POST",
        body: form,
    });
    return readJsonOrThrow<UploadedWorkspaceFile>(response, "附件上传失败");
}

export async function listProjects(authorizedFetch: AuthorizedFetch) {
    const payload = await authorizedJson<{ projects?: ProjectSummary[] }>(
        authorizedFetch,
        "/api/client/projects",
        "读取项目列表失败",
        { cache: "no-store" },
    );
    return normalizeArray<ProjectSummary>(payload.projects);
}

export async function getProjectsRegistry(authorizedFetch: AuthorizedFetch) {
    const payload = await authorizedJson<{ projects?: ProjectSummary[] }>(
        authorizedFetch,
        "/api/client/projects",
        "读取项目列表失败",
        { cache: "no-store" },
    );
    return {
        projects: normalizeArray<ProjectSummary>(payload.projects),
        defaultProjectId: typeof (payload as { defaultProjectId?: unknown }).defaultProjectId === "string"
            ? (payload as { defaultProjectId?: string }).defaultProjectId
            : null,
    };
}

export async function getSessionScope(authorizedFetch: AuthorizedFetch, sessionId: string) {
    const payload = await authorizedJson<{ binding?: ScopeBindingView | null }>(
        authorizedFetch,
        `/api/client/sessions/${encodeURIComponent(sessionId)}/scope`,
        "读取会话 scope 失败",
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
        "更新会话 scope 失败",
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
        "重新解析会话 scope 失败",
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
        "读取音乐列表失败",
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
    const payload = await authorizedJson<ConversationSummary[]>(authorizedFetch, "/api/client/conversations", "读取会话列表失败", {
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
    const payload = await authorizedJson<ConversationSummary>(authorizedFetch, "/api/client/conversations", "创建会话失败", {
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
        "删除会话失败",
        { method: "DELETE" },
    );
}

export async function getConversationDetail(authorizedFetch: AuthorizedFetch, id: string) {
    return authorizedJson<ConversationDetail>(
        authorizedFetch,
        `/api/client/conversations/${encodeURIComponent(id)}`,
        "读取会话详情失败",
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
        "读取会话进程失败",
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
        "读取命令预设失败",
        { cache: "no-store" },
    );
    return normalizeArray<CommandPresetSummary>(payload.items);
}

export async function getCommandPreset(authorizedFetch: AuthorizedFetch, name: string) {
    return authorizedJson<Record<string, unknown>>(
        authorizedFetch,
        `/api/client/commands/${encodeURIComponent(name)}`,
        "读取命令预设详情失败",
        { cache: "no-store" },
    );
}

export async function listSkills(authorizedFetch: AuthorizedFetch) {
    const payload = await authorizedJson<{ skills?: SkillReferenceSummary[] }>(
        authorizedFetch,
        "/api/client/skills/list",
        "读取技能列表失败",
        { cache: "no-store" },
    );
    return normalizeArray<SkillReferenceSummary>(payload.skills);
}

export async function listArtifacts(authorizedFetch: AuthorizedFetch, conversationId?: string | null) {
    const search = conversationId ? `?sessionId=${encodeURIComponent(conversationId)}` : "";
    const payload = await authorizedJson<{ artifacts?: ArtifactDetail[] }>(
        authorizedFetch,
        `/api/client/artifacts${search}`,
        "读取产物列表失败",
        { cache: "no-store" },
    );
    return normalizeArray<ArtifactDetail>(payload.artifacts);
}

export async function getArtifact(authorizedFetch: AuthorizedFetch, id: string) {
    return authorizedJson<ArtifactDetail>(
        authorizedFetch,
        `/api/client/artifacts/${encodeURIComponent(id)}`,
        "读取产物详情失败",
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
        throw new Error(detail || "读取产物内容失败");
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
        throw new Error(detail || "读取工作区文件失败");
    }
    return response;
}

export async function deleteMessage(authorizedFetch: AuthorizedFetch, id: string) {
    return authorizedJson<Record<string, unknown>>(
        authorizedFetch,
        `/api/client/messages/${encodeURIComponent(id)}`,
        "删除消息失败",
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
        "读取待处理确认失败",
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
    return authorizedJson<Record<string, unknown>>(authorizedFetch, path, approve ? "审批失败" : "拒绝失败", {
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

export async function dispatchRunCommand(
    authorizedFetch: AuthorizedFetch,
    runId: string,
    command: "interrupt" | "retry",
    reason?: string,
) {
    return authorizedJson<Record<string, unknown>>(
        authorizedFetch,
        `/api/client/runs/${encodeURIComponent(runId)}/commands/${encodeURIComponent(command)}`,
        command === "interrupt" ? "中断运行失败" : "重试运行失败",
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
        "读取实时快照失败",
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
            messages: [
                ...options.messages,
                { role: "user", content: userText },
            ],
            data: {
                conversationId: options.conversationId || undefined,
                commandPreset: options.commandPresetName ? { name: options.commandPresetName } : undefined,
                fileUrls: Array.isArray(options.fileUrls) && options.fileUrls.length > 0 ? options.fileUrls : undefined,
                taskPlanningMode: options.taskPlanningMode ? true : undefined,
                skillReferences: Array.isArray(options.skillReferences) && options.skillReferences.length > 0
                    ? options.skillReferences.map((skill) => ({
                        name: skill.name,
                        description: skill.description,
                        path: skill.path,
                    }))
                    : undefined,
            },
        }),
    });

    return readJsonOrThrow<ChatSubmitResponse>(response, "消息提交失败");
}

export async function getDesktopLiveStatus(authorizedFetch: AuthorizedFetch) {
    return authorizedJson<DesktopLiveStatus>(
        authorizedFetch,
        "/api/client/desktop-live/status",
        "读取 Desktop Live 状态失败",
        { cache: "no-store" },
    );
}

export async function prepareDesktopLive(authorizedFetch: AuthorizedFetch) {
    return authorizedJson<DesktopLiveStatus>(
        authorizedFetch,
        "/api/client/desktop-live/prepare",
        "预热 Desktop Live 失败",
        { method: "POST" },
    );
}

export async function createDesktopLiveSession(authorizedFetch: AuthorizedFetch) {
    return authorizedJson<DesktopLiveSessionPayload>(
        authorizedFetch,
        "/api/client/desktop-live/session",
        "创建 Desktop Live 会话失败",
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
        "创建 Desktop Live offer 失败",
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
        "提交 Desktop Live candidate 失败",
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
        "释放 Desktop Live 会话失败",
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
        "读取 RPA 可用性失败",
        { cache: "no-store" },
    );
}

export async function listRpaDrafts(authorizedFetch: AuthorizedFetch, limit = 8) {
    const payload = await authorizedJson<{ drafts?: RPADraftSummary[] }>(
        authorizedFetch,
        `/api/client/rpa/drafts?limit=${encodeURIComponent(String(limit))}`,
        "读取 RPA 草稿失败",
        { cache: "no-store" },
    );
    return normalizeArray<RPADraftSummary>(payload.drafts);
}

export async function runRpaCompile(authorizedFetch: AuthorizedFetch, runIds: string[]) {
    const endpoint = runIds.length === 1
        ? `/api/client/rpa/compile/${encodeURIComponent(runIds[0])}`
        : "/api/client/rpa/compile";
    const body = runIds.length === 1 ? { save: true } : { runIds, save: true };
    return authorizedJson<Record<string, unknown>>(authorizedFetch, endpoint, "生成 RPA 草稿失败", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
    });
}

export async function runExistingRobotFlow(
    authorizedFetch: AuthorizedFetch,
    robotFile: string,
    variables: Record<string, unknown>,
) {
    return authorizedJson<Record<string, unknown>>(
        authorizedFetch,
        "/api/client/rpa/run-existing",
        "运行现有 RPA 流程失败",
        {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ robotFile, variables }),
        },
    );
}

export async function speechToText(authorizedFetch: AuthorizedFetch, formData: FormData) {
    const response = await authorizedFetch("/api/client/audio/stt", {
        method: "POST",
        body: formData,
    });
    return readJsonOrThrow<Record<string, unknown>>(response, "语音识别失败");
}

export async function requestTextToSpeech(authorizedFetch: AuthorizedFetch, payload: Record<string, unknown>) {
    const response = await authorizedFetch("/api/client/audio/tts", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
    });
    if (!response.ok) {
        const detail = await parseTextSafe(response);
        throw new Error(detail || "语音合成失败");
    }
    return response;
}

type SendChatOptions = {
    messages: Array<{ role: string; content: string }>;
    conversationId?: string | null;
    commandPresetName?: string | null;
    skillReferences?: SkillReferenceSummary[];
    fileUrls?: string[];
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
            messages: [
                ...options.messages,
                { role: "user", content: userText },
            ],
            data: {
                conversationId: options.conversationId || undefined,
                commandPreset: options.commandPresetName ? { name: options.commandPresetName } : undefined,
                fileUrls: Array.isArray(options.fileUrls) && options.fileUrls.length > 0 ? options.fileUrls : undefined,
                taskPlanningMode: options.taskPlanningMode ? true : undefined,
                skillReferences: Array.isArray(options.skillReferences) && options.skillReferences.length > 0
                    ? options.skillReferences.map((skill) => ({
                        name: skill.name,
                        description: skill.description,
                        path: skill.path,
                    }))
                    : undefined,
            },
        }),
    });

    if (!response.ok) {
        const detail = await parseTextSafe(response);
        throw new Error(detail || "消息发送失败");
    }

    await streamNdjson(response, onEvent);
}
