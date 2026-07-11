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
