import { randomUUID } from "node:crypto";

// One disposable observation per existing Playwright page; no browser/profile owner.
const observations = new WeakMap();
const errors = new WeakMap();
const MAX_NODES = 160;
const ACTION_TIMEOUT_MS = 4000;

export function trackAgentPage(page) {
  if (errors.has(page)) return;
  const entries = [];
  errors.set(page, entries);
  page.on("pageerror", (error) => {
    entries.push({ type: "pageerror", message: String(error?.message || error).slice(0, 600) });
    if (entries.length > 12) entries.shift();
  });
  page.once("close", () => void observations.get(page)?.handle?.dispose().catch(() => {}));
}

function fail(code) { throw new Error(code); }

export async function invalidateAgentObservation(page) {
  const previous = observations.get(page);
  observations.delete(page);
  await previous?.handle.dispose().catch(() => {});
}

export function omitAriaInputValues(snapshot) {
  // Playwright's text snapshot can include password values. Do not read them
  // separately or mutate the page: retain input names/states, omit all text
  // input values (including multiline value blocks) before leaving the proxy.
  const lines = [];
  let inputIndent = null;
  for (const line of String(snapshot).split("\n")) {
    const indent = line.match(/^\s*/)[0].length;
    if (inputIndent !== null && (!line.trim() || indent > inputIndent)) continue;
    inputIndent = null;
    if (/^\s*- (?:textbox|searchbox)(?=\s|:|$)/.test(line)) {
      inputIndent = indent;
      let quoted = false;
      let escaped = false;
      let brackets = 0;
      let end = line.length;
      for (let index = 0; index < line.length; index++) {
        const ch = line[index];
        if (escaped) { escaped = false; continue; }
        if (quoted && ch === "\\") { escaped = true; continue; }
        if (ch === '"') quoted = !quoted;
        if (!quoted && ch === "[") brackets++;
        if (!quoted && ch === "]") brackets--;
        if (!quoted && !brackets && ch === ":") { end = index; break; }
      }
      lines.push(line.slice(0, end));
    } else lines.push(line);
  }
  return lines.join("\n");
}

function locatorFor(page, spec, { scope = false } = {}) {
  const selector = String(spec.selector || "").trim();
  const role = String(spec.role || "").trim();
  const name = spec.name;
  if (selector && (role || name !== undefined)) fail("ambiguous_locator_contract");
  if (selector) {
    if (selector.length > 1000) fail("selector_too_long");
    return page.locator(selector);
  }
  if (role && typeof name === "string" && name.length <= 500) return page.getByRole(role, { name, exact: true });
  if (scope && !role && name === undefined) return page.locator("css:light=html > body");
  fail("unique_selector_or_exact_role_name_required");
}

async function uniqueLocator(page, spec, options) {
  const locator = locatorFor(page, spec, options);
  const count = await locator.count();
  if (count !== 1) fail(count ? "locator_ambiguous" : "locator_not_found");
  return locator;
}

export async function observeAgentPage(page, spec = {}) {
  trackAgentPage(page);
  if (spec.selector) {
    const matches = locatorFor(page, spec);
    const count = await matches.count();
    if (count > 1) return observeCandidates(page, matches, String(spec.selector), count);
  }
  const root = await uniqueLocator(page, spec, { scope: true });
  const handle = await root.evaluateHandle((node) => {
    const all = [node, ...node.querySelectorAll("*")];
    const visible = all.filter((element) => element.getClientRects().length && !["SCRIPT", "STYLE", "NOSCRIPT"].includes(element.tagName));
    return { document: node.ownerDocument, elements: visible.slice(0, 160), total: visible.length };
  });
  try {
    const dom = await handle.evaluate((state) => ({ total: state.total, nodes: state.elements.map((el, index) => ({
      index, parent: state.elements.indexOf(el.parentElement), tag: el.tagName.toLowerCase(),
      attributes: ["id", "role", "aria-label", "aria-disabled", "disabled", "href", "type", "placeholder", "name"].map((key) => [key, el.getAttribute(key)]),
      text: ["INPUT", "TEXTAREA", "SELECT"].includes(el.tagName) ? "" : (el.innerText || "").trim().slice(0, 240),
      formAction: el.form?.action || null, formMethod: el.form?.method || null,
    })) }));
    const maxChars = Math.max(500, Math.min(20000, Number(spec.maxChars) || 6000));
    const ax = omitAriaInputValues(await root.ariaSnapshot({ timeout: ACTION_TIMEOUT_MS }));
    const old = observations.get(page);
    const record = { id: randomUUID(), url: page.url(), handle, nodes: dom.nodes, observedAt: Date.now() };
    observations.set(page, record);
    await old?.handle.dispose().catch(() => {});
    return {
      observationId: record.id, url: record.url, title: await page.title(), observedAt: record.observedAt,
      accessibility: ax.slice(0, maxChars), accessibilityChars: ax.length, accessibilityTruncated: ax.length > maxChars,
      inputValuesOmitted: true,
      dom: dom.nodes, domNodeCount: dom.total, domTruncated: dom.total > MAX_NODES,
      errors: [...(errors.get(page) || [])],
      ...(spec.screenshot ? { screenshot: { mimeType: "image/jpeg", data: (await page.screenshot({ type: "jpeg", quality: 65, timeout: ACTION_TIMEOUT_MS })).toString("base64") } } : {}),
    };
  } catch (error) {
    if (observations.get(page)?.handle !== handle) await handle.dispose().catch(() => {});
    throw error;
  }
}

async function observeCandidates(page, locator, selector, count) {
  const handles = [];
  let record, capturedHandle;
  try {
    for (let index = 0; index < Math.min(count, 20); index++) {
      const handle = await locator.nth(index).elementHandle({ timeout: ACTION_TIMEOUT_MS });
      if (!handle) fail("candidate_changed_during_observation");
      handles.push(handle);
    }
    const handle = await handles[0].evaluateHandle((first, elements) => ({ document: first.ownerDocument, elements }), handles);
    capturedHandle = handle;
    const nodes = await handle.evaluate(state => state.elements.map((el, index) => {
      const rect = el.getBoundingClientRect();
      const visible = !!el.getClientRects().length && getComputedStyle(el).visibility !== "hidden";
      return { index, parent: -1, tag: el.tagName.toLowerCase(),
        attributes: ["id", "role", "aria-label", "aria-disabled", "disabled", "href", "type", "placeholder", "name"].map(key => [key, el.getAttribute(key)]),
        text: ["INPUT", "TEXTAREA", "SELECT"].includes(el.tagName) ? "" : (el.innerText || "").trim().slice(0, 240),
        formAction: el.form?.action || null, formMethod: el.form?.method || null,
        visible, inViewport: visible && rect.bottom > 0 && rect.top < innerHeight && rect.right > 0 && rect.left < innerWidth,
        ...(el.tagName === "VIDEO" ? { video: { readyState: el.readyState, paused: el.paused,
          currentTime: el.currentTime, duration: Number.isFinite(el.duration) ? el.duration : null,
          width: el.videoWidth, height: el.videoHeight } } : {}),
      };
    }));
    const old = observations.get(page);
    record = { id: randomUUID(), url: page.url(), handle, nodes, observedAt: Date.now() };
    observations.set(page, record);
    await old?.handle.dispose().catch(() => {});
    return { observationId: record.id, url: record.url, title: await page.title(), observedAt: record.observedAt,
      candidates: nodes.map((node, index) => ({ ...node, selector: `${selector} >> nth=${index}` })),
      candidateCount: count, candidatesTruncated: count > nodes.length,
      meaning: "Select a specific observed candidate. Its selector is checked against this observation before actions; do not guess an index." };
  } finally {
    if (!record) await capturedHandle?.dispose().catch(() => {});
    for (const handle of handles) await handle.dispose().catch(() => {});
  }
}

async function currentObservation(page, spec) {
    const record = observations.get(page);
    if (!record || record.id !== spec.observationId || Date.now() - record.observedAt > 120000) fail("stale_observation");
    if (page.url() !== record.url) fail("page_changed_since_observation");
    try {
      if (!await record.handle.evaluate((state) => state.document === document)) fail("page_changed_since_observation");
    } catch { fail("page_changed_since_observation"); }
    return record;
}

export async function closeAgentPage(page, spec) {
  const record = await currentObservation(page, spec);
  await page.close({ runBeforeUnload: false });
  observations.delete(page);
  await record.handle.dispose().catch(() => {});
  return { closed: true };
}

export async function inspectAgentTarget(page, spec) {
  const record = await currentObservation(page, spec);
  const locator = await uniqueLocator(page, spec);
  const current = await locator.evaluate((el, state) => ({
    index: state.elements.indexOf(el), documentMatches: el.ownerDocument === state.document,
    tag: el.tagName.toLowerCase(),
    attributes: ["id", "role", "aria-label", "aria-disabled", "disabled", "href", "type", "placeholder", "name"].map((key) => [key, el.getAttribute(key)]),
    text: ["INPUT", "TEXTAREA", "SELECT"].includes(el.tagName) ? "" : (el.innerText || "").trim().slice(0, 240),
    formAction: el.form?.action || null, formMethod: el.form?.method || null,
  }), record.handle);
  const prior = record.nodes[current.index];
  if (!current.documentMatches || !prior) fail("target_not_in_current_observation");
  if (current.tag !== prior.tag || current.text !== prior.text || current.formAction !== prior.formAction || current.formMethod !== prior.formMethod || JSON.stringify(current.attributes) !== JSON.stringify(prior.attributes)) fail("target_changed_since_observation");
  return { locator, target: { tag: current.tag, attributes: Object.fromEntries(current.attributes), text: current.text,
                            formAction: current.formAction, formMethod: current.formMethod }, record };
}

export async function actOnAgentPage(page, spec) {
  const { locator, target, record } = await inspectAgentTarget(page, spec);
  if (spec.action === "inspect") return { target, url: page.url(), observationId: record.id };
  if (spec.action === "click") await locator.click({ timeout: ACTION_TIMEOUT_MS });
  else if (spec.action === "fill") {
    if (typeof spec.text !== "string" || spec.text.length > 20000) fail("invalid_text");
    if (target.attributes.type === "password") fail("password_input_requires_user_control");
    await locator.fill(spec.text, { timeout: ACTION_TIMEOUT_MS });
  } else if (spec.action === "press") {
    if (!["Enter", "Tab", "Escape", "ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight", "Space", "Home", "End", "PageUp", "PageDown"].includes(spec.key)) fail("unsupported_key");
    await locator.press(spec.key, { timeout: ACTION_TIMEOUT_MS });
  } else if (spec.action === "scroll") {
    const delta = Number(spec.scrollY);
    if (!Number.isFinite(delta) || Math.abs(delta) > 4000) fail("invalid_scroll");
    await locator.evaluate((el, y) => (el === document.body ? document.scrollingElement : el).scrollBy({ top: y, behavior: "instant" }), delta);
  } else fail("unsupported_agent_action");
  if (observations.get(page) === record) observations.delete(page);
  await record.handle.dispose().catch(() => {});
  return { action: spec.action, target, url: page.url(), title: await page.title(), applied: true, next: "observe_before_next_action" };
}
