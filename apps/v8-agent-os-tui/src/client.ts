import { engineJson, engineUpload } from '../../v8-agent-os-cli/src/engine_client.mjs';
import { randomUUID } from 'node:crypto';
import { openAsBlob } from 'node:fs';
import { stat } from 'node:fs/promises';
import path from 'node:path';
import { normalizeSessionRuntimeEvent } from '../../../packages/session-realtime/src/event-normalizer.js';
import { normalizeAuthoritativeSessionHistoryList } from '../../../packages/session-realtime/src/history.js';
import { isActiveRunStatus } from '../../../packages/session-realtime/src/run-status.js';
import { buildSessionOutputProjection } from '../../../packages/session-realtime/src/session-output-projection.js';
import { ViewStore, type ViewState, type Draft } from './persistence.js';

export type Api = (route: string, options?: any) => Promise<any>;
export const idOf = (item: any) => String(item?.id || item?.sessionId || item?.session_id || item?.runId || item?.run_id || '');
export const pending = (item: any) => ['pending', 'waiting', 'open', 'requested'].includes(String(item?.status || 'pending'));
export class Client {
  view: ViewState; messages: any[] = []; snapshot: any = {}; instance: any = {};
  sessions: any[] = []; sessionCursor = ''; page: any = {}; syncCursor = ''; seq = 0;
  connection = '连接中'; notice = ''; busy = false; revision = 0; generation = 0;
  private stopped = false; private listeners = new Set<() => void>(); private saveTimer?: NodeJS.Timeout;
  private lastSnapshot = 0; private lastIndex = 0; private query = ''; private workspaceOnly = false;
  private owner: any = {};
  constructor(readonly store = new ViewStore(), readonly transport: Api = engineJson) { this.view = store.read(); }
  api: Api = (route, options = {}) => this.transport(route, { ...options, headers: {
    ...options.headers,
    ...(this.instance.instanceId ? { 'x-v8-authority-instance-id': this.instance.instanceId } : {}),
    ...(this.owner.sessionIdentifier ? { 'x-v8-agent-os-user-email': this.owner.sessionIdentifier } : {}),
  } });
  subscribe = (fn: () => void) => { this.listeners.add(fn); return () => { this.listeners.delete(fn); }; };
  changed = () => { this.revision++; for (const fn of this.listeners) fn(); };
  getRevision = () => this.revision;
  save(now = false) {
    clearTimeout(this.saveTimer);
    const write = () => { try { this.store.write(this.view); } catch (e: any) { this.notice = e.message; this.changed(); if (now) throw e; } };
    if (now) write(); else this.saveTimer = setTimeout(write, 300);
  }
  get draft(): Draft { return this.view.drafts[this.view.sessionId || 'new'] ||= { text: '', attachments: [] }; }
  setDraft(text: string) { this.draft.text = text; this.save(); }
  get run() { return this.snapshot.currentRun || {}; }
  get active() { return isActiveRunStatus(this.run.status || this.snapshot.runtimeStatus); }
  get inbox() {
    return [...(this.snapshot.approvals || []).map((x: any) => ({ ...x, kind: 'approval' })),
      ...(this.snapshot.askUserInteractions || []).map((x: any) => ({ ...x, kind: 'question' }))].filter(pending);
  }
  get outputs() { return buildSessionOutputProjection(this.messages, this.snapshot.snapshot?.artifacts || [], { sessionId: this.view.sessionId }); }
  async initialize() {
    try {
      this.instance = await this.api('/v1/client-identity/instance');
      const owner = await this.api('/v1/client-identity/owner');
      this.owner = owner.user || {};
      if (!owner.initialized) {
        this.connection = '已连接'; this.notice = '首次配置：F4 → 手机 → 初始化本机 owner，然后 F3 连接模型与工作区。';
        this.changed(); return;
      }
      this.connection = '已连接';
      await this.listSessions();
      if (this.view.sessionId) await this.attach(this.view.sessionId);
      else this.notice = '新建对话 · F3 配置模型与工作区 · Ctrl+P 查看操作';
    } catch (e: any) { this.connection = '未连接'; this.notice = 'Engine 未连接。已有安装：v8os service start；首次安装见 F1 帮助。' + e.message; }
    this.changed();
  }
  async listSessions(query = this.query, more = false, workspaceOnly = this.workspaceOnly) {
    if (!this.owner.sessionIdentifier && this.instance.instanceId) {
      const owner = await this.api('/v1/client-identity/owner'); this.owner = owner.user || {};
      if (!owner.initialized) throw new Error('请先在连接页初始化本机 owner');
    }
    this.query = query; this.workspaceOnly = workspaceOnly;
    const params = new URLSearchParams({ limit: '30', q: query });
    if (more && this.sessionCursor) params.set('cursor', this.sessionCursor);
    const data = await this.api(`/v1/sessions/quick-index?${params}`);
    const raw = data.sessions || data.items || [];
    const normalized = normalizeAuthoritativeSessionHistoryList(raw);
    this.sessions = more ? [...this.sessions, ...normalized] : normalized;
    if (workspaceOnly && this.view.workspace) this.sessions = this.sessions.filter(s => s.workspacePath === this.view.workspace);
    this.sessionCursor = data.nextCursor || data.pageInfo?.nextCursor || '';
    this.lastIndex = Date.now(); this.changed();
  }
  async attach(sessionId: string) {
    this.generation++; const generation = this.generation;
    this.view.sessionId = sessionId; this.messages = []; this.snapshot = {}; this.seq = 0; this.page = {}; this.syncCursor = '';
    this.save(); this.changed();
    if (!sessionId) return;
    const data = await this.api(`/v1/sessions/${encodeURIComponent(sessionId)}/turns?limit=10`);
    if (generation !== this.generation) return;
    this.messages = data.messages || []; this.page = data.pageInfo || {}; this.syncCursor = data.syncCursor || '';
    await this.refreshSnapshot(generation); this.changed();
  }
  async refreshSnapshot(generation = this.generation) {
    const sessionId = this.view.sessionId;
    if (!sessionId) return;
    const data = await this.api(`/v1/sessions/${encodeURIComponent(sessionId)}/snapshot?compact=1`);
    if (generation !== this.generation) return;
    this.snapshot = data; this.seq = Math.max(this.seq, Number(data.latestSeq || 0)); this.lastSnapshot = Date.now();
    this.reconcile(); this.changed();
  }
  mergeMessages(incoming: any[], deletions: any[] = []) {
    const deleted = new Set(deletions.map(x => typeof x === 'string' ? x : x.messageId || x.message_id || x.id));
    const map = new Map(this.messages.filter(m => !deleted.has(m.id)).map(m => [m.id, m]));
    for (const m of incoming) if (!deleted.has(m.id)) map.set(m.id, m);
    this.messages = [...map.values()].sort((a, b) => Number(a.ordinal ?? a.timestamp ?? 0) - Number(b.ordinal ?? b.timestamp ?? 0));
  }
  reconcile() {
    const outstanding = this.draft.unknown;
    if (!outstanding) return;
    const found = [...this.messages, ...(this.snapshot.queuedMessages || [])].find(m =>
      (m.clientMessageId || m.client_message_id || m.metadata?.clientMessageId) === outstanding.clientMessageId);
    if (found) { this.draft.text = ''; this.draft.attachments = []; delete this.draft.unknown; this.save(); this.notice = '已从 Engine 确认受理，未重复发送。'; }
  }
  async older() {
    if (!this.view.sessionId || !this.page.hasMore && !this.page.hasOlder) return;
    const generation = this.generation;
    const before = this.page.beforeCursor;
    if (!before) return;
    const data = await this.api(`/v1/sessions/${encodeURIComponent(this.view.sessionId)}/turns?limit=10&before=${encodeURIComponent(before)}`);
    if (generation !== this.generation) return;
    this.mergeMessages(data.messages || []); this.page = data.pageInfo || {}; this.changed();
  }
  async tick() {
    const generation = this.generation, sessionId = this.view.sessionId;
    if (!sessionId) {
      if (this.connection !== '已连接') await this.initialize();
      else if (Date.now() - this.lastIndex > 8000) await this.listSessions();
      return;
    }
    const base = `/v1/sessions/${encodeURIComponent(sessionId)}`;
    // Canonical sync is the same durable incremental transcript as Phone/Web.
    // Runtime events only invalidate metadata; we never splice deltas over a
    // canonical snapshot from a different watermark.
    const [timeline, events] = await Promise.all([
      this.api(`${base}/timeline/sync?since=${encodeURIComponent(this.syncCursor)}`),
      this.api(`${base}/runtime-events?after_seq=${this.seq}&limit=128`),
    ]);
    if (generation !== this.generation || this.stopped) return;
    this.mergeMessages(timeline.messages || [], timeline.deletions || []);
    this.syncCursor = timeline.syncCursor || this.syncCursor;
    for (const raw of events.events || []) {
      if (Number(raw.seq) <= this.seq) continue;
      const event = normalizeSessionRuntimeEvent(raw);
      this.seq = Number(raw.seq);
      if (event?.type === 'error') this.notice = '任务报告异常；请查看对话与任务详情。';
    }
    if ((events.events || []).length || Date.now() - this.lastSnapshot > 2500) await this.refreshSnapshot(generation);
    if (generation !== this.generation) return;
    this.reconcile(); this.connection = '已连接'; this.changed();
  }
  async runLoop() {
    let failures = 0;
    while (!this.stopped) {
      try { await this.tick(); failures = 0; }
      catch (e: any) { this.connection = '连接中断 · 自动重连'; this.notice = this.draft.unknown ? '发送结果待确认；重连仅查询，不会重复发送。' : `读取失败：${e.message}`; failures++; this.changed(); }
      await new Promise(resolve => { const timer = setTimeout(resolve, Math.min(8000, 500 * 2 ** failures)); timer.unref(); });
    }
  }
  stop() { this.stopped = true; this.generation++; this.save(true); }
  async ensureSession() {
    if (this.view.sessionId) return this.view.sessionId;
    if (!this.view.workspace) throw new Error('请先在设置中选择并确认工作区。');
    const created = await this.api('/v1/sessions', { method: 'POST', body: { title: this.draft.text.slice(0, 40) || '终端会话', userId: this.owner.sessionIdentifier, workspacePath: this.view.workspace, source: 'v8os_tui', externalSurface: 'cli', clientGroup: 'local_trusted' } });
    const id = idOf(created); if (!id) throw new Error('Engine 未返回会话标识。');
    const draft = this.draft; delete this.view.drafts.new;
    this.view.drafts[id] = draft; await this.attach(id); return id;
  }
  async submit() {
    if (this.busy) return;
    if (this.draft.unknown) throw new Error('前次发送结果待确认；请先对账，不可重复提交。');
    if (!this.draft.text.trim() && !this.draft.attachments.length) return;
    this.busy = true; this.changed();
    try {
      const sessionId = await this.ensureSession(), draft = this.draft;
      const clientMessageId = randomUUID();
      draft.unknown = { clientMessageId, startedAt: new Date().toISOString() };
      this.save(true); // Persist intent before writing, including process death.
      try {
        const attachments = draft.attachments.map(x => ({ ...x, mimeType: x.type, resourceRole: 'source' }));
        const result = await this.api('/v1/chat/submit', { method: 'POST', timeoutMs: 20000, body: {
          session_id: sessionId, conversationId: sessionId, clientMessageId,
          messages: [{ id: clientMessageId, role: 'user', content: draft.text }],
          data: { conversationId: sessionId, clientMessageId, attachments, fileUrls: attachments.map(x => x.url), safetyApprovalMode: 'reduced' },
        } });
        if (!result.accepted || !(result.runId || result.run_id)) throw new Error('Engine 未返回可核对的受理结果。');
        draft.text = ''; draft.attachments = []; delete draft.unknown; this.save(true);
        this.notice = result.queued ? '消息已排队；由后台运行按顺序处理。' : '已受理；正在等待 Engine 执行。';
        await this.tick();
      } catch (e: any) {
        if (e.status >= 400 && e.status < 500 && ![408, 429].includes(e.status)) { delete draft.unknown; this.save(true); throw e; }
        this.notice = '发送结果待确认；草稿已保留，不会自动重发。请用菜单“核对发送结果”。';
      }
    } finally { this.busy = false; this.changed(); }
  }
  async interrupt(runId: string) {
    await this.refreshSnapshot();
    if (idOf(this.run) !== runId || !this.active) throw new Error('任务状态已变化，请重新打开详情。');
    await this.api(`/v1/runs/${encodeURIComponent(runId)}/commands/interrupt`, { method: 'POST', body: { reason: 'tui_user_stop' } });
    this.notice = '已请求停止，等待 Engine 确认终态。'; await this.refreshSnapshot();
  }
  async decide(item: any, action: 'approve' | 'reject' | 'answer', answer = '') {
    const identity = idOf(item), sessionId = this.view.sessionId;
    await this.refreshSnapshot();
    if (sessionId !== this.view.sessionId || !this.inbox.some(x => idOf(x) === identity && x.kind === item.kind)) throw new Error('待处理事项已过期或已处理。');
    const route = action === 'answer' ? `/v1/ask-user/${encodeURIComponent(identity)}/respond` : `/v1/approvals/${encodeURIComponent(identity)}/${action}`;
    await this.api(route, { method: 'POST', body: action === 'answer' ? { answer } : {} });
    await this.refreshSnapshot();
    if (this.inbox.some(x => idOf(x) === identity)) this.notice = '决定已送达，等待 Engine 状态更新。'; else this.notice = 'Engine 已确认处理结果。';
  }
  async attachFile(filename: string) {
    const resolved = path.resolve(filename), info = await stat(resolved);
    if (!info.isFile()) throw new Error('附件必须是可读文件。');
    const sessionId = await this.ensureSession(), generation = this.generation;
    const form = new FormData(); form.set('file', await openAsBlob(resolved), path.basename(resolved)); form.set('sessionId', sessionId);
    const result = await engineUpload('/v1/chat/upload', form);
    if (generation !== this.generation) throw new Error('上传期间会话已切换；附件已登记为原会话来源，请在那里查看。');
    this.draft.attachments.push(result); this.save(); this.notice = `已添加来源：${result.name}`; this.changed();
  }
  async trustWorkspace(filename: string) {
    const resolved = path.resolve(filename), info = await stat(resolved);
    if (!info.isDirectory()) throw new Error('工作区必须是已有目录。');
    const result = await this.api('/v1/projects', { method: 'POST', body: { name: path.basename(resolved), workspacePath: resolved, workspaceTrustState: 'trusted', workspaceTrustSource: 'tui_user_confirmed' } });
    if (result.workspaceTrustState !== 'trusted') throw new Error('Engine 未确认工作区信任。');
    this.view.workspace = resolved; this.save(); this.notice = '工作区已登记；新会话将使用此目录。'; this.changed();
  }
}
