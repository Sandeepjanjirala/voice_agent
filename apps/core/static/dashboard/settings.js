jQuery(function ($) {
  const $mode = $('#id_org-recording');
  const updateQuestion = () => {
    const $question = $('[data-recording-question]');
    $question.prop('hidden', !['test', 'consent'].includes($mode.val()) && !$question.find('.field-error').length);
    $('[data-recording-demo]').prop('hidden', $mode.val() !== 'demo');
  };
  $mode.on('change', updateQuestion);
  updateQuestion();
});
