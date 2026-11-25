/* Screenshot review of every dashboard screen at 1440 px and 390 px, run
   against tests/browser/dashboard_server.py after platform.cjs has filled it
   with made-up data. Writes browser-artifacts/screens/<name>-<width>.png and
   fails if a page errors, scrolls sideways or shows a full phone number. */
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');

const BASE = 'http://127.0.0.1:8767';
const NUMBERS = ['9876500001', '9876500002', '9000000001'];
const PAGES = [
  ['home', '/home/'],
  ['employees', '/employees/'],
  ['hire', '/employees/hire/'],
  ['leads', '/leads/'],
  ['follow-ups', '/leads/follow-ups/'],
  ['contacts', '/contacts/'],
  ['make-calls', '/make-calls/'],
  ['conversations', '/conversations/'],
  ['performance', '/performance/'],
  ['incoming', '/inbound/'],
  ['numbers', '/numbers/'],
  ['knowledge', '/knowledge/'],
  ['campuses', '/campuses/'],
  ['settings', '/settings/'],
  ['usage', '/usage/'],
  ['operations', '/operations/'],
  ['notifications', '/notifications/'],
];

(async () => {
  const only = process.argv[2] ? new Set(process.argv[2].split(',')) : null;
  fs.mkdirSync('browser-artifacts/screens', { recursive: true });
  const browser = await chromium.launch({ headless: true });
  const problems = [];
  try {
    for (const width of [1440, 390]) {
      const page = await browser.newPage({ viewport: { width, height: width === 390 ? 844 : 960 } });
      const errors = [];
      page.on('pageerror', error => errors.push(error.message));
      page.on('response', response => { if (response.status() >= 500) errors.push(`${response.status()} ${response.url()}`); });
      await page.goto(`${BASE}/accounts/login/`);
      await page.locator('[name=username]').fill('dashboard-review');
      await page.locator('[name=password]').fill('local-disposable-dashboard-check');
      await page.getByRole('button', { name: 'Sign in', exact: true }).click();
      await page.waitForURL(`${BASE}/home/`);
      // Employee and lead detail pages: the first of each.
      const extra = [];
      await page.goto(`${BASE}/employees/`);
      const employee = await page.locator('a.person').first().getAttribute('href').catch(() => null);
      if (employee && employee !== '/employees/') extra.push(['employee', employee]);
      await page.goto(`${BASE}/leads/`);
      const lead = await page.locator('#lead-table a[href^="/leads/"]').first().getAttribute('href').catch(() => null);
      if (lead) extra.push(['lead', lead]);
      // A call page, preferably one whose conversation was kept.
      await page.goto(`${BASE}/conversations/`);
      const calls = await page.locator('#call-table tbody a').evaluateAll(links => links.map(a => a.getAttribute('href'))).catch(() => []);
      let call = calls[0];
      for (const href of calls.slice(0, 10)) {
        await page.goto(BASE + href);
        if (await page.locator('#conversation .chat-turn').count()) { call = href; break; }
      }
      if (call) extra.push(['call', call]);
      for (const [name, path] of [...PAGES, ...extra]) {
        if (only && !only.has(name)) continue;
        const response = await page.goto(BASE + path);
        if (response.status() >= 400) problems.push(`${name} ${width}px: HTTP ${response.status()}`);
        const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
        if (overflow > 1) problems.push(`${name} ${width}px scrolls sideways by ${overflow}px`);
        const html = await page.content();
        for (const number of NUMBERS) if (html.includes(number)) problems.push(`${name} shows ${number}`);
        await page.screenshot({ path: `browser-artifacts/screens/${name}-${width}.png`, fullPage: true });
      }
      if (errors.length) problems.push(...errors.map(e => `${width}px: ${e}`));
      await page.close();
    }
    assert.deepEqual(problems, []);
    console.log('screen review captured');
  } catch (error) {
    console.error(error.message || error);
    process.exitCode = 1;
  } finally {
    await browser.close();
  }
})();
