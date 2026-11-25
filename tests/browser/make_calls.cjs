/* Run against tests/browser/dashboard_server.py after calling.cjs (which
   publishes an AI employee): the everyday flow in real Chromium. Home's four
   steps, Make calls with typed numbers and an Excel upload, the check, the
   consent, calls one after another (a simulated phone line), and the results.
   Made-up numbers only; the line is simulated. */
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const fs = require('node:fs');

const BASE = 'http://127.0.0.1:8767';
const EVENTS_SECRET = 'qa-call-events-secret-not-for-production-0123';

async function workerEvent(payload) {
  const body = JSON.stringify(payload);
  const timestamp = String(Math.floor(Date.now() / 1000));
  const signature = crypto.createHmac('sha256', EVENTS_SECRET).update(`${timestamp}.${body}`).digest('hex');
  const response = await fetch(`${BASE}/api/telephony/call-events/`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-Call-Timestamp': timestamp, 'X-Call-Signature': signature },
    body,
  });
  return response.status;
}

async function noSidewaysScroll(page, label) {
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  assert.ok(overflow <= 1, `${label} scrolls sideways by ${overflow}px`);
}

(async () => {
  fs.mkdirSync('browser-artifacts', { recursive: true });
  const browser = await chromium.launch({ headless: true });
  const errors = [];
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
    page.on('pageerror', error => errors.push(error.message));
    page.on('response', response => { if (response.status() >= 500) errors.push(`${response.status()} ${response.url()}`); });
    await page.goto(`${BASE}/accounts/login/`);
    await page.locator('[name=username]').fill('dashboard-review');
    await page.locator('[name=password]').fill('local-disposable-dashboard-check');
    await page.getByRole('button', { name: 'Sign in', exact: true }).click();
    await page.waitForURL(`${BASE}/home/`);

    // Home: four steps; the sidebar leads with them and folds the rest away.
    assert.equal(await page.locator('#home-steps .home-step').count(), 4);
    const nav = page.locator('.side-nav');
    for (const label of ['Home', 'AI employees', 'Make calls', 'Call results', 'Settings']) {
      assert.ok(await nav.getByRole('link', { name: label, exact: true }).isVisible(), label);
    }
    assert.equal(await nav.getByRole('link', { name: 'Campaigns', exact: true }).isVisible(), false);
    await page.screenshot({ path: 'browser-artifacts/home-steps.png', fullPage: true });

    // Step 2: type a number and upload the Excel list.
    await nav.getByRole('link', { name: 'Make calls', exact: true }).click();
    await page.waitForURL(`${BASE}/make-calls/`);
    assert.equal(await page.locator('.steps-bar .current').textContent(), '2 Numbers');
    await page.locator('[name=purpose]').fill('Fee reminder');
    await page.locator('[name=numbers]').fill('Typed parent, 9000000001\n12345');
    await page.locator('[name=upload]').setInputFiles('.test-tmp/qa-numbers.xlsx');
    await page.screenshot({ path: 'browser-artifacts/make-calls.png', fullPage: true });
    await page.getByRole('button', { name: 'Check numbers' }).click();
    await page.waitForURL(/\/make-calls\/[0-9a-f-]{36}\/$/);
    const listUrl = page.url();

    // The check: two people (the repeated number kept once), one row refused.
    assert.ok((await page.locator('#start-form h2').textContent()).startsWith('2 people will be called'));
    assert.ok((await page.locator('#rejected summary').textContent()).includes('1 row could not be used'));
    assert.ok((await page.locator('.flash').textContent()).includes('listed twice'));
    const html = await page.content();
    assert.ok(!html.includes('9000000001') && !html.includes('9876500077'), 'a full number leaked');
    // Nothing starts without the confirmation.
    await page.locator('#start-calling').click();
    assert.equal(page.url(), listUrl);
    assert.equal(await page.locator('#start-form').isVisible(), true);
    await page.locator('#consent').check();
    await page.locator('#start-calling').click();
    await page.getByText('Calling has started').waitFor();

    // Step 3: the first person is called; the page follows it.
    const first = page.locator('#entries tbody tr').first();
    await first.locator('[data-status]', { hasText: 'Calling now' }).waitFor({ timeout: 15000 });
    const call = await page.evaluate(async () => (await (await fetch('/api/telephony/calls/')).json()).calls[0]);
    assert.equal(await workerEvent({ room: call.room, event: 'answered' }), 204);
    assert.equal(await workerEvent({ room: call.room, event: 'ended', outcome: 'completed', talk_seconds: 31 }), 204);
    // Then the next: not on the approved list, so it is not called, with the reason.
    await page.locator('#list-status', { hasText: 'Finished' }).waitFor({ timeout: 20000 });
    assert.equal((await first.locator('[data-status]').textContent()).trim(), 'Answered');
    const second = page.locator('#entries tbody tr').nth(1);
    assert.equal((await second.locator('[data-status]').textContent()).trim(), 'Could not call');
    assert.ok((await second.locator('[data-reason]').textContent()).includes('OUTBOUND_ALLOWLIST_NUMBERS'));
    assert.ok((await page.locator('[data-counts]').textContent()).includes('1 answered'));
    await page.screenshot({ path: 'browser-artifacts/call-list-done.png', fullPage: true });

    // Step 4: the result opens the call page.
    await first.locator('[data-result] a').click();
    await page.waitForURL(/\/conversations\/[0-9a-f-]{36}\/$/);
    assert.ok((await page.locator('main .eyebrow').textContent()).includes('CALL RESULTS'));

    // Phone width.
    await page.setViewportSize({ width: 390, height: 900 });
    for (const path of ['/home/', '/make-calls/', new URL(listUrl).pathname]) {
      await page.goto(BASE + path);
      await noSidewaysScroll(page, path);
    }
    await page.screenshot({ path: 'browser-artifacts/call-list-mobile.png', fullPage: true });

    assert.deepEqual(errors, []);
    console.log('make calls browser checks passed');
  } finally {
    await browser.close();
  }
})().catch(error => {
  console.error(error);
  process.exit(1);
});
