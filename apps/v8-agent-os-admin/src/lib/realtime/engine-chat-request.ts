import crypto from "crypto";

type JsonRecord = Record<string, unknown>;

function asRecord(value: unknown): JsonRecord {
    return value && typeof value === "object" ? value as JsonRecord : {};
}

function toMessageList(value: unknown) {
    if (!Array.isArray(value)) {
        return [];
    }
    return value.filter((item): item is JsonRecord => Boolean(item) && typeof item === "object");
}

function toAttachmentList(value: unknown, fallbackFileUrls: string[] = []) {
    const attachments = Array.isArray(value)
        ? value.filter((item): item is JsonRecord => Boolean(item) && typeof item === "object")
        : [];
    const normalized = attachments.map((item) => ({
        id: typeof item.id === "string" ? item.id : undefined,
        name: typeof item.name === "string" ? item.name : undefined,
        url: typeof item.url === "string" ? item.url : undefined,
        publicUrl: typeof item.publicUrl === "string" ? item.publicUrl : undefined,
        workspacePath: typeof item.workspacePath === "string" ? item.workspacePath : undefined,
        mimeType: typeof item.mimeType === "string" ? item.mimeType : typeof item.type === "string" ? item.type : undefined,
        size: typeof item.size === "number" ? item.size : undefined,
        source: typeof item.source === "string" ? item.source : "client_upload",
    })).filter((item) => item.url || item.publicUrl || item.workspacePath);
    const seen = new Set<string>();
    const deduped = [];
    for (const item of normalized) {
        const fingerprint = String(item.url || item.publicUrl || item.workspacePath || "").toLowerCase();
        if (!fingerprint || seen.has(fingerprint)) {
            continue;
        }
        seen.add(fingerprint);
        deduped.push(item);
    }
    for (const url of fallbackFileUrls) {
        const normalizedUrl = String(url || "").trim();
        if (!normalizedUrl || seen.has(normalizedUrl.toLowerCase())) {
            continue;
        }
        seen.add(normalizedUrl.toLowerCase());
        deduped.push({ url: normalizedUrl, publicUrl: normalizedUrl, source: "legacy_fileUrls" });
    }
    return deduped;
}

export function buildEngineChatRequestPayload(payload: unknown, userEmail: string) {
    const root = asRecord(payload);
    const data = asRecord(root.data);
    const messages = toMessageList(root.messages);
    const toolOutputs = Array.isArray(root.tool_outputs)
        ? root.tool_outputs.filter((item): item is JsonRecord => Boolean(item) && typeof item === "object")
        : undefined;

    const projectId = root.project_id ?? root.projectId ?? data.projectId;
    const workspaceId = root.workspace_id ?? root.workspaceId ?? data.workspaceId;
    const workspacePath = root.workspace_path ?? root.workspacePath ?? data.workspacePath;
    const scopeHint = root.scope_hint ?? root.scopeHint ?? data.scopeHint;
    const scopeMode = root.scope_mode ?? root.scopeMode ?? data.scopeMode ?? "explicit";
    const conversationId = root.session_id || root.conversationId || data.conversationId || crypto.randomUUID();
    const currentContent = toolOutputs?.[0]?.output || messages[messages.length - 1]?.content || "";
    const rootFileUrls = Array.isArray(root.fileUrls) ? root.fileUrls : [];
    const dataFileUrls = Array.isArray(data.fileUrls) ? data.fileUrls : [];
    const fileUrls = [...dataFileUrls, ...rootFileUrls]
        .filter((item): item is string => typeof item === "string" && item.trim().length > 0);
    const attachments = toAttachmentList(root.attachments, fileUrls)
        .concat(toAttachmentList(data.attachments, []));
    const dedupedAttachments = toAttachmentList(attachments, fileUrls);
    const provider = data.provider;
    const modelName = data.model;
    const agentId = root.agentId;

    return {
        conversationId: String(conversationId),
        currentContent: String(currentContent || ""),
        fileUrls,
        attachments: dedupedAttachments,
        provider: typeof provider === "string" ? provider : undefined,
        modelName: typeof modelName === "string" ? modelName : undefined,
        pythonPayload: {
            session_id: String(conversationId),
            conversationId: String(conversationId),
            user_id: userEmail,
            stream: true,
            title: String(currentContent || "").substring(0, 30) || "New Chat",
            tool_outputs: toolOutputs,
            fileUrls,
            attachments: dedupedAttachments,
            project_id: typeof projectId === "string" ? projectId : undefined,
            workspace_id: typeof workspaceId === "string" ? workspaceId : undefined,
            workspace_path: typeof workspacePath === "string" ? workspacePath : undefined,
            scope_hint: typeof scopeHint === "string" ? scopeHint : undefined,
            scope_mode: typeof scopeMode === "string" ? scopeMode : "explicit",
            config: {
                provider,
                model_name: modelName,
                agent_id: agentId,
            },
            messages: messages.map((message) => ({
                role: typeof message.role === "string" ? message.role : "user",
                content: typeof message.content === "string" ? message.content : "",
                tool_call_id: typeof message.tool_call_id === "string" ? message.tool_call_id : undefined,
                name: typeof message.name === "string" ? message.name : undefined,
            })),
            data: {
                conversationId: String(conversationId),
                commandPreset: data.commandPreset,
                fileUrls,
                attachments: dedupedAttachments,
                taskPlanningMode: data.taskPlanningMode === true,
                skillReferences: Array.isArray(data.skillReferences) ? data.skillReferences : undefined,
            },
        },
    };
}
