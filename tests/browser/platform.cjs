/* Milestone E in real Chromium against tests/browser/dashboard_server.py
   (start it fresh for this run). Campus hours, holidays and call handling;
   a lead and its WhatsApp permission; missed callers; the employee's voice
   preview; operations, usage and notifications; staff limits; phone widths.
   Every person and number is made up, and the server cannot leave the machine. */
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const fs = require('node:fs');

const BASE = 'http://127.0.0.1:8767';
const EVENTS_SECRET = 'qa-call-events-secret-not-for-production-0123';
const BRIEF = 'Call every parent who sends an admission enquiry, ask which class the child is joining and offer a campus visit.';
// Made-up numbers. None may ever appear in a page in full.
const NUMBERS = ['9876500001', '9876500002'];

const day = (offset) => {
  const d = new Date();
  d.setDate(d.getDate() + offset);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
};

// Sidebar links: the everyday ones are always shown; the rest are folded
// under "More", which is opened first when needed.
async function sideLink(page, name) {
  const link = page.locator('.side-nav').getByRole('link', { name, exact: true });
  if (!(await link.isVisible())) await page.locator('.side-more summary').click();
  await link.click();
}

async function login(page, username, password) {
  await page.goto(`${BASE}/accounts/login/`);
  await page.locator('[name=username]').fill(username);
  await page.locator('[name=password]').fill(password);
  await page.getByRole('button', { name: 'Sign in', exact: true }).click();
  await page.waitForURL(`${BASE}/home/`);
}

async function noFullNumbers(page, label) {
  const html = await page.content();
  for (const number of NUMBERS) assert.ok(!html.includes(number), `${label} shows ${number} in full`);
}

async function noSidewaysScroll(page, label) {
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  assert.ok(overflow <= 1, `${label} scrolls sideways by ${overflow}px`);
}

async function inboundCall(called, caller, room) {
  const body = JSON.stringify({ room, called, caller });
  const timestamp = String(Math.floor(Date.now() / 1000));
  const signature = crypto.createHmac('sha256', EVENTS_SECRET).update(`${timestamp}.${body}`).digest('hex');
  const response = await fetch(`${BASE}/api/telephony/inbound-context/`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-Call-Timestamp': timestamp, 'X-Call-Signature': signature },
    body,
  });
  return response.status;
}

(async () => {
  fs.mkdirSync('browser-artifacts', { recursive: true });
  const browser = await chromium.launch({ headless: true });
  const errors = [];
  const watch = (page) => {
    page.on('pageerror', error => errors.push(error.message));
    page.on('console', message => { if (message.type() === 'error') errors.push(message.text()); });
    page.on('response', response => { if (response.status() >= 500) errors.push(`${response.status()} ${response.url()}`); });
  };
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
    watch(page);
    await login(page, 'dashboard-review', 'local-disposable-dashboard-check');
    const nav = await page.locator('.side-nav a').allTextContents();
    for (const label of ['Usage & costs', 'Operations']) assert.ok(nav.includes(label), label);
    for (const label of ['Integrations & actions', 'Campaigns', 'Instant leads', 'Voice studio', 'Test line', 'Agent profile']) {
      assert.ok(!nav.includes(label), `${label} was deleted`);
    }

    // --- Campus: opening hours, a holiday, and who takes escalations. ---------------
    await page.goto(`${BASE}/campuses/`);
    await page.locator('#campus-table tr', { hasText: 'North Campus' }).getByRole('link', { name: 'Edit' }).click();
    const campusUrl = page.url();
    await page.getByText('always open').waitFor();
    for (let weekday = 0; weekday < 7; weekday += 1) {
      await page.locator(`[name=hours-open_${weekday}]`).check();
      await page.locator(`[name=hours-opens_${weekday}]`).fill('08:00');
      await page.locator(`[name=hours-closes_${weekday}]`).fill('20:00');
    }
    // A day that closes before it opens is refused, and nothing is saved.
    await page.locator('[name=hours-closes_6]').fill('07:00');
    await page.getByRole('button', { name: 'Save hours' }).click();
    await page.getByText('Sunday must close after it opens.').waitFor();
    await page.locator('[name=hours-closes_6]').fill('20:00');
    await page.getByRole('button', { name: 'Save hours' }).click();
    await page.getByText('Opening hours saved: Mon–Sun 08:00–20:00.').waitFor();
    assert.ok((await page.locator('#hours-summary').textContent()).includes('Mon–Sun 08:00–20:00'));
    await page.locator('[name=holiday-date]').fill(day(10));
    await page.locator('[name=holiday-name]').fill('Founders Day');
    await page.locator('[name=holiday-whole_organisation]').check();
    await page.getByRole('button', { name: 'Add holiday' }).click();
    await page.getByText('Founders Day on').waitFor();
    assert.ok((await page.locator('#campus-holidays').textContent()).includes('Every campus'));
    await page.locator('[name=calls-escalation_user]').selectOption({ label: 'north-review' });
    await page.locator('[name=calls-after_hours_message]').fill('Office hours are 8 to 8 every day.');
    await page.getByRole('button', { name: 'Save call handling' }).click();
    await page.getByText('Call handling saved.').waitFor();
    await page.screenshot({ path: 'browser-artifacts/platform-campus.png', fullPage: true });

    // --- A lead and its WhatsApp permission. ----------------------------------------
    await page.goto(`${BASE}/leads/new/`);
    await page.locator('[name=campus]').selectOption({ label: 'North Campus' });
    await page.locator('[name=name]').fill('Asha Rao');
    await page.locator('[name=phone]').fill('98765 00001');
    await page.locator('[name=subject]').fill('Campus visit');
    await page.locator('[name=consent]').selectOption('granted');
    await page.locator('[name=consent_evidence]').fill('Ticked the box on the enquiry form');
    await page.getByRole('button', { name: 'Add lead' }).click();
    await page.getByText('Lead added.').waitFor();
    const leadUrl = page.url();

    await page.getByRole('link', { name: 'Open contact' }).click();
    await page.locator('#whatsapp [name=whatsapp_consent]').check();
    await page.locator('#whatsapp').getByRole('button', { name: 'Record' }).click();
    await page.getByText('Say how and when they agreed').waitFor();
    await page.locator('#whatsapp [name=whatsapp_consent]').check();
    await page.locator('[name=whatsapp_evidence]').fill('Said yes on the call of 17 Nov');
    await page.locator('#whatsapp').getByRole('button', { name: 'Record' }).click();
    await page.getByText('WhatsApp permission recorded.').waitFor();
    await noFullNumbers(page, 'contact');
    await page.goto(leadUrl);
    assert.equal(await page.locator('#lead-actions').count(), 0, 'approved actions were deleted');
    await noFullNumbers(page, 'lead');
    await page.screenshot({ path: 'browser-artifacts/platform-lead.png', fullPage: true });


    // --- Missed caller: a call to a line with nobody ready to answer. ---------------
    await sideLink(page, 'Numbers & channels');
    await page.locator('[name=number]').fill('+91 40 1234 5679');
    await page.locator('[name=label]').fill('North evening line');
    await page.locator('[name=campus]').selectOption({ label: 'North Campus' });
    await page.getByRole('button', { name: 'Add number' }).click();
    await page.getByText('+914012345679 is added.').waitFor();
    assert.equal(await inboundCall('+914012345679', '+919876500002', 'qa-missed-1'), 404);
    await page.goto(`${BASE}/inbound/`);
    const missedRow = page.locator('#missed-table tbody tr').first();
    assert.ok((await missedRow.textContent()).includes('No employee was ready to answer'));
    await noFullNumbers(page, 'incoming calls');

    // --- The employee: voice preview, where it works. -------------------------------
    await page.goto(`${BASE}/employees/hire/`);
    await page.locator('[name=brief]').fill(BRIEF);
    await page.locator('[name=channel][value=instant]').check({ force: true });
    await page.getByRole('button', { name: 'Draft this employee' }).click();
    await page.waitForURL(/\/employees\/[0-9a-f-]{36}\/$/);
    const editorUrl = page.url();
    await page.locator('#f-speaker').selectOption('kavya');
    await page.locator('#voice-preview-button').click();
    await page.locator('#voice-preview-status', { hasText: /Playing|Ready/ }).waitFor();
    assert.ok(await page.locator('#voice-preview-audio').isVisible());
    assert.equal(await page.locator('#approved-actions').count(), 0);
    await page.getByRole('button', { name: 'Save draft' }).click();
    await page.getByText('Draft saved.').waitFor();
    await page.getByRole('button', { name: 'Publish' }).click();
    await page.getByText('Version 1 of Meera is published.').waitFor();
    assert.ok((await page.locator('#where-it-works').textContent()).includes('Every campus'));
    await page.screenshot({ path: 'browser-artifacts/platform-employee.png', fullPage: true });

    // --- Operations and usage. ------------------------------------------------------
    await page.goto(`${BASE}/operations/`);
    assert.ok((await page.locator('#broker').textContent()).includes('REDIS_URL'));
    assert.ok((await page.locator('#job-table').textContent()).includes('operations.analyse_calls'));
    await page.goto(`${BASE}/usage/`);
    assert.ok((await page.locator('main').textContent()).includes('Voice previews'));
    assert.ok(!(await page.locator('main').textContent()).includes('WhatsApp'));
    await page.screenshot({ path: 'browser-artifacts/platform-operations.png', fullPage: true });

    // --- Campus staff: the missed caller reaches them; admin pages do not. ---------
    const staff = await browser.newPage({ viewport: { width: 1280, height: 900 } });
    watch(staff);
    await login(staff, 'north-review', 'local-disposable-north-check');
    assert.ok(await staff.locator('#bell .badge').isVisible());
    await staff.locator('#bell').click();
    assert.ok((await staff.locator('#notification-list').textContent()).includes('Call back'));
    await staff.goto(`${BASE}/inbound/`);
    await staff.locator('#missed-table').getByRole('button', { name: 'Called back' }).first().click();
    await staff.getByText('Marked as called back.').waitFor();
    assert.equal(await staff.locator('#missed-none').count(), 1);
    for (const path of ['/operations/', '/usage/', campusUrl.replace(BASE, ''), editorUrl.replace(BASE, '')]) {
      assert.equal((await staff.goto(BASE + path)).status(), 403, path);
    }
    await staff.close();

    // --- Phone width: nothing scrolls sideways. --------------------------------------
    const phone = await browser.newPage({ viewport: { width: 390, height: 800 } });
    watch(phone);
    await login(phone, 'dashboard-review', 'local-disposable-dashboard-check');
    for (const path of ['/operations/', '/usage/', '/notifications/', '/inbound/',
      campusUrl.replace(BASE, ''), leadUrl.replace(BASE, ''), editorUrl.replace(BASE, '')]) {
      await phone.goto(BASE + path);
      await noSidewaysScroll(phone, `${path} on a phone`);
    }
    await phone.goto(campusUrl);
    await phone.screenshot({ path: 'browser-artifacts/platform-campus-mobile.png', fullPage: true });
    await phone.goto(leadUrl);
    await phone.screenshot({ path: 'browser-artifacts/platform-lead-mobile.png', fullPage: true });
    await phone.close();

    assert.deepEqual(errors.filter(text => !text.includes('403')), []);
    console.log('platform browser checks passed');
  } catch (error) {
    console.error(error);
    process.exitCode = 1;
  } finally {
    await browser.close();
  }
})();
