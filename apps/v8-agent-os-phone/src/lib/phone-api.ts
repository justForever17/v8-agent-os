import { buildAdminApiUrl, parseJsonSafe, parseTextSafe, streamNdjson } from "@/src/lib/admin-client";
import { normalizeSessionHistoryItem, normalizeSessionHistoryList } from "@/src/lib/session-history";
import { translateCurrent } from "@/src/lib/locale";
import type {
    ArtifactDetail,
    AuthSessionPayload,
    ChatStreamEvent,
    ChatSubmitResponse,
    ComposerPresentation,
    CommandPresetSummary,
    ConnectionSummary,
    ContextMentionSummary,
    ConversationDetail,
    ConversationSummary,
    DesktopLiveOfferPayload,
    DesktopLiveSessionPayload,
    DesktopLiveStatus,
    PendingApproval,
    PluginReferenceSummary,
    PhoneUser,
    ProfileUpdatePayload,
    ProjectSummary,
    QueuedChatMessage,
    WorkspaceFolderNode,
    WorkspaceFolderTreeResponse,
    ScopeBindingView,
    DevicePairingInput,
    RealtimeSessionSnapshot,
    RPAAvailability,
    RPADraftSummary,
    RPARobotScriptSummary,
    RPATemplateSummary,
    SkillReferenceSummary,
    SpecDetailResponse,
    SpecListResponse,
    SpecSummary,
    SubagentFamilySummary,
    AdminProcessRef,
    MusicTrack,
    UploadedWorkspaceFile,
    DevicePairingManifest,
    WorkbenchFilePage,
} from "@/src/types/admin";
import { orderAdminBaseUrlCandidates } from "@/src/lib/admin-connection-profiles";
import type { SessionSourceRef } from "@v8/session-realtime";

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
    workspaceTrustState?: "trusted" | "restricted";
    workspaceTrustSource?: string;
};

function normalizeArray<T>(value: unknown): T[] {
    return Array.isArray(value) ? (value as T[]) : [];
}

async function readJsonOrThrow<T>(response: Response, fallbackMessage: string): Promise<T> {
    const payload = await parseJsonSafe<T & { detail?: unknown; error?: string }>(response);
    if (!response.ok) {
        const detailPayload = payload && typeof payload === "object" && payload.detail && typeof payload.detail === "object"
            ? payload.detail as Record<string, unknown>
            : {};
        const detail = payload && typeof payload === "object"
            ? payload.error
                || (typeof payload.detail === "string" ? payload.detail : "")
                || (typeof detailPayload.error === "string" ? detailPayload.error : "")
                || (typeof detailPayload.summary === "string" ? detailPayload.summary : "")
                || (typeof detailPayload.recommendedNextAction === "string" ? detailPayload.recommendedNextAction : "")
            : "";
        const message = String(detail || await parseTextSafe(response) || fallbackMessage).trim();
        throw new Error(message || fallbackMessage);
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

type ParsedDevicePairing = {
    adminBaseUrl: string;
    adminUrls: string[];
    lanUrls: string[];
    tailscaleUrls: string[];
    code: string;
    instanceId: string;
    serverId: string;
    surface: string;
    manifest?: DevicePairingManifest;
};

function readManifestFromUnknown(value: unknown): DevicePairingManifest | null {
    if (!value || typeof value !== "object") {
        return null;
    }
    const record = value as Record<string, unknown>;
    const manifest: DevicePairingManifest = {
        kind: typeof record.kind === "string" ? record.kind : "",
        version: typeof record.version === "string" || typeof record.version === "number" ? record.version : "",
        serverId: typeof record.serverId === "string" ? record.serverId : "",
        instanceId: typeof record.instanceId === "string" ? record.instanceId : "",
        adminUrls: Array.isArray(record.adminUrls) ? record.adminUrls.map((item) => String(item || "")) : [],
        lanUrls: Array.isArray(record.lanUrls) ? record.lanUrls.map((item) => String(item || "")) : [],
        tailscaleUrls: Array.isArray(record.tailscaleUrls) ? record.tailscaleUrls.map((item) => String(item || "")) : [],
        pairingCode: typeof record.pairingCode === "string" ? record.pairingCode : "",
        code: typeof record.code === "string" ? record.code : "",
        surface: typeof record.surface === "string" ? record.surface : "",
    };
    return manifest;
}

function parseManifestText(value: string): DevicePairingManifest | null {
    const normalized = String(value || "").trim();
    if (!normalized) {
        return null;
    }
    try {
        return readManifestFromUnknown(JSON.parse(normalized));
    } catch {
        try {
            return readManifestFromUnknown(JSON.parse(decodeURIComponent(normalized)));
        } catch {
            return null;
        }
    }
}

export function parseDevicePairingUri(pairingUri: string): ParsedDevicePairing {
    const normalized = String(pairingUri || "").trim();
    if (!normalized) {
        throw new Error(translateCurrent("app.login.please_enter_a_pairing_link"));
    }
    const rawManifest = parseManifestText(normalized);
    if (rawManifest) {
        const adminUrls = orderAdminBaseUrlCandidates({
            adminUrls: rawManifest.adminUrls || [],
            lanUrls: rawManifest.lanUrls || [],
            tailscaleUrls: rawManifest.tailscaleUrls || [],
        });
        const code = String(rawManifest.pairingCode || rawManifest.code || "").trim();
        if (adminUrls.length === 0 || !code) {
            throw new Error(translateCurrent("app.login.invalid_pairing_link"));
        }
        return {
            adminBaseUrl: adminUrls[0],
            adminUrls,
            lanUrls: rawManifest.lanUrls || [],
            tailscaleUrls: rawManifest.tailscaleUrls || [],
            code,
            instanceId: String(rawManifest.instanceId || "").trim(),
            serverId: String(rawManifest.serverId || rawManifest.instanceId || "").trim(),
            surface: String(rawManifest.surface || "phone").trim(),
            manifest: rawManifest,
        };
    }
    let parsed: URL;
    try {
        parsed = new URL(normalized);
    } catch {
        throw new Error(translateCurrent("app.login.invalid_pairing_link"));
    }
    const urlManifest = parseManifestText(parsed.searchParams.get("manifest") || "");
    const legacyAdminBaseUrl = String(parsed.searchParams.get("admin") || "").trim();
    const code = String(urlManifest?.pairingCode || urlManifest?.code || parsed.searchParams.get("code") || "").trim();
    const instanceId = String(urlManifest?.instanceId || parsed.searchParams.get("instance") || "").trim();
    const serverId = String(urlManifest?.serverId || instanceId || "").trim();
    const surface = String(urlManifest?.surface || parsed.searchParams.get("surface") || "phone").trim();
    const adminUrls = orderAdminBaseUrlCandidates({
        primary: legacyAdminBaseUrl,
        adminUrls: urlManifest?.adminUrls || [],
        lanUrls: urlManifest?.lanUrls || [],
        tailscaleUrls: urlManifest?.tailscaleUrls || [],
    });
    if (adminUrls.length === 0 || !code) {
        throw new Error(translateCurrent("app.login.invalid_pairing_link"));
    }
    return {
        adminBaseUrl: adminUrls[0],
        adminUrls,
        lanUrls: urlManifest?.lanUrls || [],
        tailscaleUrls: urlManifest?.tailscaleUrls || [],
        code,
        instanceId,
        serverId,
        surface,
        manifest: urlManifest || undefined,
    };
}

export async function pairDevice(input: DevicePairingInput): Promise<AuthSessionPayload> {
    const pairing = parseDevicePairingUri(input.pairingUri);
    let lastError: unknown = null;
    for (const adminBaseUrl of pairing.adminUrls) {
        try {
            const response = await fetch(buildAdminApiUrl(adminBaseUrl, "/api/client/pairing/consume"), {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    code: pairing.code,
                    instanceId: pairing.instanceId || undefined,
                    deviceName: input.deviceName || "v8-phone",
                }),
            });
            const payload = await readJsonOrThrow<AuthSessionPayload>(response, translateCurrent("app.login.pairing_failed"));
            return {
                ...payload,
                adminBaseUrl: payload.adminBaseUrl || adminBaseUrl,
                serverId: pairing.serverId || payload.serverId || payload.instanceId,
                instanceId: payload.instanceId || pairing.instanceId,
                adminUrls: pairing.adminUrls,
                pairingManifest: pairing.manifest,
            };
        } catch (error) {
            lastError = error;
        }
    }
    throw lastError instanceof Error ? lastError : new Error(translateCurrent("app.login.pairing_failed"));
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
    scope?: { sessionId?: string | null; conversationId?: string | null; workspaceId?: string | null; workspacePath?: string | null; projectId?: string | null; sourceKind?: "phone_upload" | "phone_voice" },
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
        form.append("sourceKind", scope?.sourceKind || "phone_upload");
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

export async function updateConversationPresentation(
    authorizedFetch: AuthorizedFetch,
    id: string,
    input: { title?: string; pinned?: boolean; supervisorWorkMode?: "daily" | "engineering" },
) {
    const payload = await authorizedJson<ConversationSummary>(
        authorizedFetch,
        `/api/client/conversations/${encodeURIComponent(id)}`,
        translateCurrent("src.lib.phone_api.conversation_presentation"),
        {
            method: "PATCH",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(input),
        },
    );
    return normalizeSessionHistoryItem(payload);
}

export async function updateWorkspacePresentation(
    authorizedFetch: AuthorizedFetch,
    input: { workspacePath: string; displayName?: string; pinned?: boolean },
) {
    return authorizedJson<{
        workspacePath: string;
        displayName?: string;
        pinned?: boolean;
        pinnedAt?: string | null;
        updatedAt?: string | null;
    }>(
        authorizedFetch,
        "/api/client/workspace-presentations",
        translateCurrent("src.lib.phone_api.workspace_presentation"),
        {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(input),
        },
    );
}

export async function deleteConversation(authorizedFetch: AuthorizedFetch, id: string) {
    return authorizedJson<{ success: boolean }>(
        authorizedFetch,
        `/api/client/conversations/${encodeURIComponent(id)}`,
        translateCurrent("src.lib.phone_api.text_15"),
        { method: "DELETE" },
    );
}

export async function getConversationDetail(authorizedFetch: AuthorizedFetch, id: string, omitMessages = false) {
    return authorizedJson<ConversationDetail>(
        authorizedFetch,
        `/api/client/conversations/${encodeURIComponent(id)}${omitMessages ? '?omitMessages=1' : ''}`,
        translateCurrent("src.lib.phone_api.text_16"),
        { cache: "no-store" },
    );
}

export async function getConversationTimelineSync(authorizedFetch: AuthorizedFetch, id: string, since: string) {
    return authorizedJson<{
        messages: any[];
        deletions: string[];
        syncCursor: string;
        sessionId: string;
    }>(
        authorizedFetch,
        `/api/client/conversations/${encodeURIComponent(id)}/sync?since=${encodeURIComponent(since)}&surface=phone&compact=1`,
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
        stale?: boolean;
        processPanelError?: string;
        lastUpdatedAt?: string | null;
        cacheAgeMs?: number | null;
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
        stale: Boolean(payload.stale),
        processPanelError: typeof payload.processPanelError === "string" ? payload.processPanelError : undefined,
        lastUpdatedAt: typeof payload.lastUpdatedAt === "string" ? payload.lastUpdatedAt : null,
        cacheAgeMs: Number.isFinite(Number(payload.cacheAgeMs)) ? Number(payload.cacheAgeMs) : null,
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

type SkillListScope = {
    sessionId?: string | null;
    conversationId?: string | null;
    workspacePath?: string | null;
    workspaceId?: string | null;
    projectId?: string | null;
};

function buildSkillListPath(scope?: SkillListScope | null) {
    const params = new URLSearchParams();
    const sessionId = String(scope?.sessionId || scope?.conversationId || "").trim();
    if (sessionId) params.set("sessionId", sessionId);
    const workspacePath = String(scope?.workspacePath || "").trim();
    if (workspacePath) params.set("workspacePath", workspacePath);
    const workspaceId = String(scope?.workspaceId || "").trim();
    if (workspaceId) params.set("workspaceId", workspaceId);
    const projectId = String(scope?.projectId || "").trim();
    if (projectId) params.set("projectId", projectId);
    const suffix = params.toString();
    return suffix ? `/api/client/skills/list?${suffix}` : "/api/client/skills/list";
}

export async function listSkills(authorizedFetch: AuthorizedFetch, scope?: SkillListScope | null) {
    const payload = await authorizedJson<{ skills?: SkillReferenceSummary[] }>(
        authorizedFetch,
        buildSkillListPath(scope),
        translateCurrent("src.lib.phone_api.text_20"),
        { cache: "no-store" },
    );
    return normalizeArray<SkillReferenceSummary>(payload.skills);
}

export async function listSkillsAndSubagentFamilies(authorizedFetch: AuthorizedFetch, scope?: SkillListScope | null) {
    const payload = await authorizedJson<{ skills?: SkillReferenceSummary[]; subagentFamilies?: SubagentFamilySummary[] }>(
        authorizedFetch,
        buildSkillListPath(scope),
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

export async function deleteMessage(authorizedFetch: AuthorizedFetch, id: string, conversationId?: string | null) {
    const suffix = conversationId ? `?session_id=${encodeURIComponent(conversationId)}` : "";
    return authorizedJson<Record<string, unknown>>(
        authorizedFetch,
        `/api/client/messages/${encodeURIComponent(id)}${suffix}`,
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

export async function listSpecs(authorizedFetch: AuthorizedFetch, workspacePath: string) {
    const query = new URLSearchParams({
        workspace_path: workspacePath,
        include_archived: "true",
        limit: "120",
    });
    const payload = await authorizedJson<SpecListResponse>(
        authorizedFetch,
        `/api/client/specs?${query.toString()}`,
        translateCurrent("src.lib.phone_api.specs"),
        { cache: "no-store" },
    );
    return normalizeArray<SpecSummary>(payload.specs);
}

export async function listSessionArtifacts(authorizedFetch: AuthorizedFetch, sessionId: string, limit = 100) {
    const query = new URLSearchParams({
        sessionId,
        limit: String(Math.max(1, Math.min(160, limit))),
    });
    const payload = await authorizedJson<{ artifacts?: ArtifactDetail[] }>(
        authorizedFetch,
        `/api/client/artifacts?${query.toString()}`,
        translateCurrent("src.lib.phone_api.artifacts"),
        { cache: "no-store" },
    );
    return normalizeArray<ArtifactDetail>(payload.artifacts);
}

export async function listSessionSources(authorizedFetch: AuthorizedFetch, sessionId: string, limit = 100) {
    const query = new URLSearchParams({
        sessionId,
        limit: String(Math.max(1, Math.min(160, limit))),
    });
    const payload = await authorizedJson<{ sources?: SessionSourceRef[] }>(
        authorizedFetch,
        `/api/client/sources?${query.toString()}`,
        translateCurrent("src.lib.phone_api.sources"),
        { cache: "no-store" },
    );
    return normalizeArray<SessionSourceRef>(payload.sources);
}

export async function getSpecDetail(authorizedFetch: AuthorizedFetch, specId: string, workspacePath: string) {
    const query = new URLSearchParams({
        workspace_path: workspacePath,
        max_chars: "160000",
    });
    return authorizedJson<SpecDetailResponse>(
        authorizedFetch,
        `/api/client/specs/${encodeURIComponent(specId)}?${query.toString()}`,
        translateCurrent("src.lib.phone_api.specs_2"),
        { cache: "no-store" },
    );
}

export async function approveSpecStage(
    authorizedFetch: AuthorizedFetch,
    specId: string,
    stage: string,
    workspacePath: string,
    comment: string,
) {
    return authorizedJson<Record<string, unknown>>(
        authorizedFetch,
        `/api/client/specs/${encodeURIComponent(specId)}/stages/${encodeURIComponent(stage)}/approve`,
        translateCurrent("src.lib.phone_api.specs_3"),
        {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ workspacePath, comment }),
        },
    );
}

export async function reviseSpecStage(
    authorizedFetch: AuthorizedFetch,
    specId: string,
    stage: string,
    workspacePath: string,
    sectionRef: string,
    comment: string,
) {
    return authorizedJson<Record<string, unknown>>(
        authorizedFetch,
        `/api/client/specs/${encodeURIComponent(specId)}/stages/${encodeURIComponent(stage)}/revise`,
        translateCurrent("src.lib.phone_api.specs_4"),
        {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ workspacePath, sectionRef, comment }),
        },
    );
}

export async function editSpecStage(
    authorizedFetch: AuthorizedFetch,
    specId: string,
    stage: string,
    workspacePath: string,
    sectionRef: string,
    content: string,
    reason: string,
) {
    return authorizedJson<Record<string, unknown>>(
        authorizedFetch,
        `/api/client/specs/${encodeURIComponent(specId)}/stages/${encodeURIComponent(stage)}/edit`,
        translateCurrent("src.lib.phone_api.specs_5"),
        {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                workspacePath,
                sectionRef,
                content,
                reason,
                action: sectionRef ? "replace_section" : "append_section",
            }),
        },
    );
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
        `/api/client/realtime/sessions/${encodeURIComponent(conversationId)}/snapshot?surface=phone&compact=1`,
        translateCurrent("src.lib.phone_api.text_32"),
        { cache: "no-store" },
    );
}

export async function readSessionWorkbenchFile(
    authorizedFetch: AuthorizedFetch,
    sessionId: string,
    path: string,
    startLine = 1,
    lineCount = 160,
) {
    const query = new URLSearchParams({
        path,
        startLine: String(Math.max(1, startLine)),
        lineCount: String(Math.max(1, Math.min(300, lineCount))),
    });
    return authorizedJson<WorkbenchFilePage>(
        authorizedFetch,
        `/api/client/sessions/${encodeURIComponent(sessionId)}/workbench/files/read?${query.toString()}`,
        translateCurrent("src.lib.phone_api.workbench_file"),
        { cache: "no-store" },
    );
}

export async function listPlugins(authorizedFetch: AuthorizedFetch) {
    const payload = await authorizedJson<{ items?: Array<Record<string, unknown>> }>(
        authorizedFetch,
        "/api/client/plugins/mentions",
        translateCurrent("src.lib.phone_api.plugin_catalog_load_failed"),
        { cache: "no-store" },
    );
    return normalizeArray<Record<string, unknown>>(payload.items)
        .map((item): PluginReferenceSummary => ({
            pluginId: String(item.pluginId || "").trim(),
            displayName: String(item.displayName || item.pluginId || "").trim(),
            description: String(item.description || "").trim(),
            status: ["ready", "not_installed", "needs_configuration", "offline", "invalid"].includes(String(item.status || ""))
                ? String(item.status) as PluginReferenceSummary["status"]
                : "invalid",
            configurationUrl: String(item.configurationUrl || "").trim(),
            componentIds: Array.isArray(item.componentIds) ? item.componentIds.map((value) => String(value || "").trim()).filter(Boolean) : undefined,
            grantScope: "task",
        }))
        .filter((item) => item.pluginId);
}

export type SupervisorReasoningEffortControl = {
    visible?: boolean;
    supported?: boolean;
    levels?: string[];
    defaultLevel?: string;
    modelRef?: string;
};

export async function getSupervisorReasoningEffortControl(authorizedFetch: AuthorizedFetch) {
    return authorizedJson<SupervisorReasoningEffortControl>(
        authorizedFetch,
        "/api/client/models/supervisor-reasoning-effort",
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
        `/api/client/realtime/sessions/${encodeURIComponent(conversationId)}/stream?surface=phone&compact=1`,
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
            projectId: options.projectId || undefined,
            workspaceId: options.workspaceId || undefined,
            workspacePath: options.workspacePath || undefined,
            messages: [
                ...options.messages,
                { role: "user", content: userText },
            ],
            data: {
                conversationId: options.conversationId || undefined,
                clientMessageId: options.clientMessageId || undefined,
                projectId: options.projectId || undefined,
                workspaceId: options.workspaceId || undefined,
                workspacePath: options.workspacePath || undefined,
                commandPreset: options.commandPresetName ? { name: options.commandPresetName } : undefined,
                specCommand: options.specCommand || undefined,
                fileUrls: Array.isArray(options.fileUrls) && options.fileUrls.length > 0 ? options.fileUrls : undefined,
                attachments: Array.isArray(options.attachments) && options.attachments.length > 0 ? options.attachments : undefined,
                specMode: (options.specMode || options.specCommand) ? true : undefined,
                supervisorWorkMode: options.supervisorWorkMode || undefined,
                supervisorReasoningEffort: options.supervisorReasoningEffort || undefined,
                safetyApprovalMode: options.safetyApprovalMode || undefined,
                skillReferences: Array.isArray(options.skillReferences) && options.skillReferences.length > 0
                    ? options.skillReferences.map((skill) => ({
                        name: skill.name,
                        description: skill.description,
                        path: skill.path,
                    }))
                    : undefined,
                pluginReferences: Array.isArray(options.pluginReferences) && options.pluginReferences.length > 0
                    ? options.pluginReferences.map((plugin) => ({
                        pluginId: plugin.pluginId,
                        name: plugin.displayName,
                        scope: plugin.grantScope,
                        componentIds: plugin.componentIds,
                    }))
                    : undefined,
                contextMentions: Array.isArray(options.contextMentions) && options.contextMentions.length > 0
                    ? options.contextMentions
                    : undefined,
                contextSessionRefs: Array.isArray(options.contextSessionRefs) && options.contextSessionRefs.length > 0
                    ? options.contextSessionRefs
                    : undefined,
                composerPresentation: options.composerPresentation || undefined,
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

export async function runRpaTemplate(
    authorizedFetch: AuthorizedFetch,
    templateId: string,
    variables: Record<string, unknown>,
) {
    return authorizedJson<Record<string, unknown>>(
        authorizedFetch,
        `/api/client/rpa/templates/${encodeURIComponent(templateId)}/run`,
        translateCurrent("src.lib.phone_api.rpa_template_run_failed"),
        {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ variables, triggerSource: "rpa_phone", nonChatRun: true }),
        },
    );
}

export async function getAudioInputStatus(authorizedFetch: AuthorizedFetch) {
    return authorizedJson<Record<string, unknown>>(
        authorizedFetch,
        "/api/client/audio/input-status",
        translateCurrent("src.lib.phone_api.text_34"),
        { method: "GET" },
    );
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
    projectId?: string | null;
    workspaceId?: string | null;
    workspacePath?: string | null;
    commandPresetName?: string | null;
    skillReferences?: SkillReferenceSummary[];
    pluginReferences?: PluginReferenceSummary[];
    contextMentions?: ContextMentionSummary[];
    contextSessionRefs?: Array<{ sessionId: string; source: "history_menu" }>;
    composerPresentation?: ComposerPresentation;
    fileUrls?: string[];
    attachments?: Array<Record<string, unknown>>;
    specMode?: boolean;
    supervisorWorkMode?: "daily" | "engineering";
    specCommand?: { action: "new" | "continue" | "list" | "approve" | "clarify" | "analyze" | "annex"; specId?: string; stage?: string };
    supervisorReasoningEffort?: string;
    safetyApprovalMode?: "manual" | "reduced" | "minimal";
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
            projectId: options.projectId || undefined,
            workspaceId: options.workspaceId || undefined,
            workspacePath: options.workspacePath || undefined,
            messages: [
                ...options.messages,
                { role: "user", content: userText },
            ],
            data: {
                conversationId: options.conversationId || undefined,
                clientMessageId: options.clientMessageId || undefined,
                projectId: options.projectId || undefined,
                workspaceId: options.workspaceId || undefined,
                workspacePath: options.workspacePath || undefined,
                commandPreset: options.commandPresetName ? { name: options.commandPresetName } : undefined,
                specCommand: options.specCommand || undefined,
                fileUrls: Array.isArray(options.fileUrls) && options.fileUrls.length > 0 ? options.fileUrls : undefined,
                attachments: Array.isArray(options.attachments) && options.attachments.length > 0 ? options.attachments : undefined,
                specMode: (options.specMode || options.specCommand) ? true : undefined,
                supervisorWorkMode: options.supervisorWorkMode || undefined,
                supervisorReasoningEffort: options.supervisorReasoningEffort || undefined,
                safetyApprovalMode: options.safetyApprovalMode || undefined,
                skillReferences: Array.isArray(options.skillReferences) && options.skillReferences.length > 0
                    ? options.skillReferences.map((skill) => ({
                        name: skill.name,
                        description: skill.description,
                        path: skill.path,
                    }))
                    : undefined,
                pluginReferences: Array.isArray(options.pluginReferences) && options.pluginReferences.length > 0
                    ? options.pluginReferences.map((plugin) => ({
                        pluginId: plugin.pluginId,
                        name: plugin.displayName,
                        scope: plugin.grantScope,
                        componentIds: plugin.componentIds,
                    }))
                    : undefined,
                contextMentions: Array.isArray(options.contextMentions) && options.contextMentions.length > 0
                    ? options.contextMentions
                    : undefined,
                contextSessionRefs: Array.isArray(options.contextSessionRefs) && options.contextSessionRefs.length > 0
                    ? options.contextSessionRefs
                    : undefined,
                composerPresentation: options.composerPresentation || undefined,
            },
        }),
    });

    if (!response.ok) {
        const detail = await parseTextSafe(response);
        throw new Error(detail || translateCurrent("src.lib.phone_api.text_37"));
    }

    await streamNdjson(response, onEvent);
}
