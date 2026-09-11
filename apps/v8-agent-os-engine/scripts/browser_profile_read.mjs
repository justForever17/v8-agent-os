const closingContexts = new WeakMap();
export function closeOwnedContext(context) {
  if (!closingContexts.has(context)) closingContexts.set(context, context.close().catch(() => {}));
  return closingContexts.get(context);
}

function navigationAllowed(initial, target, alreadySecure) {
  if (!["https:", "http:"].includes(target.protocol) || target.username || target.password) return false;
  const root = host => host.replace(/^www\./, "");
  if (root(initial.hostname) !== root(target.hostname)) return false;
  if (alreadySecure && target.protocol !== "https:") return false;
  if (initial.protocol === target.protocol) return initial.port === target.port;
  // Permit the normal HTTP -> HTTPS upgrade, never a different service port.
  return initial.protocol === "http:" && target.protocol === "https:" && !initial.port && !target.port;
}

function protectNavigation(cdp, page, initial, mainFrameId, blockedFrames) {
  let failure = "";
  const secureFrames = new Set();
  cdp.on("Fetch.requestPaused", async event => {
    try {
      const target = new URL(event.request.url);
      if (!navigationAllowed(initial, target, initial.protocol === "https:" || secureFrames.has(event.frameId))) {
        if (event.frameId && event.frameId !== mainFrameId) {
          // Reject the embedded document without invalidating readable main-page
          // content. A blocked advert/login widget is not a main-frame redirect.
          blockedFrames.add(target.host || "unsupported-scheme");
          await cdp.send("Fetch.failRequest", { requestId: event.requestId, errorReason: "BlockedByClient" });
          return;
        }
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
export async function withProfilePage(browser, spec, consume) {
  const url = new URL(String(spec.url || ""));
  if (!["https:", "http:"].includes(url.protocol) || url.username || url.password) throw Error("invalid_profile_read_url");
  const guest = spec.guest === true;
  const timeout = Math.max(1000, Math.min(45000, Number(spec.timeoutMs) || 15000));
  const deadline = Date.now() + timeout;
  let createTimer, context;
  const creating = (async () => {
    if (spec.existingPage) {
      context = spec.existingPage.context();
      if (!browser.contexts().includes(context) || !navigationAllowed(url, new URL(spec.existingPage.url()), true)) {
        throw Error('agent_browser_chat_verification_target_changed');
      }
      return spec.existingPage;
    }
    context = guest ? await browser.newContext() : browser.contexts()[0];
    if (!context) throw Error("agent_browser_profile_context_missing");
    return context.newPage();
  })();
  let page;
  try {
    page = await Promise.race([creating, new Promise((_, reject) => {
      createTimer = setTimeout(() => reject(Error("agent_browser_profile_read_creation_timeout")), timeout);
    })]);
  } catch (error) {
    // A retained page may have been taken over by the user. A rejected resume
    // must not close that page or its guest context.
    if (spec.existingPage) throw Error('agent_browser_chat_verification_target_changed');
    void creating.then(async latePage => {
      await latePage.close({ runBeforeUnload: false }).catch(() => {});
      if (guest) await context?.close().catch(() => {});
    }).catch(() => {});
    if (guest) await context?.close().catch(() => {});
    throw Error(error?.message === "agent_browser_profile_context_missing" ? error.message : "agent_browser_profile_read_creation_failed");
  } finally { clearTimeout(createTimer); }
  // A retained challenge/pending page is intentionally allowed to outlive
  // the request deadline so a later call can observe the same page. The timer
  // only owns cleanup while the request still owns the page.
  let retained = Boolean(spec.existingPage && spec.retainExistingPage);
  const userOwnsPage = () => typeof spec.preservePage === 'function' && spec.preservePage() === true;
  let expire;
  const deadlineReached = new Promise(resolve => { expire = resolve; });
  const timer = setTimeout(() => {
    expire();
    if (!retained && !userOwnsPage()) void page.close({ runBeforeUnload: false }).catch(() => {});
  }, Math.max(0, deadline - Date.now()));
  timer.unref();
  const abort = () => {
    if (!userOwnsPage()) void page.close({ runBeforeUnload: false }).catch(() => {});
  };
  spec.signal?.addEventListener('abort', abort, { once: true });
  let stage = "navigation_guard", cdp, navigationFailure = () => "";
  const blockedFrames = new Set();
  try {
    if (spec.signal?.aborted) throw Error("agent_browser_chat_cancelled");
    // Playwright page.route only sees the first URL of an HTTP redirect chain.
    // Fetch Request-stage interception sees every Document hop before it is sent.
    // It covers frame navigations too; it is not a general subresource/network sandbox.
    cdp = await context.newCDPSession(page);
    const { frameTree } = await cdp.send("Page.getFrameTree");
    if (!frameTree?.frame?.id) throw Error("main_frame_identity_missing");
    navigationFailure = protectNavigation(cdp, page, url, frameTree.frame.id, blockedFrames);
    await cdp.send("Fetch.enable", { patterns: [{ urlPattern: "*", resourceType: "Document", requestStage: "Request" }] });
    stage = "navigation";
    const remaining = deadline - Date.now();
    if (remaining <= 0) throw Error("profile_read_timeout");
    const response = spec.existingPage ? null : await page.goto(url.href, { waitUntil: "domcontentloaded", timeout: remaining });
    stage = "content";
    const result = await consume(page, { deadline, deadlineReached, response, navigationFailure,
      retainForUser: () => { retained = true; }, releaseForCleanup: () => { retained = false; } });
    if (spec.signal?.aborted) throw Error("agent_browser_chat_cancelled");
    if (navigationFailure()) throw Error(navigationFailure());
    return { ...result, url: page.url(), status: response?.status() || 0, contextReused: !guest,
             blockedEmbeddedHosts: [...blockedFrames],
             credentialsExported: false, inputValuesOmitted: true };
  } catch (error) {
    // Only adapter-owned stage codes leave the page boundary, never page errors.
    const code = /^agent_browser_chat_[a-z_]+$/.test(error?.message || "") ? error.message : "";
    throw Error(navigationFailure() || (spec.signal?.aborted ? "agent_browser_chat_cancelled" : code) || `agent_browser_profile_read_${stage}_failed`);
  } finally {
    clearTimeout(timer);
    spec.signal?.removeEventListener('abort', abort);
    const closePage = (!retained || spec.signal?.aborted) && !userOwnsPage();
    if (closePage) await page.close({ runBeforeUnload: false }).catch(() => {});
    if (retained) await cdp?.send('Fetch.disable').catch(() => {});
    await cdp?.detach().catch(() => {});
    if (guest && closePage) await closeOwnedContext(context);
  }
}

export async function readProfilePage(browser, spec = {}) {
  return withProfilePage(browser, spec, async (page, { deadline, navigationFailure }) => {
    const wait = Math.min(5000, Math.max(0, Number(spec.waitMs) || 0), Math.max(0, deadline - Date.now() - 500));
    if (wait) await page.waitForTimeout(wait);
    if (navigationFailure()) throw Error(navigationFailure());
    if (Date.now() >= deadline) throw Error("profile_read_timeout");
    const content = await page.evaluate(() => {
      const clone = document.documentElement.cloneNode(true);
      clone.querySelectorAll("script,input,textarea,select").forEach(node => node.remove());
      const html = clone.outerHTML;
      // Keep visible SPA text independently: generic article heuristics can
      // select an invisible QR-code paragraph while discarding all div text.
      const root = document.querySelector('main,[role="main"]') || document.body;
      const pieces = [];
      const visit = node => {
        if (node.nodeType === Node.TEXT_NODE) { pieces.push(node.textContent); return; }
        if (node.nodeType !== Node.ELEMENT_NODE || !node.getClientRects().length || /^(SCRIPT|STYLE|INPUT|TEXTAREA|SELECT|NAV|FOOTER)$/.test(node.tagName)) return;
        const style = getComputedStyle(node);
        if (style.visibility === 'hidden') return;
        const block = !['inline', 'contents'].includes(style.display);
        if (block || node.tagName === 'BR') pieces.push('\n');
        node.childNodes.forEach(visit);
        if (block) pieces.push('\n');
      };
      visit(root);
      const visibleText = pieces.join('').replace(/\n[ \t]*\n(?:[ \t]*\n)+/g, '\n\n').trim();
      return { html: html.slice(0, 2000000), htmlChars: html.length, htmlTruncated: html.length > 2000000,
               visibleText: visibleText.slice(0, 100000), visibleTextTruncated: visibleText.length > 100000 };
    });
    if (navigationFailure()) throw Error(navigationFailure());
    return content;
  });
}
