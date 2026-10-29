// Overlaycraft: play / pause / next / previous inside a music site's own player.
// Runs in the page (world MAIN) from the very start, so it sees the handlers the site gives
// to navigator.mediaSession (what Windows' media keys call). When the site has none for an
// action (a single YouTube video has no "next" for Windows), it presses the player's button.
(function () {
  if (window.__overlaycraftMedia) return;
  window.__overlaycraftMedia = true;
  const handlers = {};
  const ms = navigator.mediaSession;
  if (ms && ms.setActionHandler) {
    const set0 = ms.setActionHandler.bind(ms);
    ms.setActionHandler = function (action, handler) {
      handlers[action] = handler;
      return set0(action, handler);
    };
  }

  // the player's own buttons, per site, then generic ones
  const BUTTONS = {
    next: ['.ytp-next-button', 'ytmusic-player-bar .next-button', 'yt-icon-button.next-button',
      '.skipControl__next', '[data-testid="audio-player-controls-forward-button"]', '.audio_page_player_next',
      '.top_audio_player_next', '[aria-label="Следующий трек"]', '[aria-label="Next track"]', '[aria-label="Next"]',
      '[title="Следующий трек"]', '[title="Следующий"]', '[title="Next"]'],
    prev: ['.ytp-prev-button', 'ytmusic-player-bar .previous-button', '.skipControl__previous',
      '[data-testid="audio-player-controls-backward-button"]', '.audio_page_player_prev', '.top_audio_player_prev',
      '[aria-label="Предыдущий трек"]', '[aria-label="Previous track"]', '[aria-label="Previous"]',
      '[title="Предыдущий трек"]', '[title="Предыдущий"]', '[title="Previous"]']
  };

  function visible(el) {
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0 && !el.disabled && el.getAttribute('aria-disabled') !== 'true';
  }

  function pressButton(kind) {
    for (const sel of BUTTONS[kind]) {
      const el = [...document.querySelectorAll(sel)].find(visible);
      if (el) { el.click(); return true; }
    }
    // any button whose label says "next"/"previous"
    const word = kind === 'next' ? /^(next|след|вперёд|вперед|skip)/i : /^(prev|пред|назад|back)/i;
    const el = [...document.querySelectorAll('button,[role=button]')].find(b =>
      visible(b) && word.test((b.getAttribute('aria-label') || b.getAttribute('title') || '').trim()));
    if (el) { el.click(); return true; }
    return false;
  }

  function media() {
    return [...document.querySelectorAll('video,audio')].sort((a, b) => (b.paused ? 0 : 1) - (a.paused ? 0 : 1))[0];
  }

  function run(action) {
    const map = { next: 'nexttrack', prev: 'previoustrack', play: 'play', pause: 'pause' };
    const el = media();
    if (action === 'toggle') action = el ? (el.paused ? 'play' : 'pause') : (ms && ms.playbackState === 'playing' ? 'pause' : 'play');
    const h = handlers[map[action]];
    // YouTube: the player's own API (its "next" button is hidden in the new layout)
    const yt = document.getElementById('movie_player');
    if (yt && typeof yt.nextVideo === 'function' && /youtube\.com$/.test(location.hostname)) {
      if (action === 'next') { yt.nextVideo(); return 'youtube'; }
      if (action === 'prev') {
        if (yt.getPlaylistIndex && yt.getPlaylistIndex() > 0) yt.previousVideo();
        else if (yt.getCurrentTime && yt.getCurrentTime() > 3) yt.seekTo(0, true);
        else history.back();
        return 'youtube';
      }
    }
    if ((action === 'next' || action === 'prev') && pressButton(action)) return 'button';
    if (h) { try { h({ action: map[action] }); return 'handler'; } catch (e) { } }
    if (el && action === 'play') { el.play(); return 'element'; }
    if (el && action === 'pause') { el.pause(); return 'element'; }
    if (el && action === 'prev') { el.currentTime = 0; return 'restart'; }
    return 'none';
  }

  window.addEventListener('message', e => {
    if (e.source !== window || !e.data || e.data.overlaycraftMedia !== true) return;
    const how = run(e.data.action);
    window.postMessage({ overlaycraftMediaDone: true, action: e.data.action, how }, location.origin);
  });
})();
