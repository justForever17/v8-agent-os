function navigationAllowed(initial, target, alreadySecure) {
  if (!["https:", "http:"].includes(target.protocol) || target.username || target.password) return false;
  const root = host => host.replace(/^www\./, "");
  if (root(initial.hostname) !== root(target.hostname)) return false;
  if (alreadySecure && target.protocol !== "https:") return false;
  if (initial.protocol === target.protocol) return initial.port === target.port;
  // Permit the normal HTTP -> HTTPS upgrade, never a different service port.
  return initial.protocol === "http:" && target.protocol === "https:" && !initial.port && !target.port;
}

function protectNavigation(cdp, page, initial) {
  let failure = "";
  const secureFrames = new Set();
  cdp.on("Fetch.requestPaused", async event => {
    try {
      const target = new URL(event.request.url);
      if (!navigationAllowed(initial, target, initial.protocol === "https:" || secureFrames.has(event.frameId))) {
        // Only the authority is actionable; paths, userinfo and signed queries stay private.
        failure ||= `agent_browser_profile_redirect_requires_authorization:${target.host || "unsupported-scheme"}`;
      }
      if (failure) {
        await cdp.send("Fetch.failRequest", { requestId: event.requestId, errorReason: "BlockedByClient" });
      } else {
        if (target.protocol === "https:") secureFrames.add(event.frameId);
        await cdp.send("Fetch.continueRequest", { requestId: event.requestId });
      }
    } catch {
      failure ||= "agent_browser_profile_read_navigation_guard_failed";
      await page.close({ runBeforeUnload: false }).catch(() => {});
    }
  });
  return () => failure;
}

// Read in the existing persistent context. CDP + newContext() loses login state.
export async function readProfilePage(browser, spec = {}) {
  const url = new URL(String(spec.url || ""));
  if (!["https:", "http:"].includes(url.protocol) || url.username || url.password) throw Error("invalid_profile_read_url");
  const context = browser.contexts()[0];
  if (!context) throw Error("agent_browser_profile_context_missing");
  const timeout = Math.max(1000, Math.min(45000, Number(spec.timeoutMs) || 15000));
  const deadline = Date.now() + timeout;
  let createTimer;
  const creating = context.newPage();
  let page;
  try {
    page = await Promise.race([creating, new Promise((_, reject) => {
      createTimer = setTimeout(() => reject(Error("agent_browser_profile_read_creation_timeout")), timeout);
    })]);
  } catch {
    void creating.then(latePage => latePage.close({ runBeforeUnload: false })).catch(() => {});
    throw Error("agent_browser_profile_read_creation_failed");
  } finally { clearTimeout(createTimer); }
  const timer = setTimeout(() => void page.close({ runBeforeUnload: false }).catch(() => {}), Math.max(0, deadline - Date.now()));
  timer.unref();
  let stage = "navigation_guard", cdp, navigationFailure = () => "";
  try {
    // Playwright page.route only sees the first URL of an HTTP redirect chain.
    // Fetch Request-stage interception sees every Document hop before it is sent.
    // It covers frame navigations too; it is not a general subresource/network sandbox.
    cdp = await context.newCDPSession(page);
    navigationFailure = protectNavigation(cdp, page, url);
    await cdp.send("Fetch.enable", { patterns: [{ urlPattern: "*", resourceType: "Document", requestStage: "Request" }] });
    stage = "navigation";
    const remaining = deadline - Date.now();
    if (remaining <= 0) throw Error("profile_read_timeout");
    const response = await page.goto(url.href, { waitUntil: "domcontentloaded", timeout: remaining });
    const wait = Math.min(5000, Math.max(0, Number(spec.waitMs) || 0), Math.max(0, deadline - Date.now() - 500));
    if (wait) await page.waitForTimeout(wait);
    if (navigationFailure()) throw Error(navigationFailure());
    if (Date.now() >= deadline) throw Error("profile_read_timeout");
    stage = "content";
    const content = await page.evaluate(() => {
      const clone = document.documentElement.cloneNode(true);
      clone.querySelectorAll("script,input,textarea,select").forEach(node => node.remove());
      const html = clone.outerHTML;
      return { html: html.slice(0, 2000000), htmlChars: html.length, htmlTruncated: html.length > 2000000 };
    });
    if (navigationFailure()) throw Error(navigationFailure());
    return { ...content, url: page.url(), status: response?.status() || 0, contextReused: true,
             credentialsExported: false, inputValuesOmitted: true };
  } catch {
    // Playwright errors may contain signed URLs; keep only a stable stage code.
    throw Error(navigationFailure() || `agent_browser_profile_read_${stage}_failed`);
  } finally {
    clearTimeout(timer);
    // This page was created above; never close the shared context/browser/user tabs.
    await page.close({ runBeforeUnload: false }).catch(() => {});
    // Close first: detaching interception while the page is alive could release a paused request.
    await cdp?.detach().catch(() => {});
  }
}
