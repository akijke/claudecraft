// Overlaycraft: finds the order in what the Yandex Eda page itself loads.
// In the page (world MAIN) it watches fetch/XHR answers; the background worker imports the
// same file for overlaycraftExtract() alone. Nothing leaves this computer: the order goes
// to the extension, and from there to 127.0.0.1 (the game).
(function () {
  const STATUS_KEY = /^(status|state|order_?status|orderStatus|tracking_?status)$/i;
  const HINT_KEY = /order|nr|place|restaurant|eta|courier|tracking|deliver/i;
  const TEXT_KEYS = ['title', 'status_title', 'statusTitle', 'state_title', 'stateTitle', 'short_title',
    'shortTitle', 'description', 'subtitle', 'message', 'text', 'name'];

  function isText(v) { return typeof v === 'string' && v.trim().length > 1 && v.length < 200; }
  function cyr(v) { return isText(v) && /[а-яё]/i.test(v); }

  function textOf(o) {
    if (!o || typeof o !== 'object') return '';
    for (const k of TEXT_KEYS) if (cyr(o[k])) return o[k].trim();
    for (const k of TEXT_KEYS) if (isText(o[k])) return o[k].trim();
    return '';
  }

  /** Minutes until arrival, from numbers, ISO dates or "15–25 мин". */
  function etaOf(o, depth) {
    if (!o || typeof o !== 'object' || depth > 2) return null;
    for (const [k, v] of Object.entries(o)) {
      if (!/eta|minutes|remaining|estimat|deadline|promise|arriv/i.test(k)) continue;
      if (typeof v === 'number' && v >= 0) {
        if (v < 300) return v;                                  // minutes
        if (v > 1e12) return (v - Date.now()) / 60000;          // epoch ms
        if (v > 1e9) return (v * 1000 - Date.now()) / 60000;    // epoch s
        if (v < 18000 && /sec/i.test(k)) return v / 60;
      }
      if (typeof v === 'string') {
        if (/^\d{4}-\d\d-\d\dT/.test(v)) { const t = Date.parse(v); if (!isNaN(t)) return (t - Date.now()) / 60000; }
        const m = v.match(/(\d{1,3})\s*(?:[–-]\s*\d{1,3}\s*)?мин/);
        if (m) return Number(m[1]);
      }
      if (typeof v === 'object') { const inner = etaOf(v, depth + 1); if (inner != null) return inner; }
    }
    return null;
  }

  function placeOf(o) {
    for (const k of ['place', 'restaurant', 'brand', 'shop', 'store'])
      if (o[k] && typeof o[k] === 'object' && isText(o[k].name)) return o[k].name.trim();
    for (const k of ['place_name', 'placeName', 'restaurant_name', 'restaurantName', 'brand_name', 'brandName'])
      if (isText(o[k])) return o[k].trim();
    return '';
  }

  function stepOf(s) {
    s = s.toLowerCase();
    if (/cancel|отмен/.test(s)) return -1;
    if (/delivered|finished|completed|complete\b|доставлен|вручен|получен/.test(s)) return 3;
    if (/deliver|courier|taken|on_the_way|in_transit|picked|arrived|в пути|курьер|едет/.test(s)) return 2;
    if (/cook|prepar|kitchen|assembl|готов|собира/.test(s)) return 1;
    return 0;
  }

  /** The order in a JSON answer, or null. */
  function overlaycraftExtract(data) {
    let best = null, bestScore = 0;
    const seen = new Set();
    (function walk(o, depth) {
      if (!o || typeof o !== 'object' || depth > 9 || seen.has(o)) return;
      seen.add(o);
      if (Array.isArray(o)) { for (const x of o.slice(0, 50)) walk(x, depth + 1); return; }
      const keys = Object.keys(o);
      const statusKey = keys.find(k => STATUS_KEY.test(k));
      if (statusKey && keys.some(k => HINT_KEY.test(k))) {
        const st = o[statusKey];
        const code = typeof st === 'string' ? st : st && typeof st === 'object' ? String(st.code || st.type || st.id || st.status || '') : '';
        const statusText = (st && typeof st === 'object' ? textOf(st) : '') || textOf(o);
        const eta = etaOf(o, 0);
        const place = placeOf(o);
        const score = (statusText ? 3 : 0) + (code ? 1 : 0) + (eta != null ? 3 : 0) + (place ? 2 : 0);
        if (score > bestScore) {
          bestScore = score;
          best = { status: statusText, code, place, eta, step: stepOf(code + ' ' + statusText) };
        }
      }
      for (const k of keys) walk(o[k], depth + 1);
    })(data, 0);
    if (!best || bestScore < 3) return null;
    const order = {
      service: /market-delivery/.test(typeof location !== 'undefined' ? location.host : '') ? 'Маркет Деливери' : 'Яндекс Еда',
      place: best.place, status: best.status, step: best.step, updated: Date.now()
    };
    if (best.eta != null && best.eta > -5 && best.eta < 600) order.etaAt = Date.now() + Math.max(0, best.eta) * 60000;
    return order;
  }

  // ---- the site's real tracking API (found in its code, 2026-10-29) -------------------
  //   GET /eats/v1/eats-orders-tracking/v1/tracking-for-mobile/check → { tracked_order: { order_nr, ... } }
  //   GET /eats/v1/eats-orders-tracking/v1/tracking-for-desktop?order_nr=… → server-driven
  //       widgets ({ type, payload: { title: { value }, subtitle, progress, ... } })
  // The widget layout changes often, so the order is read from its words: the first text
  // (in page order) that names a stage, the first "N мин" / "в 19:40", a place name.
  const STAGES = [
    [/отмен|не может быть выполнен/i, -1],
    [/доставлен|заказ получен|приятного аппетита|оцените заказ|заказ завершён|заказ завершен/i, 3],
    [/курьер|в пути|везёт|везет|передали|уже рядом|на месте|подъезжает|едет к вам/i, 2],
    [/готовит|готовим|собира|на кухне|заказ готов|упаковыва|ресторан/i, 1],
    [/принят|приняли|подтвержд|ищем курьера|оформлен|ожидает|оплачен/i, 0]
  ];
  const SKIP = /^(https?:|#|rgba?\(|[0-9a-f]{6,}$|[a-z_]+$)/i;

  function textsOf(json) {
    const out = [];
    (function walk(o, depth) {
      if (!o || depth > 14 || out.length > 400) return;
      if (Array.isArray(o)) { for (const x of o) walk(x, depth + 1); return; }
      if (typeof o !== 'object') return;
      for (const [k, v] of Object.entries(o)) {
        if (typeof v === 'string') {
          const s = v.trim();
          if (/^(value|text|title|subtitle|name|description|label|status_text|message)$/i.test(k) && s.length > 1 && s.length < 140 && !SKIP.test(s)) out.push(s);
        } else walk(v, depth + 1);
      }
    })(json, 0);
    return out;
  }

  function placeIn(json) {
    let found = '';
    (function walk(o, depth) {
      if (found || !o || typeof o !== 'object' || depth > 14) return;
      for (const k of ['place', 'restaurant', 'brand', 'store'])
        if (o[k] && typeof o[k] === 'object' && isText(o[k].name)) { found = o[k].name.trim(); return; }
      for (const k of ['place_name', 'placeName', 'brand_name', 'restaurant_name'])
        if (isText(o[k])) { found = o[k].trim(); return; }
      for (const v of Object.values(o)) walk(v, depth + 1);
    })(json, 0);
    return found;
  }

  /** Minutes until arrival from "12 мин", "10–20 мин", "через 15 минут", "в 19:40", "к 19:40". */
  function etaFromTexts(texts) {
    for (const t of texts) {
      const m = t.match(/(\d{1,3})\s*(?:[–-]\s*(\d{1,3})\s*)?мин/i);
      if (m) return Number(m[1]);
      const c = t.match(/(?:^|\s)(?:в|к|до|около|~)\s*(\d{1,2}):(\d{2})/i);
      if (c) {
        const now = new Date(), at = new Date(now);
        at.setHours(Number(c[1]), Number(c[2]), 0, 0);
        if (at < now - 3600e3) at.setDate(at.getDate() + 1);
        return Math.max(0, (at - now) / 60000);
      }
    }
    return null;
  }

  /** The order from the tracking API (check + desktop details), or null. */
  function overlaycraftFromTracking(check, detail, orderNr) {
    const texts = [...textsOf(detail), ...textsOf(check && check.tracked_order)];
    let step = null, status = '';
    for (const t of texts) {
      for (const [re, s] of STAGES) if (re.test(t)) { step = s; status = t; break; }
      if (step !== null) break;
    }
    if (step === null && !texts.length) return null;
    if (step === null) { step = 0; status = texts[0]; }
    const order = {
      service: 'Яндекс Еда', place: placeIn(detail) || placeIn(check), status, step,
      orderNr: orderNr || '', updated: Date.now()
    };
    const eta = step >= 0 && step < 3 ? etaFromTexts(texts) : null;
    if (eta != null) order.etaAt = Date.now() + eta * 60000;
    return order;
  }

  self.overlaycraftExtract = overlaycraftExtract;
  self.overlaycraftFromTracking = overlaycraftFromTracking;
  self.overlaycraftStepOf = stepOf;
  if (typeof window === 'undefined' || typeof document === 'undefined') return;
  if (window.__overlaycraftEdaHook) return;   // injected twice (on install and by the manifest)
  window.__overlaycraftEdaHook = true;

  // ---- in the page: 1) the answers the site loads ---------------------------------------
  let lastApi = 0, lastSent = '';
  function send(order, request) {
    order.url = /track|order|zakaz/i.test(location.pathname) ? location.href : location.origin + '/';
    const key = order.status + '|' + order.step + '|' + Math.round((order.etaAt || 0) / 60000) + '|' + order.place;
    if (key === lastSent && !request) return;
    lastSent = key;
    window.postMessage({ overlaycraft: true, order, request }, location.origin);
  }

  function report(url, method, body, json) {
    let order;
    try {
      if (/eats-orders-tracking\/v\d\/tracking-for-(desktop|mobile\/full-update)/.test(url)) {
        const nr = (new URL(url).searchParams.get('order_nr')) || '';
        order = overlaycraftFromTracking(null, json, nr);
      } else if (/eats-orders-tracking\/v\d\/tracking-for-mobile\/check/.test(url)) {
        return;  // only the order number: the worker asks for the details itself
      } else order = overlaycraftExtract(json);
    } catch (e) { return; }
    if (!order) return;
    lastApi = Date.now();
    send(order, { url, method, body: typeof body === 'string' ? body : null });
  }

  // ---- 2) when the answers say nothing: the words on the order page ----------------------
  // Ordered from the end of the order to its start: the first phrase found wins.
  const PHRASES = [
    [/заказ отмен|отменили заказ|заказ не может быть/i, -1],
    [/доставлен|заказ получен|приятного аппетита|оцените заказ/i, 3],
    [/курьер (уже )?(в пути|едет|рядом|на месте|у вас|ждёт|ждет|подъезжает)|передали курьеру|везёт|везет|в пути/i, 2],
    [/готовит|готовим|собирает|собираем|на кухне|заказ готов|упаковыва/i, 1],
    [/заказ принят|принял заказ|подтвердил|подтверждаем|ищем курьера|ожидает подтверждения/i, 0]
  ];

  function scanPage() {
    if (Date.now() - lastApi < 60000 || !document.body) return;
    const onOrder = /order|track|zakaz/i.test(location.pathname + location.search)
      || document.querySelector('[class*="racking"],[data-testid*="racking"],[class*="OrderStatus"],[class*="order-status"]');
    if (!onOrder) return;
    const lines = document.body.innerText.split('\n').map(s => s.trim()).filter(s => s.length > 3 && s.length < 90);
    for (const [re, step] of PHRASES) {
      const line = lines.find(l => re.test(l));
      if (!line) continue;
      const order = { service: /market-delivery/.test(location.host) ? 'Маркет Деливери' : 'Яндекс Еда',
        place: placeFromTitle(), status: line, step, updated: Date.now() };
      const eta = lines.map(l => l.match(/^(?:~|≈|через\s+)?(\d{1,3})\s*(?:[–-]\s*(\d{1,3})\s*)?мин/i)).find(Boolean);
      if (eta && step >= 0 && step < 3) order.etaAt = Date.now() + Number(eta[1]) * 60000;
      send(order, null);
      return;
    }
  }

  function placeFromTitle() {
    const m = document.title.match(/(?:заказ(?:\s+№?\s*[\d-]+)?\s+(?:из|в)\s+)(.+?)(?:\s+[—|-]\s+|$)/i);
    return m ? m[1].trim() : '';
  }

  setInterval(scanPage, 4000);

  function sameSite(url) {
    try { const u = new URL(url, location.href); return u.host === location.host || u.host.endsWith('.yandex.ru'); } catch (e) { return false; }
  }

  const fetch0 = window.fetch;
  window.fetch = function (input, init) {
    const result = fetch0.apply(this, arguments);
    try {
      const url = typeof input === 'string' ? input : input && input.url;
      const method = (init && init.method) || (input && input.method) || 'GET';
      const body = init && init.body;
      if (url && sameSite(url)) result.then(r => {
        if (!(r.headers.get('content-type') || '').includes('json')) return;
        r.clone().json().then(j => report(new URL(url, location.href).href, method, body, j)).catch(() => {});
      }).catch(() => {});
    } catch (e) { /* never break the page */ }
    return result;
  };

  const open0 = XMLHttpRequest.prototype.open, send0 = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.open = function (method, url) { this.__ocMethod = method; this.__ocUrl = url; return open0.apply(this, arguments); };
  XMLHttpRequest.prototype.send = function (body) {
    try {
      if (this.__ocUrl && sameSite(this.__ocUrl)) this.addEventListener('load', () => {
        try {
          if (!(this.getResponseHeader('content-type') || '').includes('json')) return;
          const j = this.responseType === 'json' ? this.response : JSON.parse(this.responseText);
          report(new URL(this.__ocUrl, location.href).href, this.__ocMethod || 'GET', body, j);
        } catch (e) { }
      });
    } catch (e) { }
    return send0.apply(this, arguments);
  };
})();
