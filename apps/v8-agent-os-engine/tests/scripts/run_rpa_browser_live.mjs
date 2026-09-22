import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';
import { resolvePage, inspectorInstallScript, countForCandidate } from '../../scripts/rpa_playwright_inspector_sidecar.mjs';

if (!process.argv.includes('--live')) throw new Error('--live is required');
const outputIndex = process.argv.indexOf('--output');
const output = path.resolve(process.argv[outputIndex + 1] || '');
if (outputIndex < 0 || fs.existsSync(output)) throw new Error('A fresh --output directory is required');
fs.mkdirSync(output, { recursive: true });
const require = createRequire(new URL('../../../v8-agent-os-web/package.json', import.meta.url));
const { chromium } = require('playwright');
const browser = await chromium.launch({ channel: 'msedge', headless: true });
try {
  const context = await browser.newContext();
  const page = await context.newPage();
  const other = await context.newPage();
  const html = '<title>Owned RPA fixture</title><button id="a">Save</button><button id="b">Save</button><script>window.business={down:0,click:0}; document.addEventListener("pointerdown",()=>business.down++,true); document.addEventListener("click",()=>business.click++,true);</script>';
  await page.setContent(html); await other.setContent(html);
  const cdp = await context.newCDPSession(page);
  const { targetInfo } = await cdp.send('Target.getTargetInfo'); await cdp.detach();
  assert.equal(await resolvePage(browser, { targetId: targetInfo.targetId }), page);
  await assert.rejects(resolvePage(browser, { targetId: 'missing-target' }), /target_lost/);
  await assert.rejects(resolvePage(browser, { title: 'Owned RPA fixture' }), /target_ambiguous/);
  await page.evaluate(inspectorInstallScript({ captureMode: 'next_click' }));
  await page.locator('#b').click();
  assert.deepEqual(await page.evaluate(() => business), { down: 0, click: 0 });
  const { events } = await page.evaluate(() => window.__v8RpaInspector.drain());
  assert.equal(events.length, 1);
  const candidate = events[0].candidate;
  const resolved = await countForCandidate(page, candidate);
  assert.equal(resolved.count, 1);
  assert.notEqual(resolved.sameElement, false);
  assert.equal(resolved.selector.kind, 'css');
  await page.screenshot({ path: path.join(output, 'capture-no-business-effect.png') });
  await page.evaluate(() => window.__v8RpaInspector.dispose());
  await page.locator('#b').click();
  assert.deepEqual(await page.evaluate(() => business), { down: 1, click: 1 });
  const proof = { ok: true, evidenceClass: 'real_headless_edge_owned_page',
    checks: ['exact CDP target', 'missing target rejected', 'duplicate title rejected',
      'capture suppresses pointerdown and click', 'unique locator resolves captured element', 'dispose restores business input'] };
  fs.writeFileSync(path.join(output, 'result.json'), JSON.stringify(proof, null, 2));
  console.log(JSON.stringify(proof));
} finally { await browser.close(); }
