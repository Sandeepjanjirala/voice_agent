// Live size of the instructions, in the bytes the server counts against the
// budget shown by [data-budget-total] (UTF-8: a Telugu letter takes three).
// Long fields also show their own character count. The server checks again
// when saving and publishing.
//
// Default: the fields marked data-budget, as Django stores them (trimmed, and
// a line break in a textarea arrives as two bytes, \r\n). The agent profile.
// data-budget-mode="employee": the text apps/workforce/publishing.py's
// worker_payload builds from the editor (numbered steps, "Find out: ...", the
// strictness sentence), which prompt_size measures.
(() => {
  const encoder = new TextEncoder();
  const clean = (text) => text.split(/\s+/).filter(Boolean).join(' ');

  const employeeParts = (form) => {
    const sentences = JSON.parse(document.getElementById('strictness-sentences')?.textContent || '{}');
    const one = (name) => clean(form.querySelector(`[name="${name}"]`)?.value || '');
    const all = (name) => [...form.querySelectorAll(`[name="${name}"]`)].map((field) => clean(field.value));
    const guidance = all('step_guidance');
    const steps = all('step_name').map((name, i) => guidance[i] || name).filter(Boolean);
    let goals = steps.map((text, i) => `${i + 1}. ${text}`).join(' ');
    const labels = all('question').filter(Boolean);
    if (labels.length) goals = `${goals} Find out: ${labels.join(', ')}.`;
    const level = form.querySelector('[name="strictness"]:checked')?.value;
    const limitations = [one('limits'), sentences[level] || ''].filter(Boolean).join(' ');
    // publishing.situations_text: "When …: …; When …: …", only complete pairs.
    const then = all('situation_then');
    const situations = all('situation_when')
      .map((when, i) => [when, then[i] || ''])
      .filter(([when, todo]) => when && todo)
      .map(([when, todo]) => `${/^(when|if) /i.test(when) ? when : `When ${when}`}: ${todo}`)
      .join('; ');
    return [
      one('name'), one('purpose'), goals.trim(), limitations, one('ending'),
      one('success'), situations, one('escalation'),
    ];
  };

  for (const meter of document.querySelectorAll('[data-budget-total]')) {
    const form = meter.closest('form') || document;
    const employee = meter.dataset.budgetMode === 'employee';
    const budget = Number(meter.dataset.budgetTotal);
    const value = meter.querySelector('[data-budget-value]');
    const chars = meter.querySelector('[data-budget-chars]');
    const bar = meter.querySelector('progress');
    const note = meter.querySelector('[data-budget-note]');
    const update = () => {
      const parts = employee
        ? employeeParts(form)
        : [...form.querySelectorAll('[data-budget]')].map((field) => field.value.trim());
      const used = parts.reduce(
        (sum, part) => sum + encoder.encode(employee ? part : part.replace(/\n/g, '\r\n')).length, 0);
      const letters = parts.reduce((sum, part) => sum + [...part].length, 0);
      value.textContent = used.toLocaleString();
      if (chars) chars.textContent = letters.toLocaleString();
      if (bar) {
        bar.max = budget;
        bar.value = Math.min(used, budget);
        bar.classList.toggle('over', used > budget);
        bar.classList.toggle('near', used <= budget && used > budget * 0.85);
      }
      if (note) {
        note.hidden = used <= budget;
        note.textContent = used > budget
          ? `About ${Math.ceil((used - budget) / 3).toLocaleString()} Telugu letters (or ${(used - budget).toLocaleString()} English letters) too long. Shorten the longest part, or move facts into Knowledge.`
          : '';
      }
    };
    form.addEventListener('input', update);
    form.addEventListener('click', () => setTimeout(update, 0));
    update();
  }

  // Character counters for long fields.
  const counted = 'textarea[maxlength], input[type=text][maxlength][data-budget]';
  const attach = (field) => {
    if (field.dataset.counted || Number(field.maxLength) < 100) return;
    field.dataset.counted = '1';
    const counter = document.createElement('small');
    counter.className = 'char-count';
    counter.setAttribute('aria-live', 'polite');
    field.insertAdjacentElement('afterend', counter);
    const show = () => {
      const length = [...field.value].length;
      counter.textContent = `${length.toLocaleString()} / ${Number(field.maxLength).toLocaleString()} characters`;
      counter.classList.toggle('near', length > field.maxLength * 0.9);
    };
    field.addEventListener('input', show);
    show();
  };
  document.querySelectorAll(counted).forEach(attach);
  // Steps and details added later get a counter too.
  new MutationObserver(() => document.querySelectorAll(counted).forEach(attach))
    .observe(document.body, { childList: true, subtree: true });
})();
