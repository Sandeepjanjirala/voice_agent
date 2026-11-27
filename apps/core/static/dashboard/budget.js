// Live size of the instructions, in the bytes the server counts against the
// budget shown by [data-budget-total] (UTF-8: a Telugu letter takes three).
// Long fields also show their own character count. The server checks again
// when saving and publishing.
//
// Default: the fields marked data-budget, as Django stores them (trimmed, and
// a line break in a textarea arrives as two bytes, \r\n).
// data-budget-mode="employee": the text apps/workforce/publishing.py's
// worker_payload builds from the editor (numbered steps, "Find out: ...", the
// strictness sentence), which prompt_size measures.
jQuery(function ($) {
  const encoder = new TextEncoder();
  const clean = (text) => text.split(/\s+/).filter(Boolean).join(' ');

  const employeeParts = ($form) => {
    const sentences = JSON.parse($('#strictness-sentences').text() || '{}');
    const one = (name) => clean($form.find(`[name="${name}"]`).val() || '');
    const all = (name) => $form.find(`[name="${name}"]`).map((_, field) => clean($(field).val())).get();
    const guidance = all('step_guidance');
    const steps = all('step_name').map((name, i) => guidance[i] || name).filter(Boolean);
    let goals = steps.map((text, i) => `${i + 1}. ${text}`).join(' ');
    const labels = all('question').filter(Boolean);
    if (labels.length) goals = `${goals} Find out: ${labels.join(', ')}.`;
    const level = $form.find('[name="strictness"]:checked').val();
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

  $('[data-budget-total]').each((_, meter) => {
    const $meter = $(meter);
    const $parentForm = $meter.closest('form');
    const $form = $parentForm.length ? $parentForm : $(document);
    const employee = $meter.attr('data-budget-mode') === 'employee';
    const budget = Number($meter.attr('data-budget-total'));
    const $value = $meter.find('[data-budget-value]');
    const $chars = $meter.find('[data-budget-chars]');
    const $bar = $meter.find('progress');
    const $note = $meter.find('[data-budget-note]');
    const update = () => {
      const parts = employee
        ? employeeParts($form)
        : $form.find('[data-budget]').map((_, field) => $(field).val().trim()).get();
      const used = parts.reduce(
        (sum, part) => sum + encoder.encode(employee ? part : part.replace(/\n/g, '\r\n')).length, 0);
      const letters = parts.reduce((sum, part) => sum + [...part].length, 0);
      $value.text(used.toLocaleString());
      $chars.text(letters.toLocaleString());
      $bar.prop({ max: budget, value: Math.min(used, budget) })
        .toggleClass('over', used > budget)
        .toggleClass('near', used <= budget && used > budget * 0.85);
      $note.prop('hidden', used <= budget).text(used > budget
          ? `About ${Math.ceil((used - budget) / 3).toLocaleString()} Telugu letters (or ${(used - budget).toLocaleString()} English letters) too long. Shorten the longest part, or move facts into Knowledge.`
          : '');
    };
    $form.on('input', update);
    $form.on('click', () => setTimeout(update, 0));
    update();
  });

  // Character counters for long fields.
  const counted = 'textarea[maxlength], input[type=text][maxlength][data-budget]';
  const attach = (field) => {
    const $field = $(field);
    if ($field.attr('data-counted') || Number($field.prop('maxLength')) < 100) return;
    $field.attr('data-counted', '1');
    const $counter = $('<small>').addClass('char-count').attr('aria-live', 'polite');
    $field.after($counter);
    const show = () => {
      const length = [...$field.val()].length;
      const maxLength = Number($field.prop('maxLength'));
      $counter.text(`${length.toLocaleString()} / ${maxLength.toLocaleString()} characters`)
        .toggleClass('near', length > maxLength * 0.9);
    };
    $field.on('input', show);
    show();
  };
  const attachCounters = () => $(counted).each((_, field) => attach(field));
  attachCounters();
  // Steps and details added later get a counter too.
  new MutationObserver(attachCounters)
    .observe(document.body, { childList: true, subtree: true });
});
