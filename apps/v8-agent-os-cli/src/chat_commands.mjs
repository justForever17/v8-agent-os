import readline from "node:readline/promises";
import { stdin as input, stdout as output } from "node:process";
import { adminJson, requireOk } from "./client_api.mjs";
import { currentWorkspaceBinding, registerTrustedWorkspaceProject } from "./workspace_commands.mjs";

function optionValue(args, name, fallback = "") {
  const index = args.indexOf(name);
  return index >= 0 ? String(args[index + 1] || fallback) : fallback;
}

function optionValueAny(args, names, fallback = "") {
  for (const name of names) {
    const value = optionValue(args, name, "");
    if (value) return value;
  }
  return fallback;
}

function hasFlag(args, flag) {
  return args.includes(flag);
}

export function normalizeSafetyApprovalMode(value) {
  const mode = String(value || "").trim().toLowerCase();
  return ["manual", "reduced", "minimal"].includes(mode) ? mode : "reduced";
}

export function resolveChatWorkspaceSelection({
  requestedSessionId = "",
  requestedWorkspacePath = "",
  requestedWorkspaceId = "",
  requestedProjectId = "",
  storedBinding = {},
} = {}) {
  if (requestedSessionId || requestedWorkspacePath || requestedWorkspaceId || requestedProjectId) {
    return {
      workspacePath: requestedWorkspacePath,
      workspaceId: requestedWorkspaceId,
      projectId: requestedProjectId,
    };
  }
  return {
    workspacePath: requestedWorkspacePath || storedBinding.path || "",
    workspaceId: requestedWorkspaceId || storedBinding.workspaceId || "",
    projectId: requestedProjectId || storedBinding.projectId || "",
  };
}

function remainingText(args) {
  const skipped = new Set([
    "--session",
    "--workspace",
    "--workspace-id",
    "--project",
    "--timeout",
    "--message",
    "--safety-approval",
    "--safety-approval-mode",
  ]);
  const pieces = [];
  for (let index = 0; index < args.length; index += 1) {
    const item = args[index];
    if (skipped.has(item)) {
      index += 1;
      continue;
    }
    if (item.startsWith("--")) continue;
    pieces.push(item);
  }
  return pieces.join(" ").trim();
}

export function buildChatSubmitPayload({
  sessionId,
  message,
  workspacePath = "",
  workspaceId = "",
  projectId = "",
  specMode = false,
  safetyApprovalMode = "reduced",
}) {
  const normalizedSafetyApprovalMode = normalizeSafetyApprovalMode(safetyApprovalMode);
  return {
    session_id: sessionId,
    conversationId: sessionId,
    messages: [
      {
        role: "user",
        content: message,
      },
    ],
    data: {
      conversationId: sessionId,
      workspacePath: workspacePath || undefined,
      workspaceId: workspaceId || undefined,
      projectId: projectId || undefined,
      specMode,
      safetyApprovalMode: normalizedSafetyApprovalMode,
    },
  };
}

async function ensureSession({ sessionId, message, workspacePath, workspaceId, projectId }) {
  if (sessionId) return sessionId;
  const response = await adminJson("/api/client/conversations", {
    method: "POST",
    body: {
      title: message.slice(0, 40) || "CLI Chat",
      workspacePath: workspacePath || undefined,
      workspaceId: workspaceId || undefined,
      projectId: projectId || undefined,
      source: "v8os_cli",
      externalSurface: "cli",
      clientGroup: "local_trusted",
    },
    timeoutMs: 10_000,
  });
  const data = requireOk(response, "创建会话");
  return String(data.id || data.sessionId || data.conversationId || "");
}

export function extractMessageText(message) {
  if (!message || typeof message !== "object") return "";
  if (typeof message.content === "string") return message.content.trim();
  if (typeof message.text === "string") return message.text.trim();
  const parts = Array.isArray(message.content) ? message.content : Array.isArray(message.parts) ? message.parts : [];
  return parts
    .map((part) => {
      if (typeof part === "string") return part;
      if (part && typeof part === "object") return part.text || part.content || "";
      return "";
    })
    .filter(Boolean)
    .join("\n")
    .trim();
}

function isAssistantMessage(message) {
  const role = String(message?.role || message?.authorRole || message?.author || "").toLowerCase();
  const type = String(message?.type || "").toLowerCase();
  return role.includes("assistant") || role.includes("supervisor") || type.includes("assistant");
}

export function assistantTerminalFailure(message) {
  if (!isAssistantMessage(message)) return null;
  const state = String(message?.state || message?.status || "").trim().toLowerCase();
  if (!["failed", "cancelled", "canceled", "interrupted", "aborted"].includes(state)) return null;
  const reason = String(
    message?.metadata?.terminalReason
    || message?.metadata?.failureClass
    || message?.terminalReason
    || state,
  ).trim();
  const stateLabel = state === "failed" ? "失败" : "终止";
  return {
    state,
    reason,
    message: `主理人运行已${stateLabel}${reason && reason !== state ? `（${reason}）` : ""}。`,
  };
}

async function latestMessageIds(sessionId) {
  const response = await adminJson(`/api/client/conversations/${encodeURIComponent(sessionId)}/turns?limit=1`, { timeoutMs: 10_000 });
  if (!response.ok) return new Set();
  const messages = Array.isArray(response.data?.messages) ? response.data.messages : [];
  return new Set(messages.map((message, index) => String(message.id || message.messageId || `${index}:${extractMessageText(message).slice(0, 48)}`)));
}

async function waitForAssistant(sessionId, beforeIds, { timeoutMs = 120_000 } = {}) {
  const startedAt = Date.now();
  while (Date.now() - startedAt < timeoutMs) {
    const response = await adminJson(`/api/client/conversations/${encodeURIComponent(sessionId)}/turns?limit=1`, { timeoutMs: 10_000 });
    if (response.ok) {
      const messages = Array.isArray(response.data?.messages) ? response.data.messages : [];
      const candidate = [...messages].reverse().find((message, index) => {
        if (!isAssistantMessage(message)) return false;
        const id = String(message.id || message.messageId || `${messages.length - index - 1}:${extractMessageText(message).slice(0, 48)}`);
        return !beforeIds.has(id) && extractMessageText(message);
      });
      if (candidate) return candidate;
      const terminalFailure = [...messages].reverse().find((message, index) => {
        if (!assistantTerminalFailure(message)) return false;
        const id = String(message.id || message.messageId || `${messages.length - index - 1}:${extractMessageText(message).slice(0, 48)}`);
        return !beforeIds.has(id);
      });
      if (terminalFailure) {
        throw new Error(assistantTerminalFailure(terminalFailure).message);
      }
    }
    await new Promise((resolve) => setTimeout(resolve, 1500));
  }
  return null;
}

export async function sendChatMessage(args, { print = true } = {}) {
  const message = optionValue(args, "--message", "") || remainingText(args);
  if (!message) {
    throw new Error("chat 需要消息内容，例如：v8os chat \"你好\"");
  }
  const requestedSessionId = optionValue(args, "--session", "");
  const storedBinding = currentWorkspaceBinding();
  const workspaceSelection = resolveChatWorkspaceSelection({
    requestedSessionId,
    requestedWorkspacePath: optionValue(args, "--workspace", ""),
    requestedWorkspaceId: optionValue(args, "--workspace-id", ""),
    requestedProjectId: optionValue(args, "--project", ""),
    storedBinding,
  });
  let { workspacePath, workspaceId, projectId } = workspaceSelection;
  if (workspacePath) {
    const trusted = await registerTrustedWorkspaceProject(workspacePath, {
      projectId,
      workspaceId,
      required: true,
    });
    workspacePath = trusted.path || workspacePath;
    workspaceId = trusted.workspaceId || workspaceId;
    projectId = trusted.projectId || projectId;
  }
  const safetyApprovalMode = normalizeSafetyApprovalMode(
    optionValueAny(args, ["--safety-approval", "--safety-approval-mode"], "reduced"),
  );
  const sessionId = await ensureSession({
    sessionId: requestedSessionId,
    message,
    workspacePath,
    workspaceId,
    projectId,
  });
  if (!sessionId) throw new Error("无法创建或定位会话");
  const beforeIds = await latestMessageIds(sessionId);
  const payload = buildChatSubmitPayload({
    sessionId,
    message,
    workspacePath,
    workspaceId,
    projectId,
    specMode: hasFlag(args, "--spec"),
    safetyApprovalMode,
  });
  const submit = requireOk(await adminJson("/api/client/chat-submit", {
    method: "POST",
    body: payload,
    timeoutMs: 15_000,
  }), "提交消息");
  const timeoutMs = Number(optionValue(args, "--timeout", "120")) * 1000;
  const assistant = hasFlag(args, "--no-wait") ? null : await waitForAssistant(sessionId, beforeIds, { timeoutMs });
  const result = {
    sessionId,
    runId: submit.runId || submit.run_id || submit.id || "",
    response: assistant ? extractMessageText(assistant) : "",
  };
  if (print) {
    console.log(`session: ${sessionId}`);
    if (result.runId) console.log(`run: ${result.runId}`);
    if (result.response) {
      console.log("");
      console.log(result.response);
    } else if (!hasFlag(args, "--no-wait")) {
      console.log("未在等待时间内收到主理人回复，可用 v8os sessions open 查看。");
    }
  }
  return result;
}

export async function interactiveChat(args) {
  let sessionId = optionValue(args, "--session", "");
  const workspacePath = optionValue(args, "--workspace", "");
  const workspaceId = optionValue(args, "--workspace-id", "");
  const projectId = optionValue(args, "--project", "");
  const safetyApprovalMode = normalizeSafetyApprovalMode(
    optionValueAny(args, ["--safety-approval", "--safety-approval-mode"], "reduced"),
  );
  const rl = readline.createInterface({ input, output });
  console.log("V8OS 本机终端对话。输入 /exit 结束。");
  try {
    while (true) {
      const text = (await rl.question("你> ")).trim();
      if (!text) continue;
      if (text === "/exit" || text === "/quit") break;
      const result = await sendChatMessage([
        ...(sessionId ? ["--session", sessionId] : []),
        ...(workspacePath ? ["--workspace", workspacePath] : []),
        ...(workspaceId ? ["--workspace-id", workspaceId] : []),
        ...(projectId ? ["--project", projectId] : []),
        "--safety-approval",
        safetyApprovalMode,
        text,
      ], { print: false });
      sessionId = result.sessionId;
      console.log(`主理人> ${result.response || "处理中，可稍后查看会话。"}`);
    }
  } finally {
    rl.close();
  }
}
