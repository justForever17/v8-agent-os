import { engineJson, engineUpload, engineTargetOrigin } from '../../v8-agent-os-cli/src/engine_client.mjs';
import { randomUUID } from 'node:crypto';
import { openAsBlob } from 'node:fs';
import { stat, readFile, writeFile } from 'node:fs/promises';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
const execFileAsync = promisify(execFile);
import path from 'node:path';
import { normalizeSessionRuntimeEvent } from '../../../packages/session-realtime/src/event-normalizer.js';
import { normalizeAuthoritativeSessionHistoryList } from '../../../packages/session-realtime/src/history.js';
import { isActiveRunStatus } from '../../../packages/session-realtime/src/run-status.js';
import { buildSessionOutputProjection } from '../../../packages/session-realtime/src/session-output-projection.js';
import { readTranscriptIdentity, isStaleTranscript, conversationEventDisposition } from '../../../packages/session-realtime/src/conversation-recovery.js';
import { ViewStore, type ViewState, type Draft } from './persistence.js';
import { isSpecApproval, specReviewMatches, validatedSpecReplacement, verifiedSpecDocument, type SpecReviewDocument } from './spec-review.js';

export type Api = (route: string, options?: any) => Promise<any>;
// Interaction and approval projections use different aliases. Prefer their
// own identity fields before session/run fallbacks so a missing `id` can never
// accidentally target the enclosing session when deciding an inbox item.
export const idOf = (item: any) => String(item?.id || item?.interactionId || item?.interaction_id || item?.approvalId || item?.approval_id || item?.sessionId || item?.session_id || item?.runId || item?.run_id || '');
export const pending = (item: any) => ['pending', 'waiting', 'open', 'requested'].includes(String(item?.status || 'pending'));

export interface WorkspaceDiffFile {
  path: string;
  additions: number;
  deletions: number;
  chunks: Array<{
    header: string;
    lines: string[];
  }>;
  rawDiff: string;
}

export interface WorkspaceDiffResult {
  clean: boolean;
  files: WorkspaceDiffFile[];
  totalAdditions: number;
  totalDeletions: number;
  untrackedFiles: string[];
  error?: string;
}

export class Client {
  view: ViewState; messages: any[] = []; snapshot: any = {}; instance: any = {};
  sessions: any[] = []; sessionCursor = ''; page: any = {}; syncCursor = ''; seq = 0;
  sessionWorkspace = '';
  modelReady = false;
  connection = '连接中'; notice = ''; busy = false; revision = 0; generation = 0;
  identity = { transcriptRevision: 0, contextEpoch: 0 };
  private stopped = false; private listeners = new Set<() => void>(); private saveTimer?: NodeJS.Timeout;
  private lastSnapshot = 0; private lastIndex = 0; private query = ''; private workspaceOnly = true;
  private owner: any = {};
  private targetOrigin = '';
  private initialization = 0;
  private defaultWorkspaceAttempted = false;
  private lastIdentityCheck = 0;
  approvalMode: '' | 'manual' | 'reduced' | 'minimal' = '';
  get approvalBadge() { return this.approvalMode || 'engine'; }
  get pendingApproval(): any {
    return (this.inbox || []).find((item: any) => !item.answered && !item.completed && (item.type === 'approval' || item.type === 'tool_approval' || item.action === 'approval'));
  }
  cycleApprovalMode(reverse = false) {
    const modes = ['', 'manual', 'reduced', 'minimal'] as const;
    const index = modes.indexOf(this.approvalMode);
    this.approvalMode = modes[(index + (reverse ? modes.length - 1 : 1)) % modes.length];
    this.notice = `后续消息审批模式：${this.approvalMode || '沿用 Engine'}`;
    this.changed();
  }
  private transportAbort = new AbortController();
  private engineReadyPromise?: Promise<any>;
  setEngineReadyPromise(promise: Promise<any>) {
    this.engineReadyPromise = promise.then(async () => {
      await this.initialize();
    });
  }
  async waitForEngineReady() {
    if (this.engineReadyPromise) {
      await this.engineReadyPromise;
    }
  }
  constructor(readonly store = new ViewStore(), readonly transport: Api = engineJson) { this.view = store.read(); }
  api: Api = async (route, options = {}) => {
    if (options.method && options.method !== 'GET' && !this.instance.instanceId) return Promise.reject(new Error('请先连接并核对 Engine 实例。'));
    const generation = this.generation, instanceId = this.instance.instanceId;
    if (options.method && options.method !== 'GET' && this.targetOrigin) {
      const actual = await this.transport('/v1/client-identity/instance', { expectedOrigin: this.targetOrigin, signal: this.transportAbort.signal });
      if (actual.instanceId !== instanceId || generation !== this.generation) {
        this.connection = '实例已变化 · 重新连接';
        throw Object.assign(new Error('Engine 实例已变化，本次操作未发送。'), { definiteNotSent: true });
      }
    }
    const result = await this.transport(route, { ...options, expectedOrigin: this.targetOrigin, signal: this.transportAbort.signal, headers: {
    ...options.headers,
    ...(this.instance.instanceId ? { 'x-v8-authority-instance-id': this.instance.instanceId } : {}),
    ...(this.owner.sessionIdentifier ? { 'x-v8-agent-os-user-email': this.owner.sessionIdentifier } : {}),
    } });
    if (generation !== this.generation || instanceId !== this.instance.instanceId) throw Object.assign(new Error('视图或实例已变化，已丢弃旧响应。'), { staleView: true });
    return result;
  };
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
  /** The local owner is the only identity required before the TUI can create a session. */
  // initialize() stores the Engine response's `user` object; the top-level
  // `initialized` flag is intentionally not copied into that object.
  get ownerReady() { return Boolean(this.owner?.sessionIdentifier); }
  get ownerName() { return String(this.owner?.name || this.owner?.login || '本机 owner'); }
  get run() { return this.snapshot.currentRun || {}; }
  get workspace() { return (this.view.sessionId && this.sessionWorkspace) ? this.sessionWorkspace : this.view.workspace; }
  get active() { return isActiveRunStatus(this.run.status || this.snapshot.runtimeStatus); }
  get inbox() {
    return [...(this.snapshot.approvals || []).map((x: any) => ({ ...x, kind: 'approval' })),
      ...(this.snapshot.askUserInteractions || []).map((x: any) => ({ ...x, kind: 'question' }))].filter(pending);
  }
  get outputs() { return buildSessionOutputProjection(this.messages, this.snapshot.snapshot?.artifacts || [], { sessionId: this.view.sessionId }); }
  async listInbox() {
    const approvals = await this.api('/v1/approvals?status=pending');
    const items = (approvals.approvals || []).map((item: any) => ({ ...item,
      id: item.id || item.approvalId || item.approval_id,
      kind: 'approval', sessionId: item.sessionId || item.session_id,
    }));
    const sessions = this.sessions.length ? this.sessions : (await this.listSessions(), this.sessions);
    for (let i = 0; i < sessions.length; i += 3) {
      const results = await Promise.all(sessions.slice(i, i + 3).map(async session => {
        const snapshot = idOf(session) === this.view.sessionId ? this.snapshot : await this.api(`/v1/sessions/${encodeURIComponent(idOf(session))}/snapshot?compact=1`);
        return (snapshot.askUserInteractions || []).filter(pending).map((item: any) => ({ ...item,
          id: item.id || item.interactionId || item.interaction_id,
          kind: 'question', sessionId: item.sessionId || item.session_id || idOf(session), sessionTitle: session.title,
        }));
      }));
      items.push(...results.flat());
    }
    return items.filter(pending).sort((a: any, b: any) => Number(b.sessionId === this.view.sessionId) - Number(a.sessionId === this.view.sessionId));
  }
  async initialize() {
    const initialization = ++this.initialization;
    try {
      const origin = engineTargetOrigin();
      const instance = await this.transport('/v1/client-identity/instance', { expectedOrigin: origin, signal: this.transportAbort.signal });
      if (initialization !== this.initialization || this.stopped) return;
      if (!instance.instanceId) throw new Error('Engine 实例标识缺失。');
      if (this.view.instanceId !== instance.instanceId) {
        clearTimeout(this.saveTimer);
        this.store.write(this.view);
        this.transportAbort.abort(); this.transportAbort = new AbortController();
        this.generation++; this.messages = []; this.snapshot = {}; this.sessions = []; this.sessionCursor = ''; this.syncCursor = ''; this.seq = 0; this.sessionWorkspace = '';
        this.owner = {}; this.page = {}; this.approvalMode = ''; this.query = ''; this.workspaceOnly = false;
        this.identity = { transcriptRevision: 0, contextEpoch: 0 }; this.modelReady = false;
        this.view = this.store.bind(instance.instanceId);
      }
      this.instance = instance;
      this.targetOrigin = origin;
      this.lastIdentityCheck = Date.now();
      const owner = await this.api('/v1/client-identity/owner');
      this.owner = owner.user || {};
      if (!owner.initialized) {
        this.connection = '已连接'; this.notice = '首次配置：按 F3 或输入 /setup 初始化本机 owner、连接模型并选择工作区。';
        this.changed(); return;
      }
      this.connection = '已连接';
      // A TUI launched from a directory has a deterministic workspace scope.
      // Registration is idempotent on Engine; failures leave setup available
      // and never fabricate a trusted workspace locally.
      if (!this.view.workspace) {
        const cwd = path.resolve(process.cwd());
        if (!this.defaultWorkspaceAttempted) {
          this.defaultWorkspaceAttempted = true;
          try {
            const registered = await this.api('/v1/projects', { method: 'POST', body: { name: path.basename(cwd) || cwd, workspacePath: cwd, workspaceTrustState: 'restricted', workspaceTrustSource: 'tui_cwd_discovery' } });
            if (registered.workspaceTrustState === 'trusted' || registered.workspaceTrustState === 'restricted') {
              this.view.workspace = cwd;
              if (registered.workspaceTrustState !== 'trusted') this.notice = '当前目录已绑定但尚未信任，请在设置中确认工作区。';
            }
          } catch { this.notice = '当前目录待 Engine 确认；可在设置中选择工作区。'; }
        }
        this.save();
      }
      void this.refreshModelReadiness();
      await this.listSessions('', false, true);
      if (this.view.sessionId) await this.attach(this.view.sessionId);
      else this.notice = '新建对话 · F3 配置模型与工作区 · Ctrl+P 查看操作 · /resume 恢复历史会话';
    } catch (e: any) { if (e.staleView || initialization !== this.initialization) return; this.connection = '未连接'; this.notice = 'Engine 未连接。运行 v8os start 启动；首次安装见 F1 帮助。' + e.message; }
    this.changed();
  }
  async refreshModelReadiness() {
    if (!this.ownerReady || !this.instance.instanceId) return;
    const controlPlane = await this.api('/v1/models/control-plane').catch(() => null);
    if (!controlPlane) return;
    const models = Array.isArray(controlPlane.models) ? controlPlane.models : [];
    this.modelReady = models.some((model: any) => model?.eligibility?.eligible !== false && (model?.modelId || model?.model_id || model?.modelRef));
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
    if (sessionId !== this.view.sessionId) this.approvalMode = '';
    const isSameSession = Boolean(sessionId && sessionId === this.view.sessionId);
    this.view.sessionId = sessionId;
    if (!isSameSession) {
      this.messages = []; this.snapshot = {}; this.seq = 0; this.page = {}; this.syncCursor = '';
      this.sessionWorkspace = '';
    }
    this.notice = sessionId ? '正在加载会话…' : '新建对话 · F3 配置模型与工作区 · Ctrl+P 查看操作';
    if (!isSameSession) this.identity = { transcriptRevision: 0, contextEpoch: 0 };
    this.save(); this.changed();
    if (!sessionId) return;
    const [data, scope] = await Promise.all([
      this.api(`/v1/sessions/${encodeURIComponent(sessionId)}/turns?limit=10`),
      this.api(`/v1/sessions/${encodeURIComponent(sessionId)}/scope`),
    ]);
    if (generation !== this.generation) return;
    this.messages = data.messages || []; this.page = data.pageInfo || {}; this.syncCursor = data.syncCursor || '';
    this.sessionWorkspace = String(scope.binding?.workspace_path || scope.binding?.workspacePath || '');
    this.identity = readTranscriptIdentity(data);
    await this.refreshSnapshot(generation);
    if (generation !== this.generation) return;
    if (this.notice === '正在加载会话…') this.notice = this.draft.unknown ? '已恢复会话；前次发送结果待确认，未自动重发。' : '已恢复会话 · 草稿可继续编辑';
    this.changed();
  }
  async refreshSnapshot(generation = this.generation) {
    const sessionId = this.view.sessionId;
    if (!sessionId) return;
    const data = await this.api(`/v1/sessions/${encodeURIComponent(sessionId)}/snapshot?compact=1`);
    if (generation !== this.generation) return;
    const incoming = readTranscriptIdentity(data);
    if (isStaleTranscript(this.identity, incoming)) return;
    if (incoming.contextEpoch > this.identity.contextEpoch || incoming.transcriptRevision > this.identity.transcriptRevision) {
      await this.attach(sessionId); return;
    }
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
    if (found) { this.draft.text = this.draft.nextText || ''; delete this.draft.nextText; this.draft.attachments = []; delete this.draft.unknown; this.save(); this.notice = '已从 Engine 确认受理，未重复发送。'; }
  }
  async older() {
    if (!this.view.sessionId || !this.page.hasMore && !this.page.hasOlder) return;
    const generation = this.generation;
    const before = this.page.beforeCursor;
    if (!before) return;
    const data = await this.api(`/v1/sessions/${encodeURIComponent(this.view.sessionId)}/turns?limit=10&before=${encodeURIComponent(before)}`);
    if (generation !== this.generation) return;
    if (isStaleTranscript(this.identity, readTranscriptIdentity(data))) return;
    this.mergeMessages(data.messages || []); this.page = data.pageInfo || {}; this.changed();
  }
  async tick() {
    if (this.connection !== '已连接' && !this.stopped) { await this.initialize(); if (this.connection !== '已连接') return; }
    if (this.targetOrigin && Date.now() - this.lastIdentityCheck > 2000) {
      const actual = await this.transport('/v1/client-identity/instance', { expectedOrigin: this.targetOrigin, signal: this.transportAbort.signal });
      this.lastIdentityCheck = Date.now();
      if (actual.instanceId !== this.instance.instanceId) { await this.initialize(); return; }
    }
    const generation = this.generation, sessionId = this.view.sessionId;
    if (!sessionId) {
      if (this.connection !== '已连接') await this.initialize();
      // An uninitialized Engine is a valid first-run state. Do not turn the
      // reconnect loop into a stream of failed quick-index requests while the
      // welcome/setup surface is waiting for the user.
      else if (!this.ownerReady) return;
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
    const incoming = readTranscriptIdentity(timeline);
    if (isStaleTranscript(this.identity, incoming)) return;
    if (incoming.contextEpoch > this.identity.contextEpoch || incoming.transcriptRevision > this.identity.transcriptRevision) {
      await this.attach(sessionId); return;
    }
    this.mergeMessages(timeline.messages || [], timeline.deletions || []);
    this.syncCursor = timeline.syncCursor || this.syncCursor;
    for (const raw of events.events || []) {
      if (Number(raw.seq) <= this.seq) continue;
      const event = normalizeSessionRuntimeEvent(raw);
      this.seq = Number(raw.seq);
      if (conversationEventDisposition(this.identity, raw) !== 'apply') continue;
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
      catch (e: any) { if (this.stopped) break; if (!e.staleView) { this.connection = '连接中断 · 自动重连'; this.notice = this.draft.unknown ? '发送结果待确认；重连仅查询，不会重复发送。' : `读取失败：${e.message}`; failures++; this.changed(); } }
      await new Promise(resolve => { const timer = setTimeout(resolve, Math.min(8000, 500 * 2 ** failures)); timer.unref(); });
    }
  }
  stop() { this.stopped = true; this.generation++; this.transportAbort.abort(); this.save(true); }
  async ensureSession() {
    if (this.connection !== '已连接' && this.engineReadyPromise) await this.waitForEngineReady();
    if (this.view.sessionId) return this.view.sessionId;
    if (!this.view.workspace) throw new Error('请先在设置中选择并确认工作区。');
    const approvalMode = this.approvalMode;
    const created = await this.api('/v1/sessions', { method: 'POST', body: { title: this.draft.text.slice(0, 40) || '终端会话', userId: this.owner.sessionIdentifier, workspacePath: this.view.workspace, source: 'v8os_tui', externalSurface: 'cli', clientGroup: 'local_trusted' } });
    const id = idOf(created); if (!id) throw new Error('Engine 未返回会话标识。');
    const draft = this.draft; delete this.view.drafts.new;
    this.view.drafts[id] = draft; await this.attach(id); this.approvalMode = approvalMode; return id;
  }
  async submit(extraData: Record<string, unknown> = {}) {
    if (this.busy) return;
    if (this.draft.unknown) throw new Error('前次发送结果待确认；请先对账，不可重复提交。');
    if (!this.draft.text.trim() && !this.draft.attachments.length) return;
    if (this.connection !== '已连接' && this.engineReadyPromise) {
      this.notice = 'Engine 正在准备中，将在连接就绪后自动发送…';
      this.changed();
      try {
        await this.waitForEngineReady();
      } catch (err: any) {
        throw new Error(`Engine 尚未就绪：${err?.message || err}`);
      }
    }
    this.busy = true; this.changed();
    try {
      const sessionId = await this.ensureSession(), draft = this.draft;
      const view = this.view;
      const clientMessageId = randomUUID();
      draft.unknown = { clientMessageId, startedAt: new Date().toISOString() };
      this.save(true); // Persist intent before writing, including process death.
      try {
        const attachments = draft.attachments.map(x => ({ ...x, mimeType: x.type, resourceRole: 'source' }));
        const result = await this.api('/v1/chat/submit', { method: 'POST', timeoutMs: 20000, body: {
          session_id: sessionId, conversationId: sessionId, clientMessageId,
          messages: [{ id: clientMessageId, role: 'user', content: draft.text }],
          data: { conversationId: sessionId, clientMessageId, attachments, fileUrls: attachments.map(x => x.url), ...(this.approvalMode ? { safetyApprovalMode: this.approvalMode } : {}), ...extraData },
        } });
        if (!result.accepted || !(result.runId || result.run_id)) throw new Error('Engine 未返回可核对的受理结果。');
        draft.text = draft.nextText || ''; delete draft.nextText; draft.attachments = []; delete draft.unknown; this.save(true);
        this.notice = result.queued ? '消息已排队；由后台运行按顺序处理。' : '已受理；正在等待 Engine 执行。';
        await this.tick();
      } catch (e: any) {
        if (e.staleView || this.view !== view) return;
        if (e.definiteNotSent || e.status >= 400 && e.status < 500 && ![408, 429].includes(e.status)) { delete draft.unknown; this.save(true); throw e; }
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
  get retryControl() {
    const controls = this.snapshot.controls || {}, recovery = this.snapshot.recoveryClass || {};
    const runId = String(controls.runId || idOf(this.run));
    const advertised = typeof controls.canRetry === 'boolean' ? controls.canRetry : recovery.canRetry === true;
    const prior = this.view.retryRequests[runId];
    const reason = !runId ? '当前会话没有可定位的运行。'
      : prior ? prior.nextRunId ? `该运行已请求重试，新运行：${prior.nextRunId}。` : '前次重试结果待确认，请回读运行记录；不会自动重发。'
      : !advertised ? `当前状态 ${this.run.status || this.snapshot.runtimeStatus || '未知'} 未提供重试操作。${recovery.reason || 'Engine 未声明可恢复的重试入口。'}` : '';
    return { runId, allowed: Boolean(runId && advertised && !prior), reason };
  }
  async retryRun(runId: string) {
    await this.refreshSnapshot();
    const control = this.retryControl;
    if (control.runId !== runId || !control.allowed) throw new Error(control.reason || '任务已变化，请重新打开详情。');
    const view = this.view;
    view.retryRequests[runId] = { requestedAt: new Date().toISOString() }; this.save(true);
    try {
      const result = await this.api(`/v1/runs/${encodeURIComponent(runId)}/commands/retry`, { method: 'POST', body: { reason: 'tui_user_retry' } });
      const nextRunId = String(result.next_run_id || result.nextRunId || '');
      if (nextRunId) view.retryRequests[runId].nextRunId = nextRunId;
      this.save(true);
      this.notice = nextRunId ? `Engine 已调度重试 ${nextRunId}，等待运行状态。` : '重试请求已送达，调度结果待确认。';
      await this.refreshSnapshot();
    } catch (error: any) {
      if (error.staleView || this.view !== view) return;
      if (error.definiteNotSent || error.status >= 400 && error.status < 500 && ![408, 429].includes(error.status)) { delete view.retryRequests[runId]; this.save(true); throw error; }
      this.notice = '重试结果待确认；请查看任务详情，不会自动重发。';
    }
    this.changed();
  }
  async refreshSpecApproval(item: any, document: SpecReviewDocument) {
    await this.refreshSnapshot();
    const current = this.inbox.find(x => idOf(x) === idOf(item) && x.kind === 'approval');
    if (!current || !isSpecApproval(current)) throw new Error('Spec 审批已变化，请重新打开待处理事项。');
    const verified = verifiedSpecDocument(document);
    const result = await this.api(`/v1/approvals/${encodeURIComponent(idOf(current))}/refresh-spec-review`, {
      method: 'POST', body: { response: { documentSha256: verified.documentSha256 } },
    });
    const next = validatedSpecReplacement(result, current, verified);
    await this.refreshSnapshot();
    return next;
  }
  async decide(item: any, action: 'approve' | 'reject' | 'answer', answer = '', review?: SpecReviewDocument) {
    const identity = idOf(item), sessionId = this.view.sessionId;
    if (!identity) throw new Error('待处理事项缺少 Engine 交互标识，未发送决定。');
    if (action === 'answer' && !answer.trim()) throw new Error('回答不能为空，未发送决定。');
    await this.refreshSnapshot();
    if (sessionId !== this.view.sessionId || String(item.sessionId || item.session_id || sessionId) !== String(this.view.sessionId || sessionId) || !this.inbox.some(x => idOf(x) === identity && x.kind === item.kind && pending(x))) throw new Error('待处理事项已过期或已处理；已重新读取当前会话，请重新打开待处理列表。');
    const current = this.inbox.find(x => idOf(x) === identity && x.kind === item.kind);
    const spec = action === 'approve' && isSpecApproval(current);
    if (spec && (!review || !specReviewMatches(current, verifiedSpecDocument(review)))) throw new Error('请先读取完整 Spec 文档并确认当前审批版本；文档变化时需刷新审批。');
    const route = action === 'answer' ? `/v1/ask-user/${encodeURIComponent(identity)}/respond` : `/v1/approvals/${encodeURIComponent(identity)}/${action}`;
    // RunCommandPayload owns response/payload. A top-level `answer` is ignored
    // by the Engine parser and would resume the run with an empty answer.
    const result = await this.api(route, { method: 'POST', body: action === 'answer' ? { response: { answer } } : spec ? { response: { documentSha256: review!.documentSha256 } } : {} });
    if (spec && (result.spec_stage_approval?.ok !== true || result.approval?.status !== 'approved')) throw new Error('Engine 尚未确认 Spec 审批成功，请刷新文档与待处理状态。');
    await this.refreshSnapshot();
    if (this.inbox.some(x => idOf(x) === identity)) this.notice = '决定已送达，等待 Engine 状态更新。'; else this.notice = 'Engine 已确认处理结果。';
  }
  async attachFile(filename: string) {
    const resolved = path.resolve(filename), info = await stat(resolved);
    if (!info.isFile()) throw new Error('附件必须是可读文件。');
    const sessionId = await this.ensureSession(), generation = this.generation;
    const form = new FormData(); form.set('file', await openAsBlob(resolved), path.basename(resolved)); form.set('sessionId', sessionId);
    const result = await engineUpload('/v1/chat/upload', form, { signal: this.transportAbort.signal, expectedOrigin: this.targetOrigin });
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
  async branch(title?: string): Promise<{ sessionId: string; title: string }> {
    const currentSessionId = this.view.sessionId;
    if (!currentSessionId) throw new Error('当前处于新会话，尚未产生历史记录，无需分叉。');
    const payload: Record<string, any> = {
      expectedTranscriptRevision: this.identity.transcriptRevision || undefined,
    };
    const res = await this.api(`/v1/sessions/${encodeURIComponent(currentSessionId)}/branches`, {
      method: 'POST',
      body: payload,
    });
    const newSessionId = String(res.sessionId || res.id || '');
    if (!newSessionId) throw new Error('Engine 未能返回新分支会话标识。');
    const branchTitle: string = String(title?.trim() || res.title || '分支会话');
    if (title?.trim()) {
      await this.api(`/v1/sessions/${encodeURIComponent(newSessionId)}`, {
        method: 'PATCH',
        body: { title: branchTitle },
      }).catch(() => {});
    }
    await this.listSessions();
    await this.attach(newSessionId);
    this.notice = `已分叉为新分支会话：${branchTitle}`;
    this.changed();
    return { sessionId: newSessionId, title: branchTitle };
  }
  async compressContext(): Promise<{ window: number; currentTokens: number; summary: string }> {
    const sessionId = this.view.sessionId;
    if (!sessionId) throw new Error('需要先进入一个会话才能查看或压缩上下文。');
    const current = await this.api('/v1/config-registry/context').catch(() => null);
    const policy = current?.data?.policy?.compression || {};
    const window = policy.default_context_window_tokens || 32000;
    await this.refreshSnapshot();
    const timeline = this.snapshot?.runtimeTimeline || {};
    const currentTokens = Number(timeline.contextTokens || timeline.totalTokens || 0);
    return {
      window,
      currentTokens,
      summary: `窗口上限：${window} Token · 当前占用：${currentTokens || '未知'} Token · 软压缩阈值：${policy.soft_trigger_ratio ?? 0.9} · 保留轮数：${policy.keep_recent_turns || 4}`,
    };
  }

  async getWorkspaceDiff(): Promise<WorkspaceDiffResult> {
    const cwd = this.workspace || process.cwd();
    try {
      const { stdout: statusOut } = await execFileAsync('git', ['status', '--porcelain'], { cwd }).catch(() => ({ stdout: '' }));
      const untrackedFiles: string[] = [];
      for (const line of (statusOut || '').split(/\r?\n/)) {
        const trimmed = line.trim();
        if (trimmed.startsWith('?? ')) {
          untrackedFiles.push(trimmed.slice(3).trim());
        }
      }

      let rawDiff = '';
      try {
        const diffProc = await execFileAsync('git', ['diff', 'HEAD', '-U3'], { cwd });
        rawDiff = diffProc.stdout || '';
      } catch {
        try {
          const diffProc = await execFileAsync('git', ['diff', '-U3'], { cwd });
          rawDiff = diffProc.stdout || '';
        } catch (err: any) {
          return {
            clean: untrackedFiles.length === 0,
            files: [],
            totalAdditions: 0,
            totalDeletions: 0,
            untrackedFiles,
            error: `Git diff 执行失败：${err?.message || err}`,
          };
        }
      }

      if (!rawDiff.trim() && untrackedFiles.length === 0) {
        return { clean: true, files: [], totalAdditions: 0, totalDeletions: 0, untrackedFiles };
      }

      const files: WorkspaceDiffFile[] = [];
      const fileBlocks = rawDiff.split(/^diff --git /m).filter(Boolean);
      let totalAdditions = 0;
      let totalDeletions = 0;

      for (const block of fileBlocks) {
        const lines = block.split(/\r?\n/);
        const headerLine = lines[0] || '';
        const match = headerLine.match(/a\/(.+?)\s+b\/(.+)$/);
        const filePath = match ? match[2].trim() : headerLine.trim();

        let additions = 0;
        let deletions = 0;
        const chunks: Array<{ header: string; lines: string[] }> = [];
        let currentChunk: { header: string; lines: string[] } | null = null;

        for (let i = 1; i < lines.length; i++) {
          const line = lines[i];
          if (line.startsWith('@@')) {
            currentChunk = { header: line, lines: [] };
            chunks.push(currentChunk);
          } else if (currentChunk) {
            currentChunk.lines.push(line);
            if (line.startsWith('+') && !line.startsWith('+++')) {
              additions++;
              totalAdditions++;
            } else if (line.startsWith('-') && !line.startsWith('---')) {
              deletions++;
              totalDeletions++;
            }
          }
        }

        files.push({
          path: filePath,
          additions,
          deletions,
          chunks,
          rawDiff: 'diff --git ' + block,
        });
      }

      return {
        clean: files.length === 0 && untrackedFiles.length === 0,
        files,
        totalAdditions,
        totalDeletions,
        untrackedFiles,
      };
    } catch (err: any) {
      return {
        clean: true,
        files: [],
        totalAdditions: 0,
        totalDeletions: 0,
        untrackedFiles: [],
        error: `无法获取工作区修改：${err?.message || err}`,
      };
    }
  }

  async rewindLastTurn(): Promise<{ success: boolean; text: string; messageId?: string }> {
    const sessionId = this.view.sessionId;
    if (!sessionId) throw new Error('当前为新会话，尚未产生可回退的对话轮次。');
    const userMessages = this.messages.filter(m => m.role === 'user');
    if (userMessages.length === 0) {
      throw new Error('当前会话暂无用户消息可回滚。');
    }
    const lastUserMsg = userMessages[userMessages.length - 1];
    let userText = '';
    if (typeof lastUserMsg.content === 'string') {
      userText = lastUserMsg.content;
    } else if (Array.isArray(lastUserMsg.content)) {
      userText = lastUserMsg.content.map((p: any) => typeof p === 'string' ? p : p?.text || '').join('');
    } else if (lastUserMsg.content && typeof lastUserMsg.content === 'object') {
      userText = (lastUserMsg.content as any).text || '';
    } else {
      userText = String(lastUserMsg.content || '');
    }

    await this.api(`/v1/sessions/${encodeURIComponent(sessionId)}/messages/${encodeURIComponent(lastUserMsg.id)}`, {
      method: 'PATCH',
      body: {
        content: userText,
        expectedMessageVersion: lastUserMsg.version ?? 1,
        expectedTranscriptRevision: this.identity.transcriptRevision,
        tailPolicy: 'truncate',
      },
    });

    this.setDraft(userText);
    await this.refreshSnapshot();
    await this.attach(sessionId);
    this.notice = '已回退上一轮对话，原问题已恢复至输入框草稿。';
    this.changed();
    return { success: true, text: userText, messageId: lastUserMsg.id };
  }

  async listRevisions(messageId?: string): Promise<any[]> {
    const sessionId = this.view.sessionId;
    if (!sessionId) return [];
    const targetId = messageId || this.messages.filter(m => m.role === 'user').pop()?.id;
    if (!targetId) return [];
    const data = await this.api(`/v1/sessions/${encodeURIComponent(sessionId)}/messages/${encodeURIComponent(targetId)}/revisions`).catch(() => ({ revisions: [] }));
    return data.revisions || [];
  }

  async restoreRevision(messageId: string, revisionId: string): Promise<any> {
    const sessionId = this.view.sessionId;
    if (!sessionId) throw new Error('会话不存在');
    const res = await this.api(`/v1/sessions/${encodeURIComponent(sessionId)}/messages/${encodeURIComponent(messageId)}/revisions/${encodeURIComponent(revisionId)}/restore`, {
      method: 'POST',
      body: {
        expectedMessageVersion: 1,
        expectedTranscriptRevision: this.identity.transcriptRevision,
        userId: this.owner.sessionIdentifier,
      },
    });
    await this.refreshSnapshot();
    await this.attach(sessionId);
    this.notice = '已从历史快照版本成功恢复。';
    this.changed();
    return res;
  }

  async initProjectContract(): Promise<{ path: string; created: boolean; summary: string }> {
    const cwd = this.workspace || process.cwd();
    const targetFile = path.join(cwd, 'AGENTS.md');
    let alreadyExists = false;
    try {
      const s = await stat(targetFile);
      alreadyExists = s.isFile();
    } catch {}

    const detectedStacks: string[] = [];
    let testCommand = 'npm test';
    let buildCommand = 'npm run build';
    let projectName = path.basename(cwd) || 'Workspace';

    try {
      const pkgRaw = await readFile(path.join(cwd, 'package.json'), 'utf-8');
      const pkg = JSON.parse(pkgRaw);
      if (pkg.name) projectName = pkg.name;
      const allDeps = { ...pkg.dependencies, ...pkg.devDependencies };
      if (allDeps.next) detectedStacks.push('Next.js');
      else if (allDeps.react) detectedStacks.push('React');
      if (allDeps.vue) detectedStacks.push('Vue');
      if (allDeps.typescript) detectedStacks.push('TypeScript');
      if (allDeps.electron) detectedStacks.push('Electron');
      if (allDeps.tailwindcss) detectedStacks.push('Tailwind CSS');
      detectedStacks.push('Node.js');

      if (pkg.scripts?.test) testCommand = 'npm test';
      if (pkg.scripts?.build) buildCommand = 'npm run build';
    } catch {}

    try {
      await stat(path.join(cwd, 'Cargo.toml'));
      detectedStacks.push('Rust / Cargo');
      testCommand = 'cargo test';
      buildCommand = 'cargo build';
    } catch {}

    try {
      await stat(path.join(cwd, 'pyproject.toml'));
      detectedStacks.push('Python (pyproject)');
      testCommand = 'pytest';
    } catch {
      try {
        await stat(path.join(cwd, 'requirements.txt'));
        detectedStacks.push('Python (requirements.txt)');
        testCommand = 'pytest';
      } catch {}
    }

    try {
      await stat(path.join(cwd, 'go.mod'));
      detectedStacks.push('Go');
      testCommand = 'go test ./...';
      buildCommand = 'go build';
    } catch {}

    const stackDesc = detectedStacks.length ? detectedStacks.join(', ') : '通用工程工作区';

    const content = `# ${projectName} - V8OS 工程协作规范

> 本文件由 V8OS /init 命令智能生成，定义工作区协作规范与运行时契约。

## 技术栈与工程事实
- 技术栈：${stackDesc}
- 构建命令：\`${buildCommand}\`
- 测试入口：\`${testCommand}\`

## 核心协作协议 (Supervisor First, Runtime Grounded)
1. **主代理职责**：Supervisor 负责理解意图、编排调度与把关，直接执行确定性操作；不依赖二次规划层。
2. **外科手术式修改 (Surgical Modifications)**：
   - 仅修改与当前任务直接相关的文件和行；
   - 匹配项目现有代码风格，不擅自对无关代码进行批量格式化；
   - 每一个 diff 必须可追溯到用户需求或必要的验收。
3. **安全与凭据保护**：
   - 绝对禁止在代码中硬编码或提交真实 API Key、Token 或敏感凭据；
   - 保护用户本地配置与他人改动，禁止无授权执行破坏性 git reset 或 checkout。
4. **验证与回归**：
   - 完成修改后，必须运行最小必要验证（\`${testCommand}\`）；
   - 交付清单必须包含：变更内容、验证结果和剩余风险。
`;

    if (alreadyExists) {
      const backupFile = path.join(cwd, 'AGENTS.md.bak');
      const existing = await readFile(targetFile, 'utf-8');
      await writeFile(backupFile, existing, 'utf-8');
    }
    await writeFile(targetFile, content, 'utf-8');

    this.notice = alreadyExists ? '已备份并重新生成 AGENTS.md 规范契约。' : '已成功初始化 AGENTS.md 规范契约。';
    this.changed();

    return {
      path: targetFile,
      created: !alreadyExists,
      summary: `项目：${projectName} · 技术栈：${stackDesc} · 规范已就绪`,
    };
  }

}
