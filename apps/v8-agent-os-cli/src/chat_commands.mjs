import readline from "node:readline/promises";
import { stdin as input, stdout as output } from "node:process";
import { engineJson } from "./engine_client.mjs";
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
  const data = await engineJson("/v1/sessions", {
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

const SUCCESS_STATES = new Set(["complete", "completed", "succeeded", "success", "done"]);
const FAILURE_STATES = new Set(["failed", "error", "cancelled", "canceled", "interrupted", "aborted", "recoverable_failed", "degraded", "timed_out", "stopped", "terminated"]);

function chatOutcomeError(sessionId, runId, status, detail) {
  const error = new Error(`${detail} session: ${sessionId}; run: ${runId || "unknown"}; status: ${status}`);
  error.chatStatus = status;
  error.sessionId = sessionId;
  error.runId = runId;
  return error;
}

async function waitForAssistant(sessionId, runId, { timeoutMs = 120_000 } = {}) {
  if (!runId) throw chatOutcomeError(sessionId, runId, "unknown", "提交未返回运行标识，结果待确认；请检查会话，勿重复提交。");
  const startedAt = Date.now();
  while (Date.now() - startedAt < timeoutMs) {
    try {
      const remainingMs = Math.max(1, timeoutMs - (Date.now() - startedAt));
      const [data, runData] = await Promise.all([
        engineJson(`/v1/sessions/${encodeURIComponent(sessionId)}/turns?limit=10`, { timeoutMs: Math.min(10_000, remainingMs) }),
        engineJson(`/v1/runs?session_id=${encodeURIComponent(sessionId)}&limit=100`, { timeoutMs: Math.min(10_000, remainingMs) }),
      ]);
      const run = (Array.isArray(runData?.runs) ? runData.runs : []).find((item) => String(item.run_id || item.runId || item.id || "") === runId);
      const runState = String(run?.status || run?.state || "").trim().toLowerCase();
      if (FAILURE_STATES.has(runState)) {
        throw chatOutcomeError(sessionId, runId, runState, "主理人运行未成功，请检查会话中的失败或终止信息。");
      }
      const messages = Array.isArray(data?.messages) ? data.messages : [];
      const currentMessages = messages.filter((message) => isAssistantMessage(message)
        && String(message.runId || message.run_id || message.metadata?.runId || message.metadata?.run_id || "") === runId);
      const candidate = currentMessages.at(-1);
      const messageState = String(candidate?.state || candidate?.status || "").trim().toLowerCase();
      if (FAILURE_STATES.has(messageState)) {
        throw chatOutcomeError(sessionId, runId, messageState, assistantTerminalFailure(candidate)?.message || "主理人回复未完成。");
      }
      // A completed message may be a progress delivery while the run still owns
      // execution. Require both canonical run and transcript terminal evidence.
      if (SUCCESS_STATES.has(runState) && candidate && SUCCESS_STATES.has(messageState)) return candidate;
    } catch (error) {
      if (error?.chatStatus) throw error;
      if (error?.status >= 400 && error.status < 500 && ![408, 429].includes(error.status)) {
        throw chatOutcomeError(sessionId, runId, "unknown", `读取运行结果被 Engine 拒绝（HTTP ${error.status}），结果待确认；请检查会话，勿重复提交。`);
      }
      // Retry transient Engine reads until the bounded timeout.
    }
    const remainingMs = timeoutMs - (Date.now() - startedAt);
    if (remainingMs > 0) await new Promise((resolve) => setTimeout(resolve, Math.min(1500, remainingMs)));
  }
  throw chatOutcomeError(sessionId, runId, "unknown", "等待超时，结果待确认；任务可能仍在运行，本次未取消或重复提交。请检查会话状态。");
}

export async function sendChatMessage(args, { print = true } = {}) {
  const message = optionValue(args, "--message", "") || remainingText(args);
  if (!message) {
    throw new Error("chat 需要消息内容，例如：v8os chat \"你好\"");
  }
  const timeoutMs = Number(optionValue(args, "--timeout", "120")) * 1000;
  if (!Number.isFinite(timeoutMs) || timeoutMs <= 0) throw new Error("--timeout 必须是大于 0 的秒数。");
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
  const payload = buildChatSubmitPayload({
    sessionId,
    message,
    workspacePath,
    workspaceId,
    projectId,
    specMode: hasFlag(args, "--spec"),
    safetyApprovalMode,
  });
  const submit = await engineJson("/v1/chat/submit", {
    method: "POST",
    body: payload,
    timeoutMs: 15_000,
  });
  const runId = String(submit.runId || submit.run_id || submit.id || "");
  const assistant = hasFlag(args, "--no-wait") ? null : await waitForAssistant(sessionId, runId, { timeoutMs });
  const result = {
    sessionId,
    runId,
    status: assistant ? "completed" : "submitted",
    response: assistant ? extractMessageText(assistant) : "",
  };
  if (print) {
    console.log(`session: ${sessionId}`);
    if (result.runId) console.log(`run: ${result.runId}`);
    if (result.response) {
      console.log("");
      console.log(result.response);
    } else {
      console.log(assistant ? "任务已完成，可在会话中查看产物。" : "任务已提交；未等待最终结果。");
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
