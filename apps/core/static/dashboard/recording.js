// Call recording player: play and pause, a seek bar with times, and the
// conversation linked to the audio. Without JavaScript the browser's own audio
// controls remain. Transcript times count from the start of the conversation;
// the recording starts when the person agreed (data-offset seconds later).
jQuery(function ($) {
  const $player = $('[data-player]').first();
  if (!$player.length) return;
  const $audio = $player.find('[data-audio]');
  const audio = $audio[0];
  const $controls = $player.find('[data-controls]');
  const $play = $player.find('[data-play]');
  const $seek = $player.find('[data-seek]');
  const $current = $player.find('[data-current]');
  const $total = $player.find('[data-total]');
  const offset = Number($player.attr('data-offset')) || 0;
  const known = Number($player.attr('data-duration')) || 0;
  const $turns = $('.chat-turn[data-t]');

  $audio.removeAttr('controls');
  $controls.prop('hidden', false);

  const clock = (seconds) => {
    const whole = Math.max(0, Math.floor(seconds || 0));
    return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, '0')}`;
  };
  // Media playback and time are browser APIs; jQuery handles controls and events.
  // Ogg files may not report a length until fully read; the server knows it.
  const length = () => (Number.isFinite(audio.duration) && audio.duration > 0 ? audio.duration : known);

  const show = () => {
    const end = length();
    $current.text(clock(audio.currentTime));
    if (end) $total.text(clock(end));
    if (end && !$seek.is(':focus')) $seek.val(Math.round((audio.currentTime / end) * 1000));
    $play.text(audio.paused ? 'Play' : 'Pause')
      .attr('aria-label', audio.paused ? 'Play' : 'Pause');
    // The line being spoken, by its time in the conversation.
    const at = audio.currentTime + offset;
    let active = null;
    $turns.each(function () {
      if (Number($(this).attr('data-t')) <= at + 0.25) active = this;
    });
    $turns.each(function () {
      $(this).toggleClass('playing', this === active && !audio.paused);
    });
  };

  $play.on('click', () => {
    if (audio.paused) audio.play().catch(() => {});
    else audio.pause();
  });
  $seek.on('input', () => {
    const end = length();
    if (end) audio.currentTime = (Number($seek.val()) / 1000) * end;
  });
  $turns.each(function () {
    const $turn = $(this);
    const at = Number($turn.attr('data-t')) - offset;
    if (at < 0) return; // said before the recording started
    $turn.addClass('seekable').prop('tabIndex', 0).attr('title', `Play from ${clock(at)}`);
    const jump = () => { audio.currentTime = at; audio.play().catch(() => {}); };
    $turn.on('click', jump);
    $turn.on('keydown', (event) => {
      if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault();
        jump();
      }
    });
  });
  $audio.on('timeupdate play pause loadedmetadata durationchange ended', show);
  $audio.on('error', () => {
    $controls.prop('hidden', true);
    $player.prepend($('<p>').addClass('field-error').attr('role', 'alert')
      .text('The recording could not be loaded. The link lasts ten minutes: reload the page and try again.'));
  });
  show();
});
