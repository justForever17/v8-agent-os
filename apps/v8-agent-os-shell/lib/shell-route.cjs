const ALLOWED_DEEP_LINK = 'v8os://open/admin/desktop-pet';

function parseShellDeepLink(value) {
  const raw = String(value || '').trim();
  if (!raw) return null;
  try {
    const url = new URL(raw);
    if (url.protocol !== 'v8os:' || url.hostname !== 'open') return null;
    if (url.pathname.replace(/\/+$/, '') !== '/admin/desktop-pet') return null;
    if (url.search || url.hash) return null;
    return { surface: 'admin', path: '/admin/desktop-pet' };
  } catch {
    return null;
  }
}

module.exports = { ALLOWED_DEEP_LINK, parseShellDeepLink };
