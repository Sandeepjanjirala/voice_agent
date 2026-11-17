// Hire wizard, step one: example briefs and a busy state while the draft is written.
(() => {
  const form = document.getElementById('hire-form');
  if (!form) return;
  const brief = form.querySelector('textarea[name=brief]');
  const button = document.getElementById('draft-button');

  for (const example of form.querySelectorAll('.examples button')) {
    example.addEventListener('click', () => {
      brief.value = example.dataset.brief;
      const channel = form.querySelector(`input[name=channel][value="${example.dataset.channel}"]`);
      if (channel) channel.checked = true;
      brief.focus();
    });
  }

  form.addEventListener('submit', () => {
    button.disabled = true;
    button.textContent = 'Drafting…';
    form.classList.add('busy');
    form.setAttribute('aria-busy', 'true');
  });
  // A page restored from the back/forward cache must be usable again.
  window.addEventListener('pageshow', () => {
    button.disabled = false;
    button.textContent = 'Draft this employee';
    form.classList.remove('busy');
    form.removeAttribute('aria-busy');
  });
})();
