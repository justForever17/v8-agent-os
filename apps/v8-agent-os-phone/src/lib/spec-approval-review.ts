import type { PendingApproval } from "@/src/types/admin";

export function isSpecStageApproval(approval?: PendingApproval | null) {
    return String(approval?.approval_kind || approval?.request?.approvalKind || approval?.request?.approval_kind || "")
        .trim().toLowerCase() === "spec_stage_approval";
}

export function specApprovalReviewHref(approval: PendingApproval, fallbackWorkspacePath = "") {
    const request = approval.request || {};
    const query = new URLSearchParams({ approvalId: String(approval.id || approval.approval_id || "") });
    query.set("workspace", String(request.workspacePath || request.workspace_path || fallbackWorkspacePath));
    query.set("specId", String(request.specId || request.spec_id || ""));
    query.set("stage", String(request.stage || request.specStage || request.spec_stage || ""));
    return `/specs?${query}`;
}

export function specReviewDraftKey(authorityKey: string, approvalId: string) {
    return `spec-review:${JSON.stringify([authorityKey, approvalId])}`;
}
