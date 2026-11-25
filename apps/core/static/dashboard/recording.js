// Call recording player: play and pause, a seek bar with times, and the
// conversation linked to the audio. Without JavaScript the browser's own audio
// controls remain. Transcript times count from the start of the conversation;
// the recording starts when the person agreed (data-offset seconds later).
(() => {
  const player = document.querySelector('[data-player]');
  if (!player) return;
  const audio = player.querySelector('[data-audio]');
  const controls = player.querySelector('[data-controls]');
  const play = player.querySelector('[data-play]');
  const seek = player.querySelector('[data-seek]');
  const current = player.querySelector('[data-current]');
  const total = player.querySelector('[data-total]');
  const offset = Number(player.dataset.offset) || 0;
  const known = Number(player.dataset.duration) || 0;
  const turns = [...document.querySelectorAll('.chat-turn[data-t]')];

  audio.removeAttribute('controls');
  controls.hidden = false;

  const clock = (seconds) => {
    const whole = Math.max(0, Math.floor(seconds || 0));
    return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, '0')}`;
  };
  // Ogg files may not report a length until fully read; the server knows it.
  const length = () => (Number.isFinite(audio.duration) && audio.duration > 0 ? audio.duration : known);

  const show = () => {
    const end = length();
    current.textContent = clock(audio.currentTime);
    if (end) total.textContent = clock(end);
    if (end && document.activeElement !== seek) seek.value = String(Math.round((audio.currentTime / end) * 1000));
    play.textContent = audio.paused ? 'Play' : 'Pause';
    play.setAttribute('aria-label', audio.paused ? 'Play' : 'Pause');
    // The line being spoken, by its time in the conversation.
    const at = audio.currentTime + offset;
    let active = null;
    for (const turn of turns) if (Number(turn.dataset.t) <= at + 0.25) active = turn;
    for (const turn of turns) turn.classList.toggle('playing', turn === active && !audio.paused);
  };

  play.addEventListener('click', () => {
    if (audio.paused) audio.play().catch(() => {});
    else audio.pause();
  });
  seek.addEventListener('input', () => {
    const end = length();
    if (end) audio.currentTime = (Number(seek.value) / 1000) * end;
  });
  for (const turn of turns) {
    const at = Number(turn.dataset.t) - offset;
    if (at < 0) continue; // said before the recording started
    turn.classList.add('seekable');
    turn.tabIndex = 0;
    turn.title = `Play from ${clock(at)}`;
    const jump = () => { audio.currentTime = at; audio.play().catch(() => {}); };
    turn.addEventListener('click', jump);
    turn.addEventListener('keydown', (event) => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); jump(); } });
  }
  for (const name of ['timeupdate', 'play', 'pause', 'loadedmetadata', 'durationchange', 'ended']) {
    audio.addEventListener(name, show);
  }
  audio.addEventListener('error', () => {
    controls.hidden = true;
    const note = document.createElement('p');
    note.className = 'field-error';
    note.setAttribute('role', 'alert');
    note.textContent = 'The recording could not be loaded. The link lasts ten minutes: reload the page and try again.';
    player.prepend(note);
  });
  show();
})();
