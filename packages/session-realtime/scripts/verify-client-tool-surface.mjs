import { buildClientToolSurface } from "../dist/index.js";

function assert(condition, message) {
  if (!condition) {
    throw new Error(message);
  }
}

const researchSurface = buildClientToolSurface({
  toolName: "research_broker",
  state: "result",
  result: [
    "Research result pack",
    "答案：优先使用官方文档，并保留来源评分。",
    "Sources:",
    "- Official docs: https://example.com/docs",
    "Detail: rawRef=toolobs://research/abc123",
  ].join("\n"),
});

assert(researchSurface.status === "completed", "completed result should be completed");
assert(researchSurface.summary.includes("答案：优先使用官方文档"), "should pick answer line");
assert(researchSurface.refIds.includes("toolobs://research/abc123"), "should extract rawRef URI");
assert(!researchSurface.refIds.some((ref) => ref.includes("'")), "rawRef URI should not include wrapping quotes");

const blockedSurface = buildClientToolSurface({
  toolName: "write_native_file",
  state: "result",
  result: {
    status: "safety_blocked",
    summary: "安全阻断：缺少用户批准。",
    recommendedNextAction: "请求用户批准后重试。",
    rawRef: "toolobs://safety/block-1",
  },
});

assert(blockedSurface.status === "blocked", "safety_blocked should be blocked");
assert(blockedSurface.actionable?.includes("请求用户批准"), "should expose next action");
assert(blockedSurface.refIds.includes("toolobs://safety/block-1"), "should extract refs from JSON payload");

const runningSurface = buildClientToolSurface({
  toolName: "run_system_command",
  state: "call",
  result: undefined,
});

assert(runningSurface.status === "running", "call state should be running");
assert(runningSurface.title === "run_system_command", "title should be tool name");

console.log("client tool surface contract verified");
