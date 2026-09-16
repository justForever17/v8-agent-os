import { mkdirSync, readFileSync, writeFileSync, renameSync, chmodSync } from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import { randomUUID } from 'node:crypto';
export type Draft = { text: string; attachments: any[]; unknown?: { clientMessageId: string; startedAt: string } };
export type ViewState = { sessionId: string; drafts: Record<string, Draft>; sidebar: boolean; detail: boolean; workspace: string };
export const defaultView = (): ViewState => ({ sessionId: '', drafts: {}, sidebar: false, detail: false, workspace: '' });
export class ViewStore {
  readonly file: string;
  constructor(root = process.env.V8_AGENT_OS_HOME || path.join(os.homedir(), '.v8-agent-os')) { this.file = path.join(root, 'runtime', 'tui', 'view.json'); }
  read(): ViewState {
    try { const value = JSON.parse(readFileSync(this.file, 'utf8')); return { ...defaultView(), ...value }; }
    catch (error: any) { if (error.code === 'ENOENT') return defaultView(); throw new Error('终端草稿文件无法读取；请保留文件并检查权限/格式。'); }
  }
  write(value: ViewState) {
    mkdirSync(path.dirname(this.file), { recursive: true, mode: 0o700 });
    const temporary = this.file + `.${randomUUID()}.tmp`;
    writeFileSync(temporary, JSON.stringify(value), { mode: 0o600, flag: 'wx' });
    renameSync(temporary, this.file); chmodSync(this.file, 0o600);
  }
}
