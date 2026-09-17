import { spawn } from 'node:child_process';
import { mkdtemp, chmod, writeFile, readFile, rm } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';

/** Parse an executable and literal arguments. No expansion, substitution or shell. */
export function editorArgv(value: string): string[] {
  if (!value.trim()) throw new Error('请先设置 VISUAL 或 EDITOR，例如 EDITOR="nano" 或 VISUAL="code --wait"。');
  if (/[\x00-\x1f\x7f]/.test(value)) throw new Error('编辑器配置不能包含控制字符。');
  const argv: string[] = []; let word = '', quote = '', started = false;
  for (let i = 0; i < value.length; i++) {
    const c = value[i];
    if (quote) {
      if (c === quote) quote = '';
      else if (c === '\\' && quote === '"' && ['"', '\\'].includes(value[i + 1])) word += value[++i];
      else word += c;
    } else if (c === '"' || c === "'") { quote = c; started = true; }
    else if (/\s/.test(c)) { if (started) { argv.push(word); word = ''; started = false; } }
    else if (';&|<>`'.includes(c) || c === '$' && value[i + 1] === '(') throw new Error('编辑器配置只接受程序与参数，不支持 shell 表达式。');
    else if (c === '\\' && /[\s"'\\]/.test(value[i + 1] || '')) { word += value[++i]; started = true; }
    else { word += c; started = true; }
  }
  if (quote) throw new Error('编辑器配置的引号未闭合。');
  if (started) argv.push(word);
  if (!argv[0] || /^(?:sh|bash|dash|zsh|fish|cmd|powershell|pwsh)(?:\.exe)?$/i.test(path.basename(argv[0]))) throw new Error('请配置编辑器程序，不要配置 shell 命令。');
  return argv;
}

export type EditedDraft = { text: string; file: string; remove: () => Promise<void> };
export async function editExternal(text: string, options: { command?: string; signal?: AbortSignal; env?: NodeJS.ProcessEnv } = {}): Promise<EditedDraft> {
  const argv = editorArgv(options.command || process.env.VISUAL || process.env.EDITOR || '');
  const directory = await mkdtemp(path.join(os.tmpdir(), 'v8-tui-editor-'));
  await chmod(directory, 0o700);
  const file = path.join(directory, 'draft.txt');
  await writeFile(file, text, { encoding: 'utf8', mode: 0o600, flag: 'wx' });
  try {
    await new Promise<void>((resolve, reject) => {
      const child = spawn(argv[0], [...argv.slice(1), file], { shell: false, stdio: 'inherit', env: options.env || process.env, signal: options.signal });
      let failure: Error | undefined, killTimer: NodeJS.Timeout | undefined;
      const forceStop = () => { killTimer = setTimeout(() => child.kill('SIGKILL'), 1500); killTimer.unref(); };
      options.signal?.addEventListener('abort', forceStop, { once: true });
      child.once('error', error => { failure = error; });
      // Abort emits error before the child exits. Restore TTY only after close.
      child.once('close', (code, signal) => {
        clearTimeout(killTimer); options.signal?.removeEventListener('abort', forceStop);
        if (failure) reject(failure);
        else if (code === 0) resolve();
        else reject(new Error(`编辑器退出 ${signal || code}`));
      });
    });
    return { text: await readFile(file, 'utf8'), file, remove: () => rm(directory, { recursive: true, force: true }) };
  } catch (error: any) {
    throw new Error(`编辑器未完成：${error.message}。草稿副本保留在 ${file}`);
  }
}
