function normalizedOrigin(value) {
  try {
    const url = new URL(String(value || ''));
    if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password) return null;
    return url.origin;
  } catch {
    return null;
  }
}

function trustedProductOrigins(baseUrls = []) {
  return new Set(baseUrls.map(normalizedOrigin).filter(Boolean));
}

function isTrustedProductUrl(value, origins) {
  const origin = normalizedOrigin(value);
  return Boolean(origin && origins?.has(origin));
}

function isStartupSurfaceUrl(value) {
  return String(value || '').startsWith('data:text/html;charset=utf-8,');
}

function isSafeExternalUrl(value) {
  try {
    const url = new URL(String(value || ''));
    return ['http:', 'https:'].includes(url.protocol) && !url.username && !url.password;
  } catch {
    return false;
  }
}

function classifyWindowOpen(value, origins) {
  if (isTrustedProductUrl(value, origins)) return 'product';
  if (isSafeExternalUrl(value)) return 'external';
  return 'deny';
}

function isTrustedIpcSource({ senderMatches, isMainFrame, frameUrl, origins, allowStartup = false }) {
  if (!senderMatches || !isMainFrame) return false;
  if (isTrustedProductUrl(frameUrl, origins)) return true;
  return allowStartup && isStartupSurfaceUrl(frameUrl);
}

function isTrustedAdminAuthIpcSource(options = {}) {
  if (!isTrustedIpcSource(options)) return false;
  try {
    const frameUrl = new URL(String(options.frameUrl || ''));
    const adminOrigin = normalizedOrigin(options.adminBaseUrl);
    const isPublicVerifyPage = frameUrl.pathname === '/admin/verify'
      || frameUrl.pathname.startsWith('/admin/verify/');
    return Boolean(adminOrigin)
      && frameUrl.origin === adminOrigin
      && !isPublicVerifyPage
      && (frameUrl.pathname === '/admin' || frameUrl.pathname.startsWith('/admin/'));
  } catch {
    return false;
  }
}

module.exports = {
  classifyWindowOpen,
  isSafeExternalUrl,
  isStartupSurfaceUrl,
  isTrustedAdminAuthIpcSource,
  isTrustedIpcSource,
  isTrustedProductUrl,
  normalizedOrigin,
  trustedProductOrigins,
};
