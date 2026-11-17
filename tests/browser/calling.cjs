/* Run against tests/browser/dashboard_server.py: real Chromium + real Django,
   a simulated phone line, a canned LLM and made-up people. Covers Milestone D:
   knowledge, employee voice and actions, numbers and inbound routing, a
   rehearsal call with its transcript, analysis and follow-up, a campaign dry
   run, and an instant-lead source fed through its API. */
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const fs = require('node:fs');

const BASE = 'http://127.0.0.1:8767';
const EVENTS_SECRET = 'qa-call-events-secret-not-for-production-0123';
// Made-up numbers. None may ever appear in a page in full.
const NUMBERS = ['9876500001', '9876500002', '9000000001'];

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

async function deliverLead(url, secret, payload) {
  const response = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-Lead-Secret': secret },
    body: JSON.stringify(payload),
  });
  return { status: response.status, body: await response.json() };
}

(async () => {
  fs.mkdirSync('browser-artifacts', { recursive: true });
  const browser = await chromium.launch({ headless: true });
  const errors = [];
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
    page.on('pageerror', error => errors.push(error.message));
    page.on('console', message => { if (message.type() === 'error') errors.push(message.text()); });
    page.on('response', response => { if (response.status() >= 500) errors.push(`${response.status()} ${response.url()}`); });
    await login(page, 'dashboard-review', 'local-disposable-dashboard-check');

    // The organisation keeps transcripts, so calls can be summarised.
    await page.goto(`${BASE}/settings/`);
    await page.locator('[name=org-transcripts]').selectOption('keep');
    await page.getByRole('button', { name: 'Save name' }).click();
    await page.getByText('Organisation saved.').waitFor();

    // Knowledge for the North campus, previewed as a call would get it.
    await page.locator('.side-nav').getByRole('link', { name: 'Knowledge' }).click();
    await page.locator('#id_kind').selectOption('fact');
    await page.locator('#id_campus').selectOption({ label: 'North Campus' });
    await page.locator('#id_title').fill('Visit hours');
    await page.locator('#id_text').fill('Campus visits are 10 to 1 on weekdays.');
    await page.getByRole('button', { name: 'Add', exact: true }).click();
    await page.getByText('is added. It is used from the next call.').waitFor();
    await page.locator('select[aria-label=Campus]').selectOption({ label: 'North Campus' });
    await page.waitForURL(/campus=/);
    assert.ok((await page.locator('#knowledge-preview').textContent()).includes('Visit hours: Campus visits are 10 to 1'));
    await page.screenshot({ path: 'browser-artifacts/calling-knowledge.png', fullPage: true });

    // An employee with its own voice and an approved action, published.
    await page.goto(`${BASE}/employees/hire/`);
    await page.getByRole('button', { name: 'Admissions enquiry' }).click();
    await page.getByRole('button', { name: 'Draft this employee' }).click();
    await page.waitForURL(/\/employees\/[0-9a-f-]{36}\/$/);
    const employeeUrl = page.url();
    await page.locator('#f-speaker').selectOption('kavya');
    await page.locator('#f-pace').selectOption('0.9');
    await page.locator('input[name=action][value=create_follow_up]').check();
    await page.getByLabel('North Campus').check();
    await page.getByRole('button', { name: 'Save draft' }).click();
    await page.getByText('Draft saved.').waitFor();
    assert.equal(await page.locator('#f-speaker').inputValue(), 'kavya');
    assert.ok(await page.locator('input[name=action][value=create_follow_up]').isChecked());
    await page.locator('#publish-button').click();
    await page.getByText('Version 1 of Meera is published.').waitFor();
    await page.screenshot({ path: 'browser-artifacts/calling-employee.png', fullPage: true });

    // A business line routed to the employee; the inbound page shows it.
    await page.locator('.side-nav').getByRole('link', { name: 'Numbers & channels' }).click();
    await page.locator('[name=number]').fill('+91 40 1234 5678');
    await page.locator('[name=label]').fill('North front desk');
    await page.locator('[name=campus]').selectOption({ label: 'North Campus' });
    await page.locator('[name=employee]').selectOption({ index: 1 });
    await page.locator('[name=routing_enabled]').check();
    await page.getByRole('button', { name: 'Add number' }).click();
    await page.getByText('+914012345678 is added.').waitFor();
    assert.ok((await page.locator('#number-table').textContent()).includes('Routed'));
    assert.ok((await page.locator('#diagnostics').textContent()).includes('Inbound SIP trunk'));
    await page.screenshot({ path: 'browser-artifacts/calling-numbers.png', fullPage: true });
    await page.getByRole('link', { name: 'Incoming calls' }).first().click();
    assert.ok((await page.locator('#routes').textContent()).includes('+914012345678 → Meera'));

    // A lead, rehearsed on the test number with the published employee.
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
    page.once('dialog', dialog => dialog.accept());
    await page.locator('#rehearse').getByRole('button', { name: 'Call the test number' }).click();
    await page.waitForURL(/\/conversations\/[0-9a-f-]{36}\/$/);
    const callUrl = page.url();
    assert.ok((await page.locator('main').textContent()).includes('Rehearsal'));
    assert.ok((await page.locator('#call-employee').textContent()).includes('Meera, version 1'));
    await page.locator('#call-status', { hasText: 'Answered' }).waitFor({ timeout: 20000 });
    await page.getByRole('button', { name: 'End call' }).click();
    await page.getByText('The call was ended.').waitFor();

    // The voice worker reports the end of the call with its transcript.
    const call = await page.evaluate(async () => (await (await fetch('/api/telephony/calls/')).json()).calls[0]);
    const status = await workerEvent({
      room: call.room,
      event: 'ended',
      outcome: 'completed',
      talk_seconds: 42,
      transcript: [
        { role: 'assistant', text: 'Hello, I am Meera. Is this a good time to talk about your visit?' },
        { role: 'user', text: 'Yes. My son joins Class 6. Call me back Friday evening on 98765 00001.' },
      ],
    });
    assert.equal(status, 204);
    await page.goto(callUrl);
    const transcript = await page.locator('#transcript').textContent();
    assert.ok(transcript.includes('98******01'), 'the number in the transcript is masked');
    await page.getByRole('button', { name: 'Analyse now' }).click();
    await page.getByText('The call was analysed.').waitFor();
    assert.equal((await page.locator('#outcome').textContent()).trim(), 'Asked for a call back');
    assert.ok((await page.locator('#answers').textContent()).includes('Class 6'));
    assert.ok((await page.locator('#actions').textContent()).includes('Follow-up added: call back Friday evening.'));
    await page.locator('#snapshot summary').click();
    assert.ok((await page.locator('#snapshot').textContent()).includes('kavya, speed 0.9'));
    await noFullNumbers(page, 'the call page');
    await page.screenshot({ path: 'browser-artifacts/calling-call-result.png', fullPage: true });
    await page.goto(leadUrl);
    assert.ok((await page.locator('#tasks').textContent()).includes('Call back Friday evening'));

    // A campaign: people with permission, agreement recorded, a dry run.
    await page.locator('.side-nav').getByRole('link', { name: 'Campaigns' }).click();
    await page.getByRole('link', { name: 'New campaign' }).first().click();
    await page.locator('[name=name]').fill('Visit reminder');
    await page.locator('[name=campus]').selectOption({ label: 'North Campus' });
    await page.locator('[name=purpose]').fill('Remind parents about booking a campus visit');
    await page.getByRole('button', { name: 'Create campaign' }).click();
    await page.getByText('Campaign created. Add contacts next.').waitFor();
    const campaignUrl = page.url();
    await page.locator('[name=add-source]').selectOption('consented');
    await page.getByRole('button', { name: 'Add', exact: true }).click();
    await page.getByText('1 contacts added; 0 were already in the campaign.').waitFor();
    await page.locator('[name=consent-attested]').check();
    await page.locator('[name=consent-note]').fill('Enquiry forms collected in November');
    await page.getByRole('button', { name: 'Record agreement' }).click();
    await page.getByText('Recorded. The campaign is ready for a dry run.').waitFor();
    await page.getByRole('button', { name: 'Run a dry run' }).click();
    await page.getByText('Dry run done: 1 would be called, 0 skipped. Nobody was called.').waitFor();
    assert.ok((await page.locator('#dry-run-report').textContent()).includes('Asha Rao'));
    assert.ok((await page.locator('main').textContent()).includes('Real calling is switched off'));
    await noFullNumbers(page, 'the campaign page');
    await page.screenshot({ path: 'browser-artifacts/calling-campaign.png', fullPage: true });

    // An instant-lead source: the secret is shown once, then leads arrive by API.
    await page.locator('.side-nav').getByRole('link', { name: 'Instant leads' }).click();
    await page.getByRole('link', { name: 'New lead source' }).first().click();
    await page.locator('[name=name]').fill('Website enquiry form');
    await page.locator('[name=campus]').selectOption({ label: 'North Campus' });
    await page.locator('[name=employee]').selectOption({ index: 1 });
    await page.locator('[name=consent_statement]').fill('By submitting this form you agree that we may call you about your enquiry.');
    await page.getByRole('button', { name: 'Create lead source' }).click();
    await page.locator('#new-secret').waitFor();
    const secret = (await page.locator('#new-secret code').textContent()).trim();
    const sourceUrl = page.url();
    const endpoint = (await page.locator('#endpoint').textContent()).split('\n')[0].replace('POST ', '').trim();
    assert.ok(secret.startsWith('ls_'));
    const wrong = await deliverLead(endpoint, 'ls_wrong', { phone: '98765 00002' });
    assert.equal(wrong.status, 401);
    const accepted = await deliverLead(endpoint, secret, { phone: '98765 00002', name: 'Ravi Kumar', subject: 'Transport' });
    assert.equal(accepted.status, 201);
    assert.equal(accepted.body.result, 'accepted');
    const duplicate = await deliverLead(endpoint, secret, { phone: '+91 98765 00002' });
    assert.equal(duplicate.body.result, 'duplicate');
    await page.reload();
    assert.equal(await page.locator('#new-secret').count(), 0, 'the secret is shown only once');
    assert.ok((await page.locator('#events').textContent()).includes('Accepted'));
    await page.getByRole('button', { name: 'Send a test lead' }).click();
    await page.getByText('Test delivery: Test (nothing stored).').waitFor();
    await page.getByRole('button', { name: 'Run a dry run' }).click();
    await page.getByText(/Dry run done: 1 would be called/).waitFor();
    await noFullNumbers(page, 'the lead source page');
    await page.screenshot({ path: 'browser-artifacts/calling-source.png', fullPage: true });
    await page.goto(`${BASE}/conversations/?outcome=callback`);
    assert.equal(await page.locator('#call-table tbody tr').count(), 1);

    // Campus staff of the North campus may look but not manage.
    const staff = await browser.newPage({ viewport: { width: 1280, height: 900 } });
    staff.on('pageerror', error => errors.push(error.message));
    await login(staff, 'north-review', 'local-disposable-north-check');
    await staff.goto(campaignUrl);
    assert.equal(await staff.getByRole('button', { name: 'Run a dry run' }).count(), 0);
    await staff.goto(sourceUrl);
    assert.equal(await staff.getByRole('button', { name: 'Send a test lead' }).count(), 0);
    assert.ok(!(await staff.content()).includes(secret));
    await staff.goto(`${BASE}/numbers/`);
    assert.equal(await staff.getByRole('button', { name: 'Add number' }).count(), 0);
    assert.equal((await staff.goto(employeeUrl)).status(), 403);
    await staff.close();

    // Phone, 390 px: the new pages fit.
    const phone = await browser.newPage({ viewport: { width: 390, height: 800 } });
    phone.on('pageerror', error => errors.push(error.message));
    await login(phone, 'dashboard-review', 'local-disposable-dashboard-check');
    for (const url of [`${BASE}/numbers/`, `${BASE}/inbound/`, `${BASE}/knowledge/`, `${BASE}/campaigns/`, campaignUrl,
      `${BASE}/instant/`, sourceUrl, callUrl, employeeUrl]) {
      const response = await phone.goto(url);
      assert.equal(response.status(), 200, url);
      await noSidewaysScroll(phone, `${url} on a phone`);
    }
    await phone.goto(callUrl);
    await phone.screenshot({ path: 'browser-artifacts/calling-call-mobile.png', fullPage: true });
    await phone.close();

    assert.deepEqual(errors.filter(text => !/40[134]/.test(text)), []);
    console.log('calling browser checks passed');
  } finally {
    await browser.close();
  }
})().catch(error => {
  console.error(error);
  process.exit(1);
});
