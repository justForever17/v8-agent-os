const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

function readLocalEngineSecret(environment = process.env, homeDirectory = os.homedir()) {
  const stateRoot = environment.V8_AGENT_OS_HOME || path.join(homeDirectory, '.v8-agent-os');
  const config = JSON.parse(fs.readFileSync(path.join(stateRoot, 'config.json'), 'utf8'));
  const secret = String(config?.systemBase?.bridge?.internalSecret || '').trim();
  if (!secret) throw new Error('local_engine_credential_unavailable');
  return secret;
}

async function ensureLocalEngineIdentity(fetchImpl, engineBaseUrl, readSecret = readLocalEngineSecret) {
  const origin = new URL(engineBaseUrl);
  if (origin.protocol !== 'http:' || !['127.0.0.1', 'localhost', '[::1]'].includes(origin.hostname)
      || !['', '/', '/v1'].includes(origin.pathname)
      || origin.username || origin.password || origin.search || origin.hash) throw new Error('local_engine_origin_required');
  const response = await fetchImpl(`${origin.origin}/v1/client-identity/local-session`, {
    method: 'POST', redirect: 'error', signal: AbortSignal.timeout(10_000),
    headers: { 'Content-Type': 'application/json', 'x-v8-agent-os-secret': readSecret() },
    body: JSON.stringify({ surface: 'shell', deviceName: 'V8OS Desktop' }),
  });
  const payload = await response.json();
  if (!response.ok || !payload?.user?.id || !payload?.instanceId) throw new Error('local_engine_identity_unavailable');
  return { instanceId: payload.instanceId, userId: payload.user.id };
}

module.exports = { readLocalEngineSecret, ensureLocalEngineIdentity };
