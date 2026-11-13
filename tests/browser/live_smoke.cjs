/* Opt-in live-service check: dispatches the existing worker and incurs inference use. */
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');

(async () => {
  fs.mkdirSync('browser-artifacts', {recursive: true});
  const browser = await chromium.launch({headless: true, args: [
    '--use-fake-device-for-media-stream', '--use-fake-ui-for-media-stream',
  ]});
  try {
    const page = await browser.newPage({permissions: ['microphone'], viewport: {width: 1440, height: 1080}});
    await page.goto('http://127.0.0.1:8765/');
    await page.locator('[name=username]').fill('browser-review');
    await page.locator('[name=password]').fill('local-disposable-browser-check');
    await page.getByRole('button', {name: 'Sign in', exact: true}).click();
    await page.waitForURL('http://127.0.0.1:8765/');
    // The preceding UI suite also exercises token issuance with this QA account.
    await page.waitForTimeout(5100);
    await page.evaluate(() => {
      const create = LivekitClient.createLocalAudioTrack;
      LivekitClient.createLocalAudioTrack = async (...args) => {
        window.qaLiveTrack = await create(...args);
        return window.qaLiveTrack;
      };
    });
    await page.locator('#start').click();
    await page.waitForFunction(() => document.querySelector('.message.agent p')?.textContent.length > 10, null, {timeout: 60000});
    await page.waitForFunction(() => [...document.querySelectorAll('audio')].some(a => a.readyState >= 2 && !a.paused), null, {timeout: 30000});
    assert.equal(await page.locator('#connection').textContent(), 'Connected');
    await page.locator('#mic').click();
    assert.equal(await page.locator('#mic').getAttribute('aria-pressed'), 'true');
    assert.equal(await page.evaluate(() => qaLiveTrack.isMuted), true);
    await page.locator('#mic').click();
    assert.equal(await page.evaluate(() => qaLiveTrack.isMuted), false);
    await page.screenshot({path: 'browser-artifacts/studio-live.png', fullPage: true});
    await page.locator('#end').click();
    assert.equal(await page.locator('audio').count(), 0);
    assert.equal(await page.locator('#status').textContent(), 'Conversation ended');
    assert.equal(await page.evaluate(() => qaLiveTrack.mediaStreamTrack.readyState), 'ended');
    console.log('PASS: real LiveKit room connection, existing agent dispatch, streamed greeting transcript, remote audio playback state, microphone mute/unmute, end cleanup and capture readyState=ended. Synthetic browser microphone only; human speech and audible quality not assessed.');
  } finally {await browser.close();}
})().catch(error => {console.error(error); process.exitCode = 1;});
