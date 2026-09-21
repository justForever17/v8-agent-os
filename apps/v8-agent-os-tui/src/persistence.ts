import { mkdirSync, readFileSync, writeFileSync, renameSync, chmodSync } from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import { createHash, randomUUID } from 'node:crypto';
import { normalizeLocale, type Locale } from './locale.js';
export type Draft = { text: string; attachments: any[]; nextText?: string; unknown?: { clientMessageId: string; startedAt: string } };
export type ScrollAnchor = { messageId: string; offset: number; lineBreaks?: number; following: boolean };
export type ViewState = { instanceId: string; sessionId: string; drafts: Record<string, Draft>; sidebar: boolean; detail: boolean; workspace: string; scroll: Record<string, ScrollAnchor>; retryRequests: Record<string, { requestedAt: string; nextRunId?: string }>; locale: Locale };
export const defaultView = (): ViewState => ({ instanceId: '', sessionId: '', drafts: {}, sidebar: false, detail: false, workspace: '', scroll: {}, retryRequests: {}, locale: normalizeLocale(process.env.V8OS_LANG || process.env.LC_ALL || process.env.LANG) });
export class ViewStore {
  private instanceId = '';
  constructor(private readonly root = process.env.V8_AGENT_OS_HOME || path.join(os.homedir(), '.v8-agent-os'), instanceId = '') { this.instanceId = instanceId; }
  get file() { return path.join(this.root, 'runtime', 'tui', this.instanceId ? `view-${createHash('sha256').update(this.instanceId).digest('hex')}.json` : 'view.json'); }
  bind(instanceId: string): ViewState {
    if (!instanceId) throw new Error('Engine 未返回实例标识，不能绑定草稿。');
    this.instanceId = instanceId;
    return this.read();
  }
  read(): ViewState {
    try {
      const value = JSON.parse(readFileSync(this.file, 'utf8'));
      if (this.instanceId && value.instanceId !== this.instanceId) throw new Error('instance_mismatch');
      return { ...defaultView(), ...value, locale: normalizeLocale(value.locale) };
    }
    catch (error: any) { if (error.code === 'ENOENT') return { ...defaultView(), instanceId: this.instanceId }; throw new Error('终端草稿文件无法读取或实例不匹配；请保留文件并检查权限/格式。'); }
  }
  write(value: ViewState) {
    if ((value.instanceId || '') !== this.instanceId) throw new Error('实例已变化，草稿未写入其他实例。');
    mkdirSync(path.dirname(this.file), { recursive: true, mode: 0o700 });
    const temporary = this.file + `.${randomUUID()}.tmp`;
    writeFileSync(temporary, JSON.stringify(value), { mode: 0o600, flag: 'wx' });
    renameSync(temporary, this.file); chmodSync(this.file, 0o600);
  }
}
