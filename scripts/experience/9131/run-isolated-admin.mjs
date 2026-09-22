// Test-only launcher: real Admin source, webpack, owned port and managed test auth.
import path from 'node:path';
import fs from 'node:fs';
import os from 'node:os';
import { spawn } from 'node:child_process';
import { ensureManagedAuthSecret } from '../../ensure-admin-auth-secret.mjs';
import { managedAuthEnvironment } from '../../run-next-with-managed-auth.mjs';

const repo = path.resolve(import.meta.dirname, '../../..');
const state = path.resolve(process.env.V8_AGENT_OS_HOME || '');
const allowed = path.join(repo, 'tmp') + path.sep;
if (!state.startsWith(allowed)) throw new Error('isolated state under checkout/tmp is required');
if (path.resolve(os.homedir()) !== path.dirname(state)) throw new Error('os.homedir and V8_AGENT_OS_HOME must share the isolated home');
const app = path.join(repo, 'apps/v8-agent-os-web');
for (const name of ['.env', '.env.local', '.env.development.local']) {
  if (fs.existsSync(path.join(app, name))) throw new Error('test checkout must not contain local environment files');
}
const { secret } = ensureManagedAuthSecret({ stateRoot: state });
const child = spawn(process.execPath, [path.join(app, 'node_modules/next/dist/bin/next'), 'dev', '--webpack', '--hostname', '127.0.0.1', '--port', '22824'], {
  cwd: app, windowsHide: true, stdio: 'inherit',
  env: { ...managedAuthEnvironment('22824'), AUTH_SECRET: secret, NEXTAUTH_SECRET: secret, AUTH_TRUST_HOST: 'true', NEXT_TELEMETRY_DISABLED: '1' },
});
for (const signal of ['SIGINT', 'SIGTERM']) process.on(signal, () => child.kill(signal));
child.on('exit', code => process.exit(code ?? 1));
