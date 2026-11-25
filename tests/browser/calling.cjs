/* Run against tests/browser/dashboard_server.py: real Chromium + real Django,
   a simulated phone line, a canned LLM and made-up people. Covers Milestone D:
   knowledge, employee voice and actions, numbers and inbound routing, a
   rehearsal call with its transcript, analysis and follow-up. */
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const fs = require('node:fs');

const BASE = 'http://127.0.0.1:8767';
const EVENTS_SECRET = 'qa-call-events-secret-not-for-production-0123';
// Made-up numbers. None may ever appear in a page in full.
const NUMBERS = ['9876500001', '9876500002', '9000000001'];

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

// The voice worker's signed upload of a recorded call (voice_agent/recording.py).
async function uploadRecording(room, data, duration, offset) {
  const timestamp = String(Math.floor(Date.now() / 1000));
  const signature = crypto.createHmac('sha256', EVENTS_SECRET)
    .update(Buffer.concat([Buffer.from(`${timestamp}.${room}.${duration}.${offset}.`), data]))
    .digest('hex');
  const response = await fetch(`${BASE}/api/telephony/call-recording/`, {
    method: 'POST',
    headers: {
      'Content-Type': 'audio/ogg', 'X-Call-Timestamp': timestamp, 'X-Call-Signature': signature,
      'X-Call-Room': room, 'X-Recording-Duration': duration, 'X-Recording-Offset': offset,
    },
    body: data,
  });
  return response.status;
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
    // Our own recordings: approved test calls only, after the person agrees.
    await page.locator('[name=org-recording]').selectOption('test');
    await page.locator('[name=org-recording_notice]').fill('May I record this call so the team can review it?');
    await page.getByRole('button', { name: 'Save settings' }).click();
    await page.getByText('Settings saved.').waitFor();

    // Knowledge for the North campus, previewed as a call would get it.
    await sideLink(page, 'Knowledge');
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
    await sideLink(page, 'Numbers & channels');
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
    // The person said yes to the recording question; the worker uploads the audio.
    assert.equal(await workerEvent({ room: call.room, event: 'recording', state: 'started', offset: 0.3 }), 204);
    assert.equal(await uploadRecording(call.room, fs.readFileSync('.test-tmp/qa-call.ogg'), '6.00', '0.30'), 201);
    const status = await workerEvent({
      room: call.room,
      event: 'ended',
      outcome: 'completed',
      talk_seconds: 42,
      end_reason: 'caller_hung_up',
      transcript: [
        { role: 'assistant', text: 'Hello, I am Meera. Is this a good time to talk about your visit?', t: 0.4 },
        { role: 'user', text: 'Yes. My son joins Class 6. Call me back Friday evening on 98765 00001.', t: 6.2, confidence: 0.42 },
        { role: 'assistant', text: 'Thank you, a team member will call', t: 7.1, interrupted: true },
        { role: 'tool', name: 'end_call', ok: true, t: 9.0 },
      ],
    });
    assert.equal(status, 204);
    await page.goto(callUrl);
    const transcript = await page.locator('#conversation').textContent();
    assert.ok(transcript.includes('98******01'), 'the number in the transcript is masked');
    // The conversation as a chat: who said what, when, cut-offs and tools.
    assert.equal(await page.locator('#conversation .chat-turn.from-ai').count(), 2);
    assert.equal(await page.locator('#conversation .chat-turn.from-person').count(), 1);
    // The panel shows all of it, at desktop and phone width: nothing is cut off.
    for (const width of [1440, 390]) {
      await page.setViewportSize({ width, height: 900 });
      const clipped = await page.locator('#conversation').evaluate(panel => panel.scrollHeight - panel.clientHeight);
      assert.ok(clipped <= 1, `the conversation panel is cut off at ${width}px`);
    }
    await page.setViewportSize({ width: 1280, height: 900 });
    assert.ok(transcript.includes('What speech recognition heard, not a recording'));
    for (const text of ['0:06', 'cut off', 'heard unclearly', 'end_call']) {
      assert.ok(transcript.includes(text), `the conversation shows ${text}`);
    }
    // Storing the transcript queued its analysis; this server runs jobs at once.
    assert.ok(await page.getByRole('button', { name: 'Analyse again' }).isVisible());
    assert.equal((await page.locator('#outcome').textContent()).trim(), 'Call back later');
    assert.equal((await page.locator('#resolution').textContent()).trim(), 'Partly resolved');
    assert.ok((await page.locator('#reason').textContent()).includes('campus visit for their son'));
    assert.ok((await page.locator('#next-step').textContent()).includes('Friday evening'));
    assert.ok((await page.locator('#pending').textContent()).includes('A day and time for the visit'));
    const quality = await page.locator('#quality').textContent();
    assert.ok(quality.includes('The visit day was not agreed on the call.'));
    assert.ok(quality.includes('spoke over the AI employee'), 'measured from the call');
    assert.ok(!quality.includes('Invented by the model'), 'a quote not in the conversation is dropped');
    assert.equal((await page.locator('#end-reason').textContent()).trim(), 'The person hung up');
    assert.ok((await page.locator('#answers').textContent()).includes('Class 6'));
    assert.ok((await page.locator('#actions').textContent()).includes('Follow-up added: call back Friday evening.'));
    // The recording: its own section, real controls, linked to the conversation.
    assert.equal((await page.locator('#recording-status').textContent()).trim(), 'Available');
    assert.equal(await page.locator('#recording [data-controls]').isVisible(), true);
    assert.ok((await page.locator('#recording').textContent()).includes('Left ear: the person on the call'));
    const audio = page.locator('#recording audio');
    await page.waitForFunction(() => document.querySelector('#recording audio').readyState >= 1);
    await page.locator('#recording [data-play]').click();
    await page.waitForFunction(() => document.querySelector('#recording audio').currentTime > 1.2);
    assert.equal((await page.locator('#recording [data-play]').textContent()).trim(), 'Pause');
    await page.locator('#recording [data-play]').click();
    assert.equal(await audio.evaluate(el => el.paused), true);
    assert.notEqual((await page.locator('#recording [data-current]').textContent()).trim(), '0:00');
    // Dragging the seek bar moves the audio.
    await page.locator('#recording [data-seek]').evaluate(el => { el.value = '750'; el.dispatchEvent(new Event('input')); });
    assert.ok((await audio.evaluate(el => el.currentTime)) > 3.5, 'the seek bar moved the audio');
    // A line of the conversation plays from where it was said (0:06 minus the 0.3 s offset).
    await page.locator('#conversation .chat-turn.from-person').first().click();
    const at = await audio.evaluate(el => el.currentTime);
    assert.ok(at > 5.6 && at < 6.4, `the line played from ${at}`);
    await audio.evaluate(el => el.pause());
    const download = await page.locator('#recording-download').getAttribute('href');
    const saved = await page.evaluate(async (href) => {
      const response = await fetch(href);
      return { status: response.status, disposition: response.headers.get('content-disposition'), size: (await response.arrayBuffer()).byteLength };
    }, download);
    assert.equal(saved.status, 200);
    assert.ok(saved.disposition.startsWith('attachment') && !saved.disposition.includes('+91'));
    assert.equal(saved.size, fs.statSync('.test-tmp/qa-call.ogg').size);
    await page.setViewportSize({ width: 390, height: 900 });
    await noSidewaysScroll(page, 'the call page with its recording');
    await page.screenshot({ path: 'browser-artifacts/calling-recording-mobile.png', fullPage: true });
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.locator('#snapshot summary').click();
    assert.ok((await page.locator('#snapshot').textContent()).includes('kavya, speed 0.9'));
    await noFullNumbers(page, 'the call page');
    await page.screenshot({ path: 'browser-artifacts/calling-call-result.png', fullPage: true });
    await page.goto(leadUrl);
    assert.ok((await page.locator('#tasks').textContent()).includes('Call back Friday evening'));

    await page.goto(`${BASE}/conversations/?outcome=callback`);
    assert.equal(await page.locator('#call-table tbody tr').count(), 1);
    // What needs attention: the tile and the filter agree.
    await page.goto(`${BASE}/conversations/`);
    assert.equal((await page.locator('#tile-follow-up .tile-value').textContent()).trim(), '1');
    await page.locator('#tile-follow-up').click();
    await page.waitForURL(/attention=follow_up/);
    assert.equal(await page.locator('#call-table tbody tr').count(), 1);
    assert.ok((await page.locator('#call-table').textContent()).includes('Call back later'));

    // Campus staff of the North campus may look but not manage.
    const staff = await browser.newPage({ viewport: { width: 1280, height: 900 } });
    staff.on('pageerror', error => errors.push(error.message));
    await login(staff, 'north-review', 'local-disposable-north-check');
    await staff.goto(`${BASE}/numbers/`);
    assert.equal(await staff.getByRole('button', { name: 'Add number' }).count(), 0);
    assert.equal((await staff.goto(employeeUrl)).status(), 403);
    await staff.close();

    // Phone, 390 px: the new pages fit.
    const phone = await browser.newPage({ viewport: { width: 390, height: 800 } });
    phone.on('pageerror', error => errors.push(error.message));
    await login(phone, 'dashboard-review', 'local-disposable-dashboard-check');
    for (const url of [`${BASE}/numbers/`, `${BASE}/inbound/`, `${BASE}/knowledge/`, callUrl, employeeUrl]) {
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
