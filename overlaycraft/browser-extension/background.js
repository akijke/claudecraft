// Overlaycraft: tells the game (127.0.0.1:18745) which tabs are open and where the food
// order is, and carries out what the game asks (switch to a tab, open a page).
importScripts('eda-hook.js');

const BASE = 'http://127.0.0.1:18745';
const ORDER_TTL = 4 * 3600 * 1000;

function browserName() {
  const ua = navigator.userAgent;
  if (/OPR\//.test(ua)) return 'Opera';
  if (/YaBrowser\//.test(ua)) return 'Яндекс Браузер';
  if (/Edg\//.test(ua)) return 'Edge';
  return 'Chrome';
}

async function post(path, body) {
  try {
    await fetch(BASE + path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    return true;
  } catch (e) { return false; }   // the game is not running: fine
}

// ---- tabs -------------------------------------------------------------------------
async function pushState() {
  const [tabs, focused, stored] = await Promise.all([
    chrome.tabs.query({}),
    chrome.windows.getLastFocused().catch(() => null),
    chrome.storage.local.get('order')
  ]);
  const body = {
    browser: browserName(),
    tabs: tabs.filter(t => !t.incognito && /^https?:/.test(t.url || '')).map(t => ({
      id: t.id, window: t.windowId, title: t.title || '', url: t.url,
      audible: !!t.audible, active: !!t.active, focused: !!(focused && focused.id === t.windowId)
    }))
  };
  const order = stored.order;
  if (order && Date.now() - order.updated < ORDER_TTL) body.order = order;
  else body.order = { none: true };
  body.eda = await edaStatus();
  if (await post('/ext/state', body)) poll();
}

let timer = null;
function soon() { clearTimeout(timer); timer = setTimeout(pushState, 250); }

chrome.tabs.onUpdated.addListener((id, change) => {
  if ('title' in change || 'audible' in change || 'url' in change || change.status === 'complete') soon();
});
chrome.tabs.onActivated.addListener(soon);
chrome.tabs.onRemoved.addListener(soon);
chrome.tabs.onCreated.addListener(soon);
chrome.windows.onFocusChanged.addListener(soon);

// ---- commands from the game (long poll) ---------------------------------------------
let polling = false;
async function poll() {
  if (polling) return;
  polling = true;
  try {
    for (;;) {
      const r = await fetch(BASE + '/ext/poll');
      if (!r.ok) break;
      const { commands } = await r.json();
      for (const c of commands || []) await run(c).catch(() => {});
    }
  } catch (e) { /* the game closed; the alarm starts again */ }
  polling = false;
}

async function focusTab(tab) {
  await chrome.tabs.update(tab.id, { active: true });
  await chrome.windows.update(tab.windowId, { focused: true });
}

async function run(c) {
  if (c.type === 'media') {
    // the site's own player: works where Windows has no "next" (a single YouTube video)
    const r = await chrome.tabs.sendMessage(c.tab, { type: 'media', action: c.action }).catch(() => null);
    await post('/ext/done', { id: c.id, how: r ? r.how : 'no-script' });
    return;
  }
  if (c.type === 'focus') {
    const tab = await chrome.tabs.get(c.tab);
    await focusTab(tab);
  } else if (c.type === 'open' && /^https?:\/\//.test(c.url)) {
    const want = c.url.split('#')[0];
    const tabs = await chrome.tabs.query({});
    const same = tabs.find(t => (t.url || '').split('#')[0] === want);
    if (same) await focusTab(same);
    else {
      const tab = await chrome.tabs.create({ url: c.url });
      await chrome.windows.update(tab.windowId, { focused: true });
    }
  }
  soon();
}

// ---- the food order ---------------------------------------------------------------
// 1) Every 30 s the worker asks Yandex Eda itself, as its own site does, with your browser's
//    sign-in: which order is being tracked, then its details. Works with the tab closed.
//    The sign-in token (cookie "webapitoken") goes only to eda.yandex.ru, never to the game.
// 2) While an Eda tab is open, eda-hook.js also reads the answers the page loads.
const EDA_HOSTS = ['https://eda.yandex.ru', 'https://market-delivery.yandex.ru'];
const TRACK = '/eats/v1/eats-orders-tracking/v1/';
let edaState = 'unknown';   // ok | login | error | unknown — shown on the game's card

async function edaGet(host, path) {
  const headers = { 'Accept': 'application/json', 'X-Platform': 'desktop_web', 'X-App-Version': '26.50.8' };
  const token = await chrome.cookies.get({ url: host, name: 'webapitoken' }).catch(() => null);
  if (token && token.value) headers.Authorization = 'Bearer ' + token.value;
  const r = await fetch(host + path, { credentials: 'include', headers });
  if (r.status === 401 || r.status === 403) return { login: true };
  if (!r.ok) return { error: r.status };
  return { json: await r.json() };
}

async function refreshOrder() {
  const { order } = await chrome.storage.local.get('order');
  let anyLogin = false, anyOk = false;
  for (const host of EDA_HOSTS) {
    let check;
    try { check = await edaGet(host, TRACK + 'tracking-for-mobile/check'); } catch (e) { continue; }
    if (check.login) { anyLogin = true; continue; }
    if (!check.json) continue;
    anyOk = true;
    const nr = check.json.tracked_order && check.json.tracked_order.order_nr;
    if (!nr) continue;
    let detail = null;
    try { detail = (await edaGet(host, TRACK + 'tracking-for-desktop?order_nr=' + encodeURIComponent(nr))).json || null; } catch (e) { }
    if (!detail) try { detail = (await edaGet(host, TRACK + 'tracking-for-mobile/full-update?order_nr=' + encodeURIComponent(nr))).json || null; } catch (e) { }
    const fresh = overlaycraftFromTracking(check.json, detail, nr);
    if (!fresh) continue;
    if (host.includes('market-delivery')) fresh.service = 'Маркет Деливери';
    if (!fresh.place && order && order.orderNr === nr) fresh.place = order.place;
    fresh.url = host + '/tracking/' + nr;
    edaState = 'ok';
    await chrome.storage.local.set({ order: fresh });
    return;
  }
  edaState = anyOk ? 'ok' : anyLogin ? 'login' : 'error';
  // No tracked order now: one we were following has ended (the site stops tracking it)
  if (anyOk && order && order.orderNr && order.step >= 0 && order.step < 3)
    await chrome.storage.local.set({ order: Object.assign({}, order, { step: 3, status: 'Заказ завершён', updated: Date.now(), etaAt: 0 }) });
}

chrome.runtime.onMessage.addListener((msg, sender) => {
  if (!msg || msg.type !== 'order' || !msg.order) return;
  const host = sender.url ? new URL(sender.url).host : '';
  if (!/(^|\.)eda\.yandex\.(ru|kz|by)$|^market-delivery\.yandex\.ru$/.test(host)) return;
  chrome.storage.local.set({ order: msg.order }).then(soon);
});

// What the game shows when there is no order (and why), for the card and /ov status
async function edaStatus() { return edaState; }

// Tabs opened before the extension was installed get its scripts now, without a reload.
// (A late media-hook misses the site's media handlers; the player's buttons still work.)
async function injectExisting() {
  for (const cs of chrome.runtime.getManifest().content_scripts) {
    const tabs = await chrome.tabs.query({ url: cs.matches }).catch(() => []);
    for (const tab of tabs)
      await chrome.scripting.executeScript({ target: { tabId: tab.id }, files: cs.js, world: cs.world || 'ISOLATED' }).catch(() => {});
  }
}
chrome.runtime.onInstalled.addListener(injectExisting);

chrome.alarms.create('tick', { periodInMinutes: 0.5 });
chrome.alarms.onAlarm.addListener(async () => { await refreshOrder(); await pushState(); });
chrome.runtime.onStartup.addListener(pushState);
chrome.runtime.onInstalled.addListener(pushState);
refreshOrder().finally(pushState);
