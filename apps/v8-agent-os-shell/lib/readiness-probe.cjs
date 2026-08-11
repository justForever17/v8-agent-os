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

function initialProductSurfaceUrl(options = {}) {
  if (typeof options.initialized !== 'boolean') {
    throw new TypeError('initialized must be a boolean');
  }
  if (options.pendingSurfaceUrl) return String(options.pendingSurfaceUrl);
  if (options.initialized) return String(options.defaultChatUrl || '');
  return `${String(options.adminBaseUrl || '').replace(/\/$/, '')}/login`;
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
  const requiredInputSelector = surfaceKind === 'web'
    ? 'textarea[data-v8os-chat-composer="true"]:not([disabled]), button[data-v8os-start-task="true"]:not([disabled])'
    : surfaceKind === 'admin-login'
      ? '#login:not([disabled])'
      : '';
  if (!expectedTitle) return '';
  return `(() => {
    const bodyText = String(document.body?.innerText || '').trim();
    const interactive = document.querySelector('button, input, textarea, a[href], [role="button"]');
    const requiredInput = ${JSON.stringify(requiredInputSelector)}
      ? document.querySelector(${JSON.stringify(requiredInputSelector)})
      : interactive;
    const marker = document.querySelector('[data-v8os-style-probe="true"]');
    const hydrated = marker?.getAttribute('data-v8os-hydration') === 'ready';
    const stylesReady = Boolean(marker) && getComputedStyle(marker).display === 'none';
    const inputStyle = requiredInput ? getComputedStyle(requiredInput) : null;
    const inputRect = requiredInput?.getBoundingClientRect();
    const hitTarget = inputRect && inputRect.width > 0 && inputRect.height > 0
      ? document.elementFromPoint(inputRect.left + inputRect.width / 2, inputRect.top + inputRect.height / 2)
      : null;
    const inputHitTestReady = Boolean(requiredInput)
      && Boolean(hitTarget)
      && (hitTarget === requiredInput || requiredInput.contains(hitTarget));
    const previousActiveElement = document.activeElement;
    requiredInput?.focus({ preventScroll: true });
    const inputFocusReady = document.activeElement === requiredInput;
    if (previousActiveElement instanceof HTMLElement && previousActiveElement !== requiredInput) {
      previousActiveElement.focus({ preventScroll: true });
    } else if (requiredInput instanceof HTMLElement) {
      requiredInput.blur();
    }
    return document.title === ${JSON.stringify(expectedTitle)}
      && bodyText.length > 0
      && Boolean(interactive)
      && Boolean(requiredInput)
      && inputStyle?.pointerEvents !== 'none'
      && inputHitTestReady
      && inputFocusReady
      && hydrated
      && stylesReady;
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

async function waitForProductSurfaceDom(executeJavaScript, surfaceKind, options = {}) {
  const timeoutMs = Math.max(0, Number(options.timeoutMs ?? 5000));
  const intervalMs = Math.max(10, Number(options.intervalMs ?? 100));
  const now = typeof options.now === 'function' ? options.now : Date.now;
  const sleep = typeof options.sleep === 'function'
    ? options.sleep
    : (delayMs) => new Promise((resolve) => setTimeout(resolve, delayMs));
  const startedAt = now();

  while (!options.isCancelled?.()) {
    if (await verifyProductSurfaceDom(executeJavaScript, surfaceKind)) return true;
    const elapsedMs = now() - startedAt;
    if (elapsedMs >= timeoutMs) return false;
    await sleep(Math.min(intervalMs, timeoutMs - elapsedMs));
  }
  return false;
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
  initialProductSurfaceUrl,
  isWebSurfaceReady,
  productSurfaceDomScript,
  validateReadinessResponse,
  verifyProductSurfaceDom,
  waitForProductSurfaceDom,
};
