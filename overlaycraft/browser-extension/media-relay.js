// Overlaycraft: hands the game's media command from the extension to media-hook.js in the page.
if (!self.__overlaycraftMediaRelay) {
self.__overlaycraftMediaRelay = true;
chrome.runtime.onMessage.addListener((msg, sender, reply) => {
  if (!msg || msg.type !== 'media') return;
  const done = e => {
    if (e.source !== window || !e.data || e.data.overlaycraftMediaDone !== true) return;
    window.removeEventListener('message', done);
    reply({ how: e.data.how });
  };
  window.addEventListener('message', done);
  window.postMessage({ overlaycraftMedia: true, action: msg.action }, location.origin);
  setTimeout(() => { window.removeEventListener('message', done); reply({ how: 'timeout' }); }, 1500);
  return true;  // answers later
});
}
