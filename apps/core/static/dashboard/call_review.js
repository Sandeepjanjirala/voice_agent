// Anchor links also reveal collapsed diagnostic details, without submitting a form.
jQuery(function ($) {
  function reveal(hash) {
    if (hash === '#technical-details') $('#technical-details').prop('open', true);
  }
  $('[data-open-details]').on('click', function () {
    reveal(this.hash);
  });
  $(window).on('hashchange', () => reveal(window.location.hash));
  reveal(window.location.hash);

  const $live = $('[data-call-refresh]');
  if (!$live.length) return;
  const $status = $('#call-refresh-status');
  const readyText = 'This call is in progress. The page updates every few seconds.';
  let unsaved = false;
  $('main form').on('input change', ':input', () => {
    unsaved = true;
    $status.text('Automatic updates paused to keep your unsaved changes. Save your changes to resume.');
  });
  $status.text(readyText);
  setInterval(() => {
    if (unsaved || document.hidden) return;
    // A page reload must not interrupt typing or close a menu or assistant.
    const editing = $(document.activeElement).is('input, textarea, select, [contenteditable="true"]');
    if (editing || $('dialog[open]').length || $('body').hasClass('nav-open')) return;
    window.location.reload();
  }, Number($live.attr('data-call-refresh')));
});
