import assert from 'node:assert/strict';
import test from 'node:test';
import fs from 'node:fs';
import { createRequire } from 'node:module';
import { queryBrowserChat } from '../../scripts/browser_ai_chat.mjs';
import { readProfilePage } from '../../scripts/browser_profile_read.mjs';

const require = createRequire(import.meta.url);
let chromium;
try { ({ chromium } = require('../../../v8-agent-os-admin/node_modules/playwright')); } catch {}
const executablePath = process.env.V8_BROWSER_TEST_EXECUTABLE ||
  (process.platform === 'win32' ? 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe' : '');
const available = chromium && executablePath && fs.existsSync(executablePath);

function fixture(provider, { partial = false, noComposer = false } = {}) {
  const composer = provider === 'metaso' ? '<textarea class="search-consult-textarea"></textarea>' : '<textarea id="mobile-composer-prompt"></textarea>';
  return `<!doctype html><html><body><nav>Unrelated user history</nav><main>
    <p style="display:none">Scan QR to login</p><div>Visible homepage — a composer is not an article.</div>
    <input type="password" value="SECRET-INPUT-CANARY">
    ${noComposer ? '' : composer}<button data-testid="StopButton" aria-label="Stop" hidden>Stop</button><section id="answer"></section></main>
    <script>
      document.querySelector('textarea')?.addEventListener('keydown', e => {
        if(e.key !== 'Enter') return;
        document.querySelector('[data-testid="StopButton"]').hidden=false;
        setTimeout(() => {
          const root=document.querySelector('#answer');
          root.innerHTML=${JSON.stringify(provider === 'metaso'
            ? '<p data-testid="_MarkDownContent.PComponent.fixture">Observed answer: <a href="https://docs.example.org/source">Source</a></p>'
            : '<div class="fixture_assistantMessage">Observed answer<button data-assistant-sources-payload=""></button></div>')};
          const source=document.querySelector('[data-assistant-sources-payload]');
          if(source) source.setAttribute('data-assistant-sources-payload',JSON.stringify([{title:'Source',url:'https://docs.example.org/source'},{url:'javascript:alert(1)'},{url:'https://user:password@invalid.test/'}]));
          ${partial ? '' : `document.querySelector('[data-testid="StopButton"]').hidden=true;
          const done=document.createElement('button');done.textContent='Done';done.setAttribute('aria-label','Copy response');done.dataset.testid='SearchResultActions.ChatIconButton.fixture';root.append(done);`}
        },50);
      });
    </script></body></html>`;
}

async function withBrowser(fn) {
  const browser = await chromium.launch({ executablePath, headless: true });
  const context = await browser.newContext();
  const userPage = await context.newPage();
  try { await fn(browser, context, userPage); }
  finally { await browser.close(); }
}

for (const provider of ['metaso', 'chatgpt']) {
  test(`${provider}: real DOM submission, answer/citation extraction, owned-page cleanup`, { skip: !available }, async () => {
    await withBrowser(async (browser, context, userPage) => {
      await context.route('**/*', route => route.fulfill({ contentType: 'text/html', body: fixture(provider) }));
      const result = await queryBrowserChat(browser, { provider, query: 'A focused public question', timeoutMs: 5000 });
      assert.equal(result.ok, true);
      assert.match(result.text, /Observed answer/);
      assert.equal(result.sourceKind, 'ai_generated_answer');
      assert.equal(result.completion, 'observed_stable');
      assert.equal(result.citationsVerified, false);
      assert.deepEqual(result.references.map(r => r.url), ['https://docs.example.org/source']);
      assert.equal(result.contextReused, true);
      assert.deepEqual(context.pages(), [userPage]);
      assert.doesNotMatch(JSON.stringify(result), /SECRET-INPUT-CANARY|Unrelated user history|Scan QR/);
    });
  });
}

test('a stalled generation returns captured partial content without pretending completed', { skip: !available }, async () => {
  await withBrowser(async (browser, context, userPage) => {
    await context.route('**/*', route => route.fulfill({ contentType: 'text/html', body: fixture('metaso', { partial: true }) }));
    const result = await queryBrowserChat(browser, { provider: 'metaso', query: 'Question', timeoutMs: 1800 });
    assert.equal(result.completion, 'partial');
    assert.equal(result.busy, true);
    assert.deepEqual(context.pages(), [userPage]);
  });
});

test('old answers and old done controls cannot complete a new query', { skip: !available }, async () => {
  await withBrowser(async (browser, context) => {
    await context.route('**/*', route => route.fulfill({ contentType: 'text/html', body: `<main>
      <textarea class="search-consult-textarea"></textarea><p class="markdown">OLD ANSWER</p>
      <button data-testid="SearchResultActions.ChatIconButton.old">Copy</button></main>
      <script>document.querySelector('textarea').onkeydown=e=>{if(e.key==='Enter')setTimeout(()=>document.querySelector('p').textContent='NEW ANSWER',1800)}</script>` }));
    const result = await queryBrowserChat(browser, { provider: 'metaso', query: 'New question', timeoutMs: 3300 });
    assert.equal(result.text, 'NEW ANSWER');
    assert.equal(result.completion, 'partial', 'the old done button is not a completion receipt for this answer');
  });
});

test('missing composer reports the specific state and never guesses an authentication failure', { skip: !available }, async () => {
  await withBrowser(async (browser, context, userPage) => {
    await context.route('**/*', route => route.fulfill({ contentType: 'text/html', body: fixture('metaso', { noComposer: true }) }));
    await assert.rejects(queryBrowserChat(browser, { provider: 'metaso', query: 'Question', timeoutMs: 1000 }),
      error => error.message === 'agent_browser_chat_composer_unavailable');
    assert.deepEqual(context.pages(), [userPage]);
  });
});

test('guest requests never reuse an existing login context', { skip: !available }, async () => {
  await withBrowser(async (browser, context, userPage) => {
    let guestClosed = false;
    const wrapped = { contexts: () => assert.fail('guest must not inspect profiles'), newContext: async () => {
      const guest = await browser.newContext();
      guest.on('close', () => { guestClosed = true; });
      await guest.route('**/*', route => route.fulfill({ contentType: 'text/html', body: fixture('chatgpt') }));
      return guest;
    }};
    const result = await queryBrowserChat(wrapped, { provider: 'chatgpt', query: 'Question', timeoutMs: 5000, guest: true });
    assert.equal(result.contextReused, false);
    assert.equal(guestClosed, true);
    assert.deepEqual(context.pages(), [userPage]);
  });
});

test('verification pages report a challenge rather than a lost login', { skip: !available }, async () => {
  await withBrowser(async (browser, context) => {
    await context.route('**/*', route => route.fulfill({ contentType: 'text/html', body: '<title>请稍候…</title><main id="challenge-stage"></main>' }));
    await assert.rejects(queryBrowserChat(browser, { provider: 'chatgpt', query: 'Question', timeoutMs: 1500 }), /agent_browser_chat_verification_required/);
  });
});

test('disconnect cancellation closes only the query page', { skip: !available }, async () => {
  await withBrowser(async (browser, context, userPage) => {
    await context.route('**/*', route => route.fulfill({ contentType: 'text/html', body: fixture('chatgpt', { partial: true }) }));
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 800);
    try {
      await assert.rejects(queryBrowserChat(browser, { provider: 'chatgpt', query: 'Question', timeoutMs: 5000, signal: controller.signal }), /agent_browser_chat_cancelled/);
      assert.deepEqual(context.pages(), [userPage]);
    } finally { clearTimeout(timer); }
  });
});

test('SPA visible text omits hidden login adverts and secret inputs', { skip: !available }, async () => {
  await withBrowser(async (browser, context) => {
    await context.route('**/*', route => route.fulfill({ contentType: 'text/html', body: fixture('metaso') }));
    const result = await readProfilePage(browser, { url: 'https://metaso.cn/', timeoutMs: 5000 });
    assert.match(result.visibleText, /Visible homepage/);
    assert.doesNotMatch(result.visibleText, /Scan QR|SECRET|Unrelated user history/);
  });
});

test('an unauthorized embedded widget is blocked without discarding the main answer', { skip: !available }, async () => {
  await withBrowser(async (browser, context, userPage) => {
    let foreignHits = 0;
    await context.route('**/*', route => {
      if (new URL(route.request().url()).hostname === 'widget.example.org') foreignHits++;
      return route.fulfill({ contentType: 'text/html', body: fixture('chatgpt').replace('</main>',
        '<iframe src="https://widget.example.org/login?secret=CANARY"></iframe></main>') });
    });
    const result = await queryBrowserChat(browser, { provider: 'chatgpt', query: 'Question', timeoutMs: 5000 });
    assert.match(result.text, /Observed answer/);
    assert.deepEqual(result.blockedEmbeddedHosts, ['widget.example.org']);
    assert.equal(foreignHits, 0);
    assert.deepEqual(context.pages(), [userPage]);
    assert.doesNotMatch(JSON.stringify(result), /CANARY/);
  });
});
