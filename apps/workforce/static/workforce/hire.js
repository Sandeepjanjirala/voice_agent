// Hire wizard, step one: example briefs and a busy state while the draft is written.
jQuery(function ($) {
  const $form = $('#hire-form');
  if (!$form.length) return;
  const $brief = $form.find('textarea[name=brief]');
  const $button = $('#draft-button');

  $form.on('click', '.examples button', function () {
    const $example = $(this);
    $brief.val($example.attr('data-brief'));
    $form.find('input[name=channel]').filter(function () {
      return $(this).val() === $example.attr('data-channel');
    }).prop('checked', true);
    $brief.trigger('focus');
  });

  $form.on('submit', () => {
    $button.prop('disabled', true).text('Drafting…');
    $form.addClass('busy').attr('aria-busy', 'true');
  });
  // A page restored from the back/forward cache must be usable again.
  $(window).on('pageshow', () => {
    $button.prop('disabled', false).text('Draft this employee');
    $form.removeClass('busy').removeAttr('aria-busy');
  });
});
