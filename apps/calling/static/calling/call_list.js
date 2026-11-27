// A running call list: every few seconds, ask the server to take the next
// step (settle the call in progress, or call the next person) and show the
// progress. When the list has stopped (finished, paused, cancelled) and no
// call is still on the line, the page reloads.
jQuery(function ($) {
  const $root = $('#call-list');
  if (!$root.length) return;
  const url = $root.attr('data-step-url');
  const token = $('[name=csrfmiddlewaretoken]').first().val() || '';
  const $finished = $root.find('[data-finished]');
  const $bar = $root.find('[data-bar]');
  const $counts = $root.find('[data-counts]');
  const $waiting = $root.find('[data-waiting]');
  const $status = $('#list-status');
  const TAGS = { answered: 'good', calling: 'draft', failed: 'bad', no_answer: 'bad' };
  let busy = false;

  const showRetry = ($cell, retry) => {
    if ($cell.find('form[aria-busy=true]').length) return;
    $cell.empty();
    if (!retry) return;
    const $action = $('<div>').addClass('retry-action').appendTo($cell);
    if (retry.retry_target_url) {
      $('<a>').addClass('button small').attr('href', retry.retry_target_url).text('View retry').appendTo($action);
    } else if (retry.can_retry) {
      const $form = $('<form>').attr({ method: 'post', action: retry.retry_url, 'data-call-retry': '' }).appendTo($action);
      $('<input>').attr({ type: 'hidden', name: 'csrfmiddlewaretoken' }).val(token).appendTo($form);
      $('<button>').attr('type', 'submit').addClass('primary small').text('Call again').appendTo($form);
    } else if (retry.retry_pending) {
      $('<span>').addClass('help').attr('role', 'status').text('Preparing your retry…').appendTo($action);
    } else if (retry.retry_block) {
      $('<span>').addClass('help retry-reason').text(retry.retry_block).appendTo($action);
    }
  };

  const show = (data) => {
    const tally = data.tally;
    $finished.text(tally.finished);
    $bar.val(tally.finished);
    $counts.text(`${tally.answered} answered · ${tally.no_answer} no answer · ${tally.failed} could not call · ${tally.skipped} not called`);
    $waiting.text(data.waiting || 'Calling… keep this page open while the calls are made.');
    $status.text(data.status_label);
    for (const entry of data.entries) {
      const $row = $root.find(`[data-entry="${$.escapeSelector(String(entry.id))}"]`);
      if (!$row.length) continue;
      $row.find('[data-status]')
        .text(entry.label)
        .attr('class', `tag ${TAGS[entry.status] || ''}`.trim());
      $row.find('[data-reason]').text(entry.reason);
      const $cell = $row.find('[data-result]');
      if (entry.call_url && !$cell.find('a').length) {
        $cell.empty().append($('<a>').attr('href', entry.call_url).text('View result'));
      }
      showRetry($row.find('[data-retry]'), entry.retry);
    }
  };

  const step = async () => {
    if (busy) return;
    busy = true;
    try {
      const data = await $.ajax({
        url,
        method: 'POST',
        headers: { 'X-CSRFToken': token },
        dataType: 'json',
      });
      show(data);
      if (data.status !== 'running' && !data.tally.calling) {
        window.location.reload();
        return;
      }
    } catch (error) {
      $waiting.text('Having trouble reaching the server. Still trying…');
    } finally {
      busy = false;
    }
  };
  step();
  setInterval(step, 3000);
});
