import type { SessionApprovalView } from "@v8/session-realtime";

export type SpecReviewDecision = { approvalId: string; documentSha256: string; replaceSpecReview?: boolean };
export type SpecReviewDocument = { content: string; documentSha256: string; documentPath: string };

export function specRecord(value: unknown): Record<string, unknown> {
    return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

export function specError(payload: unknown, fallback: string) {
    const root = specRecord(payload);
    const detail = specRecord(root.detail);
    const message = [detail.message, detail.summary, typeof root.detail === 'string' ? root.detail : '', root.message, root.error]
        .find(value => typeof value === 'string' && value.trim());
    return Object.assign(new Error(String(message || fallback)), { code: String(detail.code || root.code || '') });
}

export async function verifiedSpecDocument(value: unknown): Promise<SpecReviewDocument> {
    const document = specRecord(value);
    const content = document.content;
    const hash = String(document.documentSha256 || '');
    const documentPath = String(document.documentPath || document.relativePath || '');
    if (document.truncated === true || typeof content !== 'string' || !documentPath || !/^[a-f0-9]{64}$/i.test(hash)) {
        throw new Error('无法读取完整的文档版本，请重新载入。');
    }
    const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(content));
    const actualHash = Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, '0')).join('');
    if (hash.toLowerCase() !== actualHash) throw new Error('文档内容与版本不一致，请重新载入。');
    return { content, documentSha256: actualHash, documentPath };
}

export function specReviewMatches(approval: SessionApprovalView, document: SpecReviewDocument) {
    const request = specRecord(approval.request);
    return request.documentSha256 === document.documentSha256 && request.documentPath === document.documentPath;
}

export function validateRefreshedSpecApproval(payload: unknown, previous: SessionApprovalView, document: SpecReviewDocument) {
    const root = specRecord(payload);
    const next = specRecord(root.approval) as SessionApprovalView;
    const oldRequest = specRecord(previous.request);
    const request = specRecord(next.request);
    const id = String(next.id || next.approval_id || '');
    if (!id || root.replacesApprovalId !== previous.id || !specReviewMatches(next, document)
        || (request.specId || request.spec_id) !== (oldRequest.specId || oldRequest.spec_id)
        || request.stage !== oldRequest.stage
        || (specRecord(request.specBrief).workspacePath || request.workspacePath || request.workspace_path)
            !== (specRecord(oldRequest.specBrief).workspacePath || oldRequest.workspacePath || oldRequest.workspace_path)) {
        throw new Error('新的审批与当前文档不匹配，请重新同步。');
    }
    return { ...next, id };
}
