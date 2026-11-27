// Run against tests.browser.dashboard_server; this suite never places a call.
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const BASE = 'http://127.0.0.1:8767';

async function expectTheme(page, theme) {
  await page.waitForFunction(value => {
    const switches = [...document.querySelectorAll('[data-theme-toggle]')];
    return document.documentElement.dataset.theme === value && switches.length > 0 &&
      switches.every(button => button.getAttribute('aria-pressed') === String(value === 'dark'));
  }, theme);
  const buttons = page.locator('[data-theme-toggle]');
  assert.ok(await buttons.count(), 'Every page includes a theme switch');
  for (const button of await buttons.all()) {
    assert.equal(await button.getAttribute('aria-pressed'), String(theme === 'dark'));
    assert.equal(await button.getAttribute('aria-label'), `Switch to ${theme === 'dark' ? 'light' : 'dark'} mode`);
    assert.equal(await button.locator('[data-theme-label]').innerText(), theme === 'dark' ? 'Light mode' : 'Dark mode');
  }
}

async function toggleTheme(page, theme) {
  await page.locator('[data-theme-toggle]:visible').first().click();
  await expectTheme(page, theme);
}

async function expectContrast(page, selector, description) {
  // Measure the selected theme after the brief colour transition finishes.
  await page.waitForFunction(value => [...document.querySelectorAll(value)]
    .every(element => element.getAnimations().every(animation => animation.playState === 'finished')), selector);
  const ratios = await page.locator(selector).evaluateAll(elements => {
    const rgba = color => color.match(/[\d.]+/g).map(Number);
    const over = (front, back) => front.slice(0, 3).map((channel, index) =>
      channel * (front[3] ?? 1) + back[index] * (1 - (front[3] ?? 1)));
    function background(element) {
      if (!element) return [255, 255, 255];
      const color = rgba(getComputedStyle(element).backgroundColor);
      return (color[3] ?? 1) === 1 ? color.slice(0, 3) : over(color, background(element.parentElement));
    }
    function luminance(color) {
      const linear = color.map(channel => {
        const value = channel / 255;
        return value <= .04045 ? value / 12.92 : ((value + .055) / 1.055) ** 2.4;
      });
      return .2126 * linear[0] + .7152 * linear[1] + .0722 * linear[2];
    }
    return elements.map(element => {
      const back = background(element);
      const foreground = luminance(over(rgba(getComputedStyle(element).color), back));
      const backdrop = luminance(back);
      return (Math.max(foreground, backdrop) + .05) / (Math.min(foreground, backdrop) + .05);
    });
  });
  assert.ok(ratios.length, `${description} exists in the browser fixture`);
  assert.ok(ratios.every(ratio => ratio >= 4.5), `${description} contrast must be at least 4.5: ${ratios.join(', ')}`);
}

(async () => {
  const browser = await chromium.launch({ headless: true });
  const errors = [];
  try {
    const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
    context.on('page', page => page.on('pageerror', error => errors.push(error.message)));
    const page = await context.newPage();
    await page.goto(`${BASE}/accounts/login/`);
    await expectTheme(page, 'light');
    await page.locator('[name=username]').fill('dashboard-review');
    await page.locator('[name=password]').fill('local-disposable-dashboard-check');
    await page.getByRole('button', { name: 'Sign in', exact: true }).click();
    await page.waitForURL('**/home/');
    await expectTheme(page, 'light');

    const loading = await page.evaluate(() => {
      const theme = document.querySelector('script[src*="dashboard/theme.js"]');
      const jquery = document.querySelector('script[src*="vendor/jquery/"]');
      const firstStyle = document.querySelector('link[rel="stylesheet"]');
      return {
        beforeStyles: Boolean(theme.compareDocumentPosition(firstStyle) & Node.DOCUMENT_POSITION_FOLLOWING),
        jqueryFirst: Boolean(jquery.compareDocumentPosition(theme) & Node.DOCUMENT_POSITION_FOLLOWING),
        synchronous: !theme.defer && !theme.async && !jquery.defer && !jquery.async,
      };
    });
    assert.deepEqual(loading, { beforeStyles: true, jqueryFirst: true, synchronous: true });
    await toggleTheme(page, 'dark');
    assert.equal(await page.evaluate(() => localStorage.getItem('vaani-theme')), 'dark');
    await page.reload();
    await expectTheme(page, 'dark');
    await page.goto(`${BASE}/employees/`);
    await expectTheme(page, 'dark');

    const otherTab = await context.newPage();
    await otherTab.goto(`${BASE}/home/`);
    await expectTheme(otherTab, 'dark');
    await toggleTheme(otherTab, 'light');
    await expectTheme(page, 'light');
    await toggleTheme(otherTab, 'dark');
    await expectTheme(page, 'dark');
    await otherTab.evaluate(() => localStorage.removeItem('vaani-theme'));
    await expectTheme(page, 'light');
    await otherTab.close();

    await page.goto(`${BASE}/conversations/?status=completed`);
    const candidateLinks = await page.locator('#call-table tbody tr', { hasText: '+919876500001' })
      .locator('a').evaluateAll(links => links.map(link => link.href));
    let foundSeededTranscript = false;
    for (const url of candidateLinks) {
      await page.goto(url);
      // dashboard_server's qa-answered fixture has this exact caller turn.
      // Other browser suites may create newer calls with the same phone number.
      const turns = await page.locator('.chat-text').allTextContents();
      if (turns.includes('Could you call me back on Friday evening?')) {
        foundSeededTranscript = true;
        break;
      }
    }
    assert.ok(foundSeededTranscript, 'The seeded qa-answered conversation is present');
    assert.equal(await page.locator('.chat-turn.from-ai').count(), 2);
    assert.equal(await page.locator('.chat-turn.from-person').count(), 2);
    if (!(await page.locator('.side-more').evaluate(element => element.open))) {
      await page.locator('.side-more > summary').click();
    }
    for (const theme of ['light', 'dark']) {
      if (await page.locator('html').getAttribute('data-theme') !== theme) await toggleTheme(page, theme);
      await expectContrast(page, '.side-nav a, .side-helper button', `${theme} sidebar and help button`);
      await expectContrast(page, '.chat-text, .chat-meta', `${theme} transcript`);
      await expectContrast(page, '#call-notes', `${theme} review input`);
    }

    await page.setViewportSize({ width: 390, height: 844 });
    for (const theme of ['light', 'dark']) {
      if (await page.locator('html').getAttribute('data-theme') !== theme) await toggleTheme(page, theme);
      for (const path of ['/home/', '/employees/', '/employees/hire/', '/make-calls/', '/conversations/', '/settings/']) {
        await page.goto(BASE + path);
        await expectTheme(page, theme);
        assert.ok(await page.locator('[data-theme-toggle]:visible').count(), `Theme switch visible on ${path}`);
        assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), `${theme} ${path} fits mobile`);
      }
    }

    const storageState = await context.storageState();
    storageState.origins = [];
    const blocked = await browser.newContext({ storageState });
    blocked.on('page', page => page.on('pageerror', error => errors.push(error.message)));
    await blocked.addInitScript(() => {
      Object.defineProperty(window, 'localStorage', { get() { throw new DOMException('Storage disabled', 'SecurityError'); } });
    });
    const privatePage = await blocked.newPage();
    await privatePage.goto(`${BASE}/home/`);
    await expectTheme(privatePage, 'light');
    await toggleTheme(privatePage, 'dark');
    await privatePage.reload();
    await expectTheme(privatePage, 'light');
    await blocked.close();
    assert.deepEqual(errors, []);
    console.log('Theme browser checks passed: early load, light default, dark persistence, navigation, cross-tab sync, blocked storage, readable transcript/input and mobile pages.');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exit(1); });
