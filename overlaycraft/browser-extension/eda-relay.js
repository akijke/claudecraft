// Overlaycraft: passes what eda-hook.js found in the page on to the extension.
if (!self.__overlaycraftEdaRelay) {
self.__overlaycraftEdaRelay = true;
window.addEventListener('message', e => {
  if (e.source !== window || !e.data || e.data.overlaycraft !== true || !e.data.order) return;
  try { chrome.runtime.sendMessage({ type: 'order', order: e.data.order, request: e.data.request }); } catch (err) { }
});
}
