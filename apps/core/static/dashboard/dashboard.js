jQuery(function ($) {
  // Times are rendered in UTC and shown in the viewer's own zone.
  $('time[data-local-time]').each(function () {
    const date = new Date($(this).attr('datetime'));
    if (!Number.isNaN(date.getTime())) {
      $(this).text(date.toLocaleString([], { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' }));
    }
  });

  $(document).on('change', '[data-autosubmit]', function () {
    this.form.requestSubmit();
  });
  $(document).on('submit', 'form[data-ask]', function (event) {
    if (!window.confirm($(this).attr('data-ask'))) event.preventDefault();
  });
  $(document).on('submit', 'form[data-call-retry]', function (event) {
    if (event.isDefaultPrevented()) return;
    const $form = $(this);
    if ($form.attr('aria-busy') === 'true') {
      event.preventDefault();
      return;
    }
    $form.attr('aria-busy', 'true');
    $form.find('button[type=submit]').prop('disabled', true).text('Starting…');
  });
  $(window).on('pageshow', function () {
    $('form[data-call-retry]').removeAttr('aria-busy')
      .find('button[type=submit]').prop('disabled', false).text('Call again');
  });

  const $button = $('#menu-button');
  const $scrim = $('#scrim');
  if (!$button.length || !$scrim.length) return;
  const $sidebar = $('#sidebar');
  const $close = $('#menu-close');
  const $background = $('.page, .skip-link, #assist-launcher, #assist-panel');
  const mobile = window.matchMedia('(max-width: 900px)');
  const focusable = 'a[href], button:not(:disabled), input:not(:disabled), select:not(:disabled), summary, [tabindex="0"]';
  const setOpen = (requested, restoreFocus = true) => {
    const open = requested && mobile.matches;
    if (open) $(document).trigger('vaani:open-navigation');
    const wasOpen = $('body').hasClass('nav-open');
    const focusInSidebar = $sidebar[0].contains(document.activeElement);
    $('body').toggleClass('nav-open', open);
    $button.attr('aria-expanded', String(open));
    $scrim.prop('hidden', !open);
    $sidebar.prop('inert', mobile.matches && !open);
    $background.prop('inert', open);
    if (open) {
      $sidebar.attr({role: 'dialog', 'aria-modal': 'true'});
      $close.trigger('focus');
    } else {
      $sidebar.removeAttr('role aria-modal');
      if (restoreFocus && (wasOpen || (mobile.matches && focusInSidebar))) {
        (mobile.matches ? $button : $sidebar.find('.side-brand')).trigger('focus');
      }
    }
  };
  $button.on('click', () => setOpen(!$('body').hasClass('nav-open')));
  $close.on('click', () => setOpen(false));
  $scrim.on('click', () => setOpen(false));
  $sidebar.on('click', 'a[href]', () => setOpen(false));
  $(document).on('vaani:close-navigation', () => setOpen(false, false));
  $(mobile).on('change', () => setOpen(false));
  $(document).on('keydown', event => {
    if (!$('body').hasClass('nav-open') || document.querySelector('dialog:modal')) return;
    if (event.key === 'Escape') {
      event.preventDefault();
      setOpen(false);
    } else if (event.key === 'Tab') {
      const $items = $sidebar.find(focusable).filter(':visible');
      const first = $items[0];
      const last = $items[$items.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        $(last).trigger('focus');
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        $(first).trigger('focus');
      }
    }
  });
  setOpen(false, false);
});
