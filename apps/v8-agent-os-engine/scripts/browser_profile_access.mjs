// Only domain-level access hints leave the browser process; never cookie names/values.
export async function profileAccessSummary(browser) {
  const sites = new Map();
  const now = Date.now() / 1000;
  // Match readProfilePage: additional incognito contexts do not carry the
  // persistent profile's authority or login state.
  for (const context of browser.contexts().slice(0, 1)) {
    for (const cookie of await context.cookies()) {
      if (cookie.expires > 0 && cookie.expires <= now) continue;
      const host = String(cookie.domain || "").replace(/^\./, "").toLowerCase();
      if (!host || /[\s/:]/.test(host)) continue;
      sites.set(host, { host, sessionPresent: true, access: "unverified", observedAt: Date.now() });
    }
    for (const page of context.pages()) {
      let host;
      try { const url = new URL(page.url()); if (!["https:", "http:"].includes(url.protocol)) continue; host = url.hostname; }
      catch { continue; }
      let storagePresent = false;
      try { storagePresent = await page.evaluate(() => localStorage.length > 0 || sessionStorage.length > 0); }
      catch { /* A closed/navigating/opaque page is not a confirmed session. */ }
      const sessionPresent = storagePresent || [...sites.keys()].some(domain => host === domain || host.endsWith(`.${domain}`));
      // A page being open or having cookies does not prove a valid login.
      sites.set(host, { ...sites.get(host), host, sessionPresent, openPage: true,
                        access: "unverified", observedAt: Date.now() });
    }
  }
  return { sites: [...sites.values()], observedAt: Date.now(), credentialsExported: false,
           meaning: "Session presence is a candidate for authenticated access, not proof that a login is valid. Actual reads can still encounter expiry or a challenge." };
}
