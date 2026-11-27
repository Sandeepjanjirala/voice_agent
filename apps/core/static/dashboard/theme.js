(function ($) {
  const storageKey = 'vaani-theme';
  const normalise = value => value === 'dark' ? 'dark' : 'light';

  function applyTheme(value) {
    const theme = normalise(value);
    const isDark = theme === 'dark';
    $('html').attr('data-theme', theme);
    $('[data-theme-toggle]')
      .attr('aria-pressed', String(isDark))
      .attr('aria-label', `Switch to ${isDark ? 'light' : 'dark'} mode`)
      .find('[data-theme-label]').text(isDark ? 'Light mode' : 'Dark mode');
  }

  let savedTheme = 'light';
  try {
    savedTheme = window.localStorage.getItem(storageKey);
  } catch (_) {
    // The switch remains usable when browser privacy settings deny storage.
  }
  // This script runs before the stylesheets, including on a saved dark theme.
  applyTheme(savedTheme);

  $(window).on('storage.vaaniTheme', function (event) {
    const change = event.originalEvent;
    if (change.key !== storageKey && change.key !== null) return;
    try {
      if (change.storageArea !== window.localStorage) return;
    } catch (_) {
      return;
    }
    applyTheme(change.newValue);
  });

  $(function () {
    applyTheme($('html').attr('data-theme'));
    $(document).on('click.vaaniTheme', '[data-theme-toggle]', function () {
      const theme = $('html').attr('data-theme') === 'dark' ? 'light' : 'dark';
      applyTheme(theme);
      try {
        window.localStorage.setItem(storageKey, theme);
      } catch (_) {
        // Keep the selected theme for this page even if it cannot be saved.
      }
    });
  });
})(jQuery);
