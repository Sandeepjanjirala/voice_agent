/* Run against tests/browser/server.py. Real Chromium + real Django, mocked transport. */
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');

(async () => {
  fs.mkdirSync('browser-artifacts', { recursive: true });
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1080 } });
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto('http://127.0.0.1:8765/');
    await page.locator('[name=username]').fill('browser-review');
    await page.locator('[name=password]').fill('local-disposable-browser-check');
    await page.getByRole('button', { name: 'Sign in', exact: true }).click();
    await page.waitForURL('http://127.0.0.1:8765/');
    await page.waitForFunction(() => window.LivekitClient?.Room);
    assert.equal(await page.evaluate(() => window.LivekitClient.version), '2.24.0');
    await page.screenshot({ path: 'browser-artifacts/studio-desktop.png', fullPage: true });
    // Exercise the real authenticated endpoint without joining or dispatching a room.
    const tokenStatus = await page.evaluate(async () => {
      const response = await fetch('/api/voice/token/', { method: 'POST', headers: {
        'X-CSRFToken': document.querySelector('[name=csrfmiddlewaretoken]').value,
      }});
      const data = await response.json();
      return { status: response.status, hasToken: !!data.token, cache: response.headers.get('cache-control') };
    });
    assert.equal(tokenStatus.status, 200);
    assert.equal(tokenStatus.hasToken, true);
    assert.match(tokenStatus.cache, /no-store/);
    await page.route('**/api/voice/token/', route => route.fulfill({json: {url: 'wss://mock.invalid', token: 'mock-only'}}));
    await page.evaluate(() => {
      window.qa = { mode: 'ok', stopped: 0, disconnected: 0 };
      const sdk = window.LivekitClient;
      sdk.createLocalAudioTrack = async () => {
        if (qa.mode === 'denied') throw new DOMException('Denied', 'NotAllowedError');
        if (qa.mode === 'pending') await new Promise(resolve => { qa.resolveMic = resolve; });
        return {
          mediaStreamTrack: new EventTarget(),
          stop() { qa.stopped++; qa.capturing = false; }, async mute() {},
          async unmute() {
            if (qa.mode === 'pending-unmute') {
              await new Promise(resolve => { qa.resolveUnmute = resolve; });
              qa.capturing = true;
            }
          },
        };
      };
      sdk.Room = class {
        constructor() {
          qa.room = this;
          this.events = new Map();
          this.remoteParticipants = new Map();
          this.localParticipant = {identity: 'browser-test', async publishTrack() {}};
          this.canPlaybackAudio = true;
        }
        on(key, fn) { this.events.set(key, fn); return this; }
        emit(key, ...args) { this.events.get(key)?.(...args); }
        registerTextStreamHandler(topic, handler) { this.transcribe = handler; }
        async connect() { if (qa.mode === 'connection-failure') throw new Error('mock connection failed'); }
        async disconnect() { qa.disconnected++; }
        async startAudio() {}
      };
      qa.agent = {identity: 'agent-test', attributes: {'lk.agent.state': 'listening'}};
      qa.state = state => {
        qa.agent.attributes['lk.agent.state'] = state;
        qa.room.remoteParticipants.set('agent-test', qa.agent);
        qa.room.emit(sdk.RoomEvent.ParticipantAttributesChanged, {}, qa.agent);
      };
      qa.text = async (identity, text, final, id = 'segment-1') => {
        const reader = {
          info: {id: Math.random().toString(), attributes: {'lk.segment_id': id, 'lk.transcription_final': String(final)}},
          async *[Symbol.asyncIterator]() { yield text; },
        };
        await qa.room.transcribe(reader, {identity});
      };
    });
    const start = page.locator('#start');
    await page.evaluate(() => { qa.mode = 'denied'; });
    await start.click();
    await page.waitForFunction(() => document.getElementById('notice').textContent.includes('permission was denied'));
    assert.equal(await start.isEnabled(), true);
    await page.evaluate(() => { qa.mode = 'ok'; });
    await start.click();
    await page.waitForFunction(() => document.getElementById('connection').textContent === 'Connected');
    for (const state of ['listening', 'thinking', 'speaking']) {
      await page.evaluate(state => qa.state(state), state);
      assert.equal((await page.locator('#status').textContent()).toLowerCase(), state);
    }
    await page.evaluate(async () => {
      await qa.text('browser-test', 'నమస్తే', false);
      await qa.text('browser-test', 'నమస్తే, మీరు ఎవరు?', true);
      await qa.text('browser-test', 'stale interim', false);
      await qa.text('agent-test', 'నమస్తే! నేను మీ AI సహాయకురాలిని. <img src=x onerror=alert(1)>', true);
    });
    assert.equal(await page.locator('.message').count(), 2);
    assert.equal(await page.locator('.message.you p').textContent(), 'నమస్తే, మీరు ఎవరు?');
    assert.equal(await page.locator('.message img').count(), 0);
    await page.locator('#mic').click();
    assert.equal(await page.locator('#mic').getAttribute('aria-pressed'), 'true');
    await page.locator('#mic').click();
    assert.equal(await page.locator('#mic').getAttribute('aria-pressed'), 'false');
    await page.evaluate(() => qa.room.emit(LivekitClient.RoomEvent.Reconnecting));
    assert.equal(await page.locator('#status').textContent(), 'Reconnecting…');
    assert.equal(await page.locator('#mic').isDisabled(), true);
    await page.evaluate(() => qa.room.emit(LivekitClient.RoomEvent.Reconnected));
    assert.equal(await page.locator('#status').textContent(), 'Speaking');
    await page.evaluate(() => qa.room.emit(LivekitClient.RoomEvent.SignalReconnecting));
    assert.equal(await page.locator('#status').textContent(), 'Reconnecting…');
    await page.evaluate(() => {
      qa.room.emit(LivekitClient.RoomEvent.Reconnecting);
      qa.room.emit(LivekitClient.RoomEvent.LocalTrackUnpublished, {source: LivekitClient.Track.Source.Microphone});
      qa.room.emit(LivekitClient.RoomEvent.ParticipantDisconnected, qa.agent);
    });
    assert.equal(await page.locator('#status').textContent(), 'Reconnecting…');
    await page.evaluate(() => qa.room.emit(LivekitClient.RoomEvent.Reconnected));
    assert.equal(await page.locator('#status').textContent(), 'Speaking');
    await page.evaluate(() => { qa.room.canPlaybackAudio = false; qa.room.emit(LivekitClient.RoomEvent.AudioPlaybackStatusChanged); });
    assert.equal(await page.locator('#audio').isVisible(), true);
    await page.locator('#audio').click();
    assert.equal(await page.locator('#audio').isVisible(), false);
    await page.screenshot({path: 'browser-artifacts/studio-transcript.png', fullPage: true});
    await page.locator('#mic').click();
    await page.evaluate(() => { qa.mode = 'pending-unmute'; });
    await page.locator('#mic').click();
    await page.locator('#end').click();
    await page.evaluate(() => qa.resolveUnmute());
    await page.waitForFunction(() => qa.capturing === false);
    assert.equal(await page.locator('#status').textContent(), 'Conversation ended');
    assert.ok(await page.evaluate(() => qa.stopped > 0 && qa.disconnected > 0));
    await page.evaluate(() => { qa.mode = 'pending'; });
    await start.click();
    await page.locator('#end').click();
    const stopped = await page.evaluate(() => qa.stopped);
    await page.evaluate(() => qa.resolveMic());
    await page.waitForFunction(n => qa.stopped > n, stopped);
    assert.equal(await page.locator('#status').textContent(), 'Conversation ended');
    await page.evaluate(() => { qa.mode = 'connection-failure'; });
    await start.click();
    await page.waitForFunction(() => document.getElementById('notice').textContent.includes('Unable to connect'));
    await page.evaluate(() => { qa.mode = 'ok'; });
    await start.click();
    await page.waitForFunction(() => document.getElementById('connection').textContent === 'Connected');
    await page.evaluate(() => qa.room.emit(LivekitClient.RoomEvent.Disconnected));
    assert.equal(await start.isEnabled(), true);
    await page.setViewportSize({width: 390, height: 844});
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    await page.screenshot({path: 'browser-artifacts/studio-mobile.png', fullPage: true});
    assert.deepEqual(errors, []);
    console.log('PASS: Chromium login, official SDK load, real token endpoint, permission denial, states, transcripts/deduplication/XSS, mute, signaling/media recovery and track republishing, audio unlock, end/cancellation and pending-unmute cleanup, connection failure, disconnect, mobile layout. Transport mocked; no live microphone conversation claimed.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
