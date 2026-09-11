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

async function unansweredChat(context, { newConversation = false } = {}) {
  let submissions = 0;
  await context.exposeBinding('recordPendingSubmission', () => submissions++);
  await context.route('**/*', route => route.fulfill({ contentType: 'text/html', body: `<main>
    <textarea id="mobile-composer-prompt"></textarea><button data-testid="stop-button">Stop</button>
    <script>document.querySelector('textarea').onkeydown = e => {
      if (e.key !== 'Enter') return;
      window.recordPendingSubmission();
      ${newConversation ? "history.pushState({}, '', '/c/created-by-submission');" : ''}
    };</script></main>` }));
  return () => submissions;
}

async function finishAnswer(page, text = 'Late answer') {
  await page.evaluate(text => {
    document.querySelector('[data-testid="stop-button"]')?.remove();
    const answer = document.createElement('div');
    answer.dataset.messageAuthorRole = 'assistant';
    answer.textContent = text;
    document.querySelector('main').append(answer);
    const done = document.createElement('button');
    done.setAttribute('aria-label', 'Copy response');
    document.querySelector('main').append(done);
  }, text);
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

test('a submitted page with no readable answer is retained and never re-submits on retry', { skip: !available }, async () => {
  await withBrowser(async (browser, context, userPage) => {
    const submissions = await unansweredChat(context);
    const first = await queryBrowserChat(browser, { provider: 'chatgpt', query: 'Question', timeoutMs: 3000 });
    assert.equal(first.failureClass, 'answer_pending');
    assert.equal(first.querySubmitted, true);
    const second = await queryBrowserChat(browser, { provider: 'chatgpt', query: 'Question', timeoutMs: 3000 });
    assert.equal(second.failureClass, 'answer_pending');
    assert.equal(second.verificationTargetId, first.verificationTargetId);
    assert.equal(context.pages().length, 2);
    assert.ok(context.pages().includes(userPage));
    assert.equal(submissions(), 1);
  });
});

test('a pending page is reread after a late answer without a duplicate submission', { skip: !available }, async () => {
  await withBrowser(async (browser, context, userPage) => {
    const submissions = await unansweredChat(context, { newConversation: true });
    const first = await queryBrowserChat(browser, { provider: 'chatgpt', query: 'Question', timeoutMs: 1500 });
    assert.equal(first.failureClass, 'answer_pending');
    const page = context.pages().find(p => p !== userPage);
    assert.equal(page.url(), 'https://chatgpt.com/c/created-by-submission');
    await finishAnswer(page);
    const resumed = await queryBrowserChat(browser, { provider: 'chatgpt', query: 'Question', timeoutMs: 3000 });
    assert.equal(resumed.ok, true);
    assert.equal(resumed.completion, 'observed_stable');
    assert.match(resumed.text, /Late answer/);
    assert.equal(submissions(), 1);
    assert.equal(page.isClosed(), true);
    assert.deepEqual(context.pages(), [userPage]);
  });
});

for (const transition of ['server-commit', 'user-navigation', 'different-question']) {
  test(`metaso pending temporary chat ${transition} preserves submission identity`, { skip: !available }, async () => {
    await withBrowser(async (browser, context, userPage) => {
      let submissions = 0;
      await context.exposeBinding('recordPendingSubmission', () => submissions++);
      await context.route('**/*', route => route.fulfill({ contentType: 'text/html', body: `<main>
        <textarea class="search-consult-textarea"></textarea><button id="navigate">Open history</button>
        <script>document.querySelector('textarea').onkeydown=e=>{ if(e.key!=='Enter')return;
          window.recordPendingSubmission(); history.pushState({},'', '/chat/temp-00000000-abcd');
          const title=document.createElement('span');title.dataset.testid='_ResultTitle.span.fixture';
          title.textContent='Question';document.querySelector('main').append(title);
        };</script></main>` }));
      const first = await queryBrowserChat(browser, { provider: 'metaso', query: 'Question', timeoutMs: 1500 });
      assert.equal(first.failureClass, 'answer_pending');
      const page = context.pages().find(p => p !== userPage);
      if (transition === 'user-navigation') await page.locator('#navigate').click();
      await page.evaluate(transition => {
        if (transition === 'different-question') document.querySelector('span').textContent='Unrelated old question';
        history.replaceState({}, '', '/chat/123456789');
        const answer=document.createElement('p');answer.className='markdown';answer.textContent='Late answer';
        const done=document.createElement('button');done.dataset.testid='SearchResultActions.ChatIconButton.fixture';
        document.querySelector('main').append(answer,done);
      }, transition);
      const second = await queryBrowserChat(browser, { provider: 'metaso', query: 'Question', timeoutMs: 3000 });
      assert.equal(submissions, 1);
      if (transition === 'server-commit') {
        assert.equal(second.ok, true);
        assert.equal(second.text, 'Late answer');
        assert.equal(second.completion, 'observed_stable');
        assert.deepEqual(context.pages(), [userPage]);
      } else {
        assert.equal(second.failureClass, 'observation_changed');
        assert.equal(second.text, undefined);
        assert.equal(page.isClosed(), false);
      }
    });
  });
}

test('a different query cannot be attributed to a pending submission', { skip: !available }, async () => {
  await withBrowser(async (browser, context, userPage) => {
    const submissions = await unansweredChat(context);
    const first = await queryBrowserChat(browser, { provider: 'chatgpt', query: 'First question', timeoutMs: 1200 });
    assert.equal(first.failureClass, 'answer_pending');
    const second = await queryBrowserChat(browser, { provider: 'chatgpt', query: 'Different question', timeoutMs: 1200 });
    assert.equal(second.failureClass, 'provider_busy');
    assert.equal(second.querySubmitted, false);
    assert.equal(second.verificationTargetId, first.verificationTargetId);
    assert.ok(context.pages().includes(userPage));
    assert.equal(submissions(), 1);
  });
});

test('a delayed browser poll cannot close a submitted page at the request deadline', { skip: !available }, async () => {
  await withBrowser(async (browser, context, userPage) => {
    const submissions = await unansweredChat(context);
    const createPage = context.newPage.bind(context);
    let queryPage;
    context.newPage = async () => {
      queryPage = await createPage();
      // A delayed browser transport reply crosses the deadline. This is a fault
      // at the browser boundary, not a change to the adapter's state machine.
      queryPage.waitForTimeout = () => new Promise(resolve => setTimeout(resolve, 1600));
      return queryPage;
    };
    const result = await queryBrowserChat(browser, { provider: 'chatgpt', query: 'Question', timeoutMs: 1200 });
    assert.equal(result.failureClass, 'answer_pending');
    assert.equal(result.verificationPageRetained, true);
    assert.equal(queryPage.isClosed(), false);
    assert.deepEqual(context.pages(), [userPage, queryPage]);
    assert.equal(submissions(), 1);
  });
});

test('an ambiguous Enter result retains its page and never retries the submission', { skip: !available }, async () => {
  await withBrowser(async (browser, context, userPage) => {
    const submissions = await unansweredChat(context);
    const createPage = context.newPage.bind(context);
    context.newPage = async () => {
      const page = await createPage();
      const locate = page.locator.bind(page);
      page.locator = (...args) => {
        const locator = locate(...args).first();
        const press = locator.press.bind(locator);
        locator.first = () => locator;
        locator.press = async (...args) => { await press(...args); throw Error('lost browser reply after Enter'); };
        return locator;
      };
      return page;
    };
    let target;
    for (let attempt = 0; attempt < 2; attempt++) {
      const result = await queryBrowserChat(browser, { provider: 'chatgpt', query: 'Question', timeoutMs: 2000 });
      assert.equal(result.failureClass, 'observation_changed');
      assert.equal(result.querySubmitted, null);
      assert.equal(result.retryable, false);
      assert.equal(result.verificationPageRetained, true);
      target ||= result.verificationTargetId;
      assert.equal(result.verificationTargetId, target);
      assert.equal(submissions(), 1);
      assert.equal(context.pages().length, 2);
      assert.ok(context.pages().includes(userPage));
    }
  });
});

for (const change of ['reload', 'lost-baseline', 'same-origin-conversation', 'away-and-back']) {
  test(`a pending ${change} requires attention and cannot resend or capture an old answer`, { skip: !available }, async () => {
    await withBrowser(async (browser, context, userPage) => {
      const submissions = await unansweredChat(context);
      const first = await queryBrowserChat(browser, { provider: 'chatgpt', query: 'Question', timeoutMs: 1200 });
      assert.equal(first.failureClass, 'answer_pending');
      const page = context.pages().find(p => p !== userPage);
      if (change === 'reload') await page.reload();
      else await page.evaluate(change => {
        if (change === 'lost-baseline') delete window.__v8ChatBaseline;
        else {
          history.pushState({}, '', '/c/unrelated-old-conversation');
          if (change === 'away-and-back') history.pushState({}, '', '/');
        }
      }, change);
      await finishAnswer(page, 'UNRELATED OLD ANSWER');
      for (let attempt = 0; attempt < 2; attempt++) {
        const result = await queryBrowserChat(browser, { provider: 'chatgpt', query: 'Question', timeoutMs: 1500 });
        assert.equal(result.failureClass, 'observation_changed');
        assert.equal(result.querySubmitted, true);
        assert.equal(result.retryable, false);
        assert.equal(result.verificationTargetId, first.verificationTargetId);
        assert.equal(result.verificationPageRetained, true);
        assert.equal(result.text, undefined);
        assert.equal(submissions(), 1);
        assert.deepEqual(context.pages(), [userPage, page]);
      }
    });
  });
}

test('cancelling a pending guest closes its owned context, but preserves a page taken over by the user', { skip: !available }, async () => {
  for (const navigated of [false, true]) {
    await withBrowser(async (browser, context, userPage) => {
      const createContext = browser.newContext.bind(browser);
      let guest, submissions;
      browser.newContext = async () => {
        guest = await createContext();
        submissions = await unansweredChat(guest);
        return guest;
      };
      const first = await queryBrowserChat(browser, { provider: 'chatgpt', query: 'Question', guest: true, timeoutMs: 1200 });
      assert.equal(first.failureClass, 'answer_pending');
      const page = guest.pages()[0];
      if (navigated) await page.goto('https://other.example.test/');
      const controller = new AbortController(); controller.abort();
      await assert.rejects(queryBrowserChat(browser, { provider: 'chatgpt', query: 'Question', guest: true,
        timeoutMs: 1500, signal: controller.signal }), /cancelled/);
      assert.equal(submissions(), 1);
      assert.deepEqual(context.pages(), [userPage]);
      assert.equal(page.isClosed(), !navigated);
      assert.deepEqual(browser.contexts(), navigated ? [context, guest] : [context]);
    });
  }
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
    const result = await queryBrowserChat(browser, { provider: 'metaso', query: 'Question', timeoutMs: 2000 });
    assert.equal(result.error, 'agent_browser_chat_composer_unavailable');
    assert.equal(result.failureClass, 'composer_unavailable');
    assert.equal(result.querySubmitted, false);
    assert.equal(result.verificationPageRetained, true);
    assert.equal(context.pages().length, 2);
    assert.ok(context.pages().includes(userPage));
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

test('verification retains one exact page, resuming after human verification submits only once', { skip: !available }, async () => {
  await withBrowser(async (browser, context, userPage) => {
    await context.route('**/*', route => route.fulfill({ contentType: 'text/html', body: '<title>请稍候…</title><main id="challenge-stage"></main>' }));
    const first = await queryBrowserChat(browser, { provider: 'chatgpt', query: 'Question', timeoutMs: 1500 });
    assert.equal(first.failureClass, 'provider_challenge');
    assert.equal(first.querySubmitted, false);
    assert.equal(first.verificationPageRetained, true);
    assert.ok(first.verificationTargetId);
    const page = context.pages().find(p => p !== userPage);
    const second = await queryBrowserChat(browser, { provider: 'chatgpt', query: 'Question', timeoutMs: 1500 });
    assert.equal(second.verificationTargetId, first.verificationTargetId);
    assert.deepEqual(context.pages(), [userPage, page]);
    // Simulate the external human-owned transition, not a CAPTCHA solver.
    await page.setContent(fixture('chatgpt'));
    let submissions = 0;
    await page.exposeFunction('recordSubmission', () => submissions++);
    await page.evaluate(() => document.querySelector('textarea').addEventListener('keydown', e => {
      if (e.key === 'Enter') window.recordSubmission();
    }));
    const resumed = await queryBrowserChat(browser, { provider: 'chatgpt', query: 'Question', timeoutMs: 5000 });
    assert.equal(resumed.completion, 'observed_stable');
    assert.equal(submissions, 1);
    assert.deepEqual(context.pages(), [userPage]);
  });
});

test('manual navigation of a retained page is preserved and never submitted into', { skip: !available }, async () => {
  await withBrowser(async (browser, context, userPage) => {
    await context.route('**/*', route => route.fulfill({ contentType: 'text/html', body: '<title>Just a moment</title><main id="challenge-stage"></main>' }));
    await queryBrowserChat(browser, { provider: 'chatgpt', query: 'Question', timeoutMs: 1500 });
    const page = context.pages().find(p => p !== userPage);
    await page.goto('https://other.example.test/');
    await assert.rejects(queryBrowserChat(browser, { provider: 'chatgpt', query: 'Question', timeoutMs: 1500 }), /verification_target_changed/);
    assert.equal(page.isClosed(), false);
    assert.equal(page.url(), 'https://other.example.test/');
  });
});

test('concurrent queries never race the same provider page', { skip: !available }, async () => {
  await withBrowser(async (browser, context, userPage) => {
    await context.route('**/*', route => route.fulfill({ contentType: 'text/html', body: fixture('chatgpt') }));
    const first = queryBrowserChat(browser, { provider: 'chatgpt', query: 'First', timeoutMs: 5000 });
    const busy = await queryBrowserChat(browser, { provider: 'chatgpt', query: 'Second', timeoutMs: 5000 });
    assert.equal(busy.failureClass, 'provider_busy');
    assert.equal(busy.querySubmitted, false);
    assert.equal((await first).ok, true);
    assert.deepEqual(context.pages(), [userPage]);
  });
});

test('cancelling a retained guest page releases only its owned context', { skip: !available }, async () => {
  await withBrowser(async (browser, context, userPage) => {
    const originalNew = browser.newContext.bind(browser);
    browser.newContext = async () => {
      const guest = await originalNew();
      await guest.route('**/*', route => route.fulfill({ contentType: 'text/html', body: '<title>Just a moment</title>' }));
      return guest;
    };
    const result = await queryBrowserChat(browser, { provider: 'chatgpt', query: 'Question', guest: true, timeoutMs: 1500 });
    assert.equal(result.querySubmitted, false);
    assert.equal(browser.contexts().length, 2);
    const controller = new AbortController(); controller.abort();
    await assert.rejects(queryBrowserChat(browser, { provider: 'chatgpt', query: 'Question', guest: true,
      timeoutMs: 1500, signal: controller.signal }), /cancelled/);
    assert.deepEqual(browser.contexts(), [context]);
    assert.deepEqual(context.pages(), [userPage]);
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
