// Identity, target selection and execution share the canonical Engine owner.
export { engineResponse as engineJson } from "./engine_client.mjs";

export function requireOk(response, label) {
  if (response.ok) return response.data;
  const detail = response.data?.detail;
  const detailText = typeof detail === "string"
    ? detail
    : detail && typeof detail === "object"
      ? detail.summary || detail.error || detail.message || detail.reason || JSON.stringify(detail)
      : "";
  const error = response.data?.summary
    || response.data?.error
    || response.data?.message
    || detailText
    || response.data?.rawText
    || "unknown_error";
  const code = typeof detail === "object" && detail ? detail.error : response.data?.error || detailText;
  const workspaceHintCodes = new Set([
    "workspace_binding_required",
    "workspace_trust_required",
    "workspace_side_effect_blocked",
  ]);
  const hint = workspaceHintCodes.has(String(code || "").trim())
    ? " 请先运行 v8os workspace create <path> --select 或 v8os workspace select <path>，确认并信任项目工作区后重试。"
    : "";
  throw new Error(`${label} 失败：${response.status} ${error}${hint}`);
}
