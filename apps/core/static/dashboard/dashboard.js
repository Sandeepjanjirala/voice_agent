// Times are rendered in UTC by the server and shown in the viewer's own zone.
for (const el of document.querySelectorAll('time[data-local-time]')) {
  const date = new Date(el.dateTime);
  if (!Number.isNaN(date.getTime())) {
    el.textContent = date.toLocaleString([], { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });
  }
}

for (const control of document.querySelectorAll('[data-autosubmit]')) {
  control.addEventListener('change', () => control.form.submit());
}
// Destructive or hard-to-undo forms ask first.
for (const form of document.querySelectorAll('form[data-ask]')) {
  form.addEventListener('submit', (event) => {
    if (!window.confirm(form.dataset.ask)) event.preventDefault();
  });
}

// Sidebar drawer on small screens. The sidebar is always visible on wide ones.
(() => {
  const button = document.getElementById('menu-button');
  const scrim = document.getElementById('scrim');
  if (!button || !scrim) return;
  const setOpen = (open) => {
    document.body.classList.toggle('nav-open', open);
    button.setAttribute('aria-expanded', String(open));
    scrim.hidden = !open;
  };
  button.addEventListener('click', () => setOpen(!document.body.classList.contains('nav-open')));
  scrim.addEventListener('click', () => setOpen(false));
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') setOpen(false);
  });
})();
