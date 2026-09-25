import { Client, idOf } from './client.js';
import { editor, edit, graphemes, safeText, type Editor, type Input } from './terminal.js';
import type { EditedDraft } from './external-editor.js';
import { featurePacks, plugins } from './extension-pages.js';
import { createPeerInvitation, consumePeerInvitation } from './peer-pages.js';
import { AsyncLocalStorage } from 'node:async_hooks';
import { visibleCommandMatches, type CommandEntry } from './command-suggestions.js';
import { type Locale } from './locale.js';
import { parseAtReferences, workspaceReferencePath } from './mentions.js';
import type { MentionCandidate, MentionGroup } from './mentions.js';
import { mentionReplacement, mentionSuggestionRows, mentionTokenAt, moveMentionSelection, selectedMention, switchMentionGroup, type MentionSuggestionState } from './mention-suggestions.js';
import { themeNames, type ThemeName } from './theme.js';
import { welcomeArtProjection, welcomeTextRow, type WelcomeArtProjection, type WelcomeArtRow } from './welcome-art.js';
import { readdir, stat } from 'node:fs/promises';
import path from 'node:path';
import { buildQuestionAnswer, createQuestionDraft, normalizeQuestions, optionDetail, optionKey, optionLabel, questionAnswered, questionDetail, questionKey, questionTitle, requestSummary, type QuestionDraft } from './inbox.js';
import { isSpecApproval, readSpecReview, specReviewMatches, type SpecReviewDocument } from './spec-review.js';

export type Action = CommandEntry & { run: () => void | Promise<void>; navigation?: boolean };
export type Field = { key: string; label: string; value: string; secret?: boolean };
export type Page = { title: string; lines: string[]; actions: Action[]; selected: number; offset: number; fields?: Field[]; fieldIndex?: number; onSave?: (fields: Record<string, string>) => Promise<void>; sensitive?: boolean; localizeLines?: boolean; tabs?: string[]; activeTab?: number; onTabChange?: (tabIndex: number) => void | Promise<void> };
const listOf = (data: any): any[] => Array.isArray(data) ? data : data.items || data.devices || data.peers || data.links || data.packs || data.models || [];
export const secretField = (key: string) => /(?:apikey|accesstoken|refreshtoken|idtoken|bearertoken|authtoken|sessiontoken|apitoken|csrftoken|pairingcode|privatekey|signingkey|secret|password)$|^(?:token|authorization|cookie|credentials?)$/i.test(key.replace(/[-_]/g, ''));
export function containsSecretField(value: unknown): boolean {
  if (!value || typeof value !== 'object') return false;
  return Object.entries(value).some(([key, item]) => secretField(key) || containsSecretField(item));
}
// Human summaries never dump the raw response or credential-bearing fields.
export function describe(value: any, prefix = ''): string[] {
  if (value == null) return [];
  if (typeof value !== 'object') return [`${prefix}${safeText(value)}`];
  return Object.entries(value).flatMap(([key, val]) => {
    if (secretField(key) || /^(raw|trace|internal)$/i.test(key)) return [];
    if (Array.isArray(val)) return [`${prefix}${key}：${val.length} 项`, ...val.flatMap((x, i) => describe(x, `${prefix}  ${i + 1}. `))];
    if (val && typeof val === 'object') return [`${prefix}${key}`, ...describe(val, prefix + '  ')];
    return [`${prefix}${key}：${safeText(val)}`];
  });
}
export function approvalLines(item: any) {
  const request = item.request || item.payload || {};
  return [`来源：${item.agentName || item.agent_name || request.agentName || 'Engine 审批记录'}`,
    `状态：${item.status || 'pending'}`, `会话：${item.session_id || item.sessionId || '当前会话'}`,
    ...describe(request), ...describe(item.risk || {}), item.expiresAt ? `有效期：${item.expiresAt}` : '有效性将在执行前重新核对'];
}
export function approvalTransparent(item: any) {
  const request = item.request || item.payload || {};
  return Object.keys(request).length > 0 && Boolean(request.target || request.path || request.command || request.action || request.tool_name || request.toolName || request.operation || request.actionRequest);
}
function schemaFields(root: any, schema = root, prefix = ''): { key: string; definition: any }[] {
  const resolved = schema.$ref ? root.$defs?.[schema.$ref.split('/').at(-1)] || schema : schema;
  if (resolved.properties) return Object.entries(resolved.properties).flatMap(([key, value]) => schemaFields(root, value, prefix ? `${prefix}.${key}` : key));
  if (resolved.readOnly || prefix.split('.').some(secretField) || /(?:^|\.)peerId$/.test(prefix)) return [];
  return [{ key: prefix, definition: resolved }];
}
function fieldPatch(key: string, value: unknown) {
  const settings: any = {}, parts = key.split('.');
  if (parts.some(x => !x || ['__proto__', 'constructor', 'prototype'].includes(x))) throw new Error('无效字段');
  let item = settings; for (const part of parts.slice(0, -1)) item = item[part] = {}; item[parts.at(-1)!] = value; return settings;
}
function numericInput(value: string, label: string) {
  if (!value.trim()) throw new Error(`${label}不能为空，请输入明确数值`);
  const number = Number(value);
  if (!Number.isFinite(number)) throw new Error(`${label}必须是有限数字`);
  return number;
}
function booleanInput(value: string, label: string) {
  const normalized = value.trim().toLowerCase();
  if (['true', '1', 'yes', 'on', '启用', '是'].includes(normalized)) return true;
  if (['false', '0', 'no', 'off', '禁用', '否'].includes(normalized)) return false;
  throw new Error(`${label}请输入 true / false`);
}
function modelRefOf(model: any) {
  return String(model?.modelRef || `${model?.providerId || ''}:${model?.modelId || ''}`).replace(/^:/, '') || '未命名模型';
}
function contextFacts(value: unknown, prefix = '', out: string[] = [], depth = 0): string[] {
  if (depth > 4 || value == null || typeof value !== 'object') return out;
  const allowed = /(?:context|token|compaction|compression|prompt|summary|baseline|latency|saved|window|usage|threshold)/i;
  for (const [key, item] of Object.entries(value as Record<string, unknown>)) {
    const path = prefix ? `${prefix}.${key}` : key;
    if (!allowed.test(path)) continue;
    if (item && typeof item === 'object') contextFacts(item, path, out, depth + 1);
    else if (typeof item === 'number' || typeof item === 'boolean' || typeof item === 'string') {
      const text = String(item);
      if (text.length <= 160) out.push(`${path}：${text}`);
    }
  }
  return out;
}
export class Surface {
  page: Page | null = null; input: Editor; multiline = false; undo = ''; anchor = 0; following = true; unread = 0;
  scrollDelta = 0; editorWidth = 77;
  busy = false; paletteQuery = ''; formEditor = editor(); private pageSerial = 0; private ticketId = '';
  suggestions: { query: Editor; selected: number; sessionId: string } | null = null;
  mentionSuggestions: MentionSuggestionState | null = null;
  private mentionRequest = 0;
  // The picker intentionally loads resources in the background, like Qwen's
  // completion surface. Keep the promise so callers/tests can observe the
  // state transition instead of guessing with a timer.
  private mentionLoad: Promise<void> | null = null;
  private workspaceMentionCache = new Map<string, { expiresAt: number; candidates?: MentionCandidate[]; pending?: Promise<MentionCandidate[]> }>();
  paletteEditor: Editor = editor();
  private questionDrafts = new Map<string, QuestionDraft>();
  private commandReturnGuard = false;
  private lastIdleInterrupt = 0;
  private navigation = 0; private pendingOperations = new Map<symbol, { navigation: number; mutable: boolean }>(); private operation = new AsyncLocalStorage<number>();
  onExit: () => void = () => {}; onChange: () => void = () => {};
  onEditor: (text: string) => Promise<EditedDraft> = async () => { throw new Error('当前终端未提供外部编辑器入口。'); };
  constructor(readonly client: Client) {
    this.input = editor(client.draft.text);
    let instanceId = client.instance.instanceId;
    client.subscribe(() => {
      if (this.suggestions && this.suggestions.sessionId !== client.view.sessionId) this.suggestions = null;
      if (this.mentionSuggestions && this.mentionSuggestions.sessionId !== client.view.sessionId) this.mentionSuggestions = null;
      if (instanceId === client.instance.instanceId) return;
      instanceId = client.instance.instanceId;
      this.invalidateNavigation();
      for (const field of this.page?.fields || []) if (field.secret) field.value = '';
      this.page = null; this.suggestions = null; this.mentionSuggestions = null; this.formEditor = editor(); this.input = editor(client.draft.text);
      this.following = client.view.scroll[client.view.sessionId]?.following ?? true;
      if (this.ticketId) { this.ticketId = ''; client.notice = '实例已切换，旧实例配对票据将在原有效期结束时失效。'; }
      this.changed();
    });
  }
  changed() { this.onChange(); }
  /**
   * Keep first-run guidance in the transcript area so the composer remains
   * usable.  Qwen/Claude both make the first screen a product surface rather
   * than a blocking modal; actions are available through /setup and F3.
   */
  welcomeProjection(locale: Locale = this.client.view.locale || 'zh-CN', compact = false, columns = 80): { rows: WelcomeArtRow[]; lines: string[]; tokens: Array<WelcomeArtRow['segments'][number]['token'] | undefined>; screenReader: string } {
    const en = locale === 'en-US';
    const status = (ready: boolean, zhReady: string, zhPending: string, enReady: string, enPending: string) => `${ready ? '●' : '○'} ${en ? (ready ? enReady : enPending) : (ready ? zhReady : zhPending)}`;
    const engineReady = this.client.connection === '已连接';
    const ownerReady = this.client.ownerReady;
    const workspaceReady = Boolean(this.client.workspace);
    const modelReady = Boolean(this.client.modelReady || this.client.snapshot?.modelReady);
    const art: WelcomeArtProjection = welcomeArtProjection(columns, compact);
    const content = compact ? [
      '',
      status(engineReady, 'Engine 已连接', 'Engine 未连接', 'Engine connected', 'Engine unavailable'),
      status(ownerReady, '本机身份已初始化', '本机身份待初始化', 'Local identity initialized', 'Local identity needs setup'),
      status(modelReady, 'Supervisor 模型已就绪', 'Supervisor 模型待配置', 'Supervisor model ready', 'Supervisor model needs setup'),
      status(workspaceReady, `工作区：${this.client.workspace || '未选择'}`, '工作区待选择', `Workspace: ${this.client.workspace || 'not selected'}`, 'Workspace needs setup'),
    ] : en ? [
      'V8 Agent OS',
      'Autonomous Local Engine Control Plane & Conversation-first Terminal.',
      '',
      status(engineReady, '● Engine connected', '○ Engine unavailable', '● Engine connected', '○ Engine unavailable'),
      status(ownerReady, '● Local identity initialized', '○ Local identity needs setup', '● Local identity initialized', '○ Local identity needs setup'),
      status(modelReady, '● Supervisor model ready', '○ Supervisor model needs setup', '● Supervisor model ready', '○ Supervisor model needs setup'),
      status(workspaceReady, `● Workspace: ${this.client.workspace || 'not selected'}`, '○ Workspace needs setup', `● Workspace: ${this.client.workspace || 'not selected'}`, '○ Workspace needs setup'),
      '',
      'Getting Started:',
      '  • Type your request and press Enter to chat or execute tasks.',
      '  • Type / for commands (/resume to switch sessions, /settings, etc.).',
      '  • Press @ to mention workspace files or artifacts.',
      '  • Press Ctrl+C twice or Ctrl+D to exit cleanly.',
      '',
      'F3 Quick Setup · F4 Phone/Peer · F1 Help · Ctrl+P Palette · Ctrl+B Sessions',
    ] : [
      'V8 Agent OS',
      '对话优先的本机终端，直接连接与调度你的 V8OS 智能体系统。',
      '',
      status(engineReady, '● Engine 已连接', '○ Engine 未连接', '● Engine connected', '○ Engine unavailable'),
      status(ownerReady, '● 本机身份已初始化', '○ 本机身份待初始化', '● Local identity initialized', '○ Local identity needs setup'),
      status(modelReady, '● Supervisor 模型已就绪', '○ Supervisor 模型待配置', '● Supervisor model ready', '○ Supervisor model needs setup'),
      status(workspaceReady, `● 工作区：${this.client.workspace || '未选择'}`, '○ 工作区待选择', `● Workspace: ${this.client.workspace || 'not selected'}`, '○ Workspace needs setup'),
      '',
      '快速上手指引：',
      '  • 直接输入需求并按 Enter 开始对话或执行工程任务',
      '  • 输入 / 查看所有操作指令（如 /resume 恢复历史会话、/settings 配置）',
      '  • 输入 @ 快速索引并引用工作区文件或项目产物',
      '  • 连续两次按 Ctrl+C 或按 Ctrl+D 安全退出终端（前台 Engine 随之释放）',
      '',
      'F3 快速配置 · F4 连接 Phone/Peer · F1 帮助 · Ctrl+P 快捷指令 · Ctrl+B 会话',
    ];
    const rows = [...art.rows, ...content.map(line => welcomeTextRow(line))];
    return { rows, lines: rows.map(row => row.raw), tokens: rows.map(row => row.segments[0]?.token), screenReader: [art.screenReader, ...content.filter(Boolean)].join('. ') };
  }

  welcomeLines(locale: Locale = this.client.view.locale || 'zh-CN', compact = false, columns = 80): string[] {
    return this.welcomeProjection(locale, compact, columns).lines;
  }

  async setup() {
    const owner = await this.client.api('/v1/client-identity/owner').catch(() => ({}));
    const ownerReady = Boolean(owner.initialized && (owner.user?.sessionIdentifier || this.client.ownerReady));
    const workspaceReady = Boolean(this.client.workspace);
    let modelReady = false;
    let modelDetail = '尚未读取模型状态';
    try {
      const controlPlane = await this.client.api('/v1/models/control-plane');
      const models = Array.isArray(controlPlane?.models) ? controlPlane.models : [];
      modelReady = models.some((model: any) => model?.eligibility?.eligible !== false && (model?.modelId || model?.model_id || model?.modelRef));
      modelDetail = modelReady ? `可用模型：${models.length} 个` : '尚未绑定可用模型';
    } catch (error: any) { modelDetail = `模型状态暂不可读：${error?.message || '请稍后重试'}`; }
    this.client.modelReady = modelReady;
    this.open('快速开始 / Quick setup', [
      '按顺序完成本机身份、模型和工作区；每一步都由 Engine 保存并回读。',
      `Engine：${this.client.connection}`, `本机身份：${ownerReady ? '已就绪' : '待初始化'}`,
      `模型：${modelDetail}`, `工作区：${workspaceReady ? this.client.workspace : '未选择'}`,
      '', '首屏输入框始终保留，Esc 返回不会清除草稿。',
    ], [
      { label: ownerReady ? `本机身份已就绪（${this.client.ownerName}）` : '初始化本机 owner', disabled: ownerReady, run: async () => {
        await this.client.api('/v1/client-identity/bootstrap', { method: 'POST', body: { login: 'owner', name: 'Owner' } });
        await this.client.initialize(); await this.setup();
      } },
      { label: '连接 Provider / Supervisor 模型', run: () => this.models() },
      { label: workspaceReady ? `工作区已选择：${this.client.workspace}` : '选择并信任工作区', run: () => this.form('选择工作区', [{ key: 'path', label: '已有目录的绝对路径', value: this.client.view.workspace }], async v => {
        await this.client.trustWorkspace(v.path); await this.setup();
      }, ['只登记已有目录；Engine 会校验并回读 trusted 状态。']) },
      { label: '连接 Phone / Peer（可选）', run: () => this.connections() },
      { label: '高级设置', run: () => this.settings() },
      { label: '返回聊天', run: () => this.close(true) },
    ]);
  }
  private updateBusy() { this.busy = [...this.pendingOperations.values()].some(operation => operation.mutable || operation.navigation === this.navigation); }
  private checkPage() {
    if (this.operation.getStore() !== undefined && this.operation.getStore() !== this.navigation) throw Object.assign(new Error('已离开此页面，旧响应不再更新界面。'), { stalePage: true });
  }
  invalidateNavigation() { this.navigation++; this.updateBusy(); }
  suggest() {
    this.invalidateNavigation();
    this.suggestions = { query: editor(), selected: 0, sessionId: this.client.view.sessionId };
    this.changed();
  }
  mentionRows(width: number, maxRows: number) {
    return this.mentionSuggestions ? mentionSuggestionRows(this.mentionSuggestions, width, maxRows) : { lines: [] as string[], selectedRow: -1, count: 0 };
  }
  async waitForMentionSuggestions() {
    await this.mentionLoad;
    return this.mentionSuggestions;
  }
  private async workspaceMentionCandidates(): Promise<MentionCandidate[]> {
    const workspace = this.client.workspace;
    if (!workspace) throw new Error('请先在设置中确认当前工作区');
    const root = path.resolve(workspace), cached = this.workspaceMentionCache.get(root);
    if (cached?.candidates && cached.expiresAt > Date.now()) return cached.candidates;
    if (cached?.pending) return cached.pending;
    const pending = (async () => {
      const result: MentionCandidate[] = [], directories = [root];
      const ignored = new Set(['.git', '.hg', '.svn', 'node_modules', 'dist', 'build', '.v8-agent-os']);
      while (directories.length && result.length < 1000) {
        const directory = directories.shift()!;
        const entries = await readdir(directory, { withFileTypes: true });
        entries.sort((a, b) => a.name.localeCompare(b.name));
        for (const entry of entries) {
          if (result.length >= 1000 || entry.name.startsWith('.') || ignored.has(entry.name) || entry.isSymbolicLink()) continue;
          const absolute = path.join(directory, entry.name);
          if (entry.isDirectory()) { directories.push(absolute); continue; }
          if (!entry.isFile()) continue;
          const relative = path.relative(root, absolute).split(path.sep).join('/');
          if (!relative || relative.startsWith('../') || path.isAbsolute(relative)) continue;
          result.push({ group: 'files', value: relative, label: relative, description: '当前工作区文件', authorized: true });
        }
      }
      return result;
    })();
    this.workspaceMentionCache.set(root, { expiresAt: Date.now() + 30_000, pending });
    try {
      const result = await pending;
      this.workspaceMentionCache.set(root, { expiresAt: Date.now() + 30_000, candidates: result });
      return result;
    } catch (error) {
      this.workspaceMentionCache.delete(root);
      throw error;
    }
  }
  private async mentionCatalogCandidates(): Promise<{ candidates: MentionCandidate[]; errors: string[] }> {
    const candidates: MentionCandidate[] = [], errors: string[] = [];
    for (const session of this.client.sessions || []) {
      const id = String(session?.id || '').trim();
      if (!id) continue;
      const title = String(session?.title || session?.name || '未命名会话').trim();
      const workspace = String(session?.workspacePath || session?.workspace_path || '').trim();
      candidates.push({ group: 'sessions', value: `session:${id}`, label: title, description: workspace && workspace !== this.client.workspace ? `跨工作区：${workspace}` : '当前工作区会话', authorized: workspace === this.client.workspace || !workspace });
    }
    const tasks: Array<[MentionGroup, Promise<any>]> = [
      ['skills', this.client.api('/v1/skills/list')],
      ['plugins', this.client.api('/v1/api/plugins/mentions')],
      ['mcp', this.client.api('/v1/mcp/status')],
    ];
    const results = await Promise.allSettled(tasks.map(([, task]) => task));
    for (let index = 0; index < results.length; index++) {
      const group = tasks[index][0], result = results[index];
      if (result.status === 'rejected') { errors.push(`${group} 目录暂不可用`); continue; }
      const data = result.value || {};
      if (group === 'skills') {
        for (const item of Array.isArray(data.skills) ? data.skills : []) {
          const value = String(item.name || item.id || '').trim(); if (!value) continue;
          candidates.push({ group: 'skills', value: `skill:${value}`, label: value, description: item.description || 'Engine skill', authorized: true });
        }
        for (const item of Array.isArray(data.subagentFamilies) ? data.subagentFamilies : []) {
          const value = String(item.familyId || item.id || item.name || '').trim(); if (!value) continue;
          candidates.push({ group: 'agents', value: `agent:${value}`, label: item.displayName || item.name || value, description: item.description || 'Engine agent family', authorized: true });
        }
      } else if (group === 'plugins') {
        for (const item of Array.isArray(data.items || data.plugins) ? (data.items || data.plugins) : []) {
          const value = String(item.pluginId || item.id || '').trim(); if (!value) continue;
          if (item.authorized === false) continue;
          candidates.push({ group: 'plugins', value: `plugin:${value}`, label: item.displayName || item.name || value, description: item.description || 'Engine plugin', authorized: true });
        }
      } else {
        const servers = Array.isArray(data.servers) ? data.servers : [];
        for (const item of servers) {
          const value = String(item.name || item.serverName || item.id || '').trim();
          if (!value || item.authorized === false || item.enabled === false) continue;
          candidates.push({ group: 'mcp', value: `mcp:${value}`, label: value, description: item.status || '授权的 MCP server', authorized: true });
        }
      }
    }
    return { candidates, errors };
  }
  private async sessionMentionCandidates(): Promise<MentionCandidate[]> {
    if (!this.client.sessions.length && this.client.ownerReady) await this.client.listSessions('', false, true);
    const result: MentionCandidate[] = [];
    for (const session of this.client.sessions) {
      const id = idOf(session), title = String(session.title || session.name || id);
      if (id) result.push({ group: 'sessions', value: `session:${id}`, label: title, description: session.workspacePath || '当前可见会话', authorized: true });
    }
    return result;
  }
  private async loadMentionCandidates(request: number) {
    const sources: MentionCandidate[][] = [[], [], []], errors: string[] = [];
    let remaining = 3;
    const publish = () => {
      if (!this.mentionSuggestions || request !== this.mentionRequest) return;
      const candidates = sources.flat();
      this.mentionSuggestions = { ...this.mentionSuggestions, candidates, loading: remaining > 0, error: errors.join('；') || undefined };
      this.changed();
    };
    const tasks = [this.workspaceMentionCandidates(), this.sessionMentionCandidates(), this.mentionCatalogCandidates()];
    await Promise.all(tasks.map((task, index) => task.then(result => {
      if (index === 2) {
        const catalog = result as { candidates: MentionCandidate[]; errors: string[] };
        sources[index] = catalog.candidates; errors.push(...catalog.errors);
      } else sources[index] = result as MentionCandidate[];
    }, () => {
      errors.push(index === 0 ? '文件目录暂不可用' : index === 1 ? '会话目录暂不可用' : 'Engine 目录暂不可用');
    }).finally(() => { remaining -= 1; publish(); })));
  }
  private openMentionAtCursor(originalText = this.input.text, originalCursor = this.input.cursor) {
    const token = mentionTokenAt(this.input.text, this.input.cursor);
    if (!token) return false;
    const continuing = this.mentionSuggestions;
    this.invalidateNavigation();
    const request = ++this.mentionRequest;
    this.mentionSuggestions = {
      query: token.query, group: continuing?.group || (token.query.startsWith('.') ? 'files' : 'all'), selected: 0, candidates: continuing?.candidates || [], loading: !continuing,
      triggerStart: token.start, triggerEnd: token.end,
      originalText: continuing?.originalText ?? originalText, originalCursor: continuing?.originalCursor ?? originalCursor,
      sessionId: this.client.view.sessionId,
    };
    if (!continuing) {
      this.mentionLoad = this.loadMentionCandidates(request).finally(() => {
        if (request === this.mentionRequest) this.mentionLoad = null;
      });
    }
    this.changed();
    return true;
  }
  private closeMention(restore = false) {
    const state = this.mentionSuggestions;
    if (!state) return;
    ++this.mentionRequest;
    if (restore) {
      this.input = { ...editor(state.originalText), cursor: state.originalCursor };
      this.client.setDraft(state.originalText);
    }
    this.mentionSuggestions = null; this.changed();
  }
  private updateMentionQuery() {
    const state = this.mentionSuggestions, token = mentionTokenAt(this.input.text, this.input.cursor);
    if (!state || !token || token.start !== state.triggerStart) { if (state) this.closeMention(false); return; }
    this.mentionSuggestions = { ...state, group: state.group === 'all' && token.query.startsWith('.') ? 'files' : state.group, query: token.query, triggerEnd: token.end, selected: 0 };
  }
  private async mentionInput(event: Input): Promise<boolean> {
    const state = this.mentionSuggestions!;
    if (event.key === 'escape' || event.key === 'ctrl-c') { this.closeMention(true); return true; }
    if (event.key === 'left') { this.mentionSuggestions = switchMentionGroup(state, -1); this.changed(); return true; }
    if (event.key === 'right') { this.mentionSuggestions = switchMentionGroup(state, 1); this.changed(); return true; }
    if (event.key === 'up' || event.key === 'pageup') { this.mentionSuggestions = moveMentionSelection(state, event.key === 'pageup' ? -5 : -1); this.changed(); return true; }
    if (event.key === 'down' || event.key === 'pagedown') { this.mentionSuggestions = moveMentionSelection(state, event.key === 'pagedown' ? 5 : 1); this.changed(); return true; }
    if (event.key === 'tab' || event.key === 'enter') {
      const candidate = selectedMention(state);
      if (!candidate) { this.client.notice = state.loading ? '候选仍在读取，请稍候。' : '当前分组没有可确认的候选。'; this.changed(); return true; }
      const replacement = mentionReplacement(this.input.text, state.triggerStart, state.triggerEnd, candidate.value);
      this.input = editor(replacement.text); this.input.cursor = replacement.cursor; this.client.setDraft(this.input.text);
      if (event.key === 'enter') this.closeMention(false); else {
        this.mentionSuggestions = { ...state, query: candidate.value, triggerEnd: replacement.cursor, selected: 0 };
        this.changed();
      }
      return true;
    }
    if (event.key === 'paste') { this.closeMention(false); return false; }
    if (['text', 'backspace', 'delete', 'home', 'end', 'ctrl-u', 'ctrl-k', 'ctrl-w'].includes(event.key)) {
      this.input = edit(this.input, event.key === 'text' ? 'insert' : event.key, event.text || '', this.editorWidth);
      this.client.setDraft(this.input.text); this.updateMentionQuery(); this.changed(); return true;
    }
    this.closeMention(false);
    return false;
  }
  setLocale(locale: Locale) { this.client.view.locale = locale; this.client.save(true); this.client.notice = locale === 'en-US' ? 'Language: English' : '界面语言：简体中文'; this.changed(); }
  private async suggestionInput(event: Input): Promise<boolean> {
    const menu = this.suggestions!;
    const matches = visibleCommandMatches(this.commands(), menu.query.text, {
      active: this.client.active,
      hasAttachments: this.client.draft.attachments.length > 0,
      configured: this.client.ownerReady && Boolean(this.client.workspace),
    });
    if (event.key === 'escape' || event.key === 'ctrl-c' || event.key === 'backspace' && !menu.query.text) { this.suggestions = null; this.changed(); return true; }
    if (event.key === 'ctrl-p') { this.suggestions = null; this.palette(menu.query.text); return true; }
    if (event.key === 'enter') {
      const action = matches[menu.selected];
      if (!action || action.disabled) { this.client.notice = action ? '此操作当前不可用。' : '没有匹配的操作；请修改关键词或 Esc 返回。'; this.changed(); return true; }
      if ((this.busy || this.client.busy) && !action.navigation) { this.client.notice = '操作仍在处理中；可继续搜索、切换页面或 Esc 返回。'; this.changed(); return true; }
      // Repeated Enter after a command must not send the draft behind the menu.
      this.commandReturnGuard = true; this.suggestions = null;
      await this.execute(action.run, action.navigation); return true;
    }
    if (event.key === 'f9') { this.client.notice = '命令候选中不会发送草稿；Enter 选择，Esc 返回。'; this.changed(); return true; }
    if (['up', 'down', 'backtab', 'pageup', 'pagedown'].includes(event.key)) {
      const delta = ['up', 'backtab', 'pageup'].includes(event.key) ? -1 : 1;
      menu.selected = Math.max(0, Math.min(matches.length - 1, menu.selected + delta));
    } else if (event.key === 'tab') {
      const action = matches[menu.selected];
      if (action) { menu.query = editor(action.command); menu.selected = 0; }
    } else if (['text', 'backspace', 'delete', 'left', 'right', 'home', 'end', 'ctrl-u', 'ctrl-k', 'ctrl-w'].includes(event.key)) {
      menu.query = edit(menu.query, event.key === 'text' ? 'insert' : event.key, event.text || '', this.editorWidth);
      if (['text', 'backspace', 'delete'].includes(event.key)) menu.selected = 0;
    } else {
      // Paste returns to the original composer and retains its inert-paste guard.
      // Other existing shortcuts keep their original owner and behavior.
      this.suggestions = null; this.changed(); return false;
    }
    this.changed(); return true;
  }
  async dispatch(event: Input) {
    if (this.mentionSuggestions) {
      const handled = await this.mentionInput(event);
      if (handled) return;
    }
    if (this.suggestions && await this.suggestionInput(event)) return;
    const pending = this.busy || this.client.busy;
    if (pending && event.key === 'ctrl-d') { this.invalidateNavigation(); this.onExit(); return; }
    const navigate = ['escape', 'ctrl-p', 'f1', 'f2', 'f3', 'f4', 'ctrl-b', 'ctrl-t'].includes(event.key)
      || event.key === 'ctrl-c' && Boolean(this.page)
      || event.key === 'text' && event.text === '/' && !this.input.text && !this.page
      || event.key === 'enter' && Boolean(!this.page?.fields && this.page?.actions[this.page.selected]?.navigation);
    const browse = !this.page?.fields && ['up', 'down', 'pageup', 'pagedown', 'tab', 'backtab'].includes(event.key)
      || this.page?.title === '操作菜单' && ['text', 'backspace'].includes(event.key)
      || Boolean(this.page?.tabs) && ['left', 'right'].includes(event.key);
    if (pending && !navigate && !browse) {
      if (!this.page && ['text', 'paste'].includes(event.key)) {
        if (this.client.busy) {
          this.client.draft.nextText = (this.client.draft.nextText || '') + (event.text || ''); this.client.save();
          this.client.notice = '发送期间输入已暂存为下一条草稿，受理后可继续编辑。';
        } else { await this.handle(event); return; }
      } else this.client.notice = '操作仍在处理中；可 Esc 返回、Ctrl+P 导航或 Ctrl+D 退出终端。';
      this.client.changed(); return;
    }
    const actionKey = ['enter', 'f9', 'f1', 'f2', 'f3', 'f4', 'ctrl-p', 'ctrl-b', 'ctrl-t', 'ctrl-n', 'escape', 'ctrl-c'].includes(event.key);
    if (actionKey || navigate) await this.execute(() => this.handle(event), navigate);
    else await this.handle(event);
  }
  async execute(action: () => void | Promise<void>, navigate = false) {
    if (this.busy && !navigate) return;
    if (navigate) this.invalidateNavigation();
    const navigation = this.navigation;
    const operation = Symbol(); this.pendingOperations.set(operation, { navigation, mutable: !navigate }); this.updateBusy(); this.changed();
    try { await this.operation.run(navigation, action); }
    catch (e: any) {
      if (e.stalePage || navigation !== this.navigation) return;
      this.client.notice = this.page?.sensitive ? '配置未确认保存。请检查连接和配置状态后重试；密钥不会回显。' : `操作未确认完成：${e.message}。请回读状态后再操作。`;
    }
    finally { this.pendingOperations.delete(operation); this.updateBusy(); this.changed(); }
  }
  open(title: string, lines: string[], actions: Action[] = [], options: Partial<Page> = {}) {
    this.checkPage();
    this.suggestions = null; this.mentionSuggestions = null; ++this.mentionRequest; this.pageSerial++; this.page = { title, lines, actions, selected: 0, offset: 0, ...options }; this.changed();
  }
  async close(force = false) {
    this.checkPage();
    if (this.page?.fields && !force) {
      const old = this.page;
      this.open('未保存的修改', ['返回将放弃此表单，聊天草稿仍保留。'], [
        { label: '继续编辑', run: () => { this.page = old; } }, { label: '放弃并返回', run: () => this.close(true) },
      ]); return;
    }
    if (this.ticketId) {
      const id = this.ticketId;
      await this.client.api(`/v1/client-identity/pairing-ticket/${encodeURIComponent(id)}`, { method: 'DELETE' }); this.ticketId = '';
      this.checkPage();
    }
    if (this.page?.fields) for (const f of this.page.fields) if (f.secret) f.value = '';
    this.formEditor = editor(); this.paletteEditor = editor(); this.page = null; this.pageSerial++; this.changed();
  }
  form(title: string, fields: Field[], save: (values: Record<string, string>) => Promise<void>, lines: string[] = []) {
    this.open(title, lines);
    this.page!.fields = fields; this.page!.fieldIndex = 0; this.page!.onSave = save;
    this.page!.sensitive = fields.some(f => f.secret); this.formEditor = editor(fields[0]?.value || '');
  }
  confirm(title: string, lines: string[], label: string, run: () => Promise<void>) {
    this.open(title, lines, [{ label: '返回', run: () => this.close(true) }, { label, run }]);
  }
  private async attachAtFiles() {
    const refs = parseAtReferences(this.client.draft.text);
    const filesRefs = refs.filter(ref => ref.kind === 'file' && !/^(skill|agent|subagent|plugin):/i.test(ref.value));
    if (!filesRefs.length) return { contextMentions: [], contextSessionRefs: [], pluginReferences: [] };
    if (!this.client.workspace) throw new Error('@ 文件引用需要先在 F3 设置并确认工作区。');
    const files = [...new Set(filesRefs.map(ref => workspaceReferencePath(this.client.workspace, ref.value)))];
    for (const filename of files) {
      const info = await stat(filename).catch(() => null);
      if (!info?.isFile()) throw new Error(`@ 文件不存在或不是普通文件：${filename}`);
    }
    for (const filename of files) await this.client.attachFile(filename);
    this.client.notice = `已登记 ${files.length} 个 @ 文件来源；发送时由 Engine 读取。`;
    return { contextMentions: [], contextSessionRefs: [], pluginReferences: [] };
  }
  private async resolveStructuredAtMentions() {
    const refs = parseAtReferences(this.client.draft.text);
    const contextSessionRefs = refs.filter(ref => ref.kind === 'session').map(ref => ({ sessionId: ref.value.slice('session:'.length), source: 'history_menu' as const }));
    if (contextSessionRefs.some(ref => !/^[A-Za-z0-9][A-Za-z0-9_.:-]{5,180}$/.test(ref.sessionId))) throw new Error('@session 引用需要完整的会话 ID。');
    const contextMentions: any[] = [], pluginReferences: any[] = [];
    const skillRefs = refs.filter(ref => ref.value.startsWith('skill:'));
    const agentRefs = refs.filter(ref => ref.value.startsWith('agent:') || ref.value.startsWith('subagent:'));
    const pluginRefs = refs.filter(ref => ref.value.startsWith('plugin:'));
    if (skillRefs.length || agentRefs.length) {
      const catalog = await this.client.api('/v1/skills/list');
      const skills = catalog.skills || [], families = catalog.subagentFamilies || [];
      for (const ref of skillRefs) {
        const name = ref.value.slice('skill:'.length), skill = skills.find((item: any) => String(item.name || item.id || '').toLowerCase() === name.toLowerCase());
        if (!skill) throw new Error(`未找到 @skill:${name}；请先查看 Engine 当前能力目录。`);
        contextMentions.push({ kind: 'skill', name: skill.name, label: skill.name, description: skill.description || '', path: skill.path || '', sourceType: 'explicit_mention' });
      }
      for (const ref of agentRefs) {
        const name = ref.value.replace(/^(agent|subagent):/, ''), family = families.find((item: any) => String(item.familyId || item.id || item.name || '').toLowerCase() === name.toLowerCase());
        if (!family) throw new Error(`未找到 @agent:${name}；请先查看 Engine 当前 subagent 族目录。`);
        contextMentions.push({ kind: 'subagent_family', id: family.familyId || family.id || family.name, familyId: family.familyId || family.id || family.name, name: family.displayName || family.name, label: family.displayName || family.name, description: family.description || '', sourceType: 'explicit_mention' });
      }
    }
    if (pluginRefs.length) {
      const catalog = await this.client.api('/v1/api/plugins/mentions'), plugins = catalog.items || [];
      for (const ref of pluginRefs) {
        const id = ref.value.slice('plugin:'.length), plugin = plugins.find((item: any) => String(item.pluginId || '').toLowerCase() === id.toLowerCase());
        if (!plugin) throw new Error(`未找到 @plugin:${id}；请先查看插件目录。`);
        pluginReferences.push({ pluginId: plugin.pluginId, name: plugin.displayName, scope: 'task', componentIds: plugin.componentIds });
      }
    }
    return { contextMentions, contextSessionRefs, pluginReferences };
  }
  async submit() {
    const text = this.client.draft.text.trim();
    if (text.startsWith('/')) {
      const parts = text.split(/\s+/);
      const command = (parts[0] || '').toLowerCase();
      const args = text.slice(parts[0].length).trim();
      if (command === '/resume' || command === '/sessions') {
        this.client.setDraft('');
        this.input = editor('');
        if (args) {
          await this.client.attach(args);
          this.following = true;
        } else {
          await this.sessions();
        }
        return;
      }
      if (command === '/model' || command === '/models') {
        this.client.setDraft('');
        this.input = editor('');
        await this.modelsHub();
        return;
      }
      if (command === '/new' || command === '/clear') {
        this.client.setDraft('');
        this.input = editor('');
        await this.newSession();
        return;
      }
      if (command === '/workspace') {
        this.client.setDraft('');
        this.input = editor('');
        this.workspaceManager();
        return;
      }
      if (command === '/mcp') {
        this.client.setDraft('');
        this.input = editor('');
        await this.extensions();
        return;
      }
      if (command === '/doctor' || command === '/status') {
        this.client.setDraft('');
        this.input = editor('');
        await this.readPage('系统诊断', '/v1/client-identity/instance');
        return;
      }
      if (command === '/settings' || command === '/config') {
        this.client.setDraft('');
        this.input = editor('');
        await this.settings();
        return;
      }
      if (command === '/inbox') {
        this.client.setDraft('');
        this.input = editor('');
        await this.inbox();
        return;
      }
      if (command === '/help') {
        this.client.setDraft('');
        this.input = editor('');
        this.help();
        return;
      }
      if (command === '/approval') {
        this.client.setDraft('');
        this.input = editor('');
        const approvalAction = this.commands().find(c => c.command === 'approval');
        if (approvalAction) await approvalAction.run();
        return;
      }
      if (command === '/exit' || command === '/quit') {
        this.client.setDraft('');
        this.input = editor('');
        this.onExit();
        return;
      }
    }
    const atData = await this.resolveStructuredAtMentions();
    await this.attachAtFiles();
    await this.client.submit(atData);
    this.input = editor(this.client.draft.text); this.following = true;
  }
  async modelsHub(tabIndex = 0) {
    const { modelsHub } = await import('./model-pages.js');
    await modelsHub(this, tabIndex);
  }
  async roleModelAssignmentTabs(activeRoleTab = 0) {
    const { roleModelAssignmentTabs } = await import('./model-pages.js');
    await roleModelAssignmentTabs(this, activeRoleTab);
  }
  workspaceManager() {
    this.open('工作区管理 / Workspace', [
      `当前工作区：${this.client.workspace || '尚未选择工作区'}`,
      `工作区状态：${this.client.workspace ? '● 已就绪并信任' : '○ 待选择'}`,
      '',
      '提示：登记已有目录作为工作区，Engine 会校验目录并建立受信凭据。',
    ], [
      { label: '选择并切换工作区', run: () => this.form('选择工作区', [{ key: 'path', label: '已有目录的绝对路径', value: this.client.view.workspace }], async v => {
        await this.client.trustWorkspace(v.path);
        this.client.notice = `已将工作区切换至：${v.path}`;
        await this.close(true);
      }, ['只登记已有目录；Engine 会校验并回读 trusted 状态。']) },
      { label: '返回对话', run: () => this.close(true) },
    ]);
  }
  async sessions() {
    await this.client.listSessions();
    const sessions = this.client.sessions || [];
    const currentId = this.client.view.sessionId;
    const sessionActions: Action[] = sessions.map(s => {
      const isCurrent = idOf(s) === currentId;
      const title = s.title || '未命名会话';
      const status = s.status ? ` · ${s.status}` : '';
      const badge = isCurrent ? '● [当前] ' : '○ ';
      return {
        label: `${badge}${title}${status}`,
        navigation: true,
        run: async () => {
          if (!isCurrent) {
            await this.client.attach(idOf(s));
            this.input = editor(this.client.draft.text);
            this.following = true;
          }
          await this.close(true);
        },
      };
    });
    if (this.client.sessionCursor) {
      sessionActions.push({ label: '加载更多会话...', run: async () => { await this.client.listSessions(undefined, true); this.sessionResults(); } });
    }
    const actions: Action[] = [
      ...sessionActions,
      { label: '＋ 新建全新会话', run: () => this.newSession() },
      { label: '🔍 搜索会话标题...', run: () => this.form('搜索会话', [{ key: 'q', label: '标题关键词', value: '' }], async v => { await this.client.listSessions(v.q); this.sessionResults(); }) },
      { label: '📁 仅查看当前工作区会话', run: async () => { await this.client.listSessions('', false, true); this.sessionResults(); } },
      { label: '返回对话', run: () => this.close(true) },
    ];
    this.open('会话', [
      '↑↓ 选择会话 · Enter 直接切换 · Esc 返回草稿',
      `共 ${sessions.length} 个历史会话${currentId ? `（当前会话：${currentId.slice(0, 8)}...）` : ''}`,
    ], actions);
    if (this.page && sessions.length > 0) {
      const firstOther = sessions.findIndex(s => idOf(s) !== currentId);
      this.page.selected = firstOther >= 0 ? firstOther : 0;
    }
  }
  sessionActions(): Action[] {
    const actions: Action[] = this.client.sessions.map(s => ({ label: `${s.title || '未命名'} · ${s.status || '历史'}`, navigation: true, run: async () => { await this.client.attach(idOf(s)); this.input = editor(this.client.draft.text); this.following = true; await this.close(true); } }));
    if (this.client.sessionCursor) actions.push({ label: '加载更多会话', run: async () => { await this.client.listSessions(undefined, true); this.sessionResults(); } });
    return actions;
  }
  sessionResults() { this.open('会话列表', [], [{ label: '返回对话', run: () => this.close(true) }, ...this.sessionActions()]); }
  async newSession() { await this.client.attach(''); this.input = editor(this.client.draft.text); this.following = true; await this.close(true); }
  async details() {
    await this.client.refreshSnapshot();
    const s = this.client.snapshot;
    this.open('任务详情', [
      `状态：${s.currentRun?.status || s.runtimeStatus || '未运行'}`,
      `重试：${this.client.retryControl.allowed ? 'Engine 支持重新执行；可能再次产生副作用。' : this.client.retryControl.reason}`,
      ...describe(s.summary || {}), ...describe(s.lane || {}), ...describe(s.workflowProjection || {}),
      ...describe(s.todos || {}), ...this.client.outputs.map(x => `产物：${x.name} · ${x.path || x.artifactId || ''}`),
    ], [
      { label: '返回对话', run: () => this.close(true) }, { label: '停止当前任务', disabled: !this.client.active, run: () => this.stopRun() },
      { label: '重试当前任务', disabled: !this.client.retryControl.allowed, run: () => this.retryRun() },
      { label: '查看完整任务证据', run: () => { this.open('任务证据', describe({ timeline: s.runtimeTimeline, controls: s.controls, recovery: s.recoverable }), [{ label: '返回', run: () => this.details() }]); } },
      { label: '重命名会话', disabled: !this.client.view.sessionId, run: () => this.form('重命名会话', [{ key: 'title', label: '标题', value: '' }], async v => { await this.client.api(`/v1/sessions/${encodeURIComponent(this.client.view.sessionId)}`, { method: 'PATCH', body: { title: v.title } }); await this.sessions(); }) },
      { label: this.client.sessions.find(s => idOf(s) === this.client.view.sessionId)?.pinned ? '取消置顶会话' : '置顶会话', disabled: !this.client.view.sessionId, run: async () => { const session = this.client.sessions.find(s => idOf(s) === this.client.view.sessionId); await this.client.api(`/v1/sessions/${encodeURIComponent(this.client.view.sessionId)}`, { method: 'PATCH', body: { pinned: !session?.pinned } }); await this.sessions(); } },
      { label: '删除会话', disabled: !this.client.view.sessionId || this.client.active, run: () => this.confirm('删除会话', [`会话：${this.client.view.sessionId}`, '删除会清理会话记录、运行检查点和会话授权，无法由 TUI 撤销。', '运行中的会话必须先停止。'], '确认删除', async () => { const deleted = this.client.view.sessionId; await this.client.api(`/v1/sessions/${encodeURIComponent(deleted)}`, { method: 'DELETE' }); await this.client.attach(''); await this.client.listSessions(); this.client.notice = '会话已删除；已回到新会话。'; await this.close(true); }) },
    ]);
  }
  stopRun() {
    const runId = idOf(this.client.run);
    this.confirm('停止当前任务', [`会话：${this.client.view.sessionId}`, `任务：${runId}`, '停止会影响当前任务；退出终端只会断开界面。'], '请求停止', async () => { await this.client.interrupt(runId); await this.details(); });
  }
  retryRun() {
    const control = this.client.retryControl;
    if (!control.allowed) { this.open('无法重试', [control.reason], [{ label: '返回任务详情', run: () => this.details() }]); return; }
    this.confirm('重试任务', [`会话：${this.client.view.sessionId}`, `运行：${control.runId}`, 'Engine 将重新执行该运行，可能新建 run 并再次产生副作用。', '确认时重新读取当前能力；不会重发聊天草稿。'], '请求 Engine 重试', async () => { await this.client.retryRun(control.runId); await this.details(); });
  }
  async externalEditor() {
    const view = this.client.view, sessionId = view.sessionId, draft = this.client.draft;
    const original = this.client.draft.text;
    if (draft.unknown) throw new Error('当前草稿的发送结果仍待确认，请先核对结果再使用外部编辑器。');
    let edited: EditedDraft;
    try { edited = await this.onEditor(original); }
    finally { this.page = null; this.changed(); }
    try { await this.client.tick(); } catch { /* Keep an offline draft; submit rechecks authority. */ }
    if (this.client.view !== view || this.client.view.sessionId !== sessionId) throw new Error(`实例或会话已变化；编辑内容保留在 ${edited.file}，没有写入其他草稿。`);
    if (this.client.draft !== draft || draft.text !== original || draft.unknown) throw new Error(`草稿在编辑期间已变化；编辑内容保留在 ${edited.file}，没有覆盖当前草稿。`);
    this.undo = original; this.client.setDraft(edited.text); this.input = editor(edited.text); this.input.pasted = true;
    this.client.save(true); await edited.remove();
    this.page = null; this.client.notice = '编辑内容已回到草稿；按 F9 或菜单“发送”提交。'; this.changed();
  }
  async inbox() {
    await this.client.refreshSnapshot();
    const items = await this.client.listInbox();
    this.open('待处理', [items.length ? '当前会话优先；选择事项查看来源和完整范围。' : '已加载会话暂无待处理事项。', ...(this.client.sessionCursor ? ['更多会话可在会话列表继续加载后查看提问。'] : [])], [
      { label: '返回对话', run: () => this.close(true) },
      ...items.map((item: any) => ({ label: `${item.kind === 'question' ? '提问' : '审批'} · ${item.title || item.question || item.request?.question || item.summary || idOf(item)}`, run: async () => {
        if (item.sessionId && item.sessionId !== this.client.view.sessionId) { await this.client.attach(item.sessionId); this.input = editor(this.client.draft.text); }
        await this.inboxItem(item);
      } })),
      { label: '其他会话', run: () => this.sessions() },
    ]);
  }
  private questionDraft(item: any) {
    const identity = idOf(item);
    let draft = this.questionDrafts.get(identity);
    if (!draft) { draft = createQuestionDraft(); this.questionDrafts.set(identity, draft); }
    return draft;
  }
  private questionPage(item: any, index = 0) {
    const questions = normalizeQuestions(item), draft = this.questionDraft(item);
    const currentIndex = Math.max(0, Math.min(index, questions.length - 1));
    const question = questions[currentIndex], qid = questionKey(question, currentIndex), multi = Boolean(question.multiSelect || question.multiple);
    const selected = draft.selected[qid] || [], summary = requestSummary(item);
    const lines = [summary.title, ...(summary.details ? [summary.details] : []), `问题 ${currentIndex + 1}/${questions.length}：${questionTitle(question, currentIndex)}`,
      ...(questionDetail(question) ? [questionDetail(question)] : []), safeText(question.question || ''),
      ...(question.options?.length ? question.options.map((option, optionIndex) => `${selected.includes(optionKey(option, optionIndex)) ? '✓' : '·'} ${optionLabel(option, optionIndex)}${optionDetail(option) ? ` — ${optionDetail(option)}` : ''}`) : ['当前问题需要文字回答。']),
      ...(draft.custom[qid] ? [`自定义回答：${safeText(draft.custom[qid])}`] : []),
      multi ? '多选：Enter 切换选项；完成后选择下一题或提交。' : '单选：选择后可继续下一题；也可输入自定义回答。'];
    const actions: Action[] = [
      { label: '返回待处理（保留选择）', run: () => this.inbox() },
    ];
    for (const [optionIndex, option] of (question.options || []).entries()) {
      const key = optionKey(option, optionIndex), label = `${selected.includes(key) ? '✓ ' : ''}${optionLabel(option, optionIndex)}`;
      actions.push({ label, run: () => {
        if (multi) {
          const next = selected.includes(key) ? selected.filter(value => value !== key) : [...selected, key];
          draft.selected[qid] = next; delete draft.custom[qid];
          this.questionPage(item, currentIndex);
        } else {
          draft.selected[qid] = [key]; delete draft.custom[qid];
          this.questionPage(item, currentIndex < questions.length - 1 ? currentIndex + 1 : currentIndex);
        }
      } });
    }
    actions.push({ label: draft.custom[qid] ? '修改自定义回答' : '输入自定义回答', run: () => this.form('自定义回答', [{ key: 'answer', label: safeText(question.question || questionTitle(question, currentIndex)), value: draft.custom[qid] || '' }], async values => {
      const answer = String(values.answer || '').trim();
      if (!answer) throw new Error('回答不能为空；已保留当前选择。');
      draft.custom[qid] = answer; draft.selected[qid] = [];
      this.questionPage(item, currentIndex < questions.length - 1 ? currentIndex + 1 : currentIndex);
    }, ['Esc 返回问题，不会撤销已选内容；F9 保存这条自定义回答。']) });
    if (currentIndex > 0) actions.push({ label: '上一题', run: () => this.questionPage(item, currentIndex - 1) });
    if (currentIndex < questions.length - 1) actions.push({ label: '下一题', disabled: !questionAnswered(question, currentIndex, draft), run: () => this.questionPage(item, currentIndex + 1) });
    const complete = questions.every((candidate, questionIndex) => questionAnswered(candidate, questionIndex, draft));
    actions.push({ label: '提交全部回答', disabled: !complete, run: async () => {
      const answer = buildQuestionAnswer(questions, draft);
      if (!answer) throw new Error('尚未完成回答；已保留当前选择。');
      try {
        await this.client.decide(item, 'answer', answer);
        this.questionDrafts.delete(idOf(item));
        await this.inbox();
      } catch (error: any) {
        // A stale interaction remains visible in the inbox and its local
        // choices stay available for review; the client has re-read Engine
        // state before deciding and never retries a side effect.
        this.client.notice = error?.message || '问题已过期；请重新打开待处理列表。';
        throw error;
      }
    } });
    this.open('回答提问', lines, actions);
  }
  inboxItem(item: any) {
    if (item.kind === 'question') {
      this.questionPage(item);
      return;
    }
    if (isSpecApproval(item)) return this.specApproval(item);
    this.open('审批详情', [...approvalLines(item), ...(approvalTransparent(item) ? [] : ['请求缺少可核对的动作目标；无法批准，请拒绝或返回。'])], [
      { label: '返回', run: () => this.inbox() },
      { label: '拒绝', run: async () => { await this.client.decide(item, 'reject'); await this.inbox(); } },
      { label: '批准本次', disabled: !approvalTransparent(item), run: async () => { await this.client.decide(item, 'approve'); await this.inbox(); } },
    ]);
  }
  async specApproval(item: any) {
    let document: SpecReviewDocument;
    try { document = await readSpecReview(this.client, item); }
    catch (error: any) {
      if (error.staleView || error.stalePage) throw error;
      this.open('Spec 文档未能读取', [safeText(error.message), '尚未确认完整文档，不会批准；可重读、拒绝或返回。'], [
        { label: '返回待处理', run: () => this.inbox() },
        { label: '重新读取文档', run: () => this.specApproval(item) },
        { label: '拒绝此审批', run: async () => { await this.client.decide(item, 'reject'); await this.inbox(); } },
      ]);
      return;
    }
    const matches = specReviewMatches(item, document);
    this.open('Spec 文档审批', [
      `文档：${document.documentPath}`, `SHA-256：${document.documentSha256}`,
      matches ? '请阅读下方完整文档；批准只针对该版本。' : '文档版本已变化；阅读后先刷新审批，再确认批准。',
      'PgUp / PgDn 阅读；终端控制字符按普通内容显示。', '', safeText(document.content),
    ], [
      { label: '返回待处理', run: () => this.inbox() },
      { label: '重新读取文档', run: () => this.specApproval(item) },
      { label: '拒绝此审批', run: async () => { await this.client.decide(item, 'reject'); await this.inbox(); } },
      ...(matches ? [{ label: '已阅读并批准此版本', run: async () => { await this.client.decide(item, 'approve', '', document); await this.inbox(); } }]
        : [{ label: '为已读取版本刷新审批（尚不批准）', run: async () => { const replacement = await this.client.refreshSpecApproval(item, document); await this.specApproval(replacement); } }]),
    ]);
    // The reviewed prose is user content. UI translation must not change the
    // text whose bytes were checked against the Engine's document version.
    this.page!.localizeLines = false;
  }
  async settings() {
    this.open('设置', ['修改通过 Engine 校验并回读；密钥只在隐藏字段输入。'], [
      { label: '返回对话', run: () => this.close(true) },
      { label: '模型管理中心 (横向分类 / 快速配置)', run: () => this.modelsHub() },
      { label: '角色模型分配 (Supervisor / Subagent / Runtime 选项卡)', run: () => this.roleModelAssignmentTabs(0) },
      { label: '高级模型参数与预算 (原生)', run: () => this.models() },
      { label: '工作区与信任', run: () => this.workspaceManager() },
      { label: 'Runtime 能力包', run: () => featurePacks(this) },
      { label: 'MCP / 插件', run: () => this.extensions() },
      { label: '记忆 / 调度', run: () => this.open('记忆 / 调度', [], [{ label: '记忆配置', run: () => this.registry('memory') }, { label: '调度配置', run: () => this.registry('cron') }, { label: 'Hooks', run: () => this.registry('hooks') }]) },
      { label: '实例 / 诊断', run: () => this.readPage('实例', '/v1/client-identity/instance') },
      { label: '手机网关设置', run: () => this.brokerSettings('client-gateway') },
      { label: '组网设置', run: () => this.brokerSettings('network') },
      { label: '语言 / Language', run: () => this.open('语言 / Language', ['选择后立即保存到本机 TUI 视图；不会修改 Engine 或其他客户端。'], [
        { label: '中文（简体）', run: () => { this.setLocale('zh-CN'); this.page = null; } },
        { label: 'English', run: () => { this.setLocale('en-US'); this.page = null; } },
        { label: '返回设置', run: () => this.settings() },
      ]) },
    ]);
  }
  async readPage(title: string, route: string) { const data = await this.client.api(route); this.open(title, describe(data), [{ label: '返回', run: () => this.close(true) }, { label: '刷新', run: () => this.readPage(title, route) }]); }
  async models() {
    const [roles, controlPlane] = await Promise.all([
      this.client.api('/v1/config-broker/roles'),
      this.client.api('/v1/models/control-plane').catch(() => ({})),
    ]);
    const modelRows = Array.isArray(controlPlane?.models) ? controlPlane.models : [];
    const modelLines = modelRows.slice(0, 12).flatMap((model: any) => {
      const eligibility = model.eligibility || {};
      const state = eligibility.eligible === false ? `不可用：${eligibility.message || '能力或元数据不完整'}` : '可用';
      return [`模型 ${modelRefOf(model)} · ${state}`,
        `  上下文：${model.contextWindow || '未知'} · 输出：${model.outputTokenMode || (model.maxTokens ? `fixed ${model.maxTokens}` : 'auto')}`];
    });
    const lines = [...describe(roles), ...(modelLines.length ? ['模型目录（前 12 项）', ...modelLines] : ['模型目录暂无可用条目'])];
    this.open('模型 / Provider / 预算', lines, [
      { label: '返回设置', run: () => this.settings() },
      { label: '连接 Provider / 模型', run: () => this.form('连接模型', [
        { key: 'providerId', label: 'Provider ID', value: '' }, { key: 'modelId', label: '模型 ID', value: '' },
        { key: 'baseUrl', label: 'API 地址（留空使用目录默认）', value: '' }, { key: 'apiKey', label: 'API 密钥（隐藏，留空复用已存凭据）', value: '', secret: true },
      ], async v => {
        try { await this.client.api('/v1/models/connect', { method: 'POST', body: v, timeoutMs: 60000 }); }
        finally { v.apiKey = ''; if (this.page?.fields) for (const f of this.page.fields) if (f.secret) f.value = ''; this.formEditor = editor(); }
        await this.models();
      }) },
      { label: '为角色选择模型', run: () => this.form('角色模型', [{ key: 'role', label: '角色 ID（上方角色列表）', value: 'supervisor' }, { key: 'modelRef', label: '模型引用', value: '' }], async v => this.prepare('/v1/config-broker/roles/prepare', v, '/v1/config-broker/roles')) },
      { label: '浏览已安装模型', run: () => this.readPage('模型目录', '/v1/config-broker/models?limit=50') },
      { label: '累计 Token / 费用预算', run: () => this.budgets() },
      { label: '近 24 小时用量', run: () => this.modelUsage() },
      { label: 'Supervisor 推理强度', run: () => this.reasoningEffort() },
      { label: '模型单次输出长度', run: () => this.outputParameters() },
      { label: '上下文配置', run: () => this.contextSettings() },
    ]);
  }
  async modelUsage() {
    const data = await this.client.api('/v1/telemetry/overview?days=1');
    const stats = data.stats || {};
    const lines = [
      `统计窗口：${stats.recentWindowDays || 1} 天`,
      `调用：${stats.recentWindowInvocations ?? 0} · Token：${stats.recentWindowTokens ?? 0}`,
      `估算费用：${stats.recentWindowEstimatedCost ?? 0}`,
      `提示：Provider 未回报用量时，Engine 会明确标记 unknown，不把估算当实际结算。`,
      ...((data.recentInvocations || []).slice(0, 8).flatMap((item: any) => [
        `${item.modelId || item.model_id || 'unknown'} · ${item.status || 'unknown'} · ${item.durationMs ?? item.latencyMs ?? item.latency_ms ?? '?'} ms`,
        `  input ${item.inputTokens ?? item.input_tokens ?? item.promptTokens ?? '?'} / output ${item.outputTokens ?? item.output_tokens ?? item.completionTokens ?? '?'}`,
      ])),
    ];
    this.open('近 24 小时模型用量', lines, [{ label: '返回模型设置', run: () => this.models() }, { label: '刷新', run: () => this.modelUsage() }]);
  }
  async reasoningEffort() {
    if (!this.client.view.sessionId) {
      this.open('Supervisor 推理强度', ['需要先选择一个会话；该设置只作用于当前会话，不修改全局模型默认值。'], [{ label: '返回模型设置', run: () => this.models() }]);
      return;
    }
    const current = await this.client.api(`/v1/models/supervisor-reasoning-effort?sessionId=${encodeURIComponent(this.client.view.sessionId)}`);
    const levels = Array.isArray(current.levels) && current.levels.length ? current.levels : ['auto', 'low', 'medium', 'high'];
    this.open('Supervisor 推理强度', [
      `当前：${current.effectiveLevel || current.sessionLevel || 'auto'}`,
      `来源：${current.selectionSource || 'model_default'}`,
      current.reason || '切换后从下一条消息生效。',
    ], [
      { label: '返回模型设置', run: () => this.models() },
      ...levels.map((level: string) => ({ label: `设为 ${level}`, run: async () => {
        const result = await this.client.api('/v1/models/supervisor-reasoning-effort', { method: 'PATCH', body: { sessionId: this.client.view.sessionId, level } });
        this.open('推理强度已更新', [`当前：${result.effectiveLevel || level}`, `来源：${result.selectionSource || 'session'}`], [{ label: '返回模型设置', run: () => this.models() }]);
      } })),
    ]);
  }
  async budgets() {
    const payload = await this.client.api('/v1/models/control-plane');
    const budgets = (payload.config || payload).governance?.budgets || {};
    const fields: Field[] = [
      ['globalDailyTokenLimit', '每日累计 Token 上限'], ['globalDailyCostLimit', '每日费用上限'],
      ['runMaxTokens', '单任务累计 Token 上限'], ['runMaxCost', '单任务费用上限'],
    ].map(([key, label]) => ({ key, label: `${label}（0 为不限）`, value: String(budgets[key] ?? 0) }));
    this.form('累计预算', fields, async values => {
      const next = Object.fromEntries(Object.entries(values).map(([key, value]) => [key, numericInput(value, fields.find(f => f.key === key)!.label)]));
      if (Object.values(next).some(value => !Number.isFinite(value) || value < 0)) throw new Error('预算必须是有限非负数');
      await this.prepare('/v1/config-broker/model-policy/prepare', { governance: { budgets: next } }, '/v1/models/control-plane');
    }, ['累计用量上限与单次输出长度、上下文窗口分别生效。', `预算启用：${budgets.enabled ?? true}`]);
  }
  async outputParameters() {
    const data = await this.client.api('/v1/models/control-plane');
    const models = (data.models || []).filter((model: any) => ['TEXT', 'MULTIMODAL', 'VISION', 'CHAT'].includes(String(model.type || 'TEXT').toUpperCase()));
    this.open('模型单次输出长度', ['设置保存在所选模型；使用同一模型的角色共享此值。auto 由协议与 Provider 决定。'], [{ label: '返回模型设置', run: () => this.models() },
      ...models.map((model: any) => ({ label: `${model.modelId} · ${model.providerName || model.providerId}`, run: () => this.form('输出长度', [
        { key: 'mode', label: '输出模式（auto / fixed）', value: model.outputTokenMode || (model.maxTokens ? 'fixed' : 'auto') },
        { key: 'maxTokens', label: 'fixed 模式的单次输出 Token 上限', value: String(model.maxTokens || '') },
      ], async values => {
        const mode = values.mode.trim(); if (!['auto', 'fixed'].includes(mode)) throw new Error('请选择 auto 或 fixed');
        const maxTokens = mode === 'fixed' ? numericInput(values.maxTokens, '单次输出上限') : 0; if (mode === 'fixed' && (!Number.isInteger(maxTokens) || maxTokens <= 0)) throw new Error('fixed 模式需要正整数 Token 上限');
        const patch = { outputTokenMode: mode, ...(mode === 'fixed' ? { maxTokens } : {}) };
        this.confirm('模型输出长度预览', [`模型：${model.modelRef}`, ...describe(patch)], '保存并回读', async () => {
          const result = await this.client.api('/v1/models/bindings', { method: 'PUT', body: { providerId: model.providerId, modelId: model.modelId, model: patch } });
          this.open('模型参数已回读', describe(result.model), [{ label: '返回模型设置', run: () => this.models() }]);
        });
      }) })),
      { label: '高级角色配置', run: () => this.registry('supervisor') },
    ]);
  }
  async contextSettings() {
    const current = await this.client.api('/v1/config-registry/context');
    const policy = current.data?.policy || {}, compression = policy.compression || {};
    const summaryModel = current.data?.modelBindings?.summaryModel || current.data?.bindings?.summary_model || current.bindings?.summary_model || 'Engine 默认';
    const c = { ...compression };
    const status = c.enabled === false ? '已关闭（不会自动压缩）' : `已启用 · ${c.mode || 'persistent_baseline'}`;
    this.open('上下文与压缩', [
      `状态：${status}`,
      `窗口：${c.default_context_window_tokens || 32000} · 软阈值：${c.soft_trigger_ratio ?? 0.90} · 硬阈值：${c.hard_trigger_ratio ?? c.trigger_ratio ?? 0.94}`,
      `保留：${c.keep_recent_turns ?? 4} 轮 / ${c.keep_recent_messages ?? 8} 条 · 摘要模型：${summaryModel}`,
      '配置保存由 Engine 校验并回读；TUI 不直接修改会话 transcript。',
      '当前 Engine 未暴露手动压缩命令；只能在下一次运行达到阈值时自动压缩。',
    ], [
      { label: '返回模型设置', run: () => this.models() },
      { label: '查看当前上下文用量', run: () => this.contextUsage() },
      { label: '查看最近压缩记录', run: () => this.compactionHistory() },
      { label: '编辑基础压缩策略', run: () => this.editContextBasics(policy, c) },
      { label: '编辑高级压缩参数', run: () => this.editContextAdvanced(policy, c) },
    ]);
  }
  private contextSaveForm(policy: any, compression: any, fields: Field[], parse: (values: Record<string, string>) => any, lines: string[]) {
    this.form('上下文压缩策略', fields, async values => {
      const patch = { policy: { ...policy, compression: { ...compression, ...parse(values) } } };
      this.confirm('上下文配置预览', [...lines, ...describe(patch)], '保存并回读', async () => {
        await this.client.api('/v1/config-registry/context', { method: 'POST', body: patch });
        await this.contextSettings();
      });
    }, lines);
  }
  editContextBasics(policy: any, compression: any) {
    this.contextSaveForm(policy, compression, [
      { key: 'enabled', label: '自动压缩（true / false）', value: String(compression.enabled !== false) },
      { key: 'mode', label: '压缩模式', value: String(compression.mode || 'persistent_baseline') },
      { key: 'window', label: '上下文窗口 Token 上限', value: String(compression.default_context_window_tokens || 32000) },
      { key: 'ratio', label: '硬压缩触发比例（0.70–0.99）', value: String(compression.trigger_ratio ?? 0.94) },
      { key: 'turns', label: '保留最近轮数（1–40）', value: String(compression.keep_recent_turns ?? 4) },
      { key: 'messages', label: '保留最近消息数（至少轮数 × 2）', value: String(compression.keep_recent_messages ?? 8) },
      { key: 'llm', label: '使用模型生成摘要（true / false）', value: String(compression.use_llm_summary !== false) },
    ], values => {
      const window = numericInput(values.window, '上下文窗口');
      const ratio = numericInput(values.ratio, '触发比例');
      const turns = numericInput(values.turns, '保留轮数');
      const messages = numericInput(values.messages, '保留消息数');
      if (!Number.isInteger(window) || window < 2048 || window > 2_000_000) throw new Error('上下文窗口必须是 2048–2000000 的整数');
      if (!Number.isInteger(turns) || turns < 1 || turns > 40 || !Number.isInteger(messages) || messages < turns * 2 || messages > 100) throw new Error('保留轮数/消息数超出范围，消息数至少为轮数的两倍');
      if (!(ratio >= 0.70 && ratio <= 0.99)) throw new Error('硬压缩触发比例必须在 0.70–0.99');
      const enabled = booleanInput(values.enabled, '自动压缩');
      const llm = booleanInput(values.llm, '模型摘要');
      const mode = values.mode.trim() || 'persistent_baseline';
      if (!['persistent_baseline', 'ephemeral'].includes(mode)) throw new Error('压缩模式只能是 persistent_baseline 或 ephemeral');
      return { enabled, mode, default_context_window_tokens: window, trigger_ratio: ratio, hard_trigger_ratio: ratio, keep_recent_turns: turns, keep_recent_messages: messages, use_llm_summary: llm };
    }, ['基础策略控制自动压缩边界；保存后由 Engine 规范化阈值和保留数量。']);
  }
  editContextAdvanced(policy: any, compression: any) {
    this.contextSaveForm(policy, compression, [
      { key: 'soft', label: '软阈值（0.10–0.99）', value: String(compression.soft_trigger_ratio ?? 0.90) },
      { key: 'input', label: '摘要输入 Token 上限', value: String(compression.max_summary_input_tokens ?? 5000) },
      { key: 'inputMessages', label: '摘要输入消息上限', value: String(compression.max_summary_input_messages ?? 60) },
      { key: 'output', label: '摘要输出 Token 上限', value: String(compression.max_summary_output_tokens ?? 800) },
      { key: 'safety', label: '压缩模型安全比例（0.50–0.95）', value: String(compression.compression_model_safety_ratio ?? 0.90) },
      { key: 'latency', label: '显著延迟提示（毫秒）', value: String(compression.noticeable_latency_ms ?? 800) },
    ], values => {
      const soft = numericInput(values.soft, '软阈值');
      const input = numericInput(values.input, '摘要输入上限');
      const inputMessages = numericInput(values.inputMessages, '摘要消息上限');
      const output = numericInput(values.output, '摘要输出上限');
      const safety = numericInput(values.safety, '安全比例');
      const latency = numericInput(values.latency, '延迟提示');
      if (soft < 0.10 || soft > 0.99 || soft >= Number(compression.hard_trigger_ratio ?? compression.trigger_ratio ?? 0.94)) throw new Error('软阈值必须低于硬阈值且在 0.10–0.99');
      if (![input, inputMessages, output, latency].every(Number.isInteger) || input < 512 || inputMessages < 5 || output < 128 || latency < 50) throw new Error('高级上限必须是合法正整数');
      if (safety < 0.50 || safety > 0.95) throw new Error('压缩模型安全比例必须在 0.50–0.95');
      return { soft_trigger_ratio: soft, max_summary_input_tokens: input, max_summary_input_messages: inputMessages, max_summary_output_tokens: output, compression_model_safety_ratio: safety, noticeable_latency_ms: latency };
    }, ['高级参数影响摘要成本和延迟；范围约束与 Engine normalize_context_policy 保持一致。']);
  }
  async contextUsage() {
    const sessionId = this.client.view.sessionId;
    if (!sessionId) { this.open('当前上下文用量', ['尚未选择会话。'], [{ label: '返回上下文设置', run: () => this.contextSettings() }]); return; }
    await this.client.refreshSnapshot();
    const [telemetry, compactions] = await Promise.all([
      this.client.api('/v1/telemetry/overview?days=1').catch(() => ({})),
      this.client.api(`/v1/observability/compactions?sessionId=${encodeURIComponent(sessionId)}&limit=5`).catch(() => ({ items: [] })),
    ]);
    const governance = this.client.snapshot.contextGovernance || this.client.snapshot.snapshot?.contextGovernance || this.client.snapshot.currentRun?.contextGovernance || {};
    const window = Number(governance.context_window_tokens || governance.contextWindowTokens || 0);
    const input = Number(governance.estimated_effective_input_tokens || governance.estimatedEffectiveInputTokens || governance.estimated_input_tokens || governance.estimatedInputTokens || 0);
    const usageLine = window > 0 && input >= 0 ? `使用率：${Math.min(100, Math.round(input / window * 100))}%（${input} / ${window} tokens）` : '使用率：Engine 尚未回报';
    const lines = [
      `会话：${sessionId}`,
      usageLine,
      ...(contextFacts(this.client.snapshot.currentRun || this.client.snapshot.snapshot || this.client.snapshot, 'snapshot') || ['Engine 尚未回报本会话的上下文计量。']),
      `近 24 小时总 Token：${telemetry.stats?.recentWindowTokens ?? '未知'}（仅全局统计）`,
      `最近压缩：${(compactions.items || []).length} 条`,
    ];
    this.open('当前上下文用量', lines, [{ label: '返回上下文设置', run: () => this.contextSettings() }, { label: '刷新', run: () => this.contextUsage() }]);
  }
  async compactionHistory() {
    const sessionId = this.client.view.sessionId;
    const query = sessionId ? `?sessionId=${encodeURIComponent(sessionId)}&limit=20` : '?limit=20';
    const result = await this.client.api(`/v1/observability/compactions${query}`);
    const lines = (result.items || []).flatMap((item: any) => [
      `${item.createdAt || item.created_at || 'unknown'} · ${item.trigger_reason || item.triggerReason || 'unknown'} · ${item.summary_method || 'summary'}`,
      `  摘要 ${item.summary_tokens || 0} tokens · 节省约 ${item.estimated_saved_tokens || 0} · 覆盖 ${item.covered_message_count || 0} 条`,
    ]);
    this.open('最近压缩记录', lines.length ? lines : ['当前没有已记录的会话压缩。'], [{ label: '返回上下文设置', run: () => this.contextSettings() }, { label: '刷新', run: () => this.compactionHistory() }]);
  }
  async prepare(route: string, payload: any, readback: string) {
    const plan = await this.client.api(route, { method: 'POST', body: payload });
    if (!plan.transactionId || !plan.planDigest) throw new Error('配置事务缺少 ID 或摘要。');
    if (plan.state !== 'ready_to_commit') throw new Error(`配置尚不能提交：${plan.state || '状态待确认'}`);
    this.confirm('配置变更预览', [...describe(payload), ...describe(plan)], '提交配置', async () => {
      const committed = await this.client.api(`/v1/config-broker/transactions/${encodeURIComponent(plan.transactionId)}/commit`, { method: 'POST', body: { planDigest: plan.planDigest } });
      if (committed.state !== 'committed') throw new Error(`配置未提交：${committed.state || '结果待确认'}`);
      const saved = await this.client.api(readback);
      this.open('配置已回读', describe(saved), [{ label: '返回设置', run: () => this.settings() },
        { label: '回滚此次配置', run: () => this.confirm('回滚配置', [plan.transactionId], '执行回滚并回读', async () => { const restored = await this.client.api(`/v1/config-broker/transactions/${encodeURIComponent(plan.transactionId)}/rollback`, { method: 'POST' }); if (restored.state !== 'rolled_back') throw new Error(`回滚未完成：${restored.state || '待确认'}`); await this.readPage('回滚后配置', readback); }) }]);
    });
  }
  async brokerSettings(domain: string) {
    const [data, schema] = await Promise.all([this.client.api(`/v1/config-broker/${domain}`), this.client.api(`/v1/config-broker/${domain}/schema`)]);
    const labels: Record<string, string> = { enabled: '启用', port: '监听端口', publicBaseUrl: '手机外部地址（HTTPS）', 'node.displayName': '节点名称', 'node.advertisedBaseUrl': 'Peer 公告地址', 'relay.enabled': '启用 Relay', 'discovery.lanEnabled': '局域网发现', 'delegation.maxConcurrent': '最大并行任务数' };
    const fields = schemaFields(schema.schema || {});
    const editable = new Set(fields.map(field => field.key));
    const actions: Action[] = fields.map(({ key, definition }) => {
      const current = key.split('.').reduce((v: any, part) => v?.[part], data.settings) ?? definition.default ?? '';
      const label = labels[key] || key;
      return { label: `${label}：${typeof current === 'object' ? '详细配置' : current}`, run: () => {
        if (definition.type === 'boolean' || definition.enum) {
          this.open(label, [definition.description || '', `当前：${current}`], [{ label: '返回', run: () => this.brokerSettings(domain) },
            ...(definition.enum || [true, false]).map((value: any) => ({ label: `${typeof value === 'boolean' ? value ? '启用' : '关闭' : value}`, run: () => this.prepare(`/v1/config-broker/${domain}/prepare`, { settings: fieldPatch(key, value) }, `/v1/config-broker/${domain}`) }))]);
        } else this.form(label, [{ key: 'value', label, value: typeof current === 'object' ? JSON.stringify(current) : String(current) }], async v => {
          const type = definition.type;
          const value = type === 'integer' || type === 'number' ? numericInput(v.value, label) : type === 'array' || type === 'object' ? JSON.parse(v.value) : v.value.trim();
          await this.prepare(`/v1/config-broker/${domain}/prepare`, { settings: fieldPatch(key, value) }, `/v1/config-broker/${domain}`);
        }, [definition.description || '', ...(domain === 'client-gateway' ? ['地址影响手机连接；端口/启用状态改动需要重启 Engine 才生效。'] : [])]);
      } };
    });
    const title = domain === 'network' ? '组网配置' : '手机网关配置';
    const settings = data.settings || {};
    const summary = [
      `当前状态：${settings.enabled === false ? '已关闭' : '已启用'}`,
      ...(domain === 'client-gateway' ? [`监听端口：${settings.port || '未设置'}`, `手机地址：${settings.publicBaseUrl || '未设置（仅本机）'}`] : [`节点：${settings.node?.displayName || '未命名'}`, `Peer 地址：${settings.node?.advertisedBaseUrl || '未公告'}`]),
      `可编辑字段：${editable.size} 个 · 凭据字段由配对/Engine 身份服务管理`,
    ];
    this.open(title, summary, [
      { label: '返回设置', run: () => this.settings() },
      { label: '重新读取状态', run: () => this.brokerSettings(domain) },
      ...actions,
      { label: '查看可修改字段', run: () => this.open('配置字段', describe(schema), [{ label: '返回', run: () => this.brokerSettings(domain) }]) },
      { label: '修改配置字段', run: () => this.form('配置字段', [{ key: 'key', label: '字段名（见 Engine 字段说明）', value: '' }, { key: 'value', label: '值（true / false / 数字 / 文本）', value: '' }], async v => {
        let value: any = v.value; try { value = JSON.parse(v.value); } catch { /* plain string */ }
        const settings: any = {}; const segments = v.key.split('.'); if (segments.some(x => !x || ['__proto__', 'constructor', 'prototype'].includes(x))) throw new Error('无效字段');
        let current = settings; for (const part of segments.slice(0, -1)) current = current[part] = {}; current[segments.at(-1)!] = value;
        await this.prepare(`/v1/config-broker/${domain}/prepare`, { settings }, `/v1/config-broker/${domain}`);
      }) },
    ]);
  }
  async registry(domain: string) {
    const result = await this.client.api(`/v1/config-registry/${domain}`);
    this.open(result.title || domain, describe(result.data), [{ label: '返回设置', run: () => this.settings() },
      { label: '编辑普通配置', run: () => this.form(result.title || domain, [{ key: 'patch', label: '配置 JSON（只包含需要修改的字段）', value: '{}' }], async v => {
        const patch = JSON.parse(v.patch); if (!patch || Array.isArray(patch) || typeof patch !== 'object' || containsSecretField(patch)) throw new Error('此表单只支持非凭据配置对象');
        this.confirm('确认配置修改', describe(patch), '保存并回读', async () => { await this.client.api(`/v1/config-registry/${domain}`, { method: 'POST', body: patch }); await this.registry(domain); });
      }, ['此页调用现有配置 registry；Engine 负责验证、保存和运行时更新。']) },
    ]);
  }
  async extensions() {
    this.open('MCP / 插件', [], [{ label: '返回设置', run: () => this.settings() },
      { label: 'MCP 状态', run: () => this.readPage('MCP 状态', '/v1/mcp/status') },
      { label: '添加 MCP', run: () => this.form('添加 MCP', [{ key: 'name', label: '名称', value: '' }, { key: 'command', label: '可执行程序', value: '' }, { key: 'args', label: '参数 JSON 数组（无凭据）', value: '[]' }], async v => {
        if (/secret|password|api.?key|token|credential/i.test(v.args)) throw new Error('不可通过普通参数传递凭据');
        const args = JSON.parse(v.args); if (!Array.isArray(args) || args.some(x => typeof x !== 'string')) throw new Error('参数需要字符串数组');
        await this.prepare('/v1/config-broker/mcp/prepare', { operation: 'install', name: v.name, server: { type: 'stdio', command: v.command, args } }, '/v1/mcp/status');
      }) },
      { label: '移除 MCP', run: () => this.form('移除 MCP', [{ key: 'name', label: '名称', value: '' }], async v => this.prepare('/v1/config-broker/mcp/prepare', { operation: 'remove', name: v.name }, '/v1/mcp/status')) },
      { label: '插件安装与管理', run: () => plugins(this) },
    ]);
  }
  async connections() {
    this.open('连接', ['手机与组网使用各自独立的配对和撤销边界。'], [
      { label: '返回对话', run: () => this.close(true) }, { label: '手机', run: () => this.phones() },
      { label: '组网 / Peer', run: () => this.peers() }, { label: '入口 / 可达性', run: () => this.readPage('连接入口', '/v1/client-identity/link-manifest') },
    ]);
  }
  async phones() {
    const owner = await this.client.api('/v1/client-identity/owner');
    if (!owner.initialized) {
      this.open('首次配置', ['创建本机所有者后，可在设置中连接模型、选择工作区，再添加手机。'], [
        { label: '返回', run: () => this.close(true) },
        { label: '初始化本机 owner', run: async () => { await this.client.api('/v1/client-identity/bootstrap', { method: 'POST', body: { login: 'owner', name: 'Owner' } }); await this.client.initialize(); await this.settings(); } },
      ]); return;
    }
    const [devices, manifest] = await Promise.all([this.client.api('/v1/client-identity/devices'), this.client.api('/v1/client-identity/link-manifest')]);
    const pairing = manifest.pairing || {};
    this.open('手机连接', [...describe(devices), `配对地址：${pairing.baseUrl || '尚未配置'}`, pairing.available ? '地址来自实例配置；网络可达性尚未验证。' : `暂不可配对：${pairing.reason || '请先配置手机网关'}`], [{ label: '返回连接', run: () => this.connections() },
      { label: '初始化本机 owner', run: () => this.confirm('初始化本机 owner', ['首次配置时创建本机所有者。已有 owner 不会替换。'], '初始化', async () => { const owner = await this.client.api('/v1/client-identity/owner'); if (!owner.initialized) await this.client.api('/v1/client-identity/bootstrap', { method: 'POST', body: { login: 'owner', name: 'Owner' } }); await this.phones(); }) },
      { label: '手机网关设置', run: () => this.brokerSettings('client-gateway') },
      { label: '添加手机', disabled: !pairing.available, run: () => this.form('添加手机', [{ key: 'deviceName', label: '设备名称', value: '' }], async v => {
        const ticket = await this.client.api('/v1/client-identity/pairing-ticket', { method: 'POST', body: { ...v, ttlMs: 300000 } });
        this.ticketId = ticket.pairingId || ticket.ticketId || '';
        if (!this.ticketId || !ticket.pairingUri || !ticket.pairingCode) throw new Error('配对票据响应不完整');
        this.open('手机配对', [`有效期：${ticket.expiresAt || '5 分钟'}`, `配对链接：${ticket.pairingUri}`, `配对码：${ticket.pairingCode}`, '关闭页面会撤销尚未使用的票据。'], [{ label: '关闭配对页', run: () => this.close(true) }]);
      }) },
      ...listOf(devices).map((device: any) => ({ label: `撤销 ${device.deviceName || device.name || idOf(device)}`, run: () => this.confirm('撤销手机', describe(device), '撤销所选设备', async () => { await this.client.api(`/v1/client-identity/devices/${encodeURIComponent(idOf(device))}`, { method: 'DELETE' }); await this.phones(); }) })),
    ]);
  }
  async peers() {
    const [links, status, relay] = await Promise.all([this.client.api('/v1/network-supervisor/neighbors/links'), this.client.api('/v1/network-supervisor/status'), this.client.api('/v1/network-supervisor/relay/status')]);
    this.open('组网 / Peer', [...describe(status), ...describe(relay), ...describe(links)], [
      { label: '返回连接', run: () => this.connections() },
      { label: '创建 Peer 邀请', run: () => createPeerInvitation(this) },
      { label: '接受 Peer 邀请 / 配对码', run: () => consumePeerInvitation(this) },
      { label: '远端任务', run: () => this.readPage('远端任务', '/v1/network-supervisor/neighbors/tasks?limit=30') },
      ...listOf(links).map((link: any) => ({ label: `撤销连接 ${link.nickname || link.peerId || idOf(link)}`, run: () => this.confirm('撤销 Peer', describe(link), '撤销所选 Peer', async () => { await this.client.api(`/v1/network-supervisor/neighbors/${encodeURIComponent(link.linkId || idOf(link))}`, { method: 'DELETE' }); await this.peers(); }) })),
    ]);
  }
  attachments() {
    this.open('附件 / 产物', [...this.client.draft.attachments.map(x => `待发送来源：${x.name} · ${x.size} 字节`), ...this.client.outputs.map(x => `生成产物：${x.name}\n位置：${x.path || x.artifactId}`)], [
      { label: '返回对话', run: () => this.close(true) },
      { label: '添加附件', run: () => this.form('添加附件', [{ key: 'path', label: '文件绝对路径（上传到当前工作区）', value: '' }], async v => { await this.client.attachFile(v.path); this.attachments(); }) },
      ...this.client.draft.attachments.map((x, i) => ({ label: `从草稿移除 ${x.name}`, run: () => { this.client.draft.attachments.splice(i, 1); this.client.save(); this.attachments(); } })),
    ]);
  }
  help() {
    this.open('帮助 / 首次安装', [
      'V8OS · 对话优先的本机终端', '对话中 Ctrl+P 或空输入 /：命令候选；↑↓ 选择、Tab 补全、Enter 执行、Esc 返回原草稿。',
      '候选中再次 Ctrl+P 打开完整操作菜单；页面内 Ctrl+P 仍打开完整菜单。',
      'Enter 发送；F8 多行开关；F9 发送；Alt+Enter 换行。',
      'Ctrl+B 会话；Ctrl+T 任务；Ctrl+N 新会话；F2 待处理；F3 快速配置/设置；F4 连接。',
      'PageUp 暂停跟随 / 读历史；PageDown 向下；菜单“回到底部”恢复。',
      'Ctrl+C 关闭页面或清空输入；Ctrl+X 使用外部编辑器；Ctrl+U/K/W 删除行首/行尾/前一个词；Ctrl+Z 撤销清空；Ctrl+D 空输入退出。',
      '退出终端不停止 Engine / Phone / Peer；停止任务须选菜单“停止当前任务”。',
      '粘贴不会执行命令；大段粘贴使用 F9 或菜单明确发送。',
      '多行输入支持 ↑↓ 按显示列移动；操作菜单可搜索。外部编辑器使用 VISUAL / EDITOR（如 nano 或 code --wait），返回后须明确发送。',
      '@文件路径会登记为当前工作区来源；支持 @"含空格的文件名"。session:/mcp:/ext: 和 URL 保留为文本引用，未确认路径不会自动读取。',
      'NO_COLOR / --no-color 无色；--screen-reader 线性阅读与编号菜单；Ctrl+P → language 切换语言。',
      'Node.js 22+。安装：npm install -g，后接下载的 .tgz 文件路径。',
      '没有 Engine：从官方 Release 下载 server 包并解压，执行 ./install.sh；不需要 Admin。',
      '首次运行 v8os 自动准备 Engine；v8os start 仅启动后台服务。F3 配置模型与工作区。',
      '无法响应时可重新连接 SSH 后运行 reset；SIGKILL/掉电无法执行终端恢复。',
    ], [{ label: '返回对话', run: () => this.close(true) }]);
  }
  commands(): Action[] { return [
    { label: '发送', run: () => this.submit() },
    { command: 'model', tier: 'daily', description: '模型管理中心、向导式注册与角色选项卡分配', label: '模型管理 /model', navigation: true, run: () => this.modelsHub() },
    { command: 'resume', tier: 'daily', description: '选择并恢复历史会话', label: '恢复会话 /resume', navigation: true, run: () => this.sessions() },
    { command: 'multiline', tier: 'daily', description: '切换单行与多行输入', label: '切换多行', navigation: true, run: () => { this.multiline = !this.multiline; this.page = null; } },
    { command: 'new', tier: 'daily', description: '保留当前草稿，进入新会话', label: '新建会话 /new', run: () => this.newSession() },
    { command: 'clear', tier: 'daily', description: '清空当前上下文，进入全新会话', label: '清空会话 /clear', run: () => this.newSession() },
    { command: 'workspace', tier: 'daily', description: '选择并信任项目工作区', label: '工作区管理 /workspace', navigation: true, run: () => this.workspaceManager() },
    { command: 'mcp', tier: 'daily', description: '管理 MCP 扩展服务与工具', label: 'MCP 扩展 /mcp', navigation: true, run: () => this.extensions() },
    { command: 'doctor', tier: 'daily', description: '检查环境与服务健康状态', label: '系统诊断 /doctor', navigation: true, run: () => this.readPage('系统诊断', '/v1/client-identity/instance') },
    { command: 'settings', tier: 'daily', description: '模型、工作区与预算配置', label: '设置 /settings', navigation: true, run: () => this.settings() },
    { command: 'approval', tier: 'daily', description: '显式选择后续消息审批模式', label: '审批模式 /approval', run: () => this.open('审批模式', ['默认沿用 Engine / 会话现有设置。显式选择仅作用于后续发送。', `当前选择：${this.client.approvalMode || '沿用 Engine'}`], [
      { label: '返回', run: () => this.close(true) },
      ...([['', '沿用 Engine'], ['manual', '逐项审批'], ['reduced', '减少审批'], ['minimal', '免审（保留系统内核与凭据边界）']] as const).map(([mode, label]) => ({ label, run: () => { this.client.approvalMode = mode; this.client.notice = `后续消息审批模式：${label}`; this.page = null; } })),
    ]) },
    { command: 'setup', tier: 'daily', description: '首次配置本机身份、模型与工作区', label: '快速配置 /setup', navigation: true, run: () => this.setup() },
    { command: 'sessions', tier: 'daily', description: '搜索并恢复已有会话', label: '会话列表 /sessions', navigation: true, run: () => this.sessions() },
    { command: 'task', tier: 'context', description: '查看当前任务与运行状态', label: '任务详情', navigation: true, run: () => this.details() },
    { command: 'inbox', tier: 'daily', description: '查看审批与待回答问题', label: '待处理 /inbox', navigation: true, run: () => this.inbox() },
    { command: 'attach', tier: 'context', description: '管理附件和生成产物', label: '附件 / 产物', navigation: true, run: () => this.attachments() },
    { command: 'connect', tier: 'context', description: '管理 Phone 与 Peer 连接', label: '连接', navigation: true, run: () => this.connections() },
    { command: 'stop', tier: 'context', description: '查看目标，再确认停止', label: '停止当前任务', disabled: !this.client.active, run: () => this.stopRun() },
    { command: 'retry', tier: 'context', description: '核对 Engine 能力并确认重试', label: '重试当前任务', run: () => this.retryRun() },
    { command: 'editor', tier: 'advanced', description: '使用 VISUAL / EDITOR 编辑草稿', label: '外部编辑器 /editor', run: () => this.externalEditor() },
    { command: 'reconcile', tier: 'advanced', description: '只回读，避免重复发送', label: '核对发送结果', run: async () => { await this.client.tick(); this.input = editor(this.client.draft.text); this.client.notice = this.client.draft.unknown ? 'Engine 尚未证明受理；仍禁止自动重发，可查看会话或保留草稿等待恢复。' : '已回读会话状态。'; await this.close(true); } },
    { command: 'history', tier: 'view', description: '读取更早的会话消息', label: '加载更早历史', run: async () => { await this.client.older(); this.following = false; this.anchor = 0; await this.close(true); } },
    { command: 'bottom', tier: 'view', description: '恢复跟随最新输出', label: '回到底部', navigation: true, run: () => { this.following = true; this.unread = 0; this.page = null; } },
    { command: 'sidebar', tier: 'view', description: '显示或隐藏会话概览', label: '切换会话侧栏', navigation: true, run: () => { this.client.view.sidebar = !this.client.view.sidebar; this.client.save(); this.page = null; } },
    { command: 'details', tier: 'view', description: '显示或隐藏任务概览', label: '切换任务侧栏', navigation: true, run: () => { this.client.view.detail = !this.client.view.detail; this.client.save(); this.page = null; } },
    { command: 'exit', tier: 'view', description: '退出终端并释放前台 Engine', label: '退出终端', navigation: true, run: () => { this.invalidateNavigation(); this.onExit(); } },
    { command: 'language', tier: 'advanced', description: '切换并持久化 TUI 界面语言', label: '语言 / Language', navigation: true, run: () => this.open('语言 / Language', ['选择后立即保存到本机 TUI 视图；不会修改 Engine 或其他客户端。'], [
      { label: '中文（简体）', run: () => { this.setLocale('zh-CN'); this.page = null; } },
      { label: 'English', run: () => { this.setLocale('en-US'); this.page = null; } },
      { label: '返回对话', run: () => this.close(true) },
    ]) },
    { command: 'theme', tier: 'advanced', description: '切换本地终端视觉主题', label: '视觉主题 / Theme', navigation: true, run: () => this.open('视觉主题 / Theme', ['主题只影响当前 TUI；不会修改 Engine、Web 或 Phone 配置。NO_COLOR 会强制无色模式。', `当前：${this.client.view.theme}`], [
      ...themeNames.map((theme: ThemeName) => ({ label: `${theme}${theme === this.client.view.theme ? ' · 当前' : ''}`, run: () => { this.client.view.theme = theme; this.client.save(true); this.client.notice = `视觉主题已切换：${theme}`; this.page = null; this.changed(); } })),
      { label: '返回对话', run: () => this.close(true) },
    ]) },
    { command: 'help', tier: 'daily', description: '快捷键与首次安装指导', label: '帮助', navigation: true, run: () => this.help() },
  ]; }
  palette(query = '', preserveEditor = false) {
    this.paletteQuery = query;
    if (!preserveEditor) this.paletteEditor = editor(query);
    const all = this.commands(), matches = all.filter(x => `${x.command || ''} ${x.label}`.toLowerCase().includes(query.toLowerCase()));
    this.open('操作菜单', [`搜索：${query || '（输入关键词）'}`, `匹配 ${matches.length} / ${all.length}`, matches.length ? '输入搜索 · 上下选择 · Enter 执行 · Esc 返回' : '没有匹配的操作；退格修改关键词，或 Esc 返回。'], matches);
  }
  async handle(event: Input) {
    const { key, text = '' } = event;
    if (key === 'ctrl-p') { if (this.page) this.palette(); else this.suggest(); return; }
    if (key === 'backtab' && !this.page && !this.suggestions) { this.client.cycleApprovalMode(); return; }
    if (key === 'escape' || key === 'ctrl-c' && this.page) { await this.close(); return; }
    if (key === 'f1') { this.help(); return; }
    if (this.page) {
      const page = this.page;
      if (page.fields) {
        const index = page.fieldIndex || 0, fields = page.fields;
        if (key === 'tab' || key === 'backtab') {
          fields[index].value = this.formEditor.text;
          page.fieldIndex = (index + (key === 'tab' ? 1 : fields.length - 1)) % fields.length; this.formEditor = editor(fields[page.fieldIndex].value);
        } else if (key === 'f9') {
          fields[index].value = this.formEditor.text;
          const values = Object.fromEntries(fields.map(f => [f.key, f.value]));
          try { await page.onSave?.(values); }
          finally { for (const f of fields) if (f.secret) { f.value = ''; values[f.key] = ''; } if (page.sensitive && this.page === page) this.formEditor = editor(); }
        } else {
          this.formEditor = edit(this.formEditor, key === 'text' ? 'insert' : key === 'enter' || key === 'newline' ? 'insert' : key, key === 'enter' || key === 'newline' ? '\n' : text, this.editorWidth, fields[index].secret);
          fields[index].value = this.formEditor.text;
        }
        this.changed(); return;
      }
      if (page.title === '操作菜单' && ['text', 'paste', 'backspace', 'delete', 'left', 'right', 'home', 'end', 'ctrl-u', 'ctrl-k', 'ctrl-w'].includes(key)) {
        if (key === 'backspace' && !this.paletteEditor.text) { await this.close(); return; }
        const action = key === 'text' || key === 'paste' ? 'insert' : key;
        this.paletteEditor = edit(this.paletteEditor, action, key === 'text' || key === 'paste' ? text : '', this.editorWidth);
        this.paletteQuery = this.paletteEditor.text;
        this.palette(this.paletteQuery, true);
        return;
      }
      if (key === 'left' && page.tabs && page.onTabChange) {
        const next = ((page.activeTab ?? 0) - 1 + page.tabs.length) % page.tabs.length;
        page.activeTab = next;
        await page.onTabChange(next);
        this.changed();
        return;
      }
      if (key === 'right' && page.tabs && page.onTabChange) {
        const next = ((page.activeTab ?? 0) + 1) % page.tabs.length;
        page.activeTab = next;
        await page.onTabChange(next);
        this.changed();
        return;
      }
      if (key === 'up' || key === 'backtab') page.selected = Math.max(0, page.selected - 1);
      else if (key === 'down' || key === 'tab') page.selected = Math.max(0, Math.min(page.actions.length - 1, page.selected + 1));
      else if (key === 'pageup') page.offset = Math.max(0, page.offset - 6);
      else if (key === 'pagedown') page.offset += 6;
      else if (key === 'enter') { const action = page.actions[page.selected]; if (action && !action.disabled) await action.run(); }
      this.changed(); return;
    }
    const pendingApproval = this.client.pendingApproval;
    if (!this.page && !this.suggestions && !this.mentionSuggestions && pendingApproval && !this.input.text.trim()) {
      if (key === 'text' && (text === 'y' || text === 'Y')) {
        await this.client.decide(pendingApproval, 'approve');
        this.client.notice = `已批准本次执行：${pendingApproval.tool || pendingApproval.name || '工具操作'}`;
        this.changed(); return;
      }
      if (key === 'text' && (text === 'n' || text === 'N')) {
        await this.client.decide(pendingApproval, 'reject');
        this.client.notice = `已拒绝本次执行：${pendingApproval.tool || pendingApproval.name || '工具操作'}`;
        this.changed(); return;
      }
      if (key === 'text' && (text === 'a' || text === 'A')) {
        await this.client.decide(pendingApproval, 'approve');
        this.client.approvalMode = 'minimal';
        this.client.notice = `已批准并放行本次会话后续同类操作：${pendingApproval.tool || pendingApproval.name || '工具操作'}`;
        this.changed(); return;
      }
      if (key === 'text' && (text === 'd' || text === 'D')) {
        await this.inbox();
        return;
      }
    }
    if (key === 'f2') await this.inbox();
    else if (key === 'f3') await (this.client.ownerReady && this.client.workspace ? this.settings() : this.setup());
    else if (key === 'f4') await this.connections();
    else if (key === 'ctrl-b') await this.sessions();
    else if (key === 'ctrl-t') await this.details();
    else if (key === 'ctrl-n') await this.newSession();
    else if (key === 'f8') this.multiline = !this.multiline;
    else if (key === 'f9') await this.submit();
    else if (key === 'ctrl-c') {
      const now = Date.now();
      if (now - this.lastIdleInterrupt <= 2000) {
        this.onExit();
        return;
      }
      this.lastIdleInterrupt = now;
      if (this.client.active) {
        const runId = String(this.client.run.id || this.client.run.runId || this.client.run.run_id || '');
        if (runId) await this.execute(() => this.client.interrupt(runId), false);
        this.client.notice = '已请求停止当前任务；2 秒内再次按 Ctrl+C 退出终端。';
      } else if (this.input.text && this.input.text.trim().length > 0) {
        this.undo = this.input.text; this.input = editor(); this.client.setDraft('');
        this.client.notice = '输入已清空；2 秒内再次按 Ctrl+C 退出终端。';
      } else {
        this.client.notice = '2 秒内再次按 Ctrl+C 或按 Ctrl+D 退出终端；前台 Engine 会随终端释放。';
      }
    }
    else if (key === 'ctrl-x') await this.externalEditor();
    else if (key === 'ctrl-z') { if (this.undo) { this.input = editor(this.undo); this.undo = ''; this.client.setDraft(this.input.text); } }
    else if (key === 'ctrl-d' && !this.input.text.trim()) this.onExit();
    else if (key === 'pageup') { this.following = false; this.scrollDelta -= 6; }
    else if (key === 'pagedown') { this.following = false; this.scrollDelta += 6; }
    else if (key === 'enter') {
      if (this.commandReturnGuard) this.client.notice = '已返回草稿；编辑后按 Enter，或 F9 明确发送。';
      else if (this.multiline) { this.input = edit(this.input, 'insert', '\n'); this.client.setDraft(this.input.text); }
      else if (this.input.pasted) this.client.notice = '粘贴内容已保留，请按 F9 或菜单“发送”确认发送。';
      else await this.submit();
    } else if (key === 'text' && text === '/' && !this.input.text) this.suggest();
    else {
      const previousText = this.input.text, previousCursor = this.input.cursor;
      if (['text', 'backspace', 'delete', 'newline'].includes(key)) this.commandReturnGuard = false;
      this.input = edit(this.input, key === 'text' ? 'insert' : key === 'newline' ? 'insert' : key === 'ctrl-d' ? 'delete' : key, key === 'newline' ? '\n' : text, this.editorWidth);
      this.client.setDraft(this.input.text);
      if (!this.mentionSuggestions && key === 'text' && text.includes('@')) this.openMentionAtCursor(previousText, previousCursor);
      else if (this.mentionSuggestions) this.updateMentionQuery();
    }
    this.changed();
  }
}
