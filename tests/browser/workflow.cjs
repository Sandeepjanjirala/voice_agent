// Run against the disposable dashboard_server; no real service or phone is used.
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const BASE = 'http://127.0.0.1:8767';

(async () => {
  const browser = await chromium.launch({ headless: true });
  const errors = [];
  fs.mkdirSync('browser-artifacts', { recursive: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
    page.on('pageerror', error => errors.push(error.message));
    page.on('response', r => { if (r.status() >= 500) errors.push(`${r.status()} ${r.url()}`); });
    await page.goto(`${BASE}/accounts/login/`);
    await page.locator('[name=username]').fill('dashboard-review');
    await page.locator('[name=password]').fill('local-disposable-dashboard-check');
    await page.getByRole('button', { name: 'Sign in', exact: true }).click();
    await page.waitForURL('**/home/');
    assert.match(await page.locator('#home-next-title').innerText(), /Set up your first/);
    await page.waitForFunction(() => !document.body.classList.contains('motion-ready'));
    await page.screenshot({ path: 'browser-artifacts/workflow-home.png', fullPage: true });

    await page.goto(`${BASE}/conversations/?status=completed`);
    await page.locator('#call-table tbody tr', { hasText: '+919876500001' }).first().locator('a').click();
    const callUrl = page.url();
    assert.match(await page.locator('#call-next-title').innerText(), /needs follow-up/);
    assert.equal(await page.locator('#technical-details').evaluate(el => el.open), false);
    assert.equal(await page.locator('#result #conversation').count(), 0, 'Transcript must be a separate panel');
    assert.ok(await page.locator('.call-layout > aside #staff-notes').count());
    const ratios = await page.locator('.chat-text, .chat-meta').evaluateAll(elements => {
      function luminance(color) {
        const rgb = color.match(/[\d.]+/g).slice(0, 3).map(Number).map(x => {
          x /= 255; return x <= .04045 ? x / 12.92 : ((x + .055) / 1.055) ** 2.4;
        });
        return .2126 * rgb[0] + .7152 * rgb[1] + .0722 * rgb[2];
      }
      return elements.map(el => {
        const fg = luminance(getComputedStyle(el).color);
        const bg = luminance(getComputedStyle(el.closest('.chat-turn')).backgroundColor);
        return (Math.max(fg, bg) + .05) / (Math.min(fg, bg) + .05);
      });
    });
    assert.ok(ratios.length >= 8, 'Fixture includes AI and caller messages');
    assert.ok(ratios.every(r => r >= 4.5), `Transcript contrast: ${ratios}`);
    await page.getByRole('link', { name: 'Technical details', exact: true }).click();
    assert.equal(await page.locator('#technical-details').evaluate(el => el.open), true);
    await page.getByRole('link', { name: 'Conversation', exact: true }).click();
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.screenshot({ path: 'browser-artifacts/workflow-conversation.png', fullPage: true });
    await page.getByRole('link', { name: 'Add review notes' }).click();
    await page.locator('#call-notes').fill('Team to confirm the callback time.');
    await page.getByRole('button', { name: 'Save notes', exact: true }).click();
    await page.getByText('Notes saved.', { exact: true }).waitFor();
    assert.equal(await page.locator('#call-notes').inputValue(), 'Team to confirm the callback time.');

    await page.goto(`${BASE}/conversations/?status=failed`);
    await page.locator('#call-table tbody a').first().click();
    assert.equal(await page.locator('#call-next-title').innerText(), 'This call did not connect');
    await page.getByRole('link', { name: 'View details', exact: true }).click();
    assert.equal(await page.locator('#technical-details').evaluate(el => el.open), true);

    for (const width of [390, 768, 1440]) {
      await page.setViewportSize({ width, height: 900 });
      for (const path of ['/home/', '/employees/', '/employees/hire/', '/make-calls/', '/conversations/', '/settings/', callUrl.replace(BASE, '')]) {
        assert.equal((await page.goto(BASE + path)).status(), 200, path);
        assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), `${path} overflows at ${width}`);
      }
      if (width === 390) {
        await page.getByRole('link', { name: 'Conversation', exact: true }).click();
        await page.evaluate(() => window.scrollTo(0, 0));
        await page.screenshot({ path: 'browser-artifacts/workflow-mobile.png', fullPage: true });
      }
    }
    assert.deepEqual(errors, []);
    console.log('Workflow checks passed: next steps, transcript contrast, disclosure navigation, saved notes, and responsive pages.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
