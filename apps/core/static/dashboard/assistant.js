jQuery(function ($) {
  const $launcher = $('#assist-launcher');
  const $panel = $('#assist-panel');
  if (!$launcher.length || !$panel.length) return;
  const $clear = $('#assist-clear');
  const $form = $('#assist-form');
  const $input = $('#assist-input');
  const $send = $('#assist-send');
  const $log = $('#assist-conversation');
  const $welcome = $('#assist-welcome');
  const $status = $('#assist-status');
  const $openers = $launcher.add('[data-open-assist]');
  let history = [];
  let busy = false;
  let $opener = $launcher;
  let restoreFocus = false;

  const syncOpen = open => {
    $('body').toggleClass('assist-open', open);
    $openers.attr('aria-expanded', String(open));
  };
  const closePanel = (returnFocus = $panel[0].contains(document.activeElement)) => {
    if (!$panel.prop('open')) return;
    restoreFocus = returnFocus;
    $panel[0].close();
    syncOpen(false);
  };
  $openers.attr('aria-controls', 'assist-panel').attr('aria-expanded', 'false').on('click', function () {
    $opener = $(this);
    // The navigation drawer is modal on phones; dismiss it before opening help.
    $(document).trigger('vaani:close-navigation');
    $panel[0].show();
    syncOpen(true);
    $input.trigger('focus');
  });
  $('#assist-close').on('click', () => closePanel(true));
  $panel.on('close', () => {
    // A queued close event must not undo a subsequent reopen.
    if ($panel.prop('open')) return;
    syncOpen(false);
    if (restoreFocus) {
      const usableOpener = $opener.is(':visible') && !$opener.closest('[inert]').length;
      (usableOpener ? $opener : $launcher).trigger('focus');
    }
    restoreFocus = false;
  });
  $(document).on('vaani:open-navigation', () => closePanel(false));
  $(document).on('keydown', event => {
    if (event.key !== 'Escape' || event.isDefaultPrevented() || !$panel.prop('open')) return;
    // Leave other dialogs and the mobile navigation in control of their own keys.
    if ($('body').hasClass('nav-open') || document.querySelector('dialog:modal')) return;
    event.preventDefault();
    closePanel();
  });
  $clear.on('click', () => {
    if (busy) return;
    history = [];
    $log.find('.assist-message').remove();
    $welcome.prop('hidden', false);
    $input.val('').trigger('focus');
    $status.text('');
  });
  const append = (role, text) => {
    const $entry = $('<div>').addClass('assist-message assist-' + role);
    $('<span>').text(role === 'user' ? 'You' : 'Vaani Assist').appendTo($entry);
    // Model output is untrusted text, never HTML.
    $('<p>').text(text).appendTo($entry);
    $log.append($entry).scrollTop($log.prop('scrollHeight'));
    return $entry;
  };

  $form.on('submit', event => {
    event.preventDefault();
    const message = $input.val().trim();
    if (busy || !message || message.length > 4000) return;
    busy = true;
    // Keep the focused composer focusable while waiting. A reply must never pull
    // focus away from a dashboard field the user switched to in the meantime.
    $input.prop('readOnly', true).trigger('focus');
    $send.add($clear).prop('disabled', true);
    $form.attr('aria-busy', 'true');
    $welcome.prop('hidden', true);
    const $pending = append('user', message);
    $input.val('');
    $status.text('Thinking…').addClass('thinking');

    const restoreDraft = error => {
      $pending.remove();
      $welcome.prop('hidden', $log.find('.assist-message').length > 0);
      $input.val(message);
      $status.text(error);
    };
    $.ajax({
      url: $form.attr('action'), method: 'POST', contentType: 'application/json',
      dataType: 'json', timeout: 45000,
      headers: {'X-CSRFToken': $form.find('[name=csrfmiddlewaretoken]').val()},
      data: JSON.stringify({message, history})
    }).done(data => {
      if (!data || typeof data.reply !== 'string' || !data.reply.trim()) {
        restoreDraft('No reply received. Please try again.');
        return;
      }
      append('model', data.reply);
      // Bounded page-only context; nothing goes into browser storage.
      history = [...history, {role:'user',content:message.slice(0,2000)}, {role:'model',content:data.reply.slice(0,2000)}].slice(-6);
      $status.text('');
    }).fail((xhr, textStatus) => {
      if (textStatus === 'timeout') {
        restoreDraft('The reply took too long. Your message is ready to retry.');
      } else if (typeof xhr.responseJSON?.error === 'string') {
        restoreDraft(xhr.responseJSON.error);
      } else if (textStatus === 'parsererror' || (xhr.status && !xhr.responseJSON)) {
        restoreDraft('Your session may have expired. Refresh the page and try again.');
      } else {
        restoreDraft('Could not send your message. Please try again.');
      }
    }).always(() => {
      busy = false;
      $send.add($clear).prop('disabled', false);
      $input.prop('readOnly', false);
      $form.removeAttr('aria-busy');
      $status.removeClass('thinking');
    });
  });
  $input.on('keydown', event => {
    if (event.key === 'Enter' && !event.shiftKey && !event.originalEvent?.isComposing) {
      event.preventDefault();
      $form[0].requestSubmit();
    }
  });
  $panel.on('click', '[data-assist-prompt]', function () {
    if (busy) return;
    $input.val($(this).attr('data-assist-prompt'));
    $form[0].requestSubmit();
  });
});
