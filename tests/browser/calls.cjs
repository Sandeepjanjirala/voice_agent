/* Run against tests/browser/calls_server.py: real Chromium + real Django + real
   templates and JavaScript, with a simulated LiveKit and fake numbers. */
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const fs = require('node:fs');

const BASE = 'http://127.0.0.1:8766';
const SECRET = 'qa-call-events-secret-not-for-production-0123';
const FULL_NUMBERS = ['+919000000001', '+918000000002'];

async function workerEvent(payload, secret = SECRET) {
  const body = JSON.stringify(payload);
  const timestamp = String(Math.floor(Date.now() / 1000));
  const signature = crypto.createHmac('sha256', secret).update(`${timestamp}.${body}`).digest('hex');
  const response = await fetch(`${BASE}/api/telephony/call-events/`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-Call-Timestamp': timestamp, 'X-Call-Signature': signature },
    body,
  });
  return response.status;
}

async function login(page, username, password) {
  await page.goto(`${BASE}/accounts/login/?next=/calls/`);
  await page.locator('[name=username]').fill(username);
  await page.locator('[name=password]').fill(password);
  await page.getByRole('button', { name: 'Sign in', exact: true }).click();
}

async function latestCall(page) {
  return page.evaluate(async () => (await (await fetch('/api/telephony/calls/')).json()).calls[0]);
}

async function placeCall(page) {
  await page.locator('#call').click();
  await page.locator('#confirm').waitFor({ state: 'visible' });
  await page.locator('#confirm-call').click();
}

async function status(page, text, timeout = 12000) {
  await page.locator('#call-status').filter({ hasText: text }).waitFor({ timeout });
}

(async () => {
  fs.mkdirSync('browser-artifacts', { recursive: true });
  const browser = await chromium.launch({ headless: true });
  const errors = [];
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
    page.on('pageerror', error => errors.push(error.message));
    await login(page, 'calls-review', 'local-disposable-calls-check');
    await page.waitForURL(`${BASE}/calls/`);

    // Masked details only; ready to call.
    const html = await page.content();
    for (const number of FULL_NUMBERS) assert.ok(!html.includes(number), `full number ${number} leaked into the page`);
    assert.equal(await page.locator('#destination').textContent(), '+91********01');
    assert.equal(await page.locator('.ready-pill').textContent(), 'Ready');
    assert.equal(await page.locator('nav a[aria-current=page]').textContent(), 'Phone calls');
    await page.screenshot({ path: 'browser-artifacts/calls-desktop.png', fullPage: true });

    // Cancelling the confirmation places no call.
    await page.locator('#call').click();
    await page.locator('#confirm').waitFor({ state: 'visible' });
    await page.locator('#confirm-cancel').click();
    assert.equal(await page.locator('#confirm').isVisible(), false);
    assert.equal(await latestCall(page), undefined, 'a cancelled confirmation created a call');

    // Call 1: initiating -> ringing -> answered -> completed by a worker report.
    await placeCall(page);
    await status(page, 'Initiating', 4000);
    assert.equal(await page.locator('#call').isDisabled(), true, 'Call stays enabled during a call');
    await status(page, 'Ringing');
    await page.screenshot({ path: 'browser-artifacts/calls-ringing.png', fullPage: true });
    await status(page, 'Answered');
    assert.equal(await page.locator('[data-step=answered]').getAttribute('data-state'), 'current');
    assert.equal(await page.locator('#hangup').isVisible(), true);
    const first = await latestCall(page);
    for (const number of FULL_NUMBERS) assert.ok(!JSON.stringify(first).includes(number));
    assert.equal(await workerEvent({ room: first.room, event: 'answered' }), 204);
    assert.equal(await workerEvent({
      room: first.room, event: 'ended', outcome: 'completed', talk_seconds: 41,
      agent_turns: 6, user_turns: 6, interruptions: 1, e2e_latency_p50: 1.42, e2e_latency_max: 1.8,
      llm_ttft_p50: 0.36, tts_ttfb_p50: 0.19,
    }), 204);
    await status(page, 'Completed');
    assert.equal(await page.locator('#m-duration').textContent(), '41.0 s');
    assert.equal(await page.locator('#m-latency').textContent(), '1.4 s');
    assert.equal(await page.locator('#hangup').isVisible(), false);
    assert.equal(await page.locator('#call').isDisabled(), false);
    await page.locator('#history tr').first().locator('.badge', { hasText: 'Completed' }).waitFor();
    await page.screenshot({ path: 'browser-artifacts/calls-completed.png', fullPage: true });

    // A forged report is refused.
    assert.equal(await workerEvent({ room: first.room, event: 'ended', outcome: 'failed' }, 'wrong-secret'), 403);

    // Call 2: rejected while ringing; the SIP reason is shown as text, never as HTML.
    await page.waitForTimeout(5200); // per-user start throttle
    await placeCall(page);
    await status(page, 'Ringing');
    const duplicate = await page.evaluate(async () => (await fetch('/api/telephony/calls/', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': document.querySelector('[name=csrfmiddlewaretoken]').value },
      body: JSON.stringify({ confirm: true }),
    })).status);
    assert.ok([409, 429].includes(duplicate), `duplicate call was not refused (HTTP ${duplicate})`);
    const second = await latestCall(page);
    assert.equal(await workerEvent({
      room: second.room, event: 'ended', outcome: 'busy_or_rejected', sip_status_code: 486,
      sip_status: '<img src=x onerror="window.xss=1">',
    }), 204);
    await status(page, 'Rejected');
    const detail = await page.locator('#call-detail').textContent();
    assert.match(detail, /SIP 486/);
    assert.match(detail, /<img src=x/);
    assert.equal(await page.evaluate(() => window.xss), undefined, 'SIP text was rendered as HTML');
    assert.equal(await page.locator('[data-step=final]').getAttribute('data-state'), 'failed');
    const rows = await page.locator('#history tr').count();
    assert.equal(rows, 2);

    // Call 3: answered, then ended from the page.
    await page.waitForTimeout(5200);
    await placeCall(page);
    await status(page, 'Answered');
    // Reloading during a call resumes following it.
    await page.reload();
    await status(page, 'Answered');
    assert.equal(await page.locator('#call').isDisabled(), true);
    await page.locator('#hangup').click();
    await status(page, 'Completed');
    assert.equal(await page.locator('#call').isDisabled(), false);

    // Phone-width layout.
    await page.setViewportSize({ width: 390, height: 900 });
    await page.screenshot({ path: 'browser-artifacts/calls-mobile.png', fullPage: true });
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1);
    assert.equal(overflow, false, 'page scrolls sideways at phone width');

    // A signed-in user without staff rights cannot open the page or the API.
    const other = await browser.newPage();
    await login(other, 'plain-review', 'local-disposable-plain-check');
    const forbidden = await other.goto(`${BASE}/calls/`);
    assert.equal(forbidden.status(), 403);
    const api = await other.evaluate(async () => (await fetch('/api/telephony/calls/')).status);
    assert.equal(api, 403);
    await other.goto(`${BASE}/`);
    assert.equal(await other.locator('a[href="/calls/"]').count(), 0);

    assert.deepEqual(errors, []);
    console.log('calls page: all browser checks passed');
  } finally {
    await browser.close();
  }
})().catch(error => {
  console.error(error);
  process.exit(1);
});
