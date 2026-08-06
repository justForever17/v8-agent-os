function validateExpectedResponseUrl(kind, response) {
  let finalUrl;
  let expectedOrigin;
  try {
    finalUrl = new URL(String(response.responseUrl || ''));
    expectedOrigin = new URL(String(response.expectedOrigin || '')).origin;
  } catch {
    return { ok: false, reason: `${kind}_response_url_invalid` };
  }
  if (finalUrl.origin !== expectedOrigin) {
    return { ok: false, reason: `${kind}_redirect_origin_mismatch` };
  }
  const pathMatches = kind === 'engine'
    ? finalUrl.pathname === '/readyz'
    : kind === 'admin'
      ? finalUrl.pathname === '/login'
        || finalUrl.pathname === '/admin'
        || finalUrl.pathname.startsWith('/admin/')
      : kind === 'web'
        ? finalUrl.pathname === '/chat' || finalUrl.pathname.startsWith('/chat/')
        : false;
  return pathMatches
    ? { ok: true, finalUrl }
    : { ok: false, reason: `${kind}_response_path_mismatch` };
}

function validateReadinessResponse(kind, response) {
  if (!response?.ok || response.status < 200 || response.status >= 300) {
    return { ok: false, reason: `http_${response?.status ?? "unknown"}` };
  }

  const urlValidation = validateExpectedResponseUrl(kind, response);
  if (!urlValidation.ok) return urlValidation;

  const body = String(response.body || '');
  if (kind === 'engine') {
    if (!String(response.contentType || '').toLowerCase().includes('application/json')) {
      return { ok: false, reason: 'unexpected_content_type' };
    }
    let payload;
    try {
      payload = JSON.parse(body);
    } catch {
      return { ok: false, reason: 'invalid_json' };
    }
    return payload?.status === 'ok'
      && payload?.service === 'v8-agent-os-engine'
      && payload?.ready === true
      ? { ok: true, marker: 'v8-agent-os-engine:ready' }
      : { ok: false, reason: 'readiness_contract_mismatch' };
  }
  if (kind === 'admin') {
    const finalUrl = urlValidation.finalUrl;
    if (finalUrl.pathname === '/login') {
      return body.includes('id="login"')
        ? { ok: true, marker: 'id="login"', surface: 'login' }
        : { ok: false, reason: 'admin_login_marker_missing' };
    }
    if (finalUrl.pathname === '/admin' || finalUrl.pathname.startsWith('/admin/')) {
      return body.includes('<title>V8 Agent OS</title>')
        ? { ok: true, marker: '<title>V8 Agent OS</title>', surface: 'admin' }
        : { ok: false, reason: 'admin_shell_marker_missing' };
    }
    return { ok: false, reason: 'admin_response_path_mismatch' };
  }
  if (kind === 'web') {
    return body.includes('V8 Agent OS - AI Assistant')
      ? { ok: true, marker: 'V8 Agent OS - AI Assistant' }
      : { ok: false, reason: 'web_chat_marker_missing' };
  }
  return { ok: false, reason: 'unknown_probe_kind' };
}

function isWebSurfaceReady(options = {}) {
  return classifyProductSurface(options) === 'web';
}

function classifyProductSurface(options = {}) {
  if (!options.coreServicesReady) return null;
  try {
    const loaded = new URL(String(options.loadedUrl || ''));
    const web = new URL(String(options.webBaseUrl || ''));
    if (loaded.origin === web.origin
      && (loaded.pathname === '/chat' || loaded.pathname.startsWith('/chat/'))) {
      return 'web';
    }
    const admin = new URL(String(options.adminBaseUrl || ''));
    if (loaded.origin !== admin.origin) return null;
    if (loaded.pathname === '/login') return 'admin-login';
    if (loaded.pathname === '/admin' || loaded.pathname.startsWith('/admin/')) return 'admin';
    return null;
  } catch {
    return null;
  }
}

function productSurfaceDomScript(surfaceKind) {
  const expectedTitle = surfaceKind === 'web'
    ? 'V8 Agent OS - AI Assistant'
    : surfaceKind === 'admin' || surfaceKind === 'admin-login'
      ? 'V8 Agent OS'
      : '';
  if (!expectedTitle) return '';
  return `(() => {
    const bodyText = String(document.body?.innerText || '').trim();
    const interactive = document.querySelector('button, input, textarea, a[href], [role="button"]');
    return document.title === ${JSON.stringify(expectedTitle)} && bodyText.length > 0 && Boolean(interactive);
  })()`;
}

async function verifyProductSurfaceDom(executeJavaScript, surfaceKind) {
  const script = productSurfaceDomScript(surfaceKind);
  if (!script || typeof executeJavaScript !== 'function') return false;
  try {
    return await executeJavaScript(script) === true;
  } catch {
    return false;
  }
}

async function fetchTextWithTimeout(fetchImpl, url, timeoutMs = 1500, options = {}) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetchImpl(url, {
      ...options,
      signal: controller.signal,
    });
    const body = await response.text();
    return { response, body, responseUrl: response.url || url };
  } finally {
    clearTimeout(timeout);
  }
}

module.exports = {
  classifyProductSurface,
  fetchTextWithTimeout,
  isWebSurfaceReady,
  productSurfaceDomScript,
  validateReadinessResponse,
  verifyProductSurfaceDom,
};
