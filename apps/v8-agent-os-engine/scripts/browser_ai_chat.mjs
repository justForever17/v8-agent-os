import { withProfilePage, closeOwnedContext } from './browser_profile_read.mjs';
import { trackAgentPage } from './browser_agent_surface.mjs';

// At most one pre-submission verification page per site/profile. The same
// browser owns it; retaining a challenge never grants access to another tab.
const verificationPages = new WeakMap();
const activeQueries = new WeakMap();
const pendingPages = new WeakMap();

// Site adapters describe observed DOM contracts, not private HTTP APIs/tokens.
const sites = {
  metaso: { url: 'https://metaso.cn/', composer: 'textarea.search-consult-textarea:visible',
    answer: '[class*="markdown"], [class*="MarkDown"], [data-testid^="_MarkDownContent.PComponent"]',
    stop: '[data-testid*="Stop"],button[aria-label*="停止"]', done: '[data-testid^="SearchResultActions.ChatIconButton"]' },
  chatgpt: { url: 'https://chatgpt.com/', composer: '#prompt-textarea:visible,textarea#mobile-composer-prompt:visible',
    answer: '[data-message-author-role="assistant"], [class$="_assistantMessage"]',
    stop: '[data-testid="stop-button"],button[aria-label*="停止"],button[aria-label*="Stop"]',
    done: '[data-testid="copy-turn-action-button"],button[aria-label="复制回复"],button[aria-label="Copy response"],button[aria-label="Copy"]' },
};

export function chatSite(provider) { return sites[provider]?.url || null; }

export async function queryBrowserChat(browser, spec = {}) {
  const site = sites[spec.provider];
  const query = typeof spec.query === 'string' ? spec.query.trim() : '';
  if (!site || !query || query.length > 12000) throw Error('agent_browser_chat_invalid_request');
  let retained = verificationPages.get(browser);
  if (!retained) { retained = new Map(); verificationPages.set(browser, retained); }
  const key = `${spec.provider}:${spec.guest === true ? 'guest' : 'profile'}`;
  let active = activeQueries.get(browser);
  if (!active) { active = new Set(); activeQueries.set(browser, active); }
  if (active.has(key)) return { ok: false, provider: spec.provider, error: 'agent_browser_chat_busy',
    failureClass: 'provider_busy', retryable: true, querySubmitted: false };
  const pending = pendingPages.get(browser)?.get(key);
  if (pending && !pending.page.isClosed()) {
    return { ok: false, provider: spec.provider, error: 'agent_browser_chat_answer_pending',
      failureClass: 'answer_pending', retryable: true, querySubmitted: true,
      verificationTargetId: pending.targetId, verificationPageRetained: true,
      recommendedNextAction: '问题已经提交但原页尚未给出可读正文；请在原 Agent 浏览器页观察或稍后继续同一问题。不要再次提交。' };
  }
  active.add(key);
  try {
  const existingPage = retained.get(key);
  if (existingPage && !existingPage.isClosed() && new URL(existingPage.url()).origin !== new URL(site.url).origin) {
    retained.delete(key);
    throw Error('agent_browser_chat_verification_target_changed');
  }
  retained.delete(key);
  return await withProfilePage(browser, { ...spec, existingPage: existingPage && !existingPage.isClosed() ? existingPage : undefined, url: site.url }, async (page, { deadline, retainForUser }) => {
    const remaining = () => Math.max(1, deadline - Date.now());
    const composer = page.locator(site.composer).first();
    const retainAttention = async (failureClass, error) => {
      trackAgentPage(page);
      retained.set(key, page);
      // Install one lifetime cleanup; repeated challenges reuse this exact page.
      if (!existingPage) page.once('close', () => {
        if (retained.get(key) === page) retained.delete(key);
        if (spec.guest === true && page.context().pages().length === 0) void closeOwnedContext(page.context());
      });
      const identity = await page.context().newCDPSession(page);
      let targetId;
      try { targetId = (await identity.send('Target.getTargetInfo')).targetInfo.targetId; }
      finally { await identity.detach(); }
      retainForUser();
      return { ok: false, provider: spec.provider, failureClass,
        error, retryable: false, querySubmitted: false,
        verificationTargetId: targetId, verificationPageRetained: true,
        recommendedNextAction: failureClass === 'provider_challenge'
          ? '在 Agent 浏览器中完成此页验证，再继续原查询。此次尚未提交问题；不要重复创建页面。'
          : '尚未找到可用输入区，原页已保留供检查站点界面或登录状态；这不证明登录失效。此次尚未提交问题。' };
    };
    let readiness;
    try {
      const handle = await page.waitForFunction(selector => {
        const visible = e => e && e.getClientRects().length && getComputedStyle(e).visibility !== 'hidden';
        const css = selector.replaceAll(':visible', '');
        if ([...document.querySelectorAll(css)].some(visible)) return 'ready';
        if (/^(Just a moment|请稍候|请稍等)/i.test(document.title.trim())
            || [...document.querySelectorAll('#challenge-running,#challenge-stage,iframe[src*="challenges.cloudflare.com"]')].some(visible)
            || /verify you are human|验证您是真人|确认您是人类/i.test(document.body?.innerText || '')) return 'challenge';
        return false;
      }, site.composer, { timeout: Math.max(1, remaining() - 700), polling: 250 });
      readiness = await handle.jsonValue();
      await handle.dispose();
    } catch {
      if (spec.signal?.aborted) throw Error('agent_browser_chat_cancelled');
      return retainAttention('composer_unavailable', 'agent_browser_chat_composer_unavailable');
    }
    if (readiness === 'challenge') return retainAttention('provider_challenge', 'agent_browser_chat_verification_required');
    await page.evaluate(({ answer, done }) => {
      // This state belongs only to this owned page and expires when it closes.
      window.__v8ChatBaseline = {
        answers: new Map([...document.querySelectorAll(answer)].map(e => [e, e.innerText])),
        done: new Set(document.querySelectorAll(done)), busySeen: false,
      };
    }, site);
    // A new owned page starts a new query. Never mutate a user's existing turn.
    await composer.fill(query, { timeout: remaining() });
    await composer.press('Enter', { timeout: remaining() });
    let previous = '', stableAt = Date.now(), latest = null;
    while (Date.now() < deadline - 100) {
      latest = await page.evaluate(({ answer, stop, done, provider }) => {
        const visible = e => Boolean(e.getClientRects().length) && getComputedStyle(e).visibility !== 'hidden';
        const nodes = [...document.querySelectorAll(answer)].filter(visible);
        // Ignore nested matches, and take only the latest assistant response.
        const roots = nodes.filter(e => !nodes.some(other => other !== e && other.contains(e)));
        const baseline = window.__v8ChatBaseline;
        const fresh = roots.filter(e => !baseline.answers.has(e) || baseline.answers.get(e) !== e.innerText);
        const selected = provider === 'chatgpt' ? fresh.slice(-1) : fresh;
        const text = selected.map(e => e.innerText).join('\n\n').trim();
        const references = [], seen = new Set();
        const add = (url, title) => {
          try {
            const parsed = new URL(url, location.href);
            if (!/^https?:$/.test(parsed.protocol) || parsed.username || parsed.password || parsed.hostname === location.hostname || seen.has(parsed.href)) return;
            seen.add(parsed.href); references.push({ url: parsed.href, title: String(title || parsed.hostname).slice(0,300) });
          } catch { /* Untrusted DOM fields are not a navigation instruction. */ }
        };
        for (const root of selected) {
          root.querySelectorAll('a[href]').forEach(e => { if (visible(e)) add(e.href, e.innerText); });
          root.querySelectorAll('[data-assistant-sources-payload]').forEach(e => {
            if (!visible(e)) return;
            try { const rows = JSON.parse(e.getAttribute('data-assistant-sources-payload')); if (Array.isArray(rows)) rows.slice(0,100).forEach(r => add(r.url, r.title)); } catch {}
          });
        }
        const busy = [...document.querySelectorAll(stop)].some(visible);
        baseline.busySeen ||= busy;
        const completed = [...document.querySelectorAll(done)].some(e => visible(e) && (!baseline.done.has(e) || baseline.busySeen));
        return { text: text.slice(0,100000), textChars: text.length, textTruncated: text.length > 100000,
          references: references.slice(0,40), referencesCount: references.length, completed,
          referencesTruncated: references.length > 40, busy };
      }, { ...site, provider: spec.provider });
      if (latest.text !== previous) { previous = latest.text; stableAt = Date.now(); }
      if (latest.text && latest.completed && !latest.busy && Date.now() - stableAt >= 900) {
        return { ...latest, ok: true, provider: spec.provider, sourceKind: 'ai_generated_answer',
          completion: 'observed_stable', querySubmitted: true, citationsVerified: false };
      }
      await page.waitForTimeout(Math.min(250, remaining()));
    }
    if (latest?.text) return { ...latest, ok: true, provider: spec.provider, sourceKind: 'ai_generated_answer',
      completion: 'partial', querySubmitted: true, citationsVerified: false };
    const identity = await page.context().newCDPSession(page);
    let targetId;
    try { targetId = (await identity.send('Target.getTargetInfo')).targetInfo.targetId; }
    finally { await identity.detach(); }
    let pendingForBrowser = pendingPages.get(browser);
    if (!pendingForBrowser) { pendingForBrowser = new Map(); pendingPages.set(browser, pendingForBrowser); }
    pendingForBrowser.set(key, { page, query, targetId });
    page.once('close', () => { if (pendingForBrowser.get(key)?.page === page) pendingForBrowser.delete(key); });
    retainForUser();
    return { ok: false, provider: spec.provider, failureClass: 'answer_pending',
      error: 'agent_browser_chat_answer_pending', retryable: true, querySubmitted: true,
      verificationTargetId: targetId, verificationPageRetained: true,
      recommendedNextAction: '问题已经提交但原页尚未给出可读正文；请在原 Agent 浏览器页观察或稍后继续同一问题。不要再次提交。' };
  });
  } finally { active.delete(key); }
}
