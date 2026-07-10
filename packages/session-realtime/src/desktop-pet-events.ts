export const DESKTOP_PET_EVENT_CATALOG = [
  { id: "agent.started", hookEvent: "on_agent_start", labelZh: "智能体开始处理", labelEn: "Agent started", defaultEmotion: "thinking", defaultSpectrum: "cyan", defaultSpeak: false },
  { id: "agent.completed", hookEvent: "on_agent_end", labelZh: "智能体处理完成", labelEn: "Agent completed", defaultEmotion: "happy", defaultSpectrum: "emerald_green", defaultSpeak: false },
  { id: "agent.failed", hookEvent: "on_agent_end", labelZh: "智能体处理失败", labelEn: "Agent failed", defaultEmotion: "worried", defaultSpectrum: "crimson_red", defaultSpeak: true },
  { id: "run.reasoning.delta", hookEvent: "on_supervisor_thinking_start", labelZh: "Supervisor 开始思考", labelEn: "Supervisor thinking", defaultEmotion: "thinking", defaultSpectrum: "violet", defaultSpeak: false },
  { id: "tool.started", hookEvent: "on_tool_execute_start", labelZh: "工具开始执行", labelEn: "Tool execution started", defaultEmotion: "tool_calling", defaultSpectrum: "blue", defaultSpeak: false },
  { id: "tool.finished", hookEvent: "on_tool_execute_end", labelZh: "工具执行结束", labelEn: "Tool execution finished", defaultEmotion: "idle", defaultSpectrum: "blue", defaultSpeak: false },
  { id: "subagent.task.started", hookEvent: "on_agent_start", labelZh: "子代理任务开始", labelEn: "Sub-agent task started", defaultEmotion: "thinking", defaultSpectrum: "violet", defaultSpeak: false },
  { id: "subagent.task.completed", hookEvent: "on_agent_end", labelZh: "子代理任务完成", labelEn: "Sub-agent task completed", defaultEmotion: "happy", defaultSpectrum: "emerald_green", defaultSpeak: false },
  { id: "subagent.task.failed", hookEvent: "on_agent_end", labelZh: "子代理任务失败", labelEn: "Sub-agent task failed", defaultEmotion: "worried", defaultSpectrum: "crimson_red", defaultSpeak: true },
  { id: "ask_user.requested", labelZh: "等待用户回答", labelEn: "Waiting for user input", defaultEmotion: "curious", defaultSpectrum: "golden_amber", defaultSpeak: true },
  { id: "approval.requested", labelZh: "等待用户审批", labelEn: "Waiting for approval", defaultEmotion: "curious", defaultSpectrum: "golden_amber", defaultSpeak: true },
  { id: "artifact.recorded", labelZh: "产物已就绪", labelEn: "Artifact ready", defaultEmotion: "happy", defaultSpectrum: "emerald_green", defaultSpeak: true },
  { id: "run.completed", hookEvent: "on_chat_end", labelZh: "本轮任务完成", labelEn: "Task completed", defaultEmotion: "happy", defaultSpectrum: "emerald_green", defaultSpeak: true },
  { id: "run.failed", hookEvent: "on_chat_end", labelZh: "本轮任务失败", labelEn: "Task failed", defaultEmotion: "worried", defaultSpectrum: "crimson_red", defaultSpeak: true },
] as const;

export type DesktopPetEventId = (typeof DESKTOP_PET_EVENT_CATALOG)[number]["id"];

const DESKTOP_PET_EVENT_IDS = new Set<string>(DESKTOP_PET_EVENT_CATALOG.map((event) => event.id));

const LEGACY_EVENT_GROUPS: Array<{ pattern: RegExp; events: DesktopPetEventId[] }> = [
  { pattern: /reasoning|thinking|思考/i, events: ["run.reasoning.delta"] },
  { pattern: /tool_start|tool\.started/i, events: ["tool.started"] },
  { pattern: /tool_result|tool\.finished/i, events: ["tool.finished"] },
  { pattern: /ask_user|等待用户/i, events: ["ask_user.requested"] },
  { pattern: /approval|审批|确认/i, events: ["approval.requested"] },
  { pattern: /artifact|产物/i, events: ["artifact.recorded"] },
  { pattern: /subagent.*(start|开始)|subagent\.task\.started/i, events: ["subagent.task.started"] },
  { pattern: /subagent.*(complete|done|完成)|subagent\.task\.completed/i, events: ["subagent.task.completed"] },
  { pattern: /subagent.*(fail|失败)|subagent\.task\.failed/i, events: ["subagent.task.failed"] },
  { pattern: /completed|succeeded|success|done|完成|idle|settled|空闲|静默/i, events: ["run.completed"] },
  { pattern: /failed|error|blocked|interrupted|失败|阻塞|异常/i, events: ["run.failed"] },
];

export function normalizeDesktopPetEventId(value: unknown): DesktopPetEventId | null {
  const normalized = String(value || "").trim().toLowerCase();
  if (DESKTOP_PET_EVENT_IDS.has(normalized)) return normalized as DesktopPetEventId;
  if (normalized === "tool_start" || normalized.endsWith(".tool.started")) return "tool.started";
  if (normalized === "tool_result" || normalized.endsWith(".tool.finished")) return "tool.finished";
  return null;
}

export function expandLegacyDesktopPetEvents(value: unknown): DesktopPetEventId[] {
  const exact = normalizeDesktopPetEventId(value);
  if (exact) return [exact];
  const text = String(value || "").trim();
  if (!text) return [];
  const matches = LEGACY_EVENT_GROUPS.flatMap((group) => group.pattern.test(text) ? group.events : []);
  const unique = [...new Set(matches)];
  const subagentEvents = unique.filter((eventId) => eventId.startsWith("subagent."));
  return subagentEvents.length ? subagentEvents : unique;
}

export function desktopPetEventLabel(eventId: DesktopPetEventId, locale = "zh-CN") {
  const event = DESKTOP_PET_EVENT_CATALOG.find((candidate) => candidate.id === eventId);
  if (!event) return eventId;
  return locale.toLowerCase().startsWith("en") ? event.labelEn : event.labelZh;
}
