// Draft editor: add, remove and reorder steps and details. The form posts
// repeated fields in page order, so the server needs no JavaScript to read it.
(() => {
  const lists = [
    { list: 'steps', add: 'add-step', template: 'step-template' },
    { list: 'questions', add: 'add-question', template: 'question-template' },
    { list: 'situations', add: 'add-situation', template: 'situation-template' },
    { list: 'outcomes', add: 'add-outcome', template: 'outcome-template' },
  ];

  const refresh = (list, addButton) => {
    const rows = [...list.children];
    rows.forEach((row, index) => {
      const number = row.querySelector('.num');
      if (number) number.textContent = `${index + 1}.`;
      const up = row.querySelector('[data-move="-1"]');
      const down = row.querySelector('[data-move="1"]');
      if (up) up.disabled = index === 0;
      if (down) down.disabled = index === rows.length - 1;
    });
    addButton.disabled = rows.length >= Number(list.dataset.max);
  };

  for (const { list: listId, add, template } of lists) {
    const list = document.getElementById(listId);
    const addButton = document.getElementById(add);
    const source = document.getElementById(template);
    if (!list || !addButton || !source) continue;

    addButton.addEventListener('click', () => {
      const row = source.content.firstElementChild.cloneNode(true);
      list.append(row);
      refresh(list, addButton);
      row.querySelector('input').focus();
    });
    list.addEventListener('click', (event) => {
      const button = event.target.closest('button');
      const row = button && button.closest('.row-item');
      if (!row) return;
      if (button.hasAttribute('data-remove')) {
        row.remove();
      } else if (button.dataset.move === '-1' && row.previousElementSibling) {
        row.previousElementSibling.before(row);
        button.focus();
      } else if (button.dataset.move === '1' && row.nextElementSibling) {
        row.nextElementSibling.after(row);
        button.focus();
      }
      refresh(list, addButton);
    });
    refresh(list, addButton);
  }

  // Trying and publishing use the saved draft, so unsaved typing blocks them.
  const editor = document.getElementById('employee-form');
  const actions = document.getElementById('publish-actions');
  const note = document.getElementById('unsaved-note');
  if (editor && actions && note) {
    const markUnsaved = () => {
      if (!note.hidden) return;
      note.hidden = false;
      for (const control of actions.querySelectorAll('button')) control.disabled = true;
      for (const link of actions.querySelectorAll('a')) {
        link.setAttribute('aria-disabled', 'true');
        link.classList.add('disabled');
        link.addEventListener('click', (event) => event.preventDefault());
      }
    };
    editor.addEventListener('input', markUnsaved);
    editor.addEventListener('click', (event) => {
      if (event.target.closest('.row-tools button, .add-row')) markUnsaved();
    });
  }

  // Voice preview: the opening line in the voice and speed chosen on the
  // form, saved or not. The server returns MP3 audio or a JSON error.
  const previewButton = document.getElementById('voice-preview-button');
  const previewAudio = document.getElementById('voice-preview-audio');
  const previewStatus = document.getElementById('voice-preview-status');
  if (editor && previewButton && previewAudio && previewStatus) {
    let previewUrl = '';
    previewButton.addEventListener('click', async () => {
      const data = new FormData();
      data.append('csrfmiddlewaretoken', editor.querySelector('[name=csrfmiddlewaretoken]').value);
      data.append('text', editor.querySelector('[name=greeting]').value);
      data.append('speaker', editor.querySelector('[name=voice_speaker]').value);
      data.append('pace', editor.querySelector('[name=voice_pace]').value);
      data.append('language', editor.querySelector('[name=language]').value);
      previewButton.disabled = true;
      previewStatus.textContent = 'Getting the audio…';
      try {
        const response = await fetch(previewButton.dataset.url, { method: 'POST', body: data, credentials: 'same-origin' });
        if (!response.ok) {
          const problem = await response.json().catch(() => ({}));
          previewStatus.textContent = problem.error || 'The preview could not be made.';
          return;
        }
        if (previewUrl) URL.revokeObjectURL(previewUrl);
        previewUrl = URL.createObjectURL(await response.blob());
        previewAudio.src = previewUrl;
        previewAudio.hidden = false;
        previewStatus.textContent = 'Playing. No phone call was made.';
        previewAudio.play().catch(() => {
          previewStatus.textContent = 'Ready: press play.';
        });
      } catch {
        previewStatus.textContent = 'The preview could not be made. Check your connection.';
      } finally {
        previewButton.disabled = false;
      }
    });
  }

  for (const form of document.querySelectorAll('form[data-confirm]')) {
    form.addEventListener('submit', (event) => {
      if (!window.confirm(form.dataset.confirm)) event.preventDefault();
    });
  }
})();
