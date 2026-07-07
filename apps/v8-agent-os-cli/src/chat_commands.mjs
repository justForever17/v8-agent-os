import readline from "node:readline/promises";
import { stdin as input, stdout as output } from "node:process";
import { adminJson, requireOk } from "./client_api.mjs";

function optionValue(args, name, fallback = "") {
  const index = args.indexOf(name);
  return index >= 0 ? String(args[index + 1] || fallback) : fallback;
}

function hasFlag(args, flag) {
  return args.includes(flag);
}

function remainingText(args) {
  const skipped = new Set([
    "--session",
    "--workspace",
    "--project",
    "--timeout",
    "--message",
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

export function buildChatSubmitPayload({ sessionId, message, workspacePath = "", projectId = "", specMode = false }) {
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
      projectId: projectId || undefined,
      specMode,
      taskPlanningMode: false,
    },
  };
}

async function ensureSession({ sessionId, message, workspacePath, projectId }) {
  if (sessionId) return sessionId;
  const response = await adminJson("/api/client/conversations", {
    method: "POST",
    body: {
      title: message.slice(0, 40) || "CLI Chat",
      workspacePath: workspacePath || undefined,
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
  const workspacePath = optionValue(args, "--workspace", "");
  const projectId = optionValue(args, "--project", "");
  const sessionId = await ensureSession({
    sessionId: optionValue(args, "--session", ""),
    message,
    workspacePath,
    projectId,
  });
  if (!sessionId) throw new Error("无法创建或定位会话");
  const beforeIds = await latestMessageIds(sessionId);
  const payload = buildChatSubmitPayload({
    sessionId,
    message,
    workspacePath,
    projectId,
    specMode: hasFlag(args, "--spec"),
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
  const projectId = optionValue(args, "--project", "");
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
        ...(projectId ? ["--project", projectId] : []),
        text,
      ], { print: false });
      sessionId = result.sessionId;
      console.log(`主理人> ${result.response || "处理中，可稍后查看会话。"}`);
    }
  } finally {
    rl.close();
  }
}
