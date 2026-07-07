import { adminJson, requireOk } from "./client_api.mjs";
import { sessionIdOf, sessionTitle, summarizeSession } from "./session_commands.mjs";

function hasFlag(args, flag) {
  return args.includes(flag);
}

function optionValue(args, name, fallback = "") {
  const index = args.indexOf(name);
  return index >= 0 ? String(args[index + 1] || fallback) : fallback;
}

function remainingText(args, startIndex = 0) {
  const skipValueFor = new Set(["--reason", "--answer", "--limit"]);
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

function normalizePendingApproval(item) {
  return {
    kind: "approval",
    id: String(item?.id || item?.approvalId || item?.requestId || "").trim(),
    title: String(item?.title || item?.summary || item?.reason || item?.riskSummary || "待审批授权").trim(),
    status: String(item?.status || "pending").trim(),
    sessionId: String(item?.sessionId || item?.session_id || "").trim(),
    raw: item,
  };
}

function normalizeAskUser(item, session) {
  return {
    kind: "ask_user",
    id: String(item?.id || item?.interactionId || item?.askUserId || item?.approvalId || "").trim(),
    title: String(item?.question || item?.title || item?.prompt || "等待用户补充信息").trim(),
    status: String(item?.status || "pending").trim(),
    sessionId: sessionIdOf(session),
    sessionTitle: sessionTitle(session),
    raw: item,
  };
}

export function filterPendingInboxItems(items) {
  return items.filter((item) => {
    if (!item.id) return false;
    const status = String(item.status || "pending").toLowerCase();
    return ["pending", "waiting", "open", "requested"].includes(status);
  });
}

async function listApprovals() {
  const response = await adminJson("/api/client/approvals?status=pending", { timeoutMs: 10_000 });
  if (!response.ok) return [];
  const data = response.data || {};
  const approvals = Array.isArray(data.approvals) ? data.approvals : Array.isArray(data.items) ? data.items : Array.isArray(data) ? data : [];
  return filterPendingInboxItems(approvals.map(normalizePendingApproval));
}

async function listAskUserInteractions(limit = 30) {
  const response = await adminJson("/api/client/conversations", { timeoutMs: 10_000 });
  if (!response.ok || !Array.isArray(response.data)) return [];
  const sessions = response.data.slice(0, limit);
  const results = [];
  for (const session of sessions) {
    const sessionId = sessionIdOf(session);
    if (!sessionId) continue;
    const detail = await adminJson(`/api/client/conversations/${encodeURIComponent(sessionId)}?omitMessages=1`, { timeoutMs: 10_000 });
    if (!detail.ok) continue;
    const record = detail.data || {};
    const interactions = Array.isArray(record.askUserInteractions) ? record.askUserInteractions : [];
    results.push(...interactions.map((item) => normalizeAskUser(item, record)));
  }
  return filterPendingInboxItems(results);
}

function renderInbox(items) {
  if (!items.length) {
    console.log("暂无待处理事项。");
    return;
  }
  for (const item of items) {
    const scope = item.sessionId ? ` session=${item.sessionId}` : "";
    console.log(`- ${item.kind} ${item.id}${scope}`);
    console.log(`  ${item.title}`);
    if (item.sessionTitle) console.log(`  会话：${item.sessionTitle}`);
  }
}

export async function listInbox(args) {
  const limit = Number(optionValue(args, "--limit", "30")) || 30;
  const [approvals, askUser] = await Promise.all([listApprovals(), listAskUserInteractions(limit)]);
  const items = [...approvals, ...askUser];
  if (hasFlag(args, "--json")) console.log(JSON.stringify(items, null, 2));
  else renderInbox(items);
  return items;
}

export async function approveInbox(args) {
  const id = String(args[0] || "").trim();
  if (!id) throw new Error("inbox approve requires <approvalId>");
  const reason = optionValue(args, "--reason", "") || remainingText(args, 1);
  const response = await adminJson(`/api/client/approvals/${encodeURIComponent(id)}/approve`, {
    method: "POST",
    body: reason ? { note: reason, reason } : {},
    timeoutMs: 10_000,
  });
  const result = requireOk(response, "审批通过");
  if (hasFlag(args, "--json")) console.log(JSON.stringify(result, null, 2));
  else console.log(`已同意：${id}`);
  return result;
}

export async function rejectInbox(args) {
  const id = String(args[0] || "").trim();
  if (!id) throw new Error("inbox reject requires <approvalId>");
  const reason = optionValue(args, "--reason", "") || remainingText(args, 1);
  const response = await adminJson(`/api/client/approvals/${encodeURIComponent(id)}/reject`, {
    method: "POST",
    body: reason ? { note: reason, reason } : {},
    timeoutMs: 10_000,
  });
  const result = requireOk(response, "拒绝审批");
  if (hasFlag(args, "--json")) console.log(JSON.stringify(result, null, 2));
  else console.log(`已拒绝：${id}`);
  return result;
}

export async function answerAskUser(args) {
  const id = String(args[0] || "").trim();
  if (!id) throw new Error("inbox answer requires <askUserId>");
  const answer = optionValue(args, "--answer", "") || remainingText(args, 1);
  if (!answer) throw new Error("inbox answer requires answer text");
  const response = await adminJson(`/api/client/ask-user/${encodeURIComponent(id)}/respond`, {
    method: "POST",
    body: { answer },
    timeoutMs: 10_000,
  });
  const result = requireOk(response, "回复 ask_user");
  if (hasFlag(args, "--json")) console.log(JSON.stringify(result, null, 2));
  else console.log(`已回复：${id}`);
  return result;
}

export async function commandInbox(args) {
  const sub = args[0] || "list";
  if (sub === "list") return listInbox(args.slice(1));
  if (sub === "approve") return approveInbox(args.slice(1));
  if (sub === "reject") return rejectInbox(args.slice(1));
  if (sub === "answer") return answerAskUser(args.slice(1));
  throw new Error(`Unknown inbox command: ${args.join(" ")}`);
}

export function summarizeInboxSessionForTest(session) {
  return summarizeSession(session);
}
