export type RuntimeSummarySignal = {
  kind?: string | null;
  topic?: string | null;
  executionType?: string | null;
};

export function shouldProjectRuntimeSummarySignal(signal: RuntimeSummarySignal) {
  const kind = String(signal.kind || "").trim().toLowerCase();
  const topic = String(signal.topic || "").trim().toLowerCase();
  const executionType = String(signal.executionType || "").trim().toLowerCase();
  if (kind === "tool" || ["tool_call", "tool_result", "reasoning"].includes(executionType)) {
    return false;
  }
  if (["heartbeat", "lease", "snapshot", "checkpoint", "metrics", "trace", "debug"].some((token) => topic.includes(token))) {
    return false;
  }
  return !(
    topic.includes("reasoning")
    || topic.includes("text.delta")
    || topic.includes("text_chunk")
    || topic.includes("tool.started")
    || topic.includes("tool.finished")
    || topic.includes("tool_start")
    || topic.includes("tool_result")
  );
}

export function humanizeRuntimeSummaryText(value: unknown, locale = "zh-CN") {
  const zh = String(locale || "").toLowerCase().startsWith("zh");
  const replacements: Array<[RegExp, string]> = [
    [/\brun_[a-z0-9_-]{6,}\b/gi, zh ? "当前任务" : "current task"],
    [/\bepisode_[a-z0-9_-]{6,}\b/gi, zh ? "执行阶段" : "execution stage"],
    [/\b(?:workflow|handoff|approval|interaction|task_brief|delegation)_[a-z0-9_-]{6,}\b/gi, zh ? "内部记录" : "internal record"],
    [/\b[a-f0-9]{24,}\b/gi, zh ? "内部记录" : "internal record"],
  ];
  let text = String(value || "").trim();
  for (const [pattern, replacement] of replacements) {
    text = text.replace(pattern, replacement);
  }
  text = text
    .replace(/\s{2,}/g, " ")
    .replace(/(?:内部记录|internal record)(?:[、,，;；:\s-]+(?:内部记录|internal record))+/gi, zh ? "内部记录" : "internal record")
    .trim();
  return text || (zh ? "任务状态已更新" : "Task status updated");
}
