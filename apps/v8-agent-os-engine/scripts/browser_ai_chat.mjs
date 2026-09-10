import { withProfilePage } from './browser_profile_read.mjs';

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
  return withProfilePage(browser, { ...spec, url: site.url }, async (page, { deadline }) => {
    const remaining = () => Math.max(1, deadline - Date.now());
    const composer = page.locator(site.composer).first();
    const challengeAtNavigation = await page.evaluate(() =>
      /^(Just a moment|请稍候|请稍等)/i.test(document.title) || Boolean(document.querySelector('#challenge-running,#challenge-stage')));
    try { await composer.waitFor({ state: 'visible', timeout: remaining() }); }
    catch { throw Error(challengeAtNavigation ? 'agent_browser_chat_verification_required' : 'agent_browser_chat_composer_unavailable'); }
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
    throw Error('agent_browser_chat_answer_timeout');
  });
}
