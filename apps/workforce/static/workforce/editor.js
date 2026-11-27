// Draft editor: add, remove and reorder steps and details. The form posts
// repeated fields in page order, so the server needs no JavaScript to read it.
jQuery(function ($) {
  const lists = [
    { list: 'steps', add: 'add-step', template: 'step-template' },
    { list: 'questions', add: 'add-question', template: 'question-template' },
    { list: 'situations', add: 'add-situation', template: 'situation-template' },
    { list: 'outcomes', add: 'add-outcome', template: 'outcome-template' },
  ];

  const refresh = ($list, $addButton) => {
    const $rows = $list.children();
    $rows.each((index, row) => {
      const $row = $(row);
      $row.find('.num').text(`${index + 1}.`);
      $row.find('[data-move="-1"]').prop('disabled', index === 0);
      $row.find('[data-move="1"]').prop('disabled', index === $rows.length - 1);
    });
    $addButton.prop('disabled', $rows.length >= Number($list.attr('data-max')));
  };

  for (const { list: listId, add, template } of lists) {
    const $list = $(`#${listId}`);
    const $addButton = $(`#${add}`);
    const $source = $(`#${template}`);
    if (!$list.length || !$addButton.length || !$source.length) continue;

    $addButton.on('click', () => {
      const $row = $($source.prop('content')).children().first().clone();
      $list.append($row);
      refresh($list, $addButton);
      $row.find('input').first().trigger('focus');
    });
    $list.on('click', 'button', function () {
      const $button = $(this);
      const $row = $button.closest('.row-item');
      if (!$row.length) return;
      if ($button.is('[data-remove]')) {
        $row.remove();
      } else if ($button.attr('data-move') === '-1' && $row.prev().length) {
        $row.insertBefore($row.prev());
        $button.trigger('focus');
      } else if ($button.attr('data-move') === '1' && $row.next().length) {
        $row.insertAfter($row.next());
        $button.trigger('focus');
      }
      refresh($list, $addButton);
    });
    refresh($list, $addButton);
  }

  // Trying and publishing use the saved draft, so unsaved typing blocks them.
  const $editor = $('#employee-form');
  const $actions = $('#publish-actions');
  const $note = $('#unsaved-note');
  if ($editor.length && $actions.length && $note.length) {
    const markUnsaved = () => {
      if (!$note.prop('hidden')) return;
      $note.prop('hidden', false);
      $actions.find('button').prop('disabled', true);
      $actions.find('a').attr('aria-disabled', 'true').addClass('disabled')
        .on('click', (event) => event.preventDefault());
    };
    $editor.on('input', markUnsaved);
    // Adding the final allowed row disables its button before this event
    // bubbles. A direct form handler also catches that last edit.
    $editor.on('click', (event) => {
      if ($(event.target).closest('.row-tools button, .add-row').length) markUnsaved();
    });
  }

  // Voice preview: the opening line in the voice and speed chosen on the
  // form, saved or not. The server returns MP3 audio or a JSON error.
  const $previewButton = $('#voice-preview-button');
  const $previewAudio = $('#voice-preview-audio');
  const $previewStatus = $('#voice-preview-status');
  if ($editor.length && $previewButton.length && $previewAudio.length && $previewStatus.length) {
    let previewUrl = '';
    $previewButton.on('click', async () => {
      const data = new FormData();
      data.append('csrfmiddlewaretoken', $editor.find('[name=csrfmiddlewaretoken]').val());
      data.append('text', $editor.find('[name=greeting]').val());
      data.append('speaker', $editor.find('[name=voice_speaker]').val());
      data.append('pace', $editor.find('[name=voice_pace]').val());
      data.append('language', $editor.find('[name=language]').val());
      $previewButton.prop('disabled', true);
      $previewStatus.text('Getting the audio…');
      try {
        const audio = await $.ajax({
          url: $previewButton.attr('data-url'),
          method: 'POST',
          data,
          processData: false,
          contentType: false,
          dataType: 'binary',
          xhrFields: { responseType: 'blob' },
          // Keep HTTP error bodies available too: this endpoint returns JSON
          // errors, even though a successful response is an audio Blob.
          responseFields: { binary: 'responseBlob' },
        });
        if (previewUrl) URL.revokeObjectURL(previewUrl);
        previewUrl = URL.createObjectURL(audio);
        $previewAudio.attr('src', previewUrl).prop('hidden', false);
        $previewStatus.text('Playing. No phone call was made.');
        $previewAudio.get(0).play().catch(() => {
          $previewStatus.text('Ready: press play.');
        });
      } catch (error) {
        if (error.status) {
          let message = 'The preview could not be made.';
          try {
            const problem = JSON.parse(await error.responseBlob.text());
            if (problem && typeof problem.error === 'string' && problem.error) {
              message = problem.error;
            }
          } catch {
            // An HTML error page or an empty body still gets a useful message.
          }
          $previewStatus.text(message);
        } else {
          $previewStatus.text('The preview could not be made. Check your connection.');
        }
      } finally {
        $previewButton.prop('disabled', false);
      }
    });
  }

  $('form[data-confirm]').on('submit', function (event) {
    if (!window.confirm($(this).attr('data-confirm'))) event.preventDefault();
  });
});
