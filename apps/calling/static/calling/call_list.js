// A running call list: every few seconds, ask the server to take the next
// step (settle the call in progress, or call the next person) and show the
// progress. When the list stops (finished, paused, cancelled) the page reloads.
(() => {
  const root = document.getElementById('call-list');
  if (!root) return;
  const url = root.dataset.stepUrl;
  const token = document.querySelector('[name=csrfmiddlewaretoken]')?.value || '';
  const finished = root.querySelector('[data-finished]');
  const bar = root.querySelector('[data-bar]');
  const counts = root.querySelector('[data-counts]');
  const waiting = root.querySelector('[data-waiting]');
  const status = document.getElementById('list-status');
  const TAGS = { answered: 'good', calling: 'draft', failed: 'bad', no_answer: 'bad' };
  let busy = false;

  const show = (data) => {
    const tally = data.tally;
    finished.textContent = tally.finished;
    bar.value = tally.finished;
    counts.textContent = `${tally.answered} answered · ${tally.no_answer} no answer · ${tally.failed} could not call · ${tally.skipped} not called`;
    waiting.textContent = data.waiting || 'Calling… keep this page open while the calls are made.';
    status.textContent = data.status_label;
    for (const entry of data.entries) {
      const row = root.querySelector(`[data-entry="${entry.id}"]`);
      if (!row) continue;
      const tag = row.querySelector('[data-status]');
      tag.textContent = entry.label;
      tag.className = `tag ${TAGS[entry.status] || ''}`.trim();
      row.querySelector('[data-reason]').textContent = entry.reason;
      const cell = row.querySelector('[data-result]');
      if (entry.call_url && !cell.querySelector('a')) {
        const link = document.createElement('a');
        link.href = entry.call_url;
        link.textContent = 'Open';
        cell.replaceChildren(link);
      }
    }
  };

  const step = async () => {
    if (busy) return;
    busy = true;
    try {
      const response = await fetch(url, { method: 'POST', headers: { 'X-CSRFToken': token } });
      if (!response.ok) throw new Error(String(response.status));
      const data = await response.json();
      show(data);
      if (data.status !== 'running') {
        window.location.reload();
        return;
      }
    } catch (error) {
      waiting.textContent = 'Having trouble reaching the server. Still trying…';
    } finally {
      busy = false;
    }
  };
  step();
  setInterval(step, 3000);
})();
