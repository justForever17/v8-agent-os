import { spawn } from "node:child_process";
import { DEFAULT_PORTS } from "./paths.mjs";
import { adminJson, requireOk } from "./client_api.mjs";
import { sendChatMessage } from "./chat_commands.mjs";

function hasFlag(args, flag) {
  return args.includes(flag);
}

function optionValue(args, name, fallback = "") {
  const index = args.indexOf(name);
  return index >= 0 ? String(args[index + 1] || fallback) : fallback;
}

function optionNumber(args, name, fallback) {
  const value = Number(optionValue(args, name, String(fallback)));
  return Number.isFinite(value) && value > 0 ? value : fallback;
}

function remainingText(args, startIndex = 0) {
  const skipValueFor = new Set(["--limit"]);
  const parts = [];
  for (let index = startIndex; index < args.length; index += 1) {
    const item = args[index];
    if (skipValueFor.has(item)) {
      index += 1;
      continue;
    }
    if (item.startsWith("--")) continue;
    parts.push(item);
  }
  return parts.join(" ").trim();
}

export function sessionTitle(session) {
  return String(session?.title || session?.name || session?.summary || "Untitled").trim();
}

export function sessionIdOf(session) {
  return String(session?.id || session?.sessionId || session?.conversationId || "").trim();
}

function sessionStatus(session) {
  return String(session?.status || session?.runStatus || session?.state || "").trim();
}

export function summarizeSession(session) {
  return {
    id: sessionIdOf(session),
    title: sessionTitle(session),
    project: session?.projectId || session?.project || "",
    workspace: session?.workspaceId || session?.workspace || session?.workspacePath || "",
    status: sessionStatus(session),
    updatedAt: session?.lastActivityAt || session?.updatedAt || session?.createdAt || "",
  };
}

function renderSessions(sessions) {
  if (!sessions.length) {
    console.log("暂无会话。");
    return;
  }
  for (const item of sessions.map(summarizeSession)) {
    const status = item.status ? ` [${item.status}]` : "";
    const scope = [item.project, item.workspace].filter(Boolean).join(" / ");
    console.log(`- ${item.id}${status}`);
    console.log(`  ${item.title}${scope ? ` (${scope})` : ""}`);
    if (item.updatedAt) console.log(`  updated: ${item.updatedAt}`);
  }
}

function renderTurnMessages(messages) {
  if (!messages.length) {
    console.log("暂无消息。");
    return;
  }
  for (const message of messages) {
    const role = String(message?.role || message?.authorRole || message?.author || "message");
    const content = typeof message?.content === "string"
      ? message.content
      : typeof message?.text === "string"
        ? message.text
        : Array.isArray(message?.content)
          ? message.content.map((part) => (typeof part === "string" ? part : part?.text || "")).filter(Boolean).join("\n")
          : "";
    const preview = String(content || message?.summary || "").trim();
    console.log(`\n[${role}]`);
    console.log(preview || "(非文本消息或组件消息)");
  }
}

function openUrl(url) {
  const command = process.platform === "win32" ? "cmd" : process.platform === "darwin" ? "open" : "xdg-open";
  const commandArgs = process.platform === "win32" ? ["/c", "start", "", url] : [url];
  spawn(command, commandArgs, { detached: true, stdio: "ignore" }).unref();
}

export async function listSessions(args) {
  const json = hasFlag(args, "--json");
  const limit = optionNumber(args, "--limit", 20);
  const response = await adminJson("/api/client/conversations", { timeoutMs: 10_000 });
  const sessions = Array.isArray(requireOk(response, "列出会话")) ? response.data.slice(0, limit) : [];
  if (json) console.log(JSON.stringify(sessions.map(summarizeSession), null, 2));
  else renderSessions(sessions);
  return sessions;
}

export async function showSession(args) {
  const sessionId = String(args[0] || "").trim();
  if (!sessionId) throw new Error("sessions show requires <sessionId>");
  const response = await adminJson(`/api/client/conversations/${encodeURIComponent(sessionId)}?omitMessages=1`, { timeoutMs: 10_000 });
  const session = requireOk(response, "读取会话");
  if (hasFlag(args, "--json")) console.log(JSON.stringify(session, null, 2));
  else renderSessions([session]);
  return session;
}

export async function showSessionTurns(args) {
  const sessionId = String(args[0] || "").trim();
  if (!sessionId) throw new Error("sessions turns requires <sessionId>");
  const limit = optionNumber(args, "--limit", 1);
  const response = await adminJson(`/api/client/conversations/${encodeURIComponent(sessionId)}/turns?limit=${limit}`, { timeoutMs: 10_000 });
  const payload = requireOk(response, "读取会话轮次");
  if (hasFlag(args, "--json")) console.log(JSON.stringify(payload, null, 2));
  else renderTurnMessages(Array.isArray(payload.messages) ? payload.messages : []);
  return payload;
}

export function openSession(args) {
  const sessionId = String(args[0] || "").trim();
  if (!sessionId) throw new Error("sessions open requires <sessionId>");
  const url = `http://127.0.0.1:${DEFAULT_PORTS.web}/chat?id=${encodeURIComponent(sessionId)}`;
  openUrl(url);
  console.log(url);
  return url;
}

export async function resumeSession(args) {
  const sessionId = String(args[0] || "").trim();
  if (!sessionId) throw new Error("sessions resume requires <sessionId>");
  const text = remainingText(args, 1);
  if (!text) return openSession([sessionId]);
  return sendChatMessage(["--session", sessionId, text]);
}

export async function commandSessions(args) {
  const sub = args[0] || "list";
  if (sub === "list") return listSessions(args.slice(1));
  if (sub === "show") return showSession(args.slice(1));
  if (sub === "turns") return showSessionTurns(args.slice(1));
  if (sub === "open") return openSession(args.slice(1));
  if (sub === "resume") return resumeSession(args.slice(1));
  throw new Error(`Unknown sessions command: ${args.join(" ")}`);
}
