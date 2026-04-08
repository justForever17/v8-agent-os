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
    const scopeMode = root.scope_mode ?? root.scopeMode ?? data.scopeMode ?? "mixed";
    const conversationId = root.session_id || root.conversationId || data.conversationId || crypto.randomUUID();
    const currentContent = toolOutputs?.[0]?.output || messages[messages.length - 1]?.content || "";
    const fileUrls = Array.isArray(data.fileUrls) ? data.fileUrls : [];
    const provider = data.provider;
    const modelName = data.model;
    const agentId = root.agentId;

    return {
        conversationId: String(conversationId),
        currentContent: String(currentContent || ""),
        fileUrls: fileUrls.filter((item): item is string => typeof item === "string" && item.trim().length > 0),
        provider: typeof provider === "string" ? provider : undefined,
        modelName: typeof modelName === "string" ? modelName : undefined,
        pythonPayload: {
            session_id: String(conversationId),
            conversationId: String(conversationId),
            user_id: userEmail,
            stream: true,
            title: String(currentContent || "").substring(0, 30) || "New Chat",
            tool_outputs: toolOutputs,
            fileUrls: fileUrls.filter((item): item is string => typeof item === "string" && item.trim().length > 0),
            project_id: typeof projectId === "string" ? projectId : undefined,
            workspace_id: typeof workspaceId === "string" ? workspaceId : undefined,
            workspace_path: typeof workspacePath === "string" ? workspacePath : undefined,
            scope_hint: typeof scopeHint === "string" ? scopeHint : undefined,
            scope_mode: typeof scopeMode === "string" ? scopeMode : "mixed",
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
                fileUrls: fileUrls.filter((item): item is string => typeof item === "string" && item.trim().length > 0),
                taskPlanningMode: data.taskPlanningMode === true,
                skillReferences: Array.isArray(data.skillReferences) ? data.skillReferences : undefined,
            },
        },
    };
}
