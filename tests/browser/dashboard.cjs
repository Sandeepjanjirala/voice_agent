/* Run against tests/browser/dashboard_server.py: real Chromium + real Django +
   real templates and JavaScript, with a canned LLM answer. */
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');

const BASE = 'http://127.0.0.1:8767';
const BRIEF = 'Call every parent who sends an admission enquiry, ask which class the child is joining and offer a campus visit.';

async function login(page, username, password) {
  await page.goto(`${BASE}/accounts/login/`);
  await page.locator('[name=username]').fill(username);
  await page.locator('[name=password]').fill(password);
  await page.getByRole('button', { name: 'Sign in', exact: true }).click();
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

    // Sign-in lands on Home, inside the shell.
    await login(page, 'dashboard-review', 'local-disposable-dashboard-check');
    await page.waitForURL(`${BASE}/home/`);
    assert.equal(await page.locator('nav a[aria-current=page]').textContent(), 'Home');
    assert.equal(await page.locator('#stat-employees').textContent(), '0');
    await page.getByText('No employees yet').waitFor();
    await page.screenshot({ path: 'browser-artifacts/dashboard-home-empty.png', fullPage: true });

    // Every sidebar entry opens a page that marks itself current.
    const links = await page.locator('.side-nav a').evaluateAll(all => all.map(a => ({ href: a.getAttribute('href'), label: a.textContent })));
    assert.ok(links.length >= 14, `only ${links.length} sidebar links`);
    for (const { href, label } of links) {
      const response = await page.goto(BASE + href);
      assert.equal(response.status(), 200, `${label} answered ${response.status()}`);
      assert.equal(await page.locator('nav a[aria-current=page]').textContent(), label);
      await noSidewaysScroll(page, label);
    }

    // No section of the sidebar is a placeholder any more.
    assert.equal(await page.locator('.side-nav a.soon').count(), 0);
    await page.goto(`${BASE}/campaigns/`);
    assert.equal(await page.getByText('Not built yet').count(), 0);

    // Hire wizard: an example fills the brief and picks its kind of call.
    await page.locator('.pagebar').getByRole('link', { name: 'Hire an employee' }).click();
    await page.waitForURL(`${BASE}/employees/hire/`);
    await page.getByRole('button', { name: 'Parent support' }).click();
    assert.ok((await page.locator('[name=brief]').inputValue()).startsWith('Answer parents who call'));
    assert.equal(await page.locator('[name=channel]:checked').inputValue(), 'inbound');

    // Too short a brief is refused with a message, and nothing is created.
    await page.locator('[name=brief]').fill('too short');
    await page.getByRole('button', { name: 'Draft this employee' }).click();
    await page.getByText('Describe the job in a sentence or two').waitFor();
    assert.equal(await page.locator('[name=channel]:checked').inputValue(), 'inbound');

    // A real brief: busy state while drafting, then the editable draft.
    await page.locator('[name=brief]').fill(BRIEF);
    await page.locator('[name=channel][value=instant]').check({ force: true });
    await page.screenshot({ path: 'browser-artifacts/dashboard-hire.png', fullPage: true });
    // Submitted from inside the page, so the busy state is read before it navigates.
    const busy = await page.evaluate(() => {
      const form = document.getElementById('hire-form');
      const button = document.getElementById('draft-button');
      form.requestSubmit(button);
      return { text: button.textContent, disabled: button.disabled, busy: form.getAttribute('aria-busy') };
    });
    assert.deepEqual(busy, { text: 'Drafting…', disabled: true, busy: 'true' });
    await page.waitForURL(/\/employees\/[0-9a-f-]{36}\/$/);
    const editorUrl = page.url();
    await page.getByText('Meera is drafted').waitFor();
    assert.equal(await page.locator('h1').textContent(), 'Meera');
    assert.equal(await page.locator('nav a[aria-current=page]').textContent(), 'AI employees');
    assert.equal(await page.locator('#steps .row-item').count(), 3);
    assert.ok((await page.locator('#f-greeting').inputValue()).includes('AI assistant'));
    await page.getByText('Fee structure').waitFor();
    await page.screenshot({ path: 'browser-artifacts/dashboard-editor.png', fullPage: true });

    // Edit: add a step, move it to the top, remove a detail, change strictness.
    await page.locator('#add-step').click();
    const added = page.locator('#steps .row-item').nth(3);
    await added.locator('[name=step_name]').fill('Greet warmly');
    await added.locator('[name=step_guidance]').fill('Thank them for enquiring.');
    for (let index = 3; index > 0; index -= 1) {
      await page.locator('#steps .row-item').nth(index).getByRole('button', { name: 'Move step up' }).click();
    }
    assert.equal(await page.locator('#steps .row-item').first().locator('[name=step_name]').inputValue(), 'Greet warmly');
    assert.ok(await page.locator('#steps .row-item').first().getByRole('button', { name: 'Move step up' }).isDisabled());
    assert.equal(await page.locator('#steps .row-item').first().locator('.num').textContent(), '1.');
    await page.locator('#questions .row-item').first().getByRole('button', { name: 'Remove detail' }).click();
    await page.locator('[name=strictness][value="4"]').check({ force: true });
    await page.locator('#f-name').fill('Lakshmi');
    await page.getByRole('button', { name: 'Save draft' }).click();
    await page.getByText('Draft saved.').waitFor();
    assert.equal(await page.locator('h1').textContent(), 'Lakshmi');
    const names = await page.locator('#steps [name=step_name]').evaluateAll(all => all.map(input => input.value));
    assert.deepEqual(names, ['Greet warmly', 'Confirm parent', 'Ask class', 'Offer visit']);
    assert.equal(await page.locator('#questions .row-item').count(), 1);
    assert.equal(await page.locator('[name=strictness]:checked').inputValue(), '4');

    // Unsaved typing blocks trying and publishing until the draft is saved.
    assert.ok(await page.locator('#publish-button').isEnabled());
    await page.locator('#f-purpose').pressSequentially(' ');
    assert.ok(await page.locator('#unsaved-note').isVisible());
    assert.ok(await page.locator('#publish-button').isDisabled());
    await page.reload();

    // Try the saved draft in the voice studio: the note names the employee.
    await page.getByRole('link', { name: 'Try the draft' }).click();
    await page.waitForURL(/\/\?employee=[0-9a-f-]{36}&source=draft$/);
    assert.ok((await page.locator('#trial-note').textContent()).includes('Lakshmi'));
    assert.ok((await page.locator('#voice-app').getAttribute('data-token-url')).includes('source=draft'));
    await page.getByRole('link', { name: 'Back to Lakshmi' }).click();

    // Publish version 1; with nothing changed, Publish is then disabled.
    await page.locator('#publish-button').click();
    await page.getByText('Version 1 of Lakshmi is published.').waitFor();
    assert.equal((await page.locator('#status-tag').textContent()).trim(), 'Published · version 1');
    assert.ok(await page.locator('#publish-button').isDisabled());
    assert.equal(await page.locator('.steps-bar li.done').count(), 3);

    // Change the draft, publish version 2, then copy version 1 back and discard.
    await page.locator('#f-purpose').fill('Second purpose for the published employee.');
    await page.getByRole('button', { name: 'Save draft' }).click();
    await page.getByText('The draft has changes that are not published.').waitFor();
    await page.locator('#publish-button').click();
    await page.getByText('Version 2 of Lakshmi is published.').waitFor();
    assert.equal(await page.locator('#versions li').count(), 2);
    page.once('dialog', dialog => dialog.accept());
    await page.getByRole('button', { name: 'Copy into draft' }).click();
    await page.getByText('Version 1 is copied into the draft.').waitFor();
    assert.ok((await page.locator('#f-purpose').inputValue()).startsWith('Follow up'));
    assert.equal((await page.locator('#status-tag').textContent()).trim(), 'Published · version 2');
    page.once('dialog', dialog => dialog.accept());
    await page.getByRole('button', { name: 'Discard changes' }).click();
    await page.getByText('Changes discarded.').waitFor();
    assert.ok((await page.locator('#f-purpose').inputValue()).startsWith('Second purpose'));
    await page.screenshot({ path: 'browser-artifacts/dashboard-published.png', fullPage: true });

    // Pause and switch back on.
    await page.getByRole('button', { name: 'Pause', exact: true }).click();
    await page.getByText('Lakshmi is paused.').waitFor();
    assert.equal((await page.locator('#status-tag').textContent()).trim(), 'Paused · version 2');
    await page.getByRole('button', { name: 'Switch on' }).click();
    await page.getByText('Lakshmi is taking calls.').waitFor();

    // Phone test with the published version, on a simulated line with a fake
    // number: placed, followed while it rings and is answered, then ended.
    assert.ok((await page.locator('#test-call').textContent()).includes('+91********01'));
    page.once('dialog', dialog => dialog.dismiss());
    await page.getByRole('button', { name: 'Call the test number' }).click();
    assert.equal(page.url(), editorUrl, 'declining the question must not place a call');
    page.once('dialog', dialog => dialog.accept());
    await page.getByRole('button', { name: 'Call the test number' }).click();
    await page.waitForURL(/\/conversations\/[0-9a-f-]{36}\/$/);
    assert.ok((await page.locator('main').textContent()).includes('Lakshmi, version 2'));
    await page.locator('#live-call').waitFor();
    await page.locator('#call-status', { hasText: 'Answered' }).waitFor({ timeout: 20000 });
    await page.screenshot({ path: 'browser-artifacts/dashboard-live-call.png', fullPage: true });
    await page.getByRole('button', { name: 'End call' }).click();
    await page.getByText('The call was ended.').waitFor();
    assert.equal((await page.locator('#call-status').textContent()).trim(), 'Completed');
    assert.equal(await page.locator('#live-call').count(), 0);
    assert.ok(!(await page.content()).includes('9000000001'), 'the full test number leaked');
    await page.goto(editorUrl);

    // The step limit disables "Add a step".
    await page.locator('#add-step').click();
    await page.locator('#add-step').click();
    assert.ok(await page.locator('#add-step').isDisabled());

    // The list and Home now show the employee.
    await page.goto(`${BASE}/employees/`);
    assert.equal(await page.locator('#roster li').count(), 1);
    await page.getByText('Lakshmi').waitFor();
    await page.screenshot({ path: 'browser-artifacts/dashboard-employees.png', fullPage: true });
    await page.goto(`${BASE}/home/`);
    assert.equal(await page.locator('#stat-employees').textContent(), '1');
    await page.screenshot({ path: 'browser-artifacts/dashboard-home.png', fullPage: true });

    // Draft again asks first; declining changes nothing, accepting redrafts.
    await page.goto(editorUrl);
    page.once('dialog', dialog => dialog.dismiss());
    await page.getByRole('button', { name: 'Draft again' }).click();
    assert.equal(await page.locator('h1').textContent(), 'Lakshmi');
    page.once('dialog', dialog => dialog.accept());
    await page.getByRole('button', { name: 'Draft again' }).click();
    await page.getByText('Drafted again from your description.').waitFor();
    assert.equal(await page.locator('h1').textContent(), 'Meera');

    // Phone, 390 px: the sidebar is a drawer, pages do not scroll sideways.
    const phone = await browser.newPage({ viewport: { width: 390, height: 800 } });
    phone.on('pageerror', error => errors.push(error.message));
    await login(phone, 'dashboard-review', 'local-disposable-dashboard-check');
    await phone.waitForURL(`${BASE}/home/`);
    await noSidewaysScroll(phone, 'home on a phone');
    assert.ok(!(await phone.locator('.sidebar').isVisible()) || (await phone.locator('.sidebar').boundingBox()).x < 0);
    await phone.locator('#menu-button').click();
    assert.equal(await phone.locator('#menu-button').getAttribute('aria-expanded'), 'true');
    await phone.waitForFunction(() => document.querySelector('.sidebar').getBoundingClientRect().x === 0);
    await phone.screenshot({ path: 'browser-artifacts/dashboard-mobile-menu.png' });
    await phone.keyboard.press('Escape');
    assert.equal(await phone.locator('#menu-button').getAttribute('aria-expanded'), 'false');
    for (const path of ['/employees/hire/', editorUrl.replace(BASE, ''), '/employees/', '/leads/', '/', '/calls/', '/agent/']) {
      await phone.goto(BASE + path);
      await noSidewaysScroll(phone, `${path} on a phone`);
    }
    await phone.goto(editorUrl);
    await phone.screenshot({ path: 'browser-artifacts/dashboard-editor-mobile.png', fullPage: true });
    await phone.close();

    // Delete asks first, then removes the employee.
    page.once('dialog', dialog => dialog.accept());
    await page.getByRole('button', { name: 'Delete' }).click();
    await page.waitForURL(`${BASE}/employees/`);
    await page.getByText('Meera was deleted.').waitFor();
    await page.getByText('No employees yet').waitFor();

    // Someone who is not staff gets the voice studio only.
    const other = await browser.newPage();
    await login(other, 'plain-review', 'local-disposable-plain-check');
    await other.waitForURL(`${BASE}/`);
    assert.deepEqual(await other.locator('.side-nav a').allTextContents(), ['Voice studio']);
    assert.equal((await other.goto(`${BASE}/employees/`)).status(), 403);
    assert.equal((await other.goto(`${BASE}/employees/hire/`)).status(), 403);
    await other.close();

    assert.deepEqual(errors.filter(text => !text.includes('403')), []);
    console.log('dashboard browser checks passed');
  } finally {
    await browser.close();
  }
})().catch(error => {
  console.error(error);
  process.exit(1);
});
