/* Outbound call page. Text is always inserted with textContent; the server never
   sends full phone numbers or credentials, and nothing is stored in the browser. */
(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const app = $("calls-app");
  if (!app) return;
  const listUrl = app.dataset.listUrl;
  const ui = Object.fromEntries([
    "call", "hangup", "confirm", "confirm-call", "confirm-cancel", "steps", "final-step",
    "call-status", "call-detail", "call-notice", "call-metrics", "m-duration", "m-latency",
    "m-turns", "m-interruptions", "elapsed", "history", "history-empty",
  ].map(id => [id, $(id)]));

  const POLL_MS = 1500;
  const POLL_ERROR_MS = 3000;
  const ORDER = ["initiating", "ringing", "answered"];
  const DETAIL = {
    initiating: "Asking the voice agent to dial…",
    ringing: "Your phone is ringing. Answer it to talk to the assistant.",
    answered: "Connected. Talk naturally; hang up on your phone or press End call.",
    completed: "The call ended normally.",
    rejected: "The call was declined or the line was busy.",
    no_answer: "Nobody answered before the ringing stopped.",
    failed: "The call could not be connected.",
  };

  let current = null;      // last call object from the server
  let pollTimer = null;
  let tickTimer = null;
  let tickBase = null;     // {elapsed, at} for the local clock between polls
  let busy = false;

  function csrfToken() {
    const field = document.querySelector("[name=csrfmiddlewaretoken]");
    return field ? field.value : "";
  }

  async function api(url, options = {}) {
    const response = await fetch(url, {
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken() },
      ...options,
    });
    let data = {};
    try { data = await response.json(); } catch { /* empty or non-JSON body */ }
    return { ok: response.ok, status: response.status, data };
  }

  function notice(message, details = []) {
    const text = [message, ...details].filter(Boolean).join(" ");
    ui["call-notice"].textContent = text;
    ui["call-notice"].hidden = !text;
  }

  function clock(seconds) {
    const total = Math.max(0, Math.round(seconds || 0));
    return `${String(Math.floor(total / 60)).padStart(2, "0")}:${String(total % 60).padStart(2, "0")}`;
  }

  function seconds(value) {
    return value == null ? "—" : `${Number(value).toFixed(1)} s`;
  }

  function renderSteps(call) {
    const status = call ? call.status : "idle";
    const reached = ORDER.indexOf(status);
    const final = call && !call.active;
    const answered = call && call.answered_at;
    ui["final-step"].textContent = final ? call.status_label : "Completed";
    ui.steps.querySelectorAll("li").forEach((item, index) => {
      let state = "";
      if (item.dataset.step === "final") {
        if (final) state = status === "completed" ? "done" : "failed";
      } else if (final) {
        const step = item.dataset.step;
        state = step === "answered" ? (answered ? "done" : "") : "done";
      } else if (index < reached) {
        state = "done";
      } else if (index === reached) {
        state = "current";
      }
      item.dataset.state = state;
    });
  }

  function renderLive(call) {
    current = call;
    renderSteps(call);
    const status = ui["call-status"];
    if (!call) {
      status.textContent = "No call in progress";
      status.dataset.status = "idle";
      ui["call-detail"].textContent = "Press Call to start. Your phone will ring within a few seconds.";
      ui.hangup.hidden = true;
      ui["call-metrics"].hidden = true;
      return;
    }
    status.textContent = call.status_label;
    status.dataset.status = call.status;
    let detail = DETAIL[call.status] || "";
    if (call.status === "initiating" && call.elapsed_seconds >= 8) {
      detail = "Waiting for the voice agent to pick up the call. Is the voice worker running?";
    }
    if (!call.active && call.failure_reason) detail = call.failure_reason;
    if (call.sip_status_code) detail += ` (SIP ${call.sip_status_code}${call.sip_status ? " " + call.sip_status : ""})`;
    ui["call-detail"].textContent = detail;
    ui.hangup.hidden = !call.active;
    ui.hangup.disabled = false;
    const metrics = call.metrics || {};
    const hasMetrics = call.duration_seconds != null || metrics.e2e_latency_p50 != null;
    ui["call-metrics"].hidden = !hasMetrics;
    ui["m-duration"].textContent = seconds(call.duration_seconds);
    ui["m-latency"].textContent = seconds(metrics.e2e_latency_p50);
    ui["m-turns"].textContent = metrics.agent_turns ?? "—";
    ui["m-interruptions"].textContent = metrics.interruptions ?? "—";
    tickBase = { elapsed: call.elapsed_seconds, at: Date.now(), active: call.active };
    ui.elapsed.textContent = clock(call.elapsed_seconds);
  }

  function tick() {
    if (tickBase && tickBase.active) {
      ui.elapsed.textContent = clock(tickBase.elapsed + (Date.now() - tickBase.at) / 1000);
    }
  }

  function cell(text) {
    const td = document.createElement("td");
    td.textContent = text;
    return td;
  }

  function renderHistory(calls) {
    ui.history.replaceChildren(...calls.map(call => {
      const row = document.createElement("tr");
      row.dataset.id = call.id;
      row.append(cell(new Date(call.created_at).toLocaleString()), cell(call.masked_number));
      const statusCell = document.createElement("td");
      const badge = document.createElement("span");
      badge.className = "badge";
      badge.dataset.status = call.status;
      badge.textContent = call.status_label;
      statusCell.append(badge);
      if (!call.active && call.failure_reason) {
        const reason = document.createElement("span");
        reason.className = "reason";
        reason.textContent = call.failure_reason;
        statusCell.append(reason);
      }
      row.append(
        statusCell,
        cell(call.duration_seconds == null ? "—" : clock(call.duration_seconds)),
        cell(call.sip_status_code ? String(call.sip_status_code) : "—"),
        cell(seconds(call.metrics && call.metrics.e2e_latency_p50)),
        cell(call.placed_by),
      );
      return row;
    }));
    ui["history-empty"].hidden = calls.length > 0;
  }

  async function loadHistory() {
    const result = await api(listUrl, { method: "GET", headers: {} });
    if (result.ok) renderHistory(result.data.calls || []);
  }

  function stopPolling() {
    clearTimeout(pollTimer);
    pollTimer = null;
  }

  async function poll(id) {
    stopPolling();
    let delay = POLL_MS;
    try {
      const result = await api(`${listUrl}${id}/`, { method: "GET", headers: {} });
      if (result.ok) {
        notice("");
        const changed = !current || current.status !== result.data.call.status;
        renderLive(result.data.call);
        if (changed && result.data.call.active) loadHistory();
        if (!result.data.call.active) {
          ui.call.disabled = !readyToCall();
          await loadHistory();
          return;
        }
      } else if (result.status === 403 || result.status === 404) {
        notice(result.data.error || "This call can no longer be followed.");
        ui.call.disabled = !readyToCall();
        return;
      } else {
        delay = POLL_ERROR_MS;
        notice("Having trouble reaching the server. Still trying…");
      }
    } catch {
      delay = POLL_ERROR_MS;
      notice("Having trouble reaching the server. Still trying…");
    }
    pollTimer = setTimeout(() => poll(id), delay);
  }

  function readyToCall() {
    return ui.call.dataset.ready === "true" && !(current && current.active);
  }

  async function placeCall() {
    if (busy || (current && current.active)) return;
    busy = true;
    ui.call.disabled = true;
    notice("");
    try {
      const result = await api(listUrl, { method: "POST", body: JSON.stringify({ confirm: true, to: $("phone-number").value }) });
      if (result.ok) {
        renderLive(result.data.call);
        await loadHistory();
        poll(result.data.call.id);
      } else {
        notice(result.data.error || `The call could not be started (HTTP ${result.status}).`, result.data.details || []);
        ui.call.disabled = !readyToCall();
        if (result.status === 409) await loadHistory();
      }
    } catch {
      notice("The server could not be reached. No call was placed.");
      ui.call.disabled = !readyToCall();
    } finally {
      busy = false;
    }
  }

  async function hangUp() {
    if (!current || !current.active) return;
    ui.hangup.disabled = true;
    const result = await api(`${listUrl}${current.id}/hangup/`, { method: "POST", body: "{}" });
    if (result.ok) {
      renderLive(result.data.call);
      stopPolling();
      ui.call.disabled = !readyToCall();
      await loadHistory();
    } else {
      notice(result.data.error || "The call could not be ended.");
      ui.hangup.disabled = false;
    }
  }

  ui.call.dataset.ready = String(!ui.call.disabled);
  ui.call.addEventListener("click", () => {
    if (busy || ui.call.disabled) return;
    if (!$("phone-number").reportValidity()) return;
    $("confirm-number").textContent = $("phone-number").value;
    ui.confirm.showModal();
    ui["confirm-cancel"].focus();
  });
  ui.confirm.addEventListener("close", () => {
    if (ui.confirm.returnValue === "call") placeCall();
  });
  ui.hangup.addEventListener("click", hangUp);

  const initial = JSON.parse(document.getElementById("initial-calls").textContent || "[]");
  renderHistory(initial);
  renderLive(null);
  tickTimer = setInterval(tick, 500);
  if (app.dataset.activeId) {
    ui.call.disabled = true;
    poll(app.dataset.activeId);
  }
  window.addEventListener("pagehide", () => { stopPolling(); clearInterval(tickTimer); });
})();
