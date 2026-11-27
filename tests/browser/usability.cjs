// Disposable dashboard QA only. No calls or external service requests are made.
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const BASE = 'http://127.0.0.1:8767';

(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 390, height: 844 } });
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(`${BASE}/accounts/login/`);
    await page.locator('[name=username]').fill('dashboard-review');
    await page.locator('[name=password]').fill('local-disposable-dashboard-check');
    await page.getByRole('button', { name: 'Sign in', exact: true }).click();
    await page.waitForURL('**/home/');
    assert.equal(await page.locator('#sidebar').evaluate(el => el.inert), true);
    await page.locator('#menu-button').click();
    assert.equal(await page.locator('#menu-close').evaluate(el => el === document.activeElement), true);
    assert.equal(await page.locator('.page').evaluate(el => el.inert), true);
    await page.keyboard.press('Shift+Tab');
    assert.equal(await page.locator('.side-foot button').evaluate(el => el === document.activeElement), true);
    await page.keyboard.press('Tab');
    assert.equal(await page.locator('#menu-close').evaluate(el => el === document.activeElement), true);

    // Opening help from the phone drawer releases the dashboard for use alongside it.
    await page.locator('.side-helper [data-open-assist]').click();
    assert.equal(await page.locator('#assist-panel').evaluate(el => el.open && !el.matches(':modal')), true);
    assert.equal(await page.locator('#menu-button').getAttribute('aria-expanded'), 'false');
    assert.equal(await page.locator('#scrim').evaluate(el => el.hidden), true);
    assert.equal(await page.locator('.page').evaluate(el => el.inert), false);
    assert.equal(await page.locator('#sidebar').evaluate(el => el.inert), true);
    await page.keyboard.press('Escape');
    await page.waitForFunction(() => !document.querySelector('#assist-panel').open);
    await page.waitForFunction(() => document.activeElement.id === 'assist-launcher');

    // Switching to navigation closes help without discarding a draft.
    await page.locator('#assist-launcher').click();
    await page.locator('#assist-input').fill('Keep this draft while I use the menu.');
    await page.locator('#menu-button').click();
    assert.equal(await page.locator('#menu-button').getAttribute('aria-expanded'), 'true');
    assert.equal(await page.locator('#assist-panel').evaluate(el => el.open), false);
    assert.equal(await page.locator('#menu-close').evaluate(el => el === document.activeElement), true);
    await page.keyboard.press('Shift+Tab');
    assert.equal(await page.locator('.side-foot button').evaluate(el => el === document.activeElement), true);
    await page.keyboard.press('Tab');
    assert.equal(await page.locator('#menu-close').evaluate(el => el === document.activeElement), true);
    await page.keyboard.press('Escape');
    assert.equal(await page.locator('#menu-button').evaluate(el => el === document.activeElement), true);
    assert.equal(await page.locator('#sidebar').evaluate(el => el.inert), true);
    await page.locator('#assist-launcher').click();
    assert.equal(await page.locator('#assist-input').inputValue(), 'Keep this draft while I use the menu.');
    await page.locator('#assist-close').click();

    await page.locator('#menu-button').click();
    await page.locator('#menu-close').click();
    assert.equal(await page.locator('#menu-button').getAttribute('aria-expanded'), 'false');
    await page.locator('#menu-button').click();
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.waitForFunction(() => document.querySelector('#scrim').hidden);
    assert.equal(await page.locator('.page').evaluate(el => el.inert), false);
    assert.equal(await page.locator('#sidebar').evaluate(el => el.inert), false);
    await page.setViewportSize({ width: 390, height: 844 });
    await page.waitForFunction(() => document.activeElement.id === 'menu-button');
    assert.equal(await page.locator('#sidebar').evaluate(el => el.inert), true);
    await page.setViewportSize({ width: 1440, height: 1000 });

    // Exercise the production refresh script on a local response fixture; no call is started.
    await page.goto(`${BASE}/conversations/?status=completed`);
    const callPath = await page.locator('#call-table tbody a').first().getAttribute('href');
    const response = await page.request.get(BASE + callPath);
    const html = (await response.text()).replace(
      '<main id="main-content" tabindex="-1">',
      '<main id="main-content" tabindex="-1"><div id="live-call" data-call-refresh="1000"><span id="call-refresh-status"></span></div>'
    );
    const fixtureURL = `${BASE}/review-refresh-fixture/`;
    let loads = 0;
    await page.route(fixtureURL, async route => {
      loads += 1;
      await route.fulfill({ status: 200, contentType: 'text/html', body: html });
    });
    await page.goto(fixtureURL);
    await page.waitForEvent('load', { timeout: 5000 });
    assert.ok(loads >= 2, 'An idle active call refreshes automatically');
    await page.locator('#call-notes').fill('Keep these unsaved follow-up notes.');
    await page.locator('#call-notes').blur();
    const editedLoads = loads;
    await page.waitForTimeout(1500);
    assert.equal(loads, editedLoads, 'Automatic refresh must not discard unsaved notes');
    assert.equal(await page.locator('#call-notes').inputValue(), 'Keep these unsaved follow-up notes.');
    assert.match(await page.locator('#call-refresh-status').innerText(), /paused.*unsaved/);

    // Keep genuine dashboard edits usable while mocked assistant responses arrive.
    // Requests are held until after focus has moved; no provider or save endpoint is used.
    await page.locator('#assist-launcher').click();
    const notes = page.locator('#call-notes');
    for (const failure of [false, true]) {
      let receiveRequest;
      const pendingRequest = new Promise(resolve => { receiveRequest = resolve; });
      await page.route('**/api/assistant/chat/', route => receiveRequest(route));
      const message = failure ? 'Keep this assistant draft after an error.' : 'Help me write follow-up notes.';
      await page.locator('#assist-input').fill(message);
      await page.locator('#assist-input').press('Enter');
      const request = await pendingRequest;
      assert.equal(await page.locator('#assist-input').evaluate(el => el.readOnly && !el.disabled), true);
      assert.equal(await page.locator('#assist-send').isDisabled(), true);
      const draft = failure ? 'Still editing while the assistant fails.' : 'Still editing while the assistant replies.';
      await notes.fill(draft);
      await request.fulfill({
        status: failure ? 502 : 200,
        contentType: 'application/json',
        body: JSON.stringify(failure ? { error: 'Assistant unavailable; retry your message.' } : { reply: 'Here is a suggested follow-up.' })
      });
      await page.waitForFunction(() => !document.querySelector('#assist-form').hasAttribute('aria-busy'));
      assert.equal(await notes.evaluate(el => el === document.activeElement), true, 'A reply or error must not steal dashboard focus');
      assert.equal(await notes.inputValue(), draft);
      await page.keyboard.type(' Keep typing.');
      assert.equal(await notes.inputValue(), `${draft} Keep typing.`);
      assert.equal(await page.locator('#assist-input').evaluate(el => el.readOnly), false);
      if (failure) {
        assert.equal(await page.locator('#assist-input').inputValue(), message);
        assert.equal(await page.locator('#assist-status').innerText(), 'Assistant unavailable; retry your message.');
      } else {
        assert.equal(await page.locator('.assist-model p').innerText(), 'Here is a suggested follow-up.');
      }
      await page.unroute('**/api/assistant/chat/');
    }
    await page.keyboard.press('Escape');
    await page.waitForFunction(() => !document.querySelector('#assist-panel').open);
    assert.equal(await notes.evaluate(el => el === document.activeElement), true, 'Closing help preserves focus in unsaved notes');
    assert.equal(loads, editedLoads, 'Assistant use must not reload unsaved dashboard edits');
    assert.deepEqual(errors, []);
    console.log('dashboard usability checks passed');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exit(1); });
