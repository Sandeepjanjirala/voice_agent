// Refresh only read-only dashboard content. No calls are started by this page.
jQuery(function ($) {
  const $region = $('#home-live');
  const $refresh = $('#refresh-home');
  const $status = $('#refresh-status');
  if (!$region.length || !$refresh.length) return;
  let busy = false;
  const $motion = $('#motion-toggle');
  let paused = false;
  try { paused = localStorage.getItem('vaani-motion') === 'paused'; } catch {}
  const applyMotion = () => {
    $('body').toggleClass('motion-paused', paused);
    $motion.attr('aria-pressed', String(paused)).text(paused ? 'Resume motion' : 'Pause motion');
  };
  if ($motion.length) {
    applyMotion();
    $motion.on('click', () => {
      paused = !paused;
      applyMotion();
      try { localStorage.setItem('vaani-motion', paused ? 'paused' : 'playing'); } catch {}
    });
  }
  $('body').addClass('motion-ready');
  setTimeout(() => $('body').removeClass('motion-ready'), 1200);
  const filter = () => {
    const query = $('#call-search').val().trim().toLowerCase();
    const $rows = $region.find('#recent-call-rows tr');
    $rows.each(function () {
      $(this).prop('hidden', !$(this).text().toLowerCase().includes(query));
    });
    $('#no-call-matches').prop('hidden', !query || $rows.filter(function () { return !this.hidden; }).length > 0);
  };
  $region.on('input', '#call-search', filter);
  const localTimes = () => $region.find('time[data-local-time]').each(function () {
    const date = new Date($(this).attr('datetime'));
    if (!Number.isNaN(date.getTime())) $(this).text(date.toLocaleString([], {day:'numeric', month:'short', hour:'2-digit', minute:'2-digit'}));
  });
  function update(manual = false) {
    if (busy || document.hidden || (!manual && $.contains($region[0], document.activeElement))) return;
    busy = true;
    $refresh.prop('disabled', true);
    $status.text('Updating…');
    $.ajax({
      url: window.location.href, method: 'GET', dataType: 'text', timeout: 15000,
      headers: {'Cache-Control': 'no-cache'}
    }).done(html => {
      try {
        // Inert parsing avoids running scripts from returned pages. Adopt only
        // dashboard content; delegated filtering remains attached to the region.
        const doc = new DOMParser().parseFromString(html, 'text/html');
        const $content = $(doc).find('#home-live');
        if (!$content.length) throw new Error('Unavailable');
        $content.find('script').remove();
        const query = $('#call-search').val();
        const open = $('#setup-checklist').prop('open');
        $region.empty().append($content.contents());
        $('#call-search').val(query);
        if (open !== undefined) $('#setup-checklist').prop('open', open);
        filter();
        localTimes();
        $region.addClass('data-updated');
        setTimeout(() => $region.removeClass('data-updated'), 800);
        $status.text('Updated ' + new Date().toLocaleTimeString([], {hour:'2-digit', minute:'2-digit'}));
      } catch {
        $status.text('Could not update. Try Refresh.');
      }
    }).fail(() => {
      $status.text('Could not update. Try Refresh.');
    }).always(() => {
      $('body').removeClass('refreshing');
      busy = false;
      $refresh.prop('disabled', false);
    });
  }
  $refresh.on('click', () => update(true));
  setInterval(() => update(), 30000);
});
