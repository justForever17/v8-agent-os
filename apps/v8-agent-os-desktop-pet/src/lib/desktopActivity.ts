import { buildSessionStreamUiEvent } from "@v8/session-realtime";

export type DesktopActivityKind =
  | "thinking"
  | "tool_calling"
  | "runtime_active"
  | "subagent_active"
  | "artifact_ready"
  | "approval_needed"
  | "message"
  | "error";

export type DesktopActivity = {
  id: string;
  kind: DesktopActivityKind;
  title: string;
  summary: string;
  status: "running" | "completed" | "warning" | "failed" | "queued" | "waiting";
  at: number;
  runtimeId?: string;
};

export type DesktopMessage = {
  id: string;
  role: "user" | "assistant" | "system";
  content: string;
  createdAt: number;
};

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" ? value as Record<string, unknown> : {};
}

function readString(...values: unknown[]) {
  for (const value of values) {
    if (typeof value === "string" && value.trim()) return value.trim();
    if (typeof value === "number" && Number.isFinite(value)) return String(value);
  }
  return "";
}

function readTimestamp(...values: unknown[]) {
  for (const value of values) {
    if (typeof value === "number" && Number.isFinite(value)) return value;
    if (typeof value === "string" && value.trim()) {
      const parsed = Date.parse(value);
      if (Number.isFinite(parsed)) return parsed;
    }
  }
  return Date.now();
}

function normalizeStatus(raw: string): DesktopActivity["status"] {
  const value = raw.toLowerCase();
  if (/(fail|error|reject|blocked|cancel|stalled)/.test(value)) return "failed";
  if (/(warn|degraded|missing|unconfirmed|attempt)/.test(value)) return "warning";
  if (/(complete|finish|done|success|ready|succeeded)/.test(value)) return "completed";
  if (/(queued|pending)/.test(value)) return "queued";
  if (/(wait|idle)/.test(value)) return "waiting";
  return "running";
}

function firstArray(root: Record<string, unknown>, keys: string[]) {
  for (const key of keys) {
    const parts = key.split(".");
    let current: unknown = root;
    for (const part of parts) {
      current = asRecord(current)[part];
    }
    if (Array.isArray(current)) return current;
  }
  return [];
}

function messageContent(record: Record<string, unknown>) {
  const direct = readString(record.content, record.text, record.message);
  if (direct) return direct;
  const nodes = Array.isArray(record.nodes) ? record.nodes : [];
  const nodeText = nodes
    .map((node) => readString(
      asRecord(node).content,
      asRecord(node).text,
      asRecord(node).message,
      asRecord(node).summary,
    ))
    .filter(Boolean)
    .join("\n");
  if (nodeText) return nodeText;
  const parts = Array.isArray(record.parts) ? record.parts : [];
  return parts
    .map((part) => readString(asRecord(part).content, asRecord(part).text))
    .filter(Boolean)
    .join("\n");
}

function isChatTextMessage(record: Record<string, unknown>, content: string) {
  const marker = readString(
    record.kind,
    record.type,
    record.messageType,
    record.message_type,
    record.contentType,
    record.content_type,
    record.topic,
  ).toLowerCase();
  if (/(reasoning|thinking|thought|tool|runtime|episode|artifact|approval|ask_user|event|activity)/.test(marker)) {
    return false;
  }
  if (/^<(think|reasoning)(\s|>)/i.test(content.trim())) {
    return false;
  }
  const role = readString(record.role, record.sender).toLowerCase();
  const hasChatRole = role === "user" || role === "assistant" || role === "system" || role === "supervisor";
  return hasChatRole || /(message|text|chat)/.test(marker);
}

export function extractDesktopMessages(snapshotPayload: unknown): DesktopMessage[] {
  const root = asRecord(snapshotPayload);
  const messages = firstArray(root, ["snapshot.messages", "messages", "projection.messages"]);
  return messages
    .map((item, index) => {
      const record = asRecord(item);
      const role = readString(record.role, record.sender).toLowerCase();
      const content = messageContent(record);
      if (!content) return null;
      if (!isChatTextMessage(record, content)) return null;
      return {
        id: readString(record.id, record.messageId, record.message_id) || `message-${index}`,
        role: role === "user" ? "user" : role === "system" ? "system" : "assistant",
        content,
        createdAt: readTimestamp(record.timestamp, record.createdAt, record.created_at),
      } satisfies DesktopMessage;
    })
    .filter(Boolean) as DesktopMessage[];
}

export function extractRuntimeActivities(snapshotPayload: unknown): DesktopActivity[] {
  const root = asRecord(snapshotPayload);
  const runtimeTimeline = firstArray(root, ["snapshot.runtimeTimeline", "runtimeTimeline", "projection.runtimeTimeline"]);
  return runtimeTimeline.map((item, index) => buildActivityFromRuntimeEntry(item, index)).filter(Boolean) as DesktopActivity[];
}

export function buildActivityFromRuntimeEntry(input: unknown, index = 0): DesktopActivity | null {
  const record = asRecord(input);
  if (!Object.keys(record).length) return null;
  const metadata = asRecord(record.metadata);
  const topic = readString(record.topic, metadata.topic, record.name);
  const summary = readString(record.summary, metadata.summary, metadata.message, metadata.content, record.content);
  const runtimeId = readString(record.runtimeId, record.runtime_id, metadata.runtimeId, metadata.runtime_id);
  const status = normalizeStatus(readString(record.status, metadata.status, topic, summary));
  return {
    id: readString(record.id, record.eventId, record.event_id) || `${topic || "runtime"}-${index}`,
    kind: inferActivityKind(topic, runtimeId, summary),
    title: labelForActivity(topic, runtimeId),
    summary: summary || topic || "运行中",
    status,
    at: readTimestamp(record.timestamp, record.ts, metadata.timestamp),
    runtimeId,
  };
}

export function buildActivityFromRealtimeEvent(raw: unknown): DesktopActivity | null {
  const normalized = buildSessionStreamUiEvent(raw as never, { locale: "zh-CN" }) as unknown;
  const normalizedRecord = asRecord(normalized);
  const record = Object.keys(normalizedRecord).length ? normalizedRecord : asRecord(raw);
  const data = asRecord(record.data);
  const topic = readString(record.topic, data.topic, record.name);
  const summary = readString(record.summary, data.summary, data.message, record.content, data.content);
  const runtimeId = readString(record.runtimeId, record.runtime_id, data.runtimeId, data.runtime_id);
  if (!topic && !summary) return null;
  return {
    id: readString(record.id, record.event_id, data.event_id) || `${topic || "event"}-${Date.now()}`,
    kind: inferActivityKind(topic, runtimeId, summary),
    title: labelForActivity(topic, runtimeId),
    summary: summary || topic,
    status: normalizeStatus(readString(record.status, data.status, topic, summary)),
    at: readTimestamp(record.ts, record.timestamp, data.timestamp),
    runtimeId,
  };
}

function inferActivityKind(topic: string, runtimeId: string, summary: string): DesktopActivityKind {
  const joined = `${topic} ${runtimeId} ${summary}`.toLowerCase();
  if (joined.includes("approval") || joined.includes("ask_user")) return "approval_needed";
  if (joined.includes("artifact")) return "artifact_ready";
  if (joined.includes("tool") || joined.includes("command")) return "tool_calling";
  if (joined.includes("reasoning") || joined.includes("thinking")) return "thinking";
  if (joined.includes("subagent") || joined.includes("delegation")) return "subagent_active";
  if (joined.includes("runtime") || joined.includes("episode") || runtimeId) return "runtime_active";
  return "message";
}

function labelForActivity(topic: string, runtimeId: string) {
  const joined = `${topic} ${runtimeId}`.toLowerCase();
  if (joined.includes("research")) return "调研运行";
  if (joined.includes("engineering")) return "工程运行";
  if (joined.includes("creative")) return "创意媒体";
  if (joined.includes("computer")) return "桌面操作";
  if (joined.includes("rpa")) return "RPA";
  if (joined.includes("subagent") || joined.includes("delegation")) return "子代理";
  if (joined.includes("artifact")) return "产物";
  if (joined.includes("tool")) return "工具";
  if (joined.includes("reasoning")) return "思考";
  return "运行事件";
}

export function upsertActivity(list: DesktopActivity[], next: DesktopActivity, limit = 18) {
  const existingIndex = list.findIndex((item) => item.id === next.id);
  const merged = existingIndex >= 0
    ? list.map((item, index) => index === existingIndex ? { ...item, ...next } : item)
    : [...list, next];
  return merged
    .sort((left, right) => left.at - right.at)
    .slice(-limit);
}

export function latestAssistantText(messages: DesktopMessage[]) {
  return [...messages].reverse().find((message) => message.role === "assistant")?.content || "";
}
