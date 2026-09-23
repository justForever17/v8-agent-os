// Two product documents, one window. The registry is the only navigation owner.
function createResidentSurfaces({ baseContents, createAdminView, attachView, getBounds, webOrigin, adminOrigin, observe }) {
  const web = { kind: 'web', contents: baseContents, view: null, loaded: false, pending: null };
  const entries = new Map([['web', web]]);
  let active = web;
  let windowVisible = true;
  const originFor = (kind) => kind === 'web' ? webOrigin() : adminOrigin();
  function entryFor(contents) { return [...entries.values()].find((entry) => entry.contents === contents); }
  function broadcast(channel, value) {
    for (const entry of entries.values()) if (!entry.contents.isDestroyed()) entry.contents.send(channel, value);
  }
  function visibility(visible = windowVisible) {
    windowVisible = visible;
    for (const entry of entries.values()) if (!entry.contents.isDestroyed()) {
      entry.view?.setVisible(entry === active);
      entry.contents.send('v8os-shell:surface-visibility', { visible: visible && entry === active });
    }
  }
  function resize() { for (const entry of entries.values()) entry.view?.setBounds(getBounds()); }
  function get(kind) {
    if (!entries.has(kind)) {
      const view = createAdminView();
      const entry = { kind, contents: view.webContents, view, loaded: false, pending: null };
      entries.set(kind, entry); attachView(view); resize(); observe(entry.contents);
    }
    return entries.get(kind);
  }
  async function open(url, { resume = false, sessionId } = {}) {
    const target = new URL(url);
    const adminRoute = target.pathname === '/login' || target.pathname === '/admin' || target.pathname.startsWith('/admin/');
    const kind = adminRoute && target.origin === adminOrigin() ? 'admin'
      : target.origin === webOrigin() ? 'web' : null;
    if (!kind || target.username || target.password) throw new Error('untrusted_surface_url');
    const entry = get(kind);
    if (sessionId) entry.pendingSession = sessionId;
    active = entry; visibility(); entry.contents.focus();
    const current = entry.contents.getURL();
    const sameOrigin = current && new URL(current).origin === target.origin;
    if (entry.loaded && sameOrigin && (resume || current === url)) { entry.pendingSession = null; return true; }
    if (entry.loaded && sameOrigin && kind === 'web' && sessionId) {
      entry.contents.send('v8os-shell:navigate-session', { sessionId });
      entry.pendingSession = null;
      return true;
    }
    if (entry.pending) {
      if (!resume && url !== entry.lastProductUrl) entry.pendingUrl = url;
      return entry.pending;
    }
    entry.lastProductUrl = url;
    entry.pending = (async () => {
      let targetUrl = url;
      do {
        entry.pendingUrl = null;
        entry.lastProductUrl = targetUrl;
        await entry.contents.loadURL(targetUrl);
        targetUrl = entry.pendingUrl;
      } while (targetUrl && targetUrl !== entry.contents.getURL());
      entry.loaded = true;
      if (entry.pendingSession) { entry.contents.send('v8os-shell:navigate-session', { sessionId: entry.pendingSession }); entry.pendingSession = null; }
      visibility(); return true;
    })()
      .finally(() => { entry.pending = null; });
    return entry.pending;
  }
  async function prewarm(url) {
    const target = new URL(url);
    const adminRoute = target.pathname === '/login' || target.pathname === '/admin' || target.pathname.startsWith('/admin/');
    const kind = adminRoute && target.origin === adminOrigin()
      ? 'admin'
      : target.origin === webOrigin() ? 'web' : null;
    if (!kind || target.username || target.password) throw new Error('untrusted_surface_url');
    const entry = get(kind);
    visibility();
    const current = entry.contents.getURL();
    const sameOrigin = current && new URL(current).origin === target.origin;
    if (entry.loaded && sameOrigin) return true;
    if (entry.pending) return entry.pending;
    entry.lastProductUrl = target.toString();
    entry.pending = (async () => {
      let targetUrl = target.toString();
      do {
        entry.pendingUrl = null;
        entry.lastProductUrl = targetUrl;
        await entry.contents.loadURL(targetUrl);
        targetUrl = entry.pendingUrl;
      } while (targetUrl && targetUrl !== entry.contents.getURL());
      entry.loaded = true;
      visibility();
      return true;
    })().finally(() => { entry.pending = null; });
    return entry.pending;
  }
  return {
    open, prewarm, broadcast, visibility, resize, entryFor,
    activeContents: () => active.contents,
    owns(contents, url, allowStartup = false) {
      const entry = entryFor(contents);
      if (!entry || contents.isDestroyed()) return false;
      if (allowStartup && String(url).startsWith('data:text/html;charset=utf-8,')) return true;
      try { return new URL(url).origin === originFor(entry.kind); } catch { return false; }
    },
    invalidate() { for (const entry of entries.values()) entry.loaded = false; },
    dispose() { for (const entry of entries.values()) { clearTimeout(entry.recoveryTimer); if (entry.view && !entry.contents.isDestroyed()) entry.contents.close(); } entries.clear(); },
  };
}
module.exports = { createResidentSurfaces };
