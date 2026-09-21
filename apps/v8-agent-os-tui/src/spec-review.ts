import { createHash } from 'node:crypto';
import type { Client } from './client.js';

export type SpecReviewDocument = { content: string; documentSha256: string; documentPath: string };
const requestOf = (item: any) => item?.request || item?.payload || {};
const identityOf = (item: any) => String(item?.id || item?.approvalId || item?.approval_id || '');
export function isSpecApproval(item: any): boolean {
  const request = requestOf(item);
  return (item?.approval_kind || item?.approvalKind || request.approvalKind || request.approval_kind) === 'spec_stage_approval';
}
export function specReviewTarget(item: any) {
  const request = requestOf(item);
  const target = { specId: String(request.specId || request.spec_id || ''),
    stage: String(request.stage || request.specStage || request.spec_stage || '').toLowerCase(),
    workspace: String(request.specBrief?.workspacePath || request.workspacePath || request.workspace_path || '') };
  if (!isSpecApproval(item) || !identityOf(item) || !target.specId || !target.stage || !target.workspace) throw new Error('Spec 审批缺少文档定位信息，请刷新待处理事项。');
  return target;
}
export function verifiedSpecDocument(value: any): SpecReviewDocument {
  const content = value?.content, hash = String(value?.documentSha256 || '');
  const documentPath = String(value?.documentPath || value?.relativePath || '');
  if (value?.truncated === true || typeof content !== 'string' || !documentPath || !/^[a-f0-9]{64}$/i.test(hash)) throw new Error('未取得完整的 Spec 文档，不能批准。');
  const actual = createHash('sha256').update(content, 'utf8').digest('hex');
  if (actual !== hash.toLowerCase()) throw new Error('Spec 文档内容与版本不一致，请重新读取。');
  return { content, documentSha256: actual, documentPath };
}
export function specReviewMatches(item: any, document: SpecReviewDocument): boolean {
  const request = requestOf(item);
  return String(request.documentSha256 || '').toLowerCase() === document.documentSha256 && request.documentPath === document.documentPath;
}
export async function readSpecReview(client: Client, item: any): Promise<SpecReviewDocument> {
  const { specId, stage, workspace } = specReviewTarget(item);
  const query = new URLSearchParams({ workspace_path: workspace, full_content: 'true' });
  const result = await client.api(`/v1/specs/${encodeURIComponent(specId)}?${query}`);
  if (result.ok !== true) throw new Error('Engine 未返回完整 Spec 文档，请刷新重试。');
  return verifiedSpecDocument(result.stages?.[stage]);
}
export function validatedSpecReplacement(result: any, previous: any, document: SpecReviewDocument) {
  const next = result?.approval, before = specReviewTarget(previous), after = specReviewTarget(next);
  if (result.replacesApprovalId !== identityOf(previous) || !identityOf(next) || next.status !== 'pending'
    || before.specId !== after.specId || before.stage !== after.stage || before.workspace !== after.workspace
    || (next.session_id || next.sessionId) !== (previous.session_id || previous.sessionId)
    || (next.run_id || next.runId) !== (previous.run_id || previous.runId)
    || !specReviewMatches(next, document)) throw new Error('新审批与已读取文档或任务不匹配，请重新同步。');
  return { ...next, id: identityOf(next), kind: 'approval' };
}
