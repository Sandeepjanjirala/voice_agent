/* Official LiveKit Room API; no credentials or tokens are persisted in the browser. */
(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const ui = Object.fromEntries(["start", "end", "mic", "audio", "status", "hint", "notice", "connection", "orb", "transcript", "empty"].map(id => [id, $(id)]));
  let current = null;
  const messages = new Map();
  const labels = {
    ready: ["Ready when you are", "Start a conversation and allow microphone access."],
    connecting: ["Connecting…", "Preparing your private conversation."],
    listening: ["Listening", "Go ahead. You can speak naturally."],
    thinking: ["Thinking", "Your assistant is preparing a reply."],
    speaking: ["Speaking", "You can interrupt if you need to."],
    reconnecting: ["Reconnecting…", "Keep this tab open while the connection recovers."],
    ended: ["Conversation ended", "Start again whenever you’re ready."],
    failed: ["Let’s try that again", "Check the message below, then start a new conversation."],
  };
  function state(value) {
    const [title, hint] = labels[value] || labels.connecting;
    ui.status.textContent = title;
    ui.hint.textContent = hint;
    ui.orb.dataset.state = value;
  }
  function notice(message) {
    ui.notice.textContent = message;
    ui.notice.hidden = !message;
  }
  function micLabel(session) {
    ui.mic.textContent = session.muted ? "Unmute microphone" : "Mute microphone";
    ui.mic.setAttribute("aria-pressed", String(session.muted));
  }
  async function dispose(session) {
    clearTimeout(session.timer);
    session.abort.abort();
    session.track?.stop();
    session.audio.forEach(el => el.remove());
    session.audio.clear();
    try { await session.room?.disconnect(); } catch { /* Local capture is already stopped. */ }
  }
  function finish(message = "", ending = "ended") {
    const session = current;
    current = null;
    if (session) void dispose(session);
    ui.start.disabled = false;
    ui.end.disabled = ui.mic.disabled = true;
    ui.audio.hidden = true;
    ui.mic.textContent = "Mute microphone";
    ui.mic.setAttribute("aria-pressed", "false");
    ui.connection.textContent = "Not connected";
    state(ending);
    notice(message);
  }
  function agentState(session, participant) {
    if (current !== session || !participant?.attributes?.["lk.agent.state"]) return;
    session.agent = participant.identity;
    if (session.reconnecting) return;
    const reported = participant.attributes["lk.agent.state"];
    if (["listening", "thinking", "speaking", "idle"].includes(reported)) clearTimeout(session.timer);
    if (reported === "failed" || reported === "disconnected") {
      finish("The assistant stopped. Start again to reconnect.", "failed");
      return;
    }
    state(reported === "idle" ? "listening" : reported);
  }
  function bind(session) {
    const {RoomEvent, Track} = window.LivekitClient;
    const room = session.room;
    const on = (event, fn) => room.on(event, (...args) => {
      if (current === session) fn(...args);
    });
    on(RoomEvent.ParticipantConnected, p => agentState(session, p));
    on(RoomEvent.ParticipantAttributesChanged, (_, p) => agentState(session, p));
    on(RoomEvent.ParticipantDisconnected, p => {
      if (p.identity === session.agent && !session.reconnecting) finish("The assistant disconnected. Start again to reconnect.", "failed");
    });
    on(RoomEvent.TrackSubscribed, track => {
      if (track.kind !== Track.Kind.Audio) return;
      const el = track.attach();
      el.hidden = true;
      session.audio.add(el);
      document.body.append(el);
      el.play().catch(() => { if (current === session) ui.audio.hidden = false; });
    });
    on(RoomEvent.TrackUnsubscribed, track => track.detach().forEach(el => {
      session.audio.delete(el);
      el.remove();
    }));
    on(RoomEvent.AudioPlaybackStatusChanged, () => { ui.audio.hidden = room.canPlaybackAudio; });
    const reconnecting = () => {
      session.reconnecting = true;
      ui.connection.textContent = "Recovering connection";
      ui.mic.disabled = true;
      state("reconnecting");
      clearTimeout(session.timer);
      session.timer = setTimeout(() => {
        if (current === session) finish("Connection recovery timed out. Check your network and start again.", "failed");
      }, 45000);
    };
    on(RoomEvent.Reconnecting, reconnecting);
    on(RoomEvent.SignalReconnecting, reconnecting);
    on(RoomEvent.Reconnected, () => {
      session.reconnecting = false;
      clearTimeout(session.timer);
      ui.connection.textContent = "Connected";
      ui.mic.disabled = false;
      state("connecting");
      session.timer = setTimeout(() => {
        if (current === session) finish("The assistant did not recover. Start a new conversation.", "failed");
      }, 45000);
      room.remoteParticipants.forEach(p => agentState(session, p));
      notice("Connection restored. Transcripts from the outage may be incomplete.");
    });
    on(RoomEvent.Disconnected, () => finish("Connection lost. Start again to open a new conversation.", "failed"));
    on(RoomEvent.LocalTrackUnpublished, publication => {
      // A full SDK reconnect unpublishes and republishes tracks. Let it finish.
      if (publication.source === Track.Source.Microphone && !session.reconnecting) finish("Your microphone stopped. Check your device and start again.", "failed");
    });
    room.registerTextStreamHandler("lk.transcription", async (reader, participant) => {
      if (current !== session) return;
      const attrs = reader.info.attributes || {};
      const key = `${participant.identity}:${attrs["lk.transcribed_track_id"] || ""}:${attrs["lk.segment_id"] || reader.info.id}`;
      const final = attrs["lk.transcription_final"] !== "false";
      let entry = messages.get(key);
      if (entry?.final && !final) return;
      if (!entry) {
        const row = document.createElement("article");
        const own = participant.identity === room.localParticipant.identity;
        row.className = `message ${own ? "you" : "agent"}`;
        const who = document.createElement("b");
        who.textContent = own ? "You" : "Telugu AI";
        const body = document.createElement("p");
        row.append(who, body);
        ui.empty.hidden = true;
        ui.transcript.append(row);
        entry = {row, body};
        messages.set(key, entry);
      }
      entry.final = final;
      entry.stream = reader.info.id;
      entry.row.dataset.final = String(final);
      let text = "";
      try {
        for await (const chunk of reader) {
          if (current !== session || entry.stream !== reader.info.id) break;
          text += chunk;
          entry.body.textContent = text; // Never render transcriptions as HTML.
          ui.transcript.scrollTop = ui.transcript.scrollHeight;
        }
      } catch {
        if (current === session) notice("A transcript was interrupted. Audio may continue; this line may be incomplete.");
      }
    });
  }
  ui.start.addEventListener("click", async () => {
    if (current) return;
    if (!window.LivekitClient) { notice("The voice SDK could not load. Reload this page."); return; }
    if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) {
      notice("Microphone access needs localhost or HTTPS and a supported browser."); return;
    }
    const session = {abort: new AbortController(), audio: new Set(), muted: false};
    current = session;
    ui.start.disabled = true;
    ui.end.disabled = false;
    ui.connection.textContent = "Connecting";
    ui.transcript.querySelectorAll(".message").forEach(el => el.remove());
    messages.clear();
    ui.empty.hidden = false;
    notice("");
    state("connecting");
    session.timer = setTimeout(() => {
      if (current === session) finish("Could not reach the assistant. Check microphone permissions, your network, and that telugu-agent is running.", "failed");
    }, 60000);
    try {
      // Ask permission before dispatching a billable agent. Stop late permission results.
      const track = await window.LivekitClient.createLocalAudioTrack({echoCancellation: true, noiseSuppression: true});
      session.track = track;
      if (current !== session) { track.stop(); return; }
      track.mediaStreamTrack.addEventListener("ended", () => {
        if (current === session) finish("Microphone access ended. Reconnect your microphone and start again.", "failed");
      });
      const response = await fetch($("voice-app").dataset.tokenUrl, {
        method: "POST", credentials: "same-origin", cache: "no-store",
        headers: {"X-CSRFToken": document.querySelector("[name=csrfmiddlewaretoken]").value},
        signal: session.abort.signal,
      });
      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        throw new Error(body.error || "Your session may have expired. Reload the page and sign in again.");
      }
      const access = await response.json();
      if (current !== session) return;
      session.room = new window.LivekitClient.Room({adaptiveStream: true});
      bind(session);
      await session.room.connect(access.url, access.token);
      if (current !== session) { await dispose(session); return; }
      await session.room.localParticipant.publishTrack(track);
      if (current !== session) { await dispose(session); return; }
      ui.connection.textContent = "Connected";
      ui.mic.disabled = false;
      micLabel(session);
      session.room.remoteParticipants.forEach(p => agentState(session, p));
      session.room.startAudio().catch(() => { if (current === session) ui.audio.hidden = false; });
    } catch (error) {
      if (current !== session) return;
      const known = {
        NotAllowedError: "Microphone permission was denied. Allow microphone access in your browser’s site settings, then try again.",
        NotFoundError: "No microphone was found. Connect a microphone and try again.",
        NotReadableError: "Your microphone is unavailable. Close other apps using it and try again.",
      };
      // SDK errors may contain connection details: show only application errors verbatim.
      const message = known[error.name] || (session.room ? "Unable to connect to the voice service. Check your network and that the worker is running." : error.message);
      finish(message || "Unable to start. Reload the page and try again.", "failed");
    }
  });
  ui.end.addEventListener("click", () => finish());
  ui.mic.addEventListener("click", async () => {
    const session = current;
    if (!session?.track) return;
    ui.mic.disabled = true;
    try {
      if (session.muted) await session.track.unmute(); else await session.track.mute();
      if (current !== session) return;
      session.muted = !session.muted;
      micLabel(session);
    } catch { if (current === session) notice("Could not change microphone state. End the conversation and try again."); }
    finally {
      // unmute() may reacquire capture after End has already stopped the old track.
      if (current !== session) session.track.stop();
      else ui.mic.disabled = session.reconnecting || false;
    }
  });
  ui.audio.addEventListener("click", async () => {
    const session = current;
    try { await session?.room.startAudio(); if (current === session) ui.audio.hidden = true; }
    catch { if (current === session) notice("Audio playback is blocked. Check your browser’s sound permissions."); }
  });
  window.addEventListener("pagehide", () => finish());
})();
