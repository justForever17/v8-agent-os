import { spawn } from 'node:child_process';
import path from 'node:path';
import { access } from 'node:fs/promises';
import { createServerServiceManager, inspectServerBundle } from '../../v8-agent-os-cli/src/server_service.mjs';
import { STATE_ROOT } from '../../v8-agent-os-cli/src/paths.mjs';

export const shellQuote = (value: string) => `'${value.replace(/'/g, `'"'"'`)}'`;
export type PackEntry = { bundleRoot: string; launcher: string; stateRoot: string };
export async function packEntry(bundleRoot = ''): Promise<PackEntry> {
  if (process.platform !== 'linux') throw new Error('能力包安装入口位于 Linux server 发行包；请在服务器终端操作。');
  if (!bundleRoot) {
    const state = await createServerServiceManager().perform('status');
    bundleRoot = String(state?.bundleRoot || '');
    if (!bundleRoot) throw new Error('尚未登记 server 服务，请选择已安装的 server 包目录。');
  }
  const bundle = inspectServerBundle(bundleRoot);
  const launcher = path.join(bundle.bundleRoot, 'v8os'); await access(launcher);
  return { bundleRoot: bundle.bundleRoot, launcher, stateRoot: STATE_ROOT };
}
export function packCommands(entry: PackEntry, packId: string) {
  if (!/^[a-z][a-z0-9_]*$/.test(packId)) throw new Error('无效能力包 ID');
  const command = `V8_AGENT_OS_HOME=${shellQuote(entry.stateRoot)} ${shellQuote(entry.launcher)} packs`;
  return { preview: `${command} install ${shellQuote(packId)} --dry-run`, install: `${command} install ${shellQuote(packId)}`, status: `${command} list` };
}
export async function previewPack(entry: PackEntry, packId: string) {
  packCommands(entry, packId);
  const { stdout, stderr, code } = await new Promise<{ stdout: string; stderr: string; code: number | null }>((resolve, reject) => {
    const child = spawn('bash', [entry.launcher, 'packs', 'install', packId, '--dry-run'], { shell: false, cwd: entry.bundleRoot, env: { ...process.env, V8_AGENT_OS_HOME: entry.stateRoot }, stdio: ['ignore', 'pipe', 'pipe'] });
    let stdout = '', stderr = '';
    const timeout = setTimeout(() => { child.kill(); reject(new Error('能力包预览超时，未发起安装。')); }, 30000);
    child.stdout.setEncoding('utf8'); child.stderr.setEncoding('utf8');
    child.stdout.on('data', chunk => { stdout += chunk; }); child.stderr.on('data', chunk => { stderr += chunk; });
    child.once('error', error => { clearTimeout(timeout); reject(error); });
    child.once('exit', code => { clearTimeout(timeout); resolve({ stdout, stderr, code }); });
  });
  if (code !== 0) throw new Error(stderr.trim() || `安装预览失败（${code}）`);
  const result = JSON.parse(stdout);
  if (result.status !== 'dry_run' || result.packId !== packId) throw new Error('CLI 未返回对应能力包的预览，未发起安装。');
  return result;
}
