/* Read-only screenshot review at 1440 px and 390 px in both themes. Run
   against tests/browser/dashboard_server.py after platform.cjs has filled it
   with made-up data. Optional calling.cjs/make_calls.cjs fixtures add knowledge
   and call-list screens. Apart from signing in, requests are GET/HEAD only.
   Writes browser-artifacts/screens/<theme>/<name>-<width>.png and fails if a
   page errors, scrolls sideways or loses its theme. */
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');

const BASE = 'http://127.0.0.1:8767';
const PAGES = [
  ['home', '/home/'],
  ['employees', '/employees/'],
  ['hire', '/employees/hire/'],
  ['leads', '/leads/'],
  ['follow-ups', '/leads/follow-ups/'],
  ['contacts', '/contacts/'],
  ['contact-import', '/contacts/import/'],
  ['lead-new', '/leads/new/'],
  ['make-calls', '/make-calls/'],
  ['conversations', '/conversations/'],
  ['performance', '/performance/'],
  ['incoming', '/inbound/'],
  ['numbers', '/numbers/'],
  ['knowledge', '/knowledge/'],
  ['campuses', '/campuses/'],
  ['settings', '/settings/'],
  ['access-log', '/settings/access-log/'],
  ['staff-add', '/staff/add/'],
  ['usage', '/usage/'],
  ['operations', '/operations/'],
  ['notifications', '/notifications/'],
];

// Only links from these list tables are followed; forms and action URLs are
// never submitted by the crawl. Missing optional fixtures are reported.
const DETAILS = [
  ['employee', '/employees/', 'a.person[href^="/employees/"]'],
  ['lead', '/leads/', '#lead-table tbody a[href^="/leads/"]'],
  ['contact', '/contacts/', '#contact-table tbody a[href^="/contacts/"]'],
  ['campus-edit', '/campuses/', '#campus-table tbody a[href^="/campuses/"]'],
  ['staff-edit', '/campuses/', '#member-table tbody a[href^="/staff/"]'],
  ['number-edit', '/numbers/', '#number-table tbody a[href^="/numbers/"]'],
  ['knowledge-edit', '/knowledge/', '#knowledge-table tbody a[href^="/knowledge/"]'],
  ['call-list', '/make-calls/', '#recent-lists tbody a[href^="/make-calls/"]'],
];

async function inspect(page, name, path, theme, width, problems) {
  const label = `${name} ${theme} ${width}px`;
  const response = await page.goto(BASE + path);
  if (!response || response.status() >= 400) {
    problems.push(`${label}: HTTP ${response ? response.status() : 'missing response'}`);
  }
  // Let the existing entrance motion finish without turning off the motion
  // itself; the screenshots should reflect the same UI people interact with.
  await page.waitForTimeout(1000);
  const presentation = await page.evaluate(() => ({
    overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
    theme: document.documentElement.getAttribute('data-theme'),
    heading: document.querySelector('main h1')?.textContent?.trim(),
  }));
  if (presentation.overflow > 1) problems.push(`${label} scrolls sideways by ${presentation.overflow}px`);
  if (presentation.theme !== theme) problems.push(`${label} uses ${presentation.theme || 'no'} theme`);
  if (!presentation.heading) problems.push(`${label} has no main heading`);
  await page.screenshot({ path: `browser-artifacts/screens/${theme}/${name}-${width}.png`, fullPage: true });
}

async function discoverDetails(page) {
  const extra = [];
  const missing = [];
  let currentPath = '';
  for (const [name, path, selector] of DETAILS) {
    if (path !== currentPath) await page.goto(BASE + path);
    currentPath = path;
    const link = page.locator(selector).first();
    const href = await link.count() ? await link.getAttribute('href') : null;
    if (href && href.startsWith('/') && !href.startsWith('//') && href !== path) extra.push([name, href]);
    else missing.push(name);
  }
  // Prefer the saved bilingual transcript rather than an empty/failed call.
  await page.goto(`${BASE}/conversations/`);
  const calls = await page.locator('#call-table tbody a[href^="/conversations/"]').evaluateAll(
    links => [...new Set(links.map(link => link.getAttribute('href')))],
  );
  let call = calls[0];
  for (const href of calls.slice(0, 10)) {
    await page.goto(BASE + href);
    if (await page.locator('#conversation .chat-turn').count()) { call = href; break; }
  }
  if (call) extra.push(['call', call]);
  else missing.push('call');
  return { extra, missing };
}

(async () => {
  const only = process.argv[2] ? new Set(process.argv[2].split(',')) : null;
  const browser = await chromium.launch({ headless: true });
  const problems = [];
  let captured = 0;
  try {
    for (const theme of ['light', 'dark']) {
      fs.mkdirSync(`browser-artifacts/screens/${theme}`, { recursive: true });
      for (const width of [1440, 390]) {
        const context = await browser.newContext({ viewport: { width, height: width === 390 ? 844 : 960 } });
        await context.addInitScript(theme => {
          localStorage.setItem('vaani-theme', theme);
        }, theme);
        const page = await context.newPage();
        const errors = [];
        page.on('pageerror', error => errors.push(error.message));
        page.on('response', response => { if (response.status() >= 500) errors.push(`${response.status()} ${response.url()}`); });
        if (!only || only.has('login')) {
          await inspect(page, 'login', '/accounts/login/', theme, width, problems);
          captured += 1;
        } else await page.goto(`${BASE}/accounts/login/`);
        await page.locator('[name=username]').fill('dashboard-review');
        await page.locator('[name=password]').fill('local-disposable-dashboard-check');
        await page.getByRole('button', { name: 'Sign in', exact: true }).click();
        await page.waitForURL(`${BASE}/home/`);
        // A running call-list page normally polls with POST. Prevent that from
        // advancing the fixture during this visual review, as well as any other
        // accidental write triggered by a page.
        const blockedWrites = [];
        await context.route('**/*', async route => {
          const request = route.request();
          if (!['GET', 'HEAD', 'OPTIONS'].includes(request.method())) {
            blockedWrites.push(`${request.method()} ${new URL(request.url()).pathname}`);
            await route.abort('blockedbyclient');
          } else await route.continue();
        });
        const { extra, missing } = await discoverDetails(page);
        if (missing.length) console.log(`${theme} ${width}px: optional fixtures absent: ${missing.join(', ')}`);
        for (const [name, path] of [...PAGES, ...extra]) {
          if (only && !only.has(name)) continue;
          await inspect(page, name, path, theme, width, problems);
          captured += 1;
        }
        if (blockedWrites.length) console.log(`${theme} ${width}px: prevented writes: ${[...new Set(blockedWrites)].join(', ')}`);
        if (errors.length) problems.push(...errors.map(e => `${theme} ${width}px: ${e}`));
        await context.close();
      }
    }
    assert.deepEqual(problems, []);
    console.log(`screen review captured: ${captured} screenshots across light/dark and desktop/mobile`);
  } catch (error) {
    console.error(error.message || error);
    process.exitCode = 1;
  } finally {
    await browser.close();
  }
})();
