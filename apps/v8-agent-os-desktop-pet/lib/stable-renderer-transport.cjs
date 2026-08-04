const STABLE_RENDERER_SCHEME = 'v8-desktop';
const STABLE_RENDERER_HOST = 'app';
const STABLE_RENDERER_ENTRY_URL = `${STABLE_RENDERER_SCHEME}://${STABLE_RENDERER_HOST}/`;

function registerStableRendererScheme(protocol) {
  protocol.registerSchemesAsPrivileged([{
    scheme: STABLE_RENDERER_SCHEME,
    privileges: {
      standard: true,
      secure: true,
      supportFetchAPI: true,
      stream: true,
      corsEnabled: true,
    },
  }]);
}

function parseUrl(value, label) {
  try {
    return new URL(String(value || ''));
  } catch {
    throw new Error(`Invalid ${label}`);
  }
}

function normalizeLoopbackBaseUrl(value) {
  const url = parseUrl(value, 'desktop pet loopback transport URL');
  const port = Number(url.port);
  if (
    url.protocol !== 'http:'
    || url.hostname !== '127.0.0.1'
    || url.username
    || url.password
    || url.pathname !== '/'
    || url.search
    || url.hash
    || !Number.isInteger(port)
    || port < 1
    || port > 65535
  ) {
    throw new Error('Desktop pet transport must be an exact loopback HTTP origin');
  }
  return { baseUrl: url.origin, port };
}

function createVerifiedTransport({ baseUrl, instanceId, serverPid }) {
  const normalized = normalizeLoopbackBaseUrl(baseUrl);
  const normalizedInstanceId = String(instanceId || '').trim();
  const normalizedServerPid = Number(serverPid);
  if (!normalizedInstanceId || !Number.isInteger(normalizedServerPid) || normalizedServerPid <= 0) {
    throw new Error('Desktop pet transport identity is incomplete');
  }
  return Object.freeze({
    mode: 'production',
    localBaseUrl: normalized.baseUrl,
    localPort: normalized.port,
    engineWebSocketUrl: `ws://127.0.0.1:${normalized.port}/api/v8/engine-ws`,
    instanceId: normalizedInstanceId,
    serverPid: normalizedServerPid,
  });
}

function createDevelopmentTransport(devServerUrl) {
  const url = parseUrl(devServerUrl, 'desktop pet development server URL');
  if (
    !['http:', 'https:'].includes(url.protocol)
    || !url.hostname
    || url.username
    || url.password
  ) {
    throw new Error('Desktop pet development server must be an explicit HTTP(S) URL');
  }
  const entryUrl = url.toString();
  const wsProtocol = url.protocol === 'https:' ? 'wss:' : 'ws:';
  return Object.freeze({
    mode: 'development',
    entryUrl,
    rendererOrigin: url.origin,
    localBaseUrl: url.origin,
    localPort: url.port ? Number(url.port) : (url.protocol === 'https:' ? 443 : 80),
    engineWebSocketUrl: `${wsProtocol}//${url.host}/api/v8/engine-ws`,
  });
}

function isStableRendererUrl(value) {
  try {
    const url = new URL(String(value || ''));
    return (
      url.protocol === `${STABLE_RENDERER_SCHEME}:`
      && url.hostname === STABLE_RENDERER_HOST
      && !url.port
      && !url.username
      && !url.password
    );
  } catch {
    return false;
  }
}

function isTrustedRendererUrl(value, developmentTransport = null) {
  if (!developmentTransport) return isStableRendererUrl(value);
  try {
    return new URL(String(value || '')).origin === developmentTransport.rendererOrigin;
  } catch {
    return false;
  }
}

function mapStableRequestUrl(requestUrl, transport) {
  if (!transport || transport.mode !== 'production' || !isStableRendererUrl(requestUrl)) {
    throw new Error('Untrusted desktop pet renderer request');
  }
  const source = new URL(requestUrl);
  const normalized = normalizeLoopbackBaseUrl(transport.localBaseUrl);
  if (
    normalized.port !== transport.localPort
    || transport.serverPid <= 0
    || !transport.instanceId
  ) {
    throw new Error('Desktop pet transport identity changed');
  }
  const target = new URL(normalized.baseUrl);
  target.pathname = source.pathname || '/';
  target.search = source.search;
  target.hash = '';
  if (target.origin !== normalized.baseUrl) {
    throw new Error('Desktop pet request escaped its verified loopback origin');
  }
  return target.toString();
}

function stableUrlForLoopbackLocation(location, requestTarget, transport) {
  const resolved = new URL(location, requestTarget);
  if (resolved.origin !== transport.localBaseUrl) {
    throw new Error('Desktop pet loopback server attempted a cross-origin redirect');
  }
  return `${STABLE_RENDERER_SCHEME}://${STABLE_RENDERER_HOST}${resolved.pathname}${resolved.search}${resolved.hash}`;
}

function forwardedRequestHeaders(request) {
  const headers = new Headers(request.headers);
  for (const name of ['connection', 'content-length', 'host', 'origin', 'referer', 'transfer-encoding']) {
    headers.delete(name);
  }
  return headers;
}

function rendererContentSecurityPolicy(webSocketOrigin) {
  return [
    "default-src 'self'",
    "script-src 'self'",
    "style-src 'self' 'unsafe-inline'",
    `connect-src 'self' ${webSocketOrigin}`,
    "img-src 'self' data: blob: http: https:",
    "media-src 'self' data: blob: http: https:",
    "font-src 'self' data:",
    "object-src 'none'",
    "base-uri 'self'",
  ].join('; ');
}

function developmentRendererContentSecurityPolicy(transport) {
  if (!transport || transport.mode !== 'development') {
    throw new Error('Desktop pet development transport is not configured');
  }
  const webSocketUrl = parseUrl(transport.engineWebSocketUrl, 'desktop pet development WebSocket URL');
  if (!['ws:', 'wss:'].includes(webSocketUrl.protocol)) {
    throw new Error('Desktop pet development WebSocket URL is invalid');
  }
  return rendererContentSecurityPolicy(webSocketUrl.origin);
}

function productionRendererContentSecurityPolicy(transport) {
  const webSocketUrl = parseUrl(transport?.engineWebSocketUrl, 'desktop pet WebSocket transport URL');
  if (
    webSocketUrl.protocol !== 'ws:'
    || webSocketUrl.hostname !== '127.0.0.1'
    || Number(webSocketUrl.port) !== transport.localPort
  ) {
    throw new Error('Desktop pet WebSocket transport is not the verified loopback endpoint');
  }
  return rendererContentSecurityPolicy(webSocketUrl.origin);
}

async function forwardStableRendererRequest(request, transport, fetchImpl) {
  let targetUrl;
  try {
    targetUrl = mapStableRequestUrl(request.url, transport);
  } catch (error) {
    return new Response(String(error?.message || error), { status: 400 });
  }

  const init = {
    method: request.method,
    headers: forwardedRequestHeaders(request),
    redirect: 'manual',
  };
  if (!['GET', 'HEAD'].includes(request.method.toUpperCase()) && request.body) {
    init.body = request.body;
    init.duplex = 'half';
  }

  const response = await fetchImpl(targetUrl, init);
  const headers = new Headers(response.headers);
  headers.set('content-security-policy', productionRendererContentSecurityPolicy(transport));
  headers.delete('content-security-policy-report-only');
  const location = headers.get('location');
  if (location) {
    try {
      headers.set('location', stableUrlForLoopbackLocation(location, targetUrl, transport));
    } catch (error) {
      return new Response(String(error?.message || error), { status: 502 });
    }
  }
  return new Response(response.body, {
    status: response.status,
    statusText: response.statusText,
    headers,
  });
}

function installStableRendererProtocol(protocol, fetchImpl, getTransport) {
  protocol.handle(STABLE_RENDERER_SCHEME, async (request) => {
    const transport = getTransport();
    if (!transport) return new Response('Desktop pet transport is not ready', { status: 503 });
    try {
      return await forwardStableRendererRequest(request, transport, fetchImpl);
    } catch {
      return new Response('Desktop pet loopback transport failed', { status: 502 });
    }
  });
}

function rendererTransportView(transport) {
  if (!transport) return null;
  return Object.freeze({
    engineWebSocketUrl: transport.engineWebSocketUrl,
  });
}

module.exports = {
  STABLE_RENDERER_ENTRY_URL,
  STABLE_RENDERER_HOST,
  STABLE_RENDERER_SCHEME,
  createDevelopmentTransport,
  createVerifiedTransport,
  developmentRendererContentSecurityPolicy,
  forwardStableRendererRequest,
  installStableRendererProtocol,
  isStableRendererUrl,
  isTrustedRendererUrl,
  mapStableRequestUrl,
  normalizeLoopbackBaseUrl,
  productionRendererContentSecurityPolicy,
  registerStableRendererScheme,
  rendererTransportView,
  stableUrlForLoopbackLocation,
};
