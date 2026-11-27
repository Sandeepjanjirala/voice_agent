/* Run against tests/browser/dashboard_server.py: real Chromium + real Django,
   one made-up organisation with two locations. Every name and number is fake. */
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');

const BASE = 'http://127.0.0.1:8767';
// Made-up numbers used to check visible numbers and location boundaries.
const NUMBERS = ['9876500001', '9876500002', '9876500003', '9876500004'];
const LIST = [
  'name,phone,subject',
  'Ravi Kumar,98765 00002,Brochure request',
  'Asha Again,+91 9876500001,Repeat of an existing contact',
  'Wrong Number,12345,',
  'Lata Devi,09876500003,',
].join('\n');

// Daily tools stay in the sidebar; setup and reports are explained in Settings.
async function sideLink(page, name) {
  const link = page.locator('.side-nav').getByRole('link', { name, exact: true, includeHidden: true });
  if (!(await link.count())) {
    await page.locator('.side-nav').getByRole('link', { name: 'Settings', exact: true }).click();
    await page.locator('#workspace-tools').getByRole('link', { name, exact: true }).click();
    return;
  }
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

async function visibleNumbers(page, selector, numbers) {
  const text = await page.locator(selector).innerText();
  for (const number of numbers) assert.ok(text.includes('+91' + number), `${selector} does not show ${number} in full`);
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
    page.on('console', message => { if (message.type() === 'error') errors.push(message.text()); });
    page.on('response', response => { if (response.status() >= 500) errors.push(`${response.status()} ${response.url()}`); });
    await login(page, 'dashboard-review', 'local-disposable-dashboard-check');
    assert.equal(await page.locator('#organisation-name').textContent(), 'Demo Organisation');
    // Other browser tests may have placed simulated calls on this server already.
    await page.goto(`${BASE}/conversations/`);
    const startingCalls = Number(await page.locator('#call-total').textContent());
    assert.ok(startingCalls >= 3, 'the three seeded calls are missing');

    // Locations and team: add a location, then a team member for it.
    await sideLink(page, 'Locations & team');
    assert.equal(await page.locator('#campus-table tbody tr').count(), 2);
    await page.locator('[name=campus-name]').fill('East Location');
    await page.locator('[name=campus-city]').fill('Vizag');
    await page.getByRole('button', { name: 'Add location' }).click();
    await page.getByText('East Location is added.').waitFor();
    assert.equal(await page.locator('#campus-table tbody tr').count(), 3);
    await page.getByRole('link', { name: 'Add a team member' }).click();
    await page.locator('[name=username]').fill('east-review');
    await page.locator('[name=password]').fill('a-long-Unusual-pass-91');
    await page.locator('[name=role]').selectOption('campus_staff');
    await page.getByRole('button', { name: 'Add team member' }).click();
    await page.getByText('Choose at least one location for a location team member.').waitFor();
    await page.locator('[name=password]').fill('a-long-Unusual-pass-91');
    await page.getByLabel('East Location').check();
    await page.getByRole('button', { name: 'Add team member' }).click();
    await page.getByText('east-review can now sign in.').waitFor();
    assert.ok((await page.locator('#member-table').textContent()).includes('East Location'));
    await page.screenshot({ path: 'browser-artifacts/crm-campuses.png', fullPage: true });

    // A lead in the North location. Its number is visible from the first page.
    await page.goto(`${BASE}/leads/`);
    await page.getByText('No leads yet').waitFor();
    await page.getByRole('link', { name: 'Add a lead' }).first().click();
    await page.locator('[name=campus]').selectOption({ label: 'North Location' });
    await page.locator('[name=name]').fill('Asha Rao');
    await page.locator('[name=phone]').fill('98765 00001');
    await page.locator('[name=subject]').fill('Wants a call back');
    assert.equal(await page.locator('[name=consent]').isVisible(), false);
    await page.locator('#lead-permission > summary').click();
    await page.locator('[name=consent]').selectOption('granted');
    await page.getByRole('button', { name: 'Add lead' }).click();
    await page.getByText('Say how and when the person agreed').waitFor();
    await page.locator('[name=consent_evidence]').fill('Ticked the box on the enquiry form');
    await page.getByRole('button', { name: 'Add lead' }).click();
    await page.getByText('Lead added.').waitFor();
    const northLead = page.url();
    assert.ok((await page.locator('main').textContent()).includes('+919876500001'));
    assert.ok((await page.locator('#call-block').textContent()).includes('Yes'));
    await visibleNumbers(page, 'main', [NUMBERS[0]]);

    // Work the lead: a note, a follow-up, done, and a new stage.
    await page.locator('#note-text').fill('Prefers a call after 5 pm.');
    await page.getByRole('button', { name: 'Add note' }).click();
    await page.getByText('Note added.').waitFor();
    assert.equal(await page.locator('#notes li').count(), 1);
    await page.locator('[name=task-title]').fill('Ring back after 5 pm');
    await page.locator('[name=task-due_at]').fill('2026-11-01T17:00');
    await page.getByRole('button', { name: 'Add follow-up' }).click();
    await page.getByText('Follow-up added.').waitFor();
    await page.goto(`${BASE}/leads/follow-ups/?who=all`);
    assert.equal(await page.locator('#task-table tbody tr').count(), 1);
    assert.equal(await page.locator('#task-table .overdue').count(), 1);
    await page.getByRole('button', { name: 'Mark done' }).click();
    await page.getByText('Follow-up done.').waitFor();
    await page.getByText('Nothing to follow up').waitFor();
    await page.goto(northLead);
    await page.locator('[name=stage]').selectOption('interested');
    await page.getByRole('button', { name: 'Save', exact: true }).click();
    await page.getByText('Lead updated.').waitFor();
    assert.equal((await page.locator('#lead-stage').textContent()).trim(), 'Interested');
    await page.screenshot({ path: 'browser-artifacts/crm-lead.png', fullPage: true });

    // Import into the South location: checked first, then only the good rows.
    await page.goto(`${BASE}/contacts/import/`);
    await page.locator('[name=campus]').selectOption({ label: 'South Location' });
    await page.locator('[name=calling_purpose]').fill('Follow-up on their enquiry');
    await page.locator('[name=text]').fill(LIST);
    await page.getByRole('button', { name: 'Check the list' }).click();
    await page.locator('#preview').waitFor();
    assert.equal((await page.locator('#preview-new').textContent()).trim(), '2 will be added');
    const preview = await page.locator('#preview').textContent();
    for (const text of ['Already a contact', 'Invalid', '+919876500002']) assert.ok(preview.includes(text), text);
    await visibleNumbers(page, '#preview', NUMBERS.slice(0, 3));
    await page.screenshot({ path: 'browser-artifacts/crm-import.png', fullPage: true });
    await page.locator('#import-button').click();
    await page.getByText('2 contacts added. 1 duplicates, 1 invalid and 0 on the').waitFor();
    assert.equal(await page.locator('#contact-table tbody tr').count(), 3);
    await visibleNumbers(page, '#contact-table', NUMBERS.slice(0, 3));
    await page.locator('[name=q]').fill('+91 98765 00002');
    await page.getByRole('button', { name: 'Filter' }).click();
    assert.equal(await page.locator('#contact-table tbody tr').count(), 1);
    await page.getByRole('link', { name: 'Ravi Kumar' }).click();
    const southContact = page.url();

    // A contact: no agreement recorded, so it may not be called; then opt it out.
    assert.equal((await page.locator('#contact-number').textContent()).trim(), '+919876500002');
    assert.ok((await page.locator('#call-block').textContent()).includes('May not be called'));
    page.once('dialog', dialog => dialog.accept());
    await page.getByRole('button', { name: 'Do not call' }).click();
    await page.getByText('Added to the do-not-call list.').waitFor();
    await visibleNumbers(page, '#contact-number', [NUMBERS[1]]);
    await page.screenshot({ path: 'browser-artifacts/crm-contact.png', fullPage: true });

    // Settings: the list, a new number, the retention policy, the log.
    await sideLink(page, 'Settings');
    assert.equal(await page.locator('#optout-table tbody tr').count(), 1);
    await page.locator('[name=dnc-phone]').fill('98765 00004');
    await page.locator('[name=dnc-reason]').fill('Rang to ask');
    await page.getByRole('button', { name: 'Add to the list' }).click();
    await page.getByText('+919876500004 is on the do-not-call list.').waitFor();
    await page.locator('[name=keep-contact_days]').fill('365');
    await page.getByRole('button', { name: 'Save retention' }).click();
    await page.getByText('Retention policy saved.').waitFor();
    assert.ok((await page.locator('#retention-preview').textContent()).includes('erase 0 contacts'));
    await visibleNumbers(page, '#optout-table', [NUMBERS[1], NUMBERS[3]]);
    await page.screenshot({ path: 'browser-artifacts/crm-settings.png', fullPage: true });
    await page.getByRole('link', { name: 'Access log', exact: true }).click();
    const log = await page.locator('#audit-table').textContent();
    for (const action of ['contact.created', 'contacts.imported', 'contact.viewed', 'contact.opted_out', 'optout.added']) {
      assert.ok(log.includes(action), `the access log lacks ${action}`);
    }
    for (const number of NUMBERS) assert.ok(!log.includes(number), 'access log should not copy phone numbers');

    // Conversations: totals, a filter, and one call's details with notes.
    await sideLink(page, 'Call results');
    assert.equal(await page.locator('#call-total').textContent(), String(startingCalls));
    await page.locator('[name=status]').selectOption('no_answer');
    await page.getByRole('button', { name: 'Filter' }).click();
    assert.equal(await page.locator('#call-table tbody tr').count(), 1);
    await page.goto(`${BASE}/conversations/?status=completed&campus=`);
    await page.locator('#call-table tbody tr', { hasText: '+919876500001' }).last().locator('a').click();
    assert.equal((await page.locator('#call-status').textContent()).trim(), 'Completed');
    assert.ok((await page.locator('main').textContent()).includes('84 seconds'));
    await page.locator('#call-notes').fill('Clear line, caller was happy.');
    await page.getByRole('button', { name: 'Save notes' }).click();
    await page.getByText('Notes saved.').waitFor();
    assert.equal(await page.locator('#call-notes').inputValue(), 'Clear line, caller was happy.');
    await page.screenshot({ path: 'browser-artifacts/crm-conversation.png', fullPage: true });

    // Performance: the numbers match what was just done.
    await sideLink(page, 'Performance');
    assert.equal(await page.locator('#perf-calls').textContent(), String(startingCalls));
    assert.equal(await page.locator('#perf-leads').textContent(), '3');
    const report = await page.locator('#campus-report').textContent();
    for (const campus of ['North Location', 'South Location', 'East Location']) assert.ok(report.includes(campus));
    await page.locator('[name=campus]').selectOption({ label: 'South Location' });
    await page.waitForURL(/campus=/);
    assert.equal(await page.locator('#perf-calls').textContent(), '1');
    await page.screenshot({ path: 'browser-artifacts/crm-performance.png', fullPage: true });
    await page.goto(`${BASE}/home/`);
    assert.equal(await page.locator('#stat-open-leads').textContent(), '3');

    // Location team members of the North location: their own records, and nothing else.
    const staff = await browser.newPage({ viewport: { width: 1280, height: 900 } });
    staff.on('pageerror', error => errors.push(error.message));
    await login(staff, 'north-review', 'local-disposable-north-check');
    const links = await staff.locator('.side-nav a').allTextContents();
    for (const hidden of ['Test line', 'Agent profile']) assert.ok(!links.includes(hidden), hidden);
    // Location team members may place calls, so the one button they get is Make calls.
    assert.deepEqual(await staff.locator('.pagebar a:not(#bell)').evaluateAll(all => all.map(a => a.id)), ['make-calls-button']);
    await staff.goto(`${BASE}/leads/`);
    assert.equal(await staff.locator('#lead-table tbody tr').count(), 1);
    assert.ok((await staff.locator('#lead-table').textContent()).includes('Wants a call back'));
    await staff.goto(`${BASE}/contacts/`);
    assert.equal(await staff.locator('#contact-table tbody tr').count(), 1);
    await visibleNumbers(staff, '#contact-table', [NUMBERS[0]]);
    assert.ok(!(await staff.locator('#contact-table').innerText()).includes(NUMBERS[1]), 'a South location number was shown to North staff');
    await staff.goto(`${BASE}/conversations/`);
    assert.equal(await staff.locator('#call-total').textContent(), '2');
    assert.equal((await staff.goto(northLead)).status(), 200);
    // A South-location contact, and admin pages, by typing the address.
    assert.equal((await staff.goto(southContact)).status(), 404);
    for (const path of ['/settings/access-log/', '/contacts/export/', '/conversations/export/', '/employees/hire/', '/staff/add/']) {
      assert.equal((await staff.goto(BASE + path)).status(), 403, path);
    }
    await staff.goto(`${BASE}/campuses/`);
    assert.equal(await staff.locator('#campus-table tbody tr').count(), 1);
    assert.equal(await staff.locator('#member-table').count(), 0);
    await staff.close();

    // Phone, 390 px: every new page fits.
    const phone = await browser.newPage({ viewport: { width: 390, height: 800 } });
    phone.on('pageerror', error => errors.push(error.message));
    await login(phone, 'dashboard-review', 'local-disposable-dashboard-check');
    for (const path of ['/home/', '/campuses/', '/staff/add/', '/leads/', '/leads/new/', northLead.replace(BASE, ''), '/leads/follow-ups/',
      '/contacts/', southContact.replace(BASE, ''), '/contacts/import/', '/conversations/', '/performance/', '/settings/', '/settings/access-log/']) {
      const response = await phone.goto(BASE + path);
      assert.equal(response.status(), 200, path);
      await noSidewaysScroll(phone, `${path} on a phone`);
    }
    await phone.goto(northLead);
    await phone.screenshot({ path: 'browser-artifacts/crm-lead-mobile.png', fullPage: true });
    await phone.close();

    assert.deepEqual(errors.filter(text => !/40[34]/.test(text)), []);
    console.log('crm browser checks passed');
  } finally {
    await browser.close();
  }
})().catch(error => {
  console.error(error);
  process.exit(1);
});
