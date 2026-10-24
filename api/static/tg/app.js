/* LinQ Guard · Telegram Mini App. Vanilla JS, no build step.
 * Three roles over /api/tg/*: worker (own shift), supervisor (alarms, feed, people, live, report),
 * director (overview + everything the supervisor sees + settings). Works in a plain browser as a demo. */
(function () {
  'use strict';
  const tg = window.Telegram && window.Telegram.WebApp;
  const INIT = (tg && tg.initData) || '';
  const IN_TG = !!INIT;
  const ver = (v) => !!(tg && tg.isVersionAtLeast && tg.isVersionAtLeast(v));
  const HAS_MAIN = IN_TG && !!tg.MainButton;
  const $ = (s, r) => (r || document).querySelector(s);
  const $$ = (s, r) => Array.from((r || document).querySelectorAll(s));
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
  function keep(k, v) {
    try {
      if (v === undefined) return localStorage.getItem(k);
      if (v === null) localStorage.removeItem(k); else localStorage.setItem(k, v);
    } catch (e) { /* storage blocked: the demo still works for this page */ }
    return null;
  }
  function once(k) {
    try { if (sessionStorage.getItem(k)) return false; sessionStorage.setItem(k, '1'); } catch (e) { /* ignore */ }
    return true;
  }

  const ROLE = { worker: 'Рабочий', operator: 'Оператор', supervisor: 'Начальник смены', director: 'Руководитель' };
  const ROLE_D = {
    worker: 'Мои нарушения за смену и сводка: кто и где нарушал',
    operator: 'Смотрит все камеры: живая лента и передача нарушения начальнику смены',
    supervisor: 'Тревоги со сроком устранения, живая лента, люди, камеры и отчёт',
    director: 'Показатели смены, время реакции, тревоги не закрытые в срок',
  };
  const ROLE_ORDER = ['worker', 'operator', 'supervisor', 'director'];
  const AL_ST = { open: 'тревога открыта', ack: 'тревога принята', resolved: 'тревога устранена' };
  const SEV = { critical: 'Критично', high: 'Высокая', medium: 'Средняя' };
  const EV_ST = { new: 'Новое', confirmed: 'Подтверждено', false: 'Ложное', sent_to_master: 'Передано мастеру', resolved: 'Устранено' };
  const LIVE_ST = { playing: 'идёт анализ', done: 'готово' };
  const QUICK = ['Помощь оказана', 'Очаг потушен', 'Опасность устранена', 'Ложная тревога'];
  const TABS = {
    worker: [['mine', 'Моя смена', 'user'], ['summary', 'Сводка', 'grid']],
    operator: [['feed', 'Лента', 'list'], ['alarms', 'Тревоги', 'bell'], ['people', 'Люди', 'users'], ['report', 'Отчёт', 'doc']],
    supervisor: [['alarms', 'Тревоги', 'bell'], ['feed', 'Лента', 'list'], ['people', 'Люди', 'users'], ['live', 'Камеры', 'cam'], ['report', 'Отчёт', 'doc']],
    director: [['overview', 'Обзор', 'gauge'], ['alarms', 'Тревоги', 'bell'], ['feed', 'Лента', 'list'], ['people', 'Люди', 'users'], ['more', 'Ещё', 'dots']],
  };
  const MORE = [['live', 'Камеры и лайв', 'cam'], ['report', 'Отчёт смены', 'doc'], ['settings', 'Настройки', 'gear']];

  // ------------------------------------------------------------ time and words (Europe/Moscow)
  const fHM = new Intl.DateTimeFormat('ru-RU', { timeZone: 'Europe/Moscow', hour: '2-digit', minute: '2-digit' });
  const hm = (s) => (s ? fHM.format(new Date(s * 1000)) : '—');
  let skew = 0;
  const now = () => (Date.now() + skew) / 1000;
  const pad = (n) => String(n).padStart(2, '0');
  const mmss = (sec) => { sec = Math.max(0, Math.round(sec)); const m = Math.floor(sec / 60); return (m >= 60 ? Math.floor(m / 60) + ':' + pad(m % 60) : pad(m)) + ':' + pad(sec % 60); };
  const dur = (s) => { s = Math.round(s || 0); if (s < 60) return s + ' с'; const m = Math.floor(s / 60); return m < 60 ? m + ' мин' + (s % 60 ? ' ' + (s % 60) + ' с' : '') : Math.floor(m / 60) + ' ч ' + (m % 60) + ' мин'; };
  const plural = (n, f) => { const a = Math.abs(n) % 100, b = a % 10; return f[a > 10 && a < 20 ? 2 : b === 1 ? 0 : b >= 2 && b <= 4 ? 1 : 2]; };
  const NV = ['нарушение', 'нарушения', 'нарушений'];
  const fmtMin = (m) => (m == null ? '—' : m < 1 ? Math.round(m * 60) + ' с' : (m % 1 ? (+m).toFixed(1) : m) + ' мин');
  const initials = (n) => (n || '?').split(/\s+/).filter(Boolean).map((w) => w[0]).slice(0, 2).join('').toUpperCase();
  const shortName = (n) => { const p = (n || '').split(/\s+/).filter(Boolean); return p.length > 1 && /^[А-ЯЁA-Z]/.test(p[1]) ? p[0] + ' ' + p[1][0] + '.' : n || ''; };

  // ------------------------------------------------------------ state
  const S = {
    me: null, role: null, tab: null, sheet: false, rolesOpen: false,
    shift: null, alarms: [], alarmSig: '', seenAlarms: null, events: [], seenEv: null, evIndex: new Map(),
    cams: new Map(), f: { sev: '', cam: '' }, resOpen: false, focus: null, refresh: null, onAlarms: null,
  };
  const stack = [];
  const isBoss = () => S.role === 'supervisor' || S.role === 'director';      // ack / resolve / close shift / live
  const seesAlarms = () => isBoss() || S.role === 'operator';                  // alarms list, read-only for the operator
  const camLabel = (x) => x.camera_label || (x.camera_no ? x.camera_no + ' · ' : '') + (x.camera_title || x.camera || '');
  const camShort = (x) => x.camera_no || x.camera_title || x.camera || '';
  const zoneOf = (x) => (x.zone === 'frame' || x.zone_title === 'Весь кадр' ? '' : x.zone_title || '');

  // ------------------------------------------------------------ api
  function demoId() {
    let id = keep('lg.demo');
    if (!id) { id = 'web-' + Math.random().toString(36).slice(2, 10); keep('lg.demo', id); }
    return id;
  }
  function authHeaders() {
    const h = { 'X-Tg-Init-Data': INIT };
    if (!INIT) { h['X-Demo-User'] = demoId(); const r = keep('lg.role'); if (r) h['X-Demo-Role'] = r; }
    return h;
  }
  async function api(path, opt) {
    opt = opt || {};
    const h = authHeaders();
    let body;
    if (opt.body !== undefined) { h['Content-Type'] = 'application/json'; body = JSON.stringify(opt.body); }
    let res;
    try { res = await fetch(path, { method: opt.method || (body !== undefined ? 'POST' : 'GET'), headers: h, body, cache: 'no-store' }); }
    catch (e) { throw Object.assign(new Error('Нет связи с сервером'), { net: true }); }
    const d = Date.parse(res.headers.get('Date') || '');
    if (d) { const raw = d - Date.now(); skew = Math.abs(raw) > 4000 ? raw : 0; }
    if (!res.ok) {
      let msg = '';
      try { const j = await res.json(); msg = typeof j.detail === 'string' ? j.detail : j.reason || ''; } catch (e) { /* not json */ }
      throw Object.assign(new Error(msg || 'Ошибка сервера · ' + res.status), { status: res.status });
    }
    return (res.headers.get('Content-Type') || '').includes('json') ? res.json() : res.text();
  }
  function fail(e) {
    if (e && e.status === 401) return gate(e);
    hap.err();
    toast(e && e.status === 403 ? e.message || 'Недостаточно прав' : (e && e.message) || 'Что-то пошло не так', e && e.net ? 'warn' : 'err');
  }

  // ------------------------------------------------------------ telegram glue
  const hap = {
    call(fn) { try { if (tg && tg.HapticFeedback && ver('6.1')) fn(tg.HapticFeedback); } catch (e) { /* old client */ } },
    tap() { this.call((h) => h.impactOccurred('light')); },
    heavy() { this.call((h) => h.impactOccurred('medium')); },
    sel() { this.call((h) => h.selectionChanged()); },
    ok() { this.call((h) => h.notificationOccurred('success')); },
    warn() { this.call((h) => h.notificationOccurred('warning')); },
    err() { this.call((h) => h.notificationOccurred('error')); },
  };
  let mainFn = null;
  function mainBtn(text, fn) {
    mainFn = fn || null;
    if (!HAS_MAIN) return;
    if (!fn) { tg.MainButton.hide(); return; }
    tg.MainButton.setParams({ text, color: '#FF8A1F', text_color: '#1A0E04', is_active: true, is_visible: true });
  }
  function syncBack() {
    if (!IN_TG || !tg.BackButton || !ver('6.1')) return;
    if (stack.length || S.sheet || (S.rolesOpen && S.role)) tg.BackButton.show(); else tg.BackButton.hide();
  }
  function applyTheme() {
    const q = new URLSearchParams(location.search).get('theme');
    let dark;
    if (q === 'dark' || q === 'light') dark = q === 'dark';
    else if (IN_TG && tg.colorScheme) dark = tg.colorScheme === 'dark';
    else if (IN_TG && tg.themeParams && tg.themeParams.bg_color) {
      const c = tg.themeParams.bg_color.replace('#', '');
      dark = parseInt(c.slice(0, 2), 16) * 0.3 + parseInt(c.slice(2, 4), 16) * 0.59 + parseInt(c.slice(4, 6), 16) * 0.11 < 128;
    } else dark = !window.matchMedia || matchMedia('(prefers-color-scheme: dark)').matches;
    const root = document.documentElement;
    const was = root.dataset.theme;
    root.dataset.theme = dark ? 'dark' : 'light';
    if (tg && IN_TG) {
      try {
        if (ver('6.1')) { tg.setHeaderColor('#0B1230'); tg.setBackgroundColor(dark ? '#0B1230' : '#ECE3D1'); }
        if (ver('7.10') && tg.setBottomBarColor) tg.setBottomBarColor(dark ? '#070B20' : '#F7F1E5');
      } catch (e) { /* older client */ }
    }
    if (was && was !== root.dataset.theme && S.tab && !$('#app').hidden) render();
  }

  // ------------------------------------------------------------ ui atoms
  function toast(msg, kind) {
    const el = document.createElement('div');
    el.className = 'toast ' + (kind || '');
    el.textContent = msg;
    $('#toasts').appendChild(el);
    setTimeout(() => { el.classList.add('out'); setTimeout(() => el.remove(), 300); }, kind === 'err' ? 4200 : 2800);
  }
  const ic = (n) => '<svg><use href="#i-' + n + '"/></svg>';
  // миниатюра не отдалась (старый сервер, нет кадра) — показываем исходный кадр
  window.__lgFull = (src) => { const m = /[?&]src=([^&]+)/.exec(src || ''); return m ? decodeURIComponent(m[1]) : src; };
  const img = (src, alt) => (src ? '<img src="' + esc(src) + '" alt="' + esc(alt || '') + '" loading="lazy" decoding="async"' +
    (src.indexOf('/api/tg/thumb') === 0 ? ' onerror="this.onerror=null;this.src=__lgFull(this.getAttribute(\'src\'))"' : '') + '>' : '');
  // в списках — уменьшенный кадр с сервера: через туннель 1280-пиксельные снимки идут медленно
  const th = (src) => (src && src.startsWith('/media/') && !src.includes('/media/face-') ? '/api/tg/thumb?w=480&src=' + encodeURIComponent(src) : src);
  const shot = (src, tag, rec) => '<span class="shot">' + img(th(src)) + (rec ? '<span class="rec">' + rec + '</span>' : '') + (tag ? '<span class="tag">' + esc(tag) + '</span>' : '') + '</span>';
  const facePh = (p, extra) => '<span class="ph">' + (p && p.photo ? img(p.photo, p.name) : '<span class="ini">' + esc(initials(p && p.name)) + '</span>') + (extra || '') + '</span>';
  const sev = (s) => '<span class="sev ' + esc(s) + '">' + (SEV[s] || esc(s)) + '</span>';
  const skel = (n, h) => Array.from({ length: n }, () => '<div class="skel" style="height:' + h + 'px"></div>').join('');
  const secH = (n, t, meta) => '<div class="sec-h"><span class="n">' + n + '</span><h2>' + t + '</h2><span class="dim"></span>' + (meta ? '<span class="meta">' + meta + '</span>' : '') + '</div>';
  const empty = (kind, t, sub) => '<div class="empty"><canvas data-still="' + kind + '"></canvas><b>' + t + '</b>' + (sub ? '<span>' + sub + '</span>' : '') + '</div>';
  function inkColors() {
    const cs = getComputedStyle(document.documentElement);
    return { ink: cs.getPropertyValue('--fg').trim(), line: cs.getPropertyValue('--accent').trim(), fill: cs.getPropertyValue('--surface-2').trim() };
  }
  function put(el, html) {
    if (!el) return;
    el.innerHTML = html;
    const col = inkColors();
    $$('canvas[data-still]', el).forEach((c) => window.Art && Art.still(c, c.dataset.still, col));
  }
  document.addEventListener('error', (e) => { const t = e.target; if (t && t.tagName === 'IMG' && !t.dataset.live) t.classList.add('img-x'); }, true);
  document.addEventListener('toggle', (e) => { if (e.target.classList && e.target.classList.contains('resolved')) S.resOpen = e.target.open; }, true);
  function busy(btn, on) { if (btn && btn.tagName === 'BUTTON') btn.disabled = on; }
  function indexEvents(list) { (list || []).forEach((e) => { S.evIndex.set(String(e.id), e); if (e.camera) S.cams.set(e.camera, camShort(e)); }); }

  // ------------------------------------------------------------ sheet + back stack
  function openSheet(html) {
    const sh = $('#sheet'), sc = $('#scrim');
    sh.innerHTML = '<div class="grab"></div><button class="x" type="button" data-act="close" aria-label="Закрыть">' + ic('x') + '</button>' + html;
    sh.hidden = sc.hidden = false;
    sh.scrollTop = 0;
    requestAnimationFrame(() => { sh.classList.add('on'); sc.classList.add('on'); });
    S.sheet = true;
    syncBack();
    const col = inkColors();
    $$('canvas[data-still]', sh).forEach((c) => Art.still(c, c.dataset.still, col));
  }
  function closeSheet() {
    const sh = $('#sheet'), sc = $('#scrim');
    sh.classList.remove('on'); sc.classList.remove('on');
    S.sheet = false;
    syncBack();
    setTimeout(() => { if (!S.sheet) { sh.hidden = sc.hidden = true; sh.innerHTML = ''; } }, 320);
  }
  function back() {
    if (S.sheet) return closeSheet();
    if (S.rolesOpen && S.role) return hideRoles();
    if (stack.length) { stack.pop(); render(); window.scrollTo(0, 0); }
  }

  // ------------------------------------------------------------ polling (paused while the app is hidden)
  let timers = [];
  function poll(fn, ms) { timers.push(setInterval(() => { if (!document.hidden) fn(); }, ms)); }
  function clearPolls() { timers.forEach(clearInterval); timers = []; S.refresh = null; S.onAlarms = null; }
  document.addEventListener('visibilitychange', () => { if (!document.hidden && S.refresh) S.refresh(); });

  // ------------------------------------------------------------ header
  let hdrArt = null;
  function shiftObj() { return (S.shift && S.shift.shift) || (S.me && S.me.shift) || null; }
  function renderHeader() {
    const sh = shiftObj();
    $('#shiftLbl').textContent = sh ? hm(sh.start) + '–' + hm(sh.end) + ' · смена ' + sh.id : 'смена —';
    const rb = $('#roleBtn');
    rb.hidden = !(S.me && S.me.demo);
    $('span', rb).textContent = ROLE[S.role] || 'роль';
    if (sh) {
      const p = clamp((now() - sh.start) / Math.max(1, sh.end - sh.start), 0, 1) * 100;
      $('#ruler i').style.width = p + '%';
      $('#ruler b').style.left = p + '%';
    }
    renderAlarmState();
  }
  function renderAlarmState() {
    const open = seesAlarms() ? S.alarms.filter((a) => a.status !== 'resolved') : [];
    const pill = $('#alarmPill');
    pill.hidden = !open.length;
    pill.textContent = open.length + ' ' + plural(open.length, ['тревога', 'тревоги', 'тревог']);
    if (hdrArt) hdrArt.setAlarm(open.length > 0);
    const b = $('#alBadge');
    if (b) { b.hidden = !open.length; b.textContent = open.length; }
  }
  setInterval(() => { if (S.me && !document.hidden) renderHeader(); }, 30000);

  function renderTabs() {
    const cur = S.role === 'director' && MORE.some((m) => m[0] === S.tab) ? 'more' : S.tab;
    $('#tabs').innerHTML = TABS[S.role].map(([id, label, i]) =>
      '<button class="tab' + (id === cur ? ' on' : '') + '" type="button" data-tab="' + id + '">' + ic(i) + label +
      (id === 'alarms' ? '<span class="badge" id="alBadge" hidden></span>' : '') + '</button>').join('');
    renderAlarmState();
  }
  function go(tab) {
    if (tab !== S.tab) hap.sel();
    stack.length = 0;
    S.tab = tab;
    renderTabs();
    render();
    window.scrollTo(0, 0);
  }
  function render() {
    clearPolls();
    syncBack();
    // a fresh #view per render: late responses from the previous screen write into the detached node, not over this one
    const old = $('#view'), v = old.cloneNode(false);
    old.replaceWith(v);
    const top = stack[stack.length - 1];
    if (top && top.kind === 'person') viewPerson(v, top.id);
    else ({ mine: viewMine, summary: viewSummary, alarms: viewAlarms, feed: viewFeed, people: viewPeople, live: viewLive, report: viewReport, overview: viewOverview, settings: viewSettings }[S.tab] || viewAlarms)(v);
    if (S.tab === 'report' && !top && isBoss()) mainBtn('Закрыть смену', closeShift); else mainBtn(null);
  }

  // ------------------------------------------------------------ data loaders
  async function loadShift() {
    const s = await api('/api/tg/shift');
    S.shift = s;
    (s.by_camera || []).forEach((c) => S.cams.set(c.camera, camShort(c)));
    renderHeader();
    return s;
  }
  async function loadAlarms() {
    const list = await api('/api/tg/alarms');
    S.alarms = Array.isArray(list) ? list : [];
    const open = S.alarms.filter((a) => a.status !== 'resolved');
    if (S.seenAlarms) {
      const fresh = open.filter((a) => !S.seenAlarms.has(a.event_id));
      if (fresh.length) { hap.warn(); toast('Новая тревога: ' + fresh[0].title + (camLabel(fresh[0]) ? ' · ' + camLabel(fresh[0]) : ''), 'err'); }
    }
    S.seenAlarms = new Set(S.alarms.map((a) => a.event_id));
    renderAlarmState();
    const sig = JSON.stringify(S.alarms.map((a) => [a.event_id, a.status, a.escalated_at, a.asked_at, a.ack_at, a.deadline]));
    if (sig !== S.alarmSig) { S.alarmSig = sig; if (S.onAlarms) S.onAlarms(); }
    return S.alarms;
  }
  let watchId = 0;
  function startWatch() {
    clearInterval(watchId);
    S.alarms = []; S.alarmSig = ''; S.seenAlarms = null;
    renderAlarmState();
    if (!seesAlarms()) return;
    loadAlarms().catch(() => {});
    watchId = setInterval(() => { if (!document.hidden) loadAlarms().catch(() => {}); }, 3000);
  }

  // ------------------------------------------------------------ alarms (supervisor, director)
  function timer(a) {
    return '<div class="timer" data-dl="' + a.deadline + '" data-c="' + a.created + '"><svg viewBox="0 0 26 26"><circle class="bg" cx="13" cy="13" r="10"/>' +
      '<circle class="fg" cx="13" cy="13" r="10" stroke-dasharray="62.83" stroke-dashoffset="0"/></svg><span>осталось до срока<br><b>--:--</b></span></div>';
  }
  function tickTimers() {
    $$('[data-since]').forEach((el) => { el.textContent = mmss(now() - +el.dataset.since); });
    $$('[data-dl]').forEach((el) => {
      const dl = +el.dataset.dl, c = +el.dataset.c, left = dl - now(), frac = clamp(left / Math.max(1, dl - c), 0, 1);
      const fg = $('.fg', el);
      if (fg) fg.setAttribute('stroke-dashoffset', (62.83 * (1 - frac)).toFixed(2));
      if (left <= 0) { if (!el.classList.contains('late')) { el.classList.add('late'); $('span', el).innerHTML = 'срок прошёл<br><b>руководитель уведомлён</b>'; } }
      else { const b = $('b', el); if (b) b.textContent = mmss(left); }
    });
  }
  setInterval(tickTimers, 1000);

  function srcLine(a) {
    return a.source === 'operator' ? '<div class="src">' + ic('mega') + '<span>Передал ' + esc(a.source_by || 'оператор') + (a.comment ? ': «' + esc(a.comment) + '»' : '') + '</span></div>' : '';
  }
  const overdue = (a) => !!(S.shift && S.shift.sla && (S.shift.sla.overdue_ids || []).map(String).indexOf(String(a.event_id)) >= 0);
  function alarmCard(a) {
    const ack = a.status === 'ack', escd = !!a.escalated_at, id = esc(a.event_id), act = S.role === 'supervisor';   // принимает и устраняет только начальник смены
    let st = ack ? 'Принял: <b>' + esc(a.ack_by || '—') + '</b> в ' + hm(a.ack_at) : 'Ждёт реакции начальника смены';
    if (escd) st += '<br><b style="color:var(--crit)">Передано руководителю в ' + hm(a.escalated_at) + '</b>';
    if (a.asked_at) st += '<br>Руководитель запросил отчёт в ' + hm(a.asked_at);
    const ask = S.role === 'director' && !a.asked_at ? '<button class="btn signal sm" type="button" data-act="ask" data-id="' + id + '">Запросить отчёт</button>' : '';
    const btns = act ? '<div class="btn-row">' + (ack ? '' : '<button class="btn ghost" type="button" data-act="ack" data-id="' + id + '">Принял</button>') +
      '<button class="btn" type="button" data-act="resolve" data-id="' + id + '">Устранено</button></div>' : '';
    const fl = S.flash && S.flash.id === String(a.event_id) && Date.now() < S.flash.until;
    return '<article class="card crop alarm' + (ack ? ' ack' : '') + (escd ? ' esc' : '') + (fl ? ' flash' : '') + '" id="al-' + id + '">' +
      '<button class="shot" type="button" data-act="snap" data-id="' + id + '">' + img(th(a.snapshot)) + '<span class="rec">' + hm(a.created) + '</span><span class="tag">' + esc(camShort(a)) + '</span></button>' +
      '<div class="alarm-b"><div class="row-top">' + sev(a.severity || 'critical') + (overdue(a) ? '<span class="ast late">срок прошёл</span>' : '') + '<span class="mono since" data-since="' + a.created + '"></span></div>' +
      '<h3>' + esc(a.title) + '</h3><div class="where">' + esc([camLabel(a), zoneOf(a)].filter(Boolean).join(' · ')) + '</div>' +
      (a.person ? '<div class="who">Сотрудник: <b>' + esc(a.person.name) + '</b></div>' : '') + srcLine(a) +
      (escd ? '' : timer(a)) + '<div class="status-line">' + st + '</div>' + btns + ask + '</div></article>';
  }
  function resRow(a) {
    return '<button class="res-row" type="button" data-act="snap" data-id="' + esc(a.event_id) + '" style="width:100%;text-align:left">' + shot(a.snapshot) +
      '<span><b>' + esc(a.title) + '</b><small>' + esc([camLabel(a), a.resolved_by].filter(Boolean).join(' · ')) + (a.note ? ' · «' + esc(a.note) + '»' : '') + (a.source === 'operator' ? ' · передал ' + esc(a.source_by || 'оператор') : '') + '</small></span>' +
      '<span class="mono">' + hm(a.created) + '<br>→ ' + hm(a.resolved_at) + '</span></button>';
  }
  function viewAlarms(v) {
    delete v.dataset.drawn;          // контейнер общий для вкладок: пометка от прошлого экрана не должна мешать отрисовке
    put(v, '<section class="sec">' + secH('01', 'Тревоги', '') + skel(2, 176) + '</section>');
    const draw = () => {
      const w = { esc: 0, open: 1, ack: 2 };
      const open = S.alarms.filter((a) => a.status !== 'resolved').sort((a, b) => (a.escalated_at ? w.esc : w[a.status] || 1) - (b.escalated_at ? w.esc : w[b.status] || 1) || b.created - a.created);
      const done = S.alarms.filter((a) => a.status === 'resolved');
      let h = '<section class="sec">' + secH('01', 'Тревоги', 'срок устранения ' + fmtMin(S.me.escalate_min));
      if (S.role === 'director') h += '<p class="note" style="margin:-4px 0 14px">Принимает и устраняет тревоги начальник смены. Если нужен статус — запросите отчёт.</p>';
      else if (S.role !== 'supervisor') h += '<p class="note" style="margin:-4px 0 14px">Только просмотр: принимает и закрывает тревоги начальник смены.</p>';
      // одна тревога со странными полями не должна ломать весь список
      const card = (a) => { try { return alarmCard(a); } catch (e) { console.error('alarm card', a, e); return ''; } };
      h += open.length ? open.map(card).join('') : empty('ok', 'Открытых тревог нет', 'Критичные нарушения появятся здесь сразу — со сроком устранения.');
      if (done.length) h += '<details class="resolved"' + (S.resOpen ? ' open' : '') + '><summary>Устранено за смену · ' + done.length + '</summary>' + done.map(resRow).join('') + '</details>';
      put(v, h + '</section>');
      v.dataset.drawn = '1';
      tickTimers();
      focusAlarm();
    };
    S.onAlarms = draw;
    S.refresh = () => loadAlarms().catch(fail);
    loadAlarms().then(() => { if (!v.dataset.drawn) draw(); }).catch(fail);
  }
  function findAlarm(id) { return S.alarms.find((a) => String(a.event_id) === String(id)); }
  function mergeAlarm(a) {
    if (!a || !a.event_id) return;
    const i = S.alarms.findIndex((x) => x.event_id === a.event_id);
    if (i >= 0) S.alarms[i] = Object.assign({}, S.alarms[i], a); else S.alarms.unshift(a);
    S.alarmSig = '';
    renderAlarmState();
    if (S.onAlarms) S.onAlarms();
  }
  async function doAck(id, btn) {
    busy(btn, true);
    try { mergeAlarm(await api('/api/tg/alarms/' + encodeURIComponent(id) + '/ack', { method: 'POST' })); hap.ok(); toast('Принято. Когда закончите — «Устранено»', 'ok'); }
    catch (e) { busy(btn, false); fail(e); }
  }
  function openResolve(id) {
    const a = findAlarm(id) || {};
    openSheet('<div class="row-top">' + sev(a.severity || 'critical') + '<span class="mono">' + hm(a.created) + '</span></div><h3>Устранено</h3>' +
      '<p class="note">' + esc(a.title || '') + (camLabel(a) ? ' · ' + esc(camLabel(a)) : '') + '</p>' +
      '<div class="notes">' + QUICK.map((q) => '<button class="chip" type="button" data-act="note">' + q + '</button>').join('') + '</div>' +
      '<textarea id="resNote" maxlength="400" placeholder="Что сделано — коротко, своими словами"></textarea>' +
      '<div class="btn-col" style="margin-top:12px"><button class="btn wide" type="button" data-act="do-resolve" data-id="' + esc(id) + '">Закрыть тревогу</button></div>');
  }
  async function doResolve(id, btn) {
    const chips = $$('#sheet .notes .chip.on').map((c) => c.textContent);
    const free = ($('#resNote') || {}).value || '';
    const note = chips.concat(free.trim() ? [free.trim()] : []).join('; ');
    if (!note) { hap.warn(); toast('Отметьте, что сделано, или напишите пару слов', 'warn'); return; }
    busy(btn, true);
    try { mergeAlarm(await api('/api/tg/alarms/' + encodeURIComponent(id) + '/resolve', { body: { note } })); hap.ok(); closeSheet(); toast('Тревога закрыта: ' + note, 'ok'); }
    catch (e) { busy(btn, false); fail(e); }
  }
  async function doAsk(id, btn) {
    busy(btn, true);
    try { mergeAlarm(await api('/api/tg/alarms/' + encodeURIComponent(id) + '/ask', { method: 'POST' })); hap.ok(); toast('Запрос отчёта отправлен начальнику смены', 'ok'); }
    catch (e) { busy(btn, false); fail(e); }
  }
  function openAlarmSheet(a) {
    openSheet('<div class="row-top">' + sev(a.severity || 'critical') + '<span class="mono">' + hm(a.created) + '</span></div><h3>' + esc(a.title) + '</h3>' +
      '<div class="big shot">' + img(a.snapshot) + '</div><dl class="dl">' +
      '<dt>Камера</dt><dd>' + esc(camLabel(a) || '—') + '</dd><dt>Зона</dt><dd>' + esc(zoneOf(a) || '—') + '</dd>' +
      (a.person ? '<dt>Сотрудник</dt><dd>' + esc(a.person.name) + '</dd>' : '') +
      '<dt>Принял</dt><dd>' + (a.ack_at ? esc(a.ack_by || '—') + ' · ' + hm(a.ack_at) + ' <span class="note">(' + dur(a.ack_at - a.created) + ')</span>' : '—') + '</dd>' +
      '<dt>Устранил</dt><dd>' + (a.resolved_at ? esc(a.resolved_by || '—') + ' · ' + hm(a.resolved_at) + ' <span class="note">(' + dur(a.resolved_at - a.created) + ')</span>' : '—') + '</dd>' +
      (a.note ? '<dt>Что сделано</dt><dd>' + esc(a.note) + '</dd>' : '') +
      (a.source === 'operator' ? '<dt>Передал</dt><dd>' + esc(a.source_by || 'оператор') + (a.comment ? ' · «' + esc(a.comment) + '»' : '') + '</dd>' : '') +
      (a.escalated_at ? '<dt>Передано руководителю</dt><dd style="color:var(--crit)">' + hm(a.escalated_at) + '</dd>' : '') + '</dl>');
  }
  function focusAlarm() {
    if (!S.focus || !S.alarmSig) return;
    const id = S.focus;
    S.focus = null;
    const el = document.getElementById('al-' + id);
    if (el) { S.flash = { id: String(id), until: Date.now() + 3200 }; el.scrollIntoView({ block: 'center', behavior: 'smooth' }); el.classList.add('flash'); hap.warn(); return; }
    const a = findAlarm(id);
    if (a) openAlarmSheet(a); else toast('Тревога #' + id + ' не найдена в текущей смене', 'warn');
  }

  // ------------------------------------------------------------ feed
  const canPass = (e) => S.role === 'operator' && !e.alarm_status;
  const astChip = (e) => (e.alarm_status ? '<span class="ast ' + esc(e.alarm_status) + '">' + (AL_ST[e.alarm_status] || 'тревога') + '</span>' : '');
  function evRow(e, fresh, noPerson) {
    return '<article class="ev card ' + esc(e.severity) + (fresh ? ' fresh' : '') + '" role="button" tabindex="0" data-act="event" data-id="' + esc(e.id) + '">' +
      '<span class="t">' + hm(e.started_at) + '</span>' + shot(e.snapshot) +
      '<span class="b"><span class="h">' + esc(e.title) + '</span><span class="m">' + sev(e.severity) + '<span>' + esc(camLabel(e)) + '</span>' +
      (e.person && !noPerson ? '<span>· ' + esc(shortName(e.person.name)) + '</span>' : '') + (e.closed ? '' : '<span class="live-dot">идёт</span>') + astChip(e) + '</span>' +
      (canPass(e) ? '<button class="btn signal sm pass" type="button" data-act="pass" data-id="' + esc(e.id) + '">' + ic('mega') + 'Передать начальнику</button>' : '') +
      '</span></article>';
  }
  function drawFilters() {
    const el = $('#flt');
    if (!el) return;
    const row = $$('.chips', el)[1], sl = row ? row.scrollLeft : 0;
    const sv = [['', 'Все'], ['critical', 'Критично'], ['high', 'Высокая'], ['medium', 'Средняя']];
    const cams = [['', 'Все камеры']].concat(Array.from(S.cams.entries()));
    el.innerHTML = '<div class="chips">' + sv.map(([v, t]) => '<button class="chip' + (v ? ' sev-' + v : '') + (S.f.sev === v ? ' on' : '') + '" type="button" data-act="fsev" data-v="' + v + '">' + t + '</button>').join('') + '</div>' +
      '<div class="chips">' + cams.map(([v, t]) => '<button class="chip' + (S.f.cam === v ? ' on' : '') + '" type="button" data-act="fcam" data-v="' + esc(v) + '">' + esc(t) + '</button>').join('') + '</div>';
    const r2 = $$('.chips', el)[1];
    if (r2) r2.scrollLeft = sl;
  }
  function viewFeed(v) {
    S.seenEv = null;
    put(v, '<div class="filters" id="flt"></div><section class="sec">' + secH('02', 'Лента смены', '<span class="live-dot" id="feedMeta">live</span>') + '<div id="feed">' + skel(5, 68) + '</div></section>');
    drawFilters();
    let last = '', camsN = S.cams.size;
    const refresh = () => {
      const q = new URLSearchParams({ limit: '150' });
      if (S.f.sev) q.set('severity', S.f.sev);
      if (S.f.cam) q.set('camera', S.f.cam);
      const key = q.toString();
      return api('/api/tg/events?' + key).then((list) => {
        list = Array.isArray(list) ? list : [];
        indexEvents(list);
        if (S.cams.size !== camsN) { camsN = S.cams.size; drawFilters(); }
        const sig = key + list.map((e) => e.id + e.status + e.closed + e.alarm_status).join('|');
        if (sig === last || !$('#feed')) return;
        last = sig;
        const prev = S.seenEv;
        S.seenEv = new Set(list.map((e) => e.id));
        $('#feedMeta').textContent = list.length + ' ' + plural(list.length, NV);
        put($('#feed'), list.length ? '<div class="tl">' + list.map((e) => evRow(e, prev && !prev.has(e.id))).join('') + '</div>'
          : empty('ok', 'Нарушений нет', S.f.sev || S.f.cam ? 'С этими фильтрами пусто — попробуйте снять их.' : 'Лента обновляется сама каждые 3 секунды.'));
      }).catch(fail);
    };
    S.refresh = refresh;
    if (!S.shift) loadShift().then(() => { if (S.cams.size !== camsN) { camsN = S.cams.size; drawFilters(); } }).catch(() => {});
    refresh();
    poll(refresh, 3000);
  }
  function openEvent(id) {
    const e = S.evIndex.get(String(id));
    if (!e) return;
    hap.tap();
    const media = e.clip
      ? '<div class="big shot"><video src="' + esc(e.clip) + '" poster="' + esc(e.snapshot || '') + '" controls playsinline preload="metadata"></video></div>'
      : '<div class="big shot">' + img(e.snapshot) + '</div>';
    const who = e.person
      ? (seesAlarms() ? '<button class="link" type="button" data-act="person" data-id="' + esc(e.person.id) + '">' + esc(e.person.name) + ' →</button>' : esc(e.person.name))
      : '<span class="note">не опознан</span>';
    openSheet('<div class="row-top">' + sev(e.severity) + '<span class="mono">' + hm(e.started_at) + '</span></div><h3>' + esc(e.title) + '</h3>' + media +
      '<dl class="dl"><dt>Камера</dt><dd>' + esc(camLabel(e) || '—') + '</dd><dt>Зона</dt><dd>' + esc(zoneOf(e) || '—') + '</dd>' +
      '<dt>Начало</dt><dd>' + hm(e.started_at) + '</dd><dt>Длительность</dt><dd>' + dur(e.duration_s) + (e.closed ? '' : ' · продолжается') + '</dd>' +
      '<dt>Сотрудник</dt><dd>' + who + '</dd><dt>Статус</dt><dd>' + esc(EV_ST[e.status] || e.status || '—') + '</dd></dl>' +
      (e.alarm_status ? '<p class="note">' + astChip(e) + '</p>' : '') +
      (e.clip ? '<p class="note">Видео нарушения — запись с камеры вокруг момента срабатывания.</p>' : '') +
      (canPass(e) ? '<button class="btn signal wide" type="button" data-act="pass" data-id="' + esc(e.id) + '" style="margin-top:6px">' + ic('mega') + 'Передать начальнику смены</button>' : ''));
  }
  // operator: hand a violation to the shift supervisor as an alarm
  function openPass(id) {
    const e = S.evIndex.get(String(id));
    if (!e) return;
    hap.tap();
    openSheet('<div class="row-top">' + sev(e.severity) + '<span class="mono">' + hm(e.started_at) + '</span></div><h3>Передать начальнику смены</h3>' +
      '<p class="note">' + esc(e.title) + ' · ' + esc(camLabel(e)) + '</p><div class="big shot">' + img(e.snapshot) + '</div>' +
      '<textarea id="passNote" maxlength="300" placeholder="Комментарий для начальника смены (необязательно): что видно, куда бежать"></textarea>' +
      '<div class="btn-col" style="margin-top:12px"><button class="btn signal wide" type="button" data-act="do-pass" data-id="' + esc(id) + '">' + ic('mega') + 'Передать — создать тревогу</button></div>');
  }
  async function doPass(id, btn) {
    const comment = (($('#passNote') || {}).value || '').trim();
    busy(btn, true);
    try {
      const a = await api('/api/tg/events/' + encodeURIComponent(id) + '/escalate', { body: comment ? { comment } : {} });
      const e = S.evIndex.get(String(id));
      if (e) e.alarm_status = (a && a.status) || 'open';
      if (S.seenAlarms && a && a.event_id) S.seenAlarms.add(a.event_id);
      hap.ok();
      openSheet('<div class="kicker" style="color:var(--fg-3)">тревога создана</div><h3>Передано</h3>' +
        '<p class="note">Начальник смены получил тревогу в мини-приложении и в чате. Срок устранения пошёл.</p>' +
        (a && a.event_id ? '<div class="card crop" style="padding:12px;margin-top:12px"><div class="row-top">' + sev(a.severity || (e && e.severity) || 'critical') +
          '<span class="ast ' + esc(a.status || 'open') + '">' + (AL_ST[a.status] || AL_ST.open) + '</span></div><h4 style="margin:8px 0 4px;font:700 18px/1.1 var(--f-disp);text-transform:uppercase">' + esc(a.title || (e && e.title) || '') + '</h4>' +
          '<div class="note">' + esc(camLabel(a.camera_label || a.camera_no || a.camera_title ? a : e || {})) + ' · ' + hm(a.created) + '</div>' + srcLine(a) +
          (a.deadline ? '<div class="note" style="margin-top:6px">Необходимо устранить до ' + hm(a.deadline) + '</div>' : '') + '</div>' : '') +
        '<div class="btn-col" style="margin-top:14px"><button class="btn ghost wide" type="button" data-act="close">Готово</button></div>');
      if (S.refresh) S.refresh();
      loadAlarms().catch(() => {});
    } catch (err) { busy(btn, false); fail(err); }
  }

  // ------------------------------------------------------------ people
  function faceCard(p, act) {
    const extra = (p.events ? '<span class="cnt' + (p.critical ? ' c' : '') + '">' + p.events + '</span>' : '') + (p.tg_bound ? '<span class="tgb" title="в Telegram">' + ic('send') + '</span>' : '');
    return '<button class="face-card' + (p.events || act === 'pick' ? '' : ' zero') + '" type="button" data-act="' + act + '" data-id="' + esc(p.id) + '">' + facePh(p, extra) +
      '<b>' + esc(p.name) + '</b><small>' + (act === 'pick' ? (p.tg_bound ? 'уже привязан' : 'это я?')
        : p.events ? (p.critical ? '<span class="c">' + p.critical + ' крит.</span> · ' : 'последнее ') + hm(p.last_at) : 'без нарушений') + '</small></button>';
  }
  function viewPeople(v) {
    put(v, '<section class="sec">' + secH('03', 'Люди смены', '') + '<div class="faces">' + skel(6, 140) + '</div></section>');
    let last = '';
    const refresh = () => api('/api/tg/people').then((list) => {
      list = Array.isArray(list) ? list : [];
      const sig = JSON.stringify(list.map((p) => [p.id, p.events, p.critical]));
      if (sig === last) return;
      last = sig;
      const bad = list.filter((p) => p.events).length;
      put(v, '<section class="sec">' + secH('03', 'Люди смены', bad + ' из ' + list.length + ' с нарушениями') +
        (list.length ? '<div class="faces">' + list.map((p) => faceCard(p, 'person')).join('') + '</div>' : empty('people', 'Лиц пока нет', 'Система добавит сотрудников, как только увидит их на камерах.')) + '</section>');
    }).catch(fail);
    S.refresh = refresh;
    refresh();
    poll(refresh, 8000);
  }
  function badge(p, evs, kicker) {
    const n = evs.length, crit = evs.filter((e) => e.severity === 'critical').length, sh = shiftObj();
    return '<div class="paper badge-card"><span class="tape"></span>' + facePh(p) + '<div><div class="k">' + kicker + '</div><h3>' + esc(p.name) + '</h3>' +
      '<div class="kv">Смена: <b>' + (sh ? hm(sh.start) + '–' + hm(sh.end) : '—') + '</b><br>Нарушений: <b>' + n + '</b>' + (crit ? ' · крит.: <b>' + crit + '</b>' : '') +
      '<br>Последнее: <b>' + (n ? hm(Math.max.apply(null, evs.map((e) => e.started_at))) : '—') + '</b></div>' +
      '<span class="stamp' + (n ? '' : ' ok') + '">' + (n ? 'нарушения: ' + n : 'без нарушений') + '</span></div></div>';
  }
  function pushPerson(id) { stack.push({ kind: 'person', id }); hap.tap(); render(); window.scrollTo(0, 0); }
  function viewPerson(v, pid) {
    const backBtn = '<button class="back" type="button" data-act="back">' + ic('back') + 'Назад</button>';
    put(v, backBtn + skel(1, 160) + skel(3, 68));
    let last = '';
    const refresh = () => api('/api/tg/people/' + encodeURIComponent(pid)).then((d) => {
      const evs = (d && d.events) || [], p = (d && d.person) || { id: pid, name: '—' };
      indexEvents(evs);
      const sig = JSON.stringify([p.name, evs.map((e) => e.id + e.status + e.alarm_status)]);
      if (sig === last) return;
      last = sig;
      put(v, backBtn + badge(p, evs, 'Карточка сотрудника') +
        '<div class="btn-row" style="margin:16px 0 22px"><button class="btn ghost sm" type="button" data-act="csv-send" data-scope="person" data-person="' + esc(pid) + '">' + ic('send') + 'В чат</button>' +
        '<button class="btn ghost sm" type="button" data-act="csv-dl" data-scope="person" data-person="' + esc(pid) + '">' + ic('down') + 'CSV</button></div>' +
        '<section class="sec">' + secH('02', 'Нарушения за смену', evs.length + '') +
        (evs.length ? '<div class="tl">' + evs.map((e) => evRow(e, false, true)).join('') + '</div>' : empty('ok', 'Чисто', 'За эту смену нарушений не зафиксировано.')) + '</section>');
    }).catch(fail);
    S.refresh = refresh;
    refresh();
    poll(refresh, 6000);
  }

  // ------------------------------------------------------------ live cameras
  function viewLive(v) {
    put(v, '<section class="sec">' + secH('04', 'Камеры · лайв', '') +
      '<label class="drop card" id="drop"><input type="file" accept="video/*" id="file"><canvas data-still="upload"></canvas>' +
      '<span><b>Загрузить видео</b><span>Запись пойдёт в анализ как живая камера: нарушения и тревоги — как с настоящих камер.</span></span></label>' +
      '<div id="upl"></div><div id="lives">' + skel(1, 220) + '</div></section>');
    $('#file').addEventListener('change', (e) => { const f = e.target.files && e.target.files[0]; if (f) upload(f); e.target.value = ''; });
    let ids = '';
    const refresh = () => api('/api/tg/live').then((list) => {
      list = Array.isArray(list) ? list : [];
      const box = $('#lives');
      if (!box) return;
      const now2 = list.map((c) => c.camera_id).join('|');
      if (now2 !== ids) {
        ids = now2;
        put(box, list.length ? list.map((c) => '<article class="card crop live-card" data-cam="' + esc(c.camera_id) + '"><div class="shot"><img data-live="' + esc(c.camera_id) + '" alt="">' +
          '<span class="rec">LIVE</span><span class="tag">' + esc(c.camera_id) + '</span></div><div class="meta"><h4>' + esc(c.title) + '</h4>' +
          '<div class="row-top"><span class="badge-st"></span><span class="mono pct"></span></div><div class="prog"><i></i></div></div></article>').join('')
          : empty('cam', 'Лайв-камер пока нет', 'Загрузите запись с камеры цеха — здесь появится её живой кадр с разметкой.'));
        pumpFrames();
      }
      list.forEach((c) => {
        const card = box.querySelector('[data-cam="' + CSS.escape(c.camera_id) + '"]');
        if (!card) return;
        const p = clamp(c.progress > 1 ? c.progress / 100 : c.progress || 0, 0, 1);
        const st = $('.badge-st', card);
        st.className = 'badge-st ' + esc(c.status);
        st.textContent = LIVE_ST[c.status] || c.status || '';
        $('.pct', card).textContent = Math.round(p * 100) + '%';
        $('.prog i', card).style.width = p * 100 + '%';
      });
    }).catch(fail);
    S.refresh = refresh;
    refresh();
    poll(refresh, 2500);
    poll(pumpFrames, 500);
  }
  function pumpFrames() {
    $$('img[data-live]').forEach((im) => {
      if (im._busy) return;
      im._busy = true;
      const n = new Image();
      n.onload = () => { im.src = n.src; im._busy = false; };
      n.onerror = () => { im._busy = false; };
      n.src = '/api/cameras/' + encodeURIComponent(im.dataset.live) + '/frame.jpg?t=' + Date.now();
    });
  }
  function upload(file) {
    const box = $('#upl'), drop = $('#drop');
    put(box, '<div class="card upl"><div class="row-top"><span>' + esc(file.name) + '</span><span id="uplPct">0%</span></div><div class="prog"><i id="uplBar"></i></div>' +
      '<div class="note" id="uplNote" style="margin-top:6px">' + (file.size / 1048576).toFixed(1) + ' МБ · загрузка…</div></div>');
    drop.classList.add('busy');
    hap.tap();
    const done = () => { const d = $('#drop'); if (d) d.classList.remove('busy'); const u = $('#upl'); if (u) u.innerHTML = ''; };
    const xhr = new XMLHttpRequest();
    xhr.open('POST', '/api/tg/live/upload?name=' + encodeURIComponent(file.name));
    Object.entries(authHeaders()).forEach(([k, v2]) => xhr.setRequestHeader(k, v2));
    xhr.setRequestHeader('Content-Type', 'application/octet-stream');
    xhr.upload.onprogress = (e) => {
      if (!e.lengthComputable) return;
      const p = e.loaded / e.total, bar = $('#uplBar'), pct = $('#uplPct'), note = $('#uplNote');
      if (bar) bar.style.width = p * 100 + '%';
      if (pct) pct.textContent = Math.round(p * 100) + '%';
      if (note && p >= 1) note.textContent = 'проверяем видео и запускаем анализ…';
    };
    xhr.onload = () => {
      let d = {};
      try { d = JSON.parse(xhr.responseText); } catch (e) { /* not json */ }
      done();
      if (xhr.status >= 200 && xhr.status < 300) { hap.ok(); toast('Запущено: ' + (d.title || file.name), 'ok'); if (S.refresh) S.refresh(); }
      else { hap.err(); toast((typeof d.detail === 'string' && d.detail) || 'Не удалось загрузить · ' + xhr.status, 'err'); }
    };
    xhr.onerror = () => { done(); hap.err(); toast('Обрыв связи при загрузке', 'err'); };
    xhr.send(file);
  }

  // ------------------------------------------------------------ report, overview, settings
  function bars(items, label) {
    const max = Math.max.apply(null, [1].concat(items.map((i) => i.count)));
    return items.length ? '<div class="cams-list">' + items.map((i, k) => '<div class="bar' + (i.count === max && max > 1 ? ' hot' : '') + '" style="--w:' + ((i.count / max) * 100).toFixed(1) + '%"><span>' + esc(typeof label === 'function' ? label(i) : i[label] || i.type || i.camera) + '</span><b>' + i.count + '</b><i></i></div>').join('') + '</div>'
      : '<p class="note">За смену пусто</p>';
  }
  // reaction-time numbers: from shift.sla when the backend sends it, else derived from the alarms list
  const tdur = (x) => (x == null ? '—' : x < 60 ? Math.round(x) + '<small>с</small>' : mmss(x) + '<small>мин</small>');
  const tcell = (x) => (x == null ? '—' : x < 60 ? Math.round(x) + ' с' : mmss(x));
  function slaOf() {
    const sla = S.shift && S.shift.sla;
    if (sla) return sla;
    const al = S.alarms, lim = (S.me.escalate_min || 30) * 60, t = now();
    const med = (xs) => { xs = xs.filter((x) => x != null && x >= 0).sort((p, q) => p - q); const n = xs.length; return n ? (n % 2 ? xs[(n - 1) / 2] : (xs[n / 2 - 1] + xs[n / 2]) / 2) : null; };
    const onTime = al.filter((x) => x.resolved_at && x.resolved_at - x.created <= lim).length;
    const late = al.filter((x) => (x.resolved_at ? x.resolved_at - x.created > lim : t - x.created > lim));
    return { alarms: al.length, acked: al.filter((x) => x.ack_at).length, resolved: al.filter((x) => x.resolved_at).length,
      median_ack_s: med(al.map((x) => (x.ack_at ? x.ack_at - x.created : null))), median_resolve_s: med(al.map((x) => (x.resolved_at ? x.resolved_at - x.created : null))),
      on_time: onTime, on_time_share: onTime + late.length ? onTime / (onTime + late.length) : null, escalated: al.filter((x) => x.escalated_at).length,
      overdue: late.filter((x) => !x.resolved_at).length, limit_min: S.me.escalate_min, by_who: [] };
  }
  function slaBlock(sla, paper) {
    const sh = sla.on_time_share, pct = sh == null ? '—' : Math.round(sh * 100) + '<small>%</small>';
    const who = (sla.by_who || []).filter((w) => w && w.who);
    return '<div class="sla' + (paper ? ' on-paper' : ' card crop') + '"><div class="sla-k">' +
      '<div><div class="v">' + tdur(sla.median_ack_s) + '</div><div class="l">медиана до «Принял»</div></div>' +
      '<div><div class="v">' + tdur(sla.median_resolve_s) + '</div><div class="l">медиана до «Устранено»</div></div>' +
      '<div class="' + (sh != null && sh < 0.8 ? 'hot' : 'good') + '"><div class="v">' + pct + '</div><div class="l">в норматив ' + fmtMin(sla.limit_min) + '</div></div></div>' +
      '<div class="norm"><i style="width:' + (sh == null ? 0 : Math.round(sh * 100)) + '%"></i><b title="цель 80%"></b></div>' +
      '<div class="sla-meta">тревог <b>' + (sla.alarms || 0) + '</b> · принято <b>' + (sla.acked || 0) + '</b> · устранено <b>' + (sla.resolved || 0) + '</b> · передано руководителю <b' + (sla.escalated ? ' class="w"' : '') + '>' + (sla.escalated || 0) +
      '</b> · не закрыто в срок <b' + (sla.overdue ? ' class="c"' : '') + '>' + (sla.overdue || 0) + '</b></div>' +
      (who.length ? '<table class="who"><thead><tr><th>Кто</th><th>«Принял»</th><th>«Устр.»</th><th>норм.</th><th>проср.</th></tr></thead><tbody>' +
        who.map((w) => '<tr><td><b>' + esc(shortName(w.who)) + '</b><small>' + (w.alarms || 0) + ' трев. · устр. ' + (w.resolved || 0) + '</small></td><td>' + tcell(w.median_ack_s) + '</td><td>' + tcell(w.median_resolve_s) +
          '</td><td' + (w.on_time_share != null && w.on_time_share < 0.8 ? ' class="c"' : '') + '>' + (w.on_time_share == null ? '—' : Math.round(w.on_time_share * 100) + '%') + '</td><td' + (w.overdue ? ' class="c"' : '') + '>' + (w.overdue || 0) + '</td></tr>').join('') +
        '</tbody></table>' : '') + '</div>';
  }
  const exportRow = (scope, title, sub) => '<div class="exp-row"><span><b>' + title + '</b><small>' + sub + '</small></span>' +
    '<button class="btn ghost sm" type="button" data-act="csv-dl" data-scope="' + scope + '">' + ic('down') + 'CSV</button>' +
    '<button class="btn ghost sm" type="button" data-act="csv-send" data-scope="' + scope + '">' + ic('send') + 'в чат</button></div>';
  function viewReport(v) {
    put(v, '<section class="sec">' + secH('05', 'Отчёт смены', '') + skel(1, 380) + '</section>');
    let last = '';
    const draw = () => {
      const s = S.shift;
      if (!s) return;
      const sla = slaOf(), sig = JSON.stringify([s, sla]);
      if (sig === last) return;
      last = sig;
      const t = s.totals || {}, sh = s.shift || {};
      put(v, '<section class="sec">' + secH('05', 'Отчёт смены', '') +
        '<div class="paper form crop"><div class="k">Акт смены · форма ЛГ-1</div><h3>Смена ' + esc(sh.id || '') + '</h3>' +
        '<table class="tblock"><tr><td class="l">Начало</td><td>' + hm(sh.start) + '</td><td class="l">Конец</td><td>' + hm(sh.end) + '</td></tr>' +
        '<tr><td class="l">Составил</td><td colspan="3">' + esc(S.me.name || '—') + ' · ' + esc(ROLE[S.role] || '') + '</td></tr></table>' +
        '<div class="kpis k4"><div class="kpi"><div class="v">' + (t.violations || 0) + '</div><div class="l">нарушений</div></div>' +
        '<div class="kpi' + (t.critical ? ' hot' : '') + '"><div class="v">' + (t.critical || 0) + '</div><div class="l">критичных</div></div>' +
        '<div class="kpi' + (t.open_alarms ? ' hot' : '') + '"><div class="v">' + (t.open_alarms || 0) + '</div><div class="l">тревог открыто</div></div>' +
        '<div class="kpi"><div class="v">' + (t.people || 0) + '</div><div class="l">людей</div></div></div>' +
        '<h5>Реакция на тревоги</h5>' + slaBlock(sla, true) +
        '<h5>Что нарушали</h5>' + bars(s.by_type || [], 'title') + '<h5>Где</h5>' + bars(s.by_camera || [], camLabel) +
        '<div class="sign"><span>подпись ответственного</span><span class="hand">' + esc(shortName(S.me.name)) + '</span></div></div>' +
        '<div class="exp card crop">' + exportRow('shift', 'Журнал смены', 'все нарушения за смену') + exportRow('alarms', 'CSV по тревогам', 'кто и когда принял, устранил') + '</div>' +
        (isBoss() ? (HAS_MAIN ? '' : '<button class="btn signal wide" type="button" data-act="close-shift" style="margin-top:16px">Закрыть смену</button>') +
          '<p class="note">«Закрыть смену» разошлёт отчёты по чатам ролей и начнёт новую смену.</p>' : '') + '</section>');
    };
    S.onAlarms = draw;
    const refresh = () => loadShift().then(draw).catch(fail);
    S.refresh = refresh;
    refresh();
    poll(refresh, 10000);
  }
  function closeShift() {
    const run = async () => {
      try {
        const r = await api('/api/tg/shift/close', { method: 'POST' });
        hap.ok();
        toast(r && r.sent ? 'Смена закрыта · отправлено отчётов: ' + r.sent : 'Смена закрыта, началась новая', 'ok');
        S.me = await api('/api/tg/me');
        await loadShift();
        render();
      } catch (e) { fail(e); }
    };
    const q = 'Закрыть смену? Отчёты уйдут в чаты, начнётся новая смена.';
    hap.heavy();
    if (IN_TG && ver('6.2')) tg.showConfirm(q, (ok) => { if (ok) run(); });
    else if (window.confirm(q)) run();
  }
  async function csvSend(scope, person, btn) {
    busy(btn, true);
    try {
      const r = await api('/api/tg/csv/send', { body: person ? { scope, person } : { scope } });
      if (r && r.ok) { hap.ok(); toast('Отчёт отправлен в чат с ботом', 'ok'); } else { hap.warn(); toast((r && r.reason) || 'Не удалось отправить', 'warn'); }
    } catch (e) { fail(e); }
    busy(btn, false);
  }
  async function csvDownload(scope, person) {
    const q = new URLSearchParams({ scope });
    if (person) q.set('person', person);
    hap.tap();
    if (IN_TG) {
      // a link opened outside the WebView cannot carry headers: the backend reads tg_init from the query
      q.set('tg_init', INIT);
      tg.openLink(location.origin + '/api/tg/csv?' + q.toString());
      return;
    }
    try {
      const res = await fetch('/api/tg/csv?' + q.toString(), { headers: authHeaders() });
      if (!res.ok) { let m = ''; try { m = (await res.json()).detail; } catch (e) { /* ignore */ } throw Object.assign(new Error(m || 'CSV недоступен · ' + res.status), { status: res.status }); }
      const blob = await res.blob();
      const m = /filename\*?=(?:UTF-8'')?"?([^";]+)/i.exec(res.headers.get('Content-Disposition') || '');
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = m ? decodeURIComponent(m[1]) : 'linq-guard-' + scope + '.csv';
      document.body.appendChild(a);
      a.click();
      setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 1500);
      toast('CSV сохранён', 'ok');
    } catch (e) { fail(e); }
  }

  function viewOverview(v) {
    put(v, '<section class="sec">' + secH('01', 'Обзор смены', '') + '<div class="kpis">' + skel(4, 78) + '</div></section>');
    const draw = () => {
      if (!S.shift) return;
      const t = S.shift.totals || {}, al = S.alarms, sla = slaOf();
      const open = al.filter((a) => a.status !== 'resolved');
      const escd = al.filter((a) => a.escalated_at);
      const kpi = (val, label, cls) => '<div class="card crop kpi' + (cls ? ' ' + cls : '') + '"><div class="v">' + val + '</div><div class="l">' + label + '</div></div>';
      let h = '<section class="sec">' + secH('01', 'Обзор смены', esc((shiftObj() || {}).id || '')) + '<div class="kpis">' +
        kpi(t.violations || 0, 'нарушений за смену') + kpi(t.critical || 0, 'из них критичных', t.critical ? 'hot' : '') +
        kpi(open.length, 'тревог открыто', open.length ? 'hot' : '') + kpi(sla.escalated != null ? sla.escalated : escd.length, 'передано руководителю', escd.length ? 'warn' : '') + '</div></section>';
      h += '<section class="sec">' + secH('02', 'Время реакции', 'норматив ' + fmtMin(sla.limit_min)) + slaBlock(sla, false) + '</section>';
      const hot = escd.filter((a) => a.status !== 'resolved');
      h += '<section class="sec">' + secH('03', 'Не закрыто в срок', hot.length ? hot.length + ' в работе' : '') +
        (hot.length ? hot.map((a) => '<div class="card esc-card"><button type="button" data-act="snap" data-id="' + esc(a.event_id) + '">' + shot(a.snapshot) + '</button><div>' +
          '<h4>' + esc(a.title) + '</h4><div class="note">' + esc(camLabel(a)) + ' · с ' + hm(a.created) + ' · руководителю ' + hm(a.escalated_at) + '</div>' +
          '<div class="note">' + (a.status === 'ack' ? 'Принял ' + esc(a.ack_by || '') + ', но не устранил' : 'Никто не принял') + '</div>' + srcLine(a) +
          (a.asked_at ? '<div class="status-line" style="margin-top:6px">Отчёт запрошен в ' + hm(a.asked_at) + '</div>' : '<button class="btn signal sm" type="button" data-act="ask" data-id="' + esc(a.event_id) + '">Запросить отчёт</button>') +
          '</div></div>').join('') : empty('ok', 'Всё закрыто в срок', 'Критичные тревоги закрыты вовремя.')) + '</section>';
      h += '<section class="sec">' + secH('04', 'Что и где', '') + '<div class="card crop" style="padding:14px"><div class="note" style="margin-bottom:8px">По типам</div>' + bars(S.shift.by_type || [], 'title') +
        '<div class="note" style="margin:14px 0 8px">По камерам</div>' + bars(S.shift.by_camera || [], camLabel) + '</div>' +
        '<div class="btn-row" style="margin-top:16px"><button class="btn ghost sm" type="button" data-act="goto" data-v="alarms">Тревоги</button><button class="btn ghost sm" type="button" data-act="goto" data-v="report">Отчёт</button></div></section>';
      put(v, h);
    };
    S.onAlarms = draw;
    const refresh = () => Promise.all([loadShift(), loadAlarms()]).then(draw).catch(fail);
    S.refresh = refresh;
    refresh();
    poll(() => loadShift().then(draw).catch(() => {}), 8000);
  }

  function viewSettings(v) {
    put(v, '<section class="sec">' + secH('06', 'Настройки', '') + skel(2, 150) + '</section>');
    api('/api/tg/settings').then((s) => {
      const m = s && s.escalate_min != null ? s.escalate_min : S.me.escalate_min;
      const bot = S.me.bot || {};
      put(v, '<section class="sec">' + secH('06', 'Настройки', '') +
        '<div class="card crop set"><h4>Срок устранения тревог</h4><p class="note">Если критичную тревогу не закрыли за это время, руководитель получит уведомление.</p>' +
        '<div class="stepper"><button type="button" data-act="step" data-v="-1" aria-label="меньше">−</button><input id="escMin" type="number" inputmode="decimal" min="0.5" max="1440" step="0.5" value="' + esc(m) + '"><button type="button" data-act="step" data-v="1" aria-label="больше">+</button></div>' +
        '<div class="hint">для показа поставьте 1–2 мин ↑</div><button class="btn wide" type="button" data-act="save-settings" style="margin-top:12px">Сохранить</button></div>' +
        '<div class="card crop set"><h4>Telegram-бот</h4><div class="kv-list">' + (bot.enabled ? 'Подключён: <b>@' + esc(bot.username || '') + '</b><br>Тревоги и отчёты приходят в чат.' : 'Не подключён — уведомления и «CSV в чат» недоступны.') + '</div>' +
        (bot.enabled && bot.username ? '<button class="btn ghost sm" type="button" data-act="open-bot" style="margin-top:10px">Открыть бота</button>' : '') + '</div>' +
        (S.me.demo ? '<div class="card crop set"><h4>Демо-режим</h4><p class="note">Роль: <b>' + ROLE[S.role] + '</b>. В рабочем режиме роли назначает администратор.</p><div class="btn-row">' +
          '<button class="btn ghost sm" type="button" data-act="roles">' + ic('swap') + 'Сменить роль</button>' +
          (isBoss() ? '<button class="btn danger sm" type="button" data-act="demo-reset">Сбросить тревоги (демо)</button>' : '') + '</div></div>' : '') +
        '<p class="note mono" style="opacity:.7">LinQ Guard · детекция на LinQ TPU · tg ' + esc(S.me.tg_id) + '</p></section>');
    }).catch(fail);
  }
  function stepMin(d) {
    const el = $('#escMin');
    if (!el) return;
    let v2 = parseFloat(el.value) || 1;
    const st = v2 + (d > 0 ? 0 : -0.01) < 2 ? 0.5 : v2 + (d > 0 ? 0 : -0.01) < 10 ? 1 : 5;
    v2 = clamp(Math.round((v2 + d * st) / st) * st, 0.5, 1440);
    el.value = v2;
    hap.sel();
  }
  async function saveSettings(btn) {
    const val = parseFloat(($('#escMin') || {}).value);
    if (!(val > 0)) { toast('Укажите минуты', 'warn'); return; }
    busy(btn, true);
    try {
      const s = await api('/api/tg/settings', { body: { escalate_min: val } });
      S.me.escalate_min = (s && s.escalate_min) || val;
      hap.ok();
      toast('Срок устранения: ' + fmtMin(S.me.escalate_min), 'ok');
      loadAlarms().catch(() => {});
    } catch (e) { fail(e); }
    busy(btn, false);
  }

  function demoReset(btn) {
    const run = async () => {
      busy(btn, true);
      try {
        const r = await api('/api/tg/demo/reset', { method: 'POST' });
        hap.ok();
        toast('Демо: сброшено тревог — ' + ((r && r.alarms) || 0), 'ok');
        S.seenAlarms = null;
        await Promise.all([loadAlarms(), loadShift()]);
      } catch (e) { fail(e); }
      busy(btn, false);
    };
    const q = 'Сбросить все тревоги смены? Только для демонстрации.';
    hap.heavy();
    if (IN_TG && ver('6.2')) tg.showConfirm(q, (ok) => { if (ok) run(); });
    else if (window.confirm(q)) run();
  }

  // ------------------------------------------------------------ worker
  function viewMine(v) {
    const p = S.me.person;
    if (!p) return viewFindMe(v);
    put(v, skel(1, 170) + skel(3, 68));
    let last = '';
    const refresh = () => api('/api/tg/events?person=' + encodeURIComponent(p.id) + '&limit=200').then((list) => {
      list = Array.isArray(list) ? list : [];
      indexEvents(list);
      const sig = list.map((e) => e.id + e.status).join('|');
      if (sig === last && last) return;
      last = sig;
      put(v, '<section class="sec">' + secH('01', 'Моя смена', '') + badge(p, list, 'Пропуск · ' + ROLE.worker) + '</section>' +
        '<section class="sec">' + secH('02', 'Мои нарушения', list.length + '') +
        (list.length ? '<div class="tl">' + list.map((e) => evRow(e, false, true)).join('') + '</div>' : empty('ok', 'Нарушений нет', 'Так держать: каска, жилет и внимательность у техники.')) + '</section>' +
        '<button class="btn wide" type="button" data-act="csv-send" data-scope="person" data-person="' + esc(p.id) + '">' + ic('send') + 'Прислать отчёт в чат</button>' +
        '<p class="note" style="text-align:center;margin-top:14px">Не вы на фото? <button class="link" type="button" data-act="unbind">Выбрать заново</button></p>');
    }).catch(fail);
    S.refresh = refresh;
    refresh();
    poll(refresh, 8000);
  }
  function viewFindMe(v) {
    put(v, '<section class="sec">' + secH('01', 'Найдите себя', '') + '<p class="note" style="margin:-2px 0 16px">Система узнаёт сотрудников по лицу на камерах. Выберите своё фото — и увидите свои нарушения за смену.</p><div class="faces">' + skel(6, 140) + '</div></section>');
    api('/api/tg/people').then((list) => {
      list = Array.isArray(list) ? list : [];
      S.peopleById = new Map(list.map((x) => [String(x.id), x]));
      put(v, '<section class="sec">' + secH('01', 'Найдите себя', list.length + ' лиц') + '<p class="note" style="margin:-2px 0 16px">Система узнаёт сотрудников по лицу на камерах. Выберите своё фото — и увидите свои нарушения за смену.</p>' +
        (list.length ? '<div class="faces">' + list.map((x) => faceCard(Object.assign({}, x, { events: 0 }), 'pick')).join('') + '</div>' : empty('people', 'Лиц пока нет', 'Пройдите мимо камеры — система добавит вас в справочник.')) + '</section>');
    }).catch(fail);
  }
  function openPick(id) {
    const p = (S.peopleById && S.peopleById.get(String(id))) || { id, name: '—' };
    hap.tap();
    openSheet('<div class="kicker" style="color:var(--fg-3)">подтверждение</div><h3>' + esc(p.name) + '</h3><div class="face-big">' + facePh(p) + '</div>' +
      '<p class="note" style="text-align:center">Привяжем этот профиль к вашему Telegram. Сменить можно в любой момент.</p>' +
      '<div class="btn-col"><button class="btn wide" type="button" data-act="bind" data-id="' + esc(p.id) + '">Это я</button><button class="btn ghost wide" type="button" data-act="close">Нет, не я</button></div>');
  }
  async function doBind(id, btn) {
    busy(btn, true);
    try {
      S.me = await api('/api/tg/bind', { body: { person_id: id } });
      if (id) { hap.ok(); toast('Готово: ' + ((S.me.person && S.me.person.name) || 'профиль привязан'), 'ok'); } else hap.tap();
      closeSheet();
      render();
    } catch (e) { busy(btn, false); fail(e); }
  }
  function viewSummary(v) {
    put(v, '<section class="sec">' + secH('02', 'Сводка смены', '') + '<div class="kpis">' + skel(2, 78) + '</div>' + skel(2, 140) + '</section>');
    let last = '';
    const refresh = () => Promise.all([loadShift(), api('/api/tg/people')]).then(([s, people]) => {
      const bad = (Array.isArray(people) ? people : []).filter((p) => p.events);
      const sig = JSON.stringify([s.totals, s.by_camera, bad.map((p) => [p.id, p.events])]);
      if (sig === last) return;
      last = sig;
      const t = s.totals || {};
      put(v, '<section class="sec">' + secH('02', 'Сводка смены', esc((s.shift || {}).id || '')) + '<div class="kpis">' +
        '<div class="card crop kpi"><div class="v">' + (t.violations || 0) + '</div><div class="l">нарушений в цехе</div></div>' +
        '<div class="card crop kpi' + (t.critical ? ' hot' : '') + '"><div class="v">' + (t.critical || 0) + '</div><div class="l">критичных</div></div></div></section>' +
        '<section class="sec">' + secH('03', 'Кто нарушал', bad.length ? bad.length + ' чел.' : '') +
        (bad.length ? '<div class="faces">' + bad.map((p) => faceCard(p, 'noop')).join('') + '</div>' : empty('people', 'Никто', 'Смена идёт без нарушений — так и держим.')) + '</section>' +
        '<section class="sec">' + secH('04', 'Где нарушали', '') + '<div class="card crop" style="padding:14px">' + bars(s.by_camera || [], camLabel) + '</div></section>');
    }).catch(fail);
    S.refresh = refresh;
    refresh();
    poll(refresh, 10000);
  }

  // ------------------------------------------------------------ more sheet, role chooser, gate
  function openMore() {
    hap.tap();
    openSheet('<h3>Ещё</h3><div class="menu-list">' + MORE.map(([id, t, i]) => '<button type="button" data-act="more-go" data-v="' + id + '">' + ic(i) + t + (id === S.tab ? '<small>открыто</small>' : '') + '</button>').join('') +
      (S.me.demo ? '<button type="button" data-act="roles">' + ic('swap') + 'Сменить роль<small>' + ROLE[S.role] + '</small></button>' : '') + '</div>');
  }
  function showRoles(first) {
    const el = $('#roles');
    el.innerHTML = '<div class="roles-in"><div class="kicker">LinQ Guard · ' + (S.me && S.me.demo ? 'демо-режим' : 'вход') + ' · лист 01</div>' +
      '<h1>Кто вы<br>на смене? <em>выберите роль</em></h1><p class="lead">Интерфейс подстроится под ваши задачи. Сменить роль можно в любой момент.</p>' +
      ROLE_ORDER.map((r, i) => '<button class="role-card' + (S.role === r ? ' cur' : '') + '" type="button" data-role="' + r + '"><canvas></canvas><span><b>' + ROLE[r] + '</b><span>' + ROLE_D[r] + '</span></span><i>0' + (i + 1) + (S.role === r ? ' · сейчас' : '') + '</i></button>').join('') +
      (first ? '' : '<button class="btn ghost wide" type="button" data-act="roles-close" style="color:var(--chalk)">Остаться: ' + ROLE[S.role] + '</button>') +
      '<div class="foot">' + (IN_TG ? 'Демо-режим: роль можно выбрать самому. В рабочем режиме её назначает администратор.' : 'Открыто без Telegram: демо-вход, роль хранится на этом устройстве.') + '</div></div>';
    el.hidden = false;
    el.scrollTop = 0;
    S.rolesOpen = true;
    if (S.sheet) closeSheet();
    $$('.role-card canvas', el).forEach((c) => Art.role(c, c.closest('.role-card').dataset.role));
    mainBtn(null);
    syncBack();
  }
  function hideRoles() { $('#roles').hidden = true; $('#roles').innerHTML = ''; S.rolesOpen = false; syncBack(); render(); }
  async function chooseRole(r) {
    hap.heavy();
    keep('lg.role', r);
    try { S.me = await api('/api/tg/role', { body: { role: r } }); }
    catch (e) { fail(e); return; }
    const changed = S.role !== (S.me.role || r);
    S.role = S.me.role || r;
    $('#roles').hidden = true; $('#roles').innerHTML = ''; S.rolesOpen = false;
    if (changed) { S.tab = null; stack.length = 0; }
    startApp();
    toast('Роль: ' + ROLE[S.role], 'ok');
  }
  function gate(e) {
    const g = $('#gate');
    const s = e && e.status;
    const title = s === 401 ? 'Откройте через бота' : s === 403 ? 'Нет доступа' : 'Нет связи';
    const text = s === 401 ? 'Мини-приложение работает внутри Telegram: откройте его кнопкой в чате с ботом LinQ Guard.'
      : s === 403 ? (e.message || 'Роль не назначена.') + ' Ваш Telegram ID нужно добавить в настройки ролей.'
        : 'Сервер не ответил. Проверьте сеть и попробуйте ещё раз.';
    const id = tg && tg.initDataUnsafe && tg.initDataUnsafe.user ? tg.initDataUnsafe.user.id : '';
    put(g, '<div><canvas data-still="' + (s ? 'alarm' : 'cam') + '"></canvas><h2>' + title + '</h2><p>' + esc(text) + '</p>' + (id ? '<p><code>Telegram ID: ' + esc(id) + '</code></p>' : '') +
      '<button class="btn" type="button" data-act="retry">Попробовать снова</button></div>');
    g.hidden = false;
    $('#app').hidden = true;
  }

  // ------------------------------------------------------------ deep link: #alarm=<id> or start_param alarm_<id>
  function deepLinkId() {
    const m = /(?:^|[#&?])alarm=([^&]+)/.exec(location.hash || '');
    if (m) return decodeURIComponent(m[1]);
    const sp = tg && tg.initDataUnsafe && tg.initDataUnsafe.start_param;
    const m2 = sp && /^alarm[_-](.+)$/.exec(sp);
    return m2 ? m2[1] : null;
  }
  function handleDeepLink() {
    const id = deepLinkId();
    if (!id || !S.role || id === S.dlDone) return;
    S.dlDone = id;
    if (!seesAlarms()) { toast('Тревоги видят оператор, начальник смены и руководитель', 'warn'); return; }
    S.focus = id;
    go('alarms');
  }
  window.addEventListener('hashchange', () => { S.dlDone = null; handleDeepLink(); });

  // ------------------------------------------------------------ events
  document.addEventListener('click', (e) => {
    const t = e.target.closest('[data-act],[data-tab],[data-role]');
    if (!t) return;
    if (t.dataset.tab) { if (t.dataset.tab === 'more') openMore(); else go(t.dataset.tab); return; }
    if (t.dataset.role) { chooseRole(t.dataset.role); return; }
    const id = t.dataset.id;
    switch (t.dataset.act) {
      case 'close': closeSheet(); break;
      case 'back': back(); break;
      case 'ack': doAck(id, t); break;
      case 'resolve': hap.tap(); openResolve(id); break;
      case 'note': t.classList.toggle('on'); hap.sel(); break;
      case 'do-resolve': doResolve(id, t); break;
      case 'ask': doAsk(id, t); break;
      case 'snap': { const a = findAlarm(id); if (a) { hap.tap(); openAlarmSheet(a); } break; }
      case 'event': openEvent(id); break;
      case 'pass': openPass(id); break;
      case 'do-pass': doPass(id, t); break;
      case 'person': closeSheet(); pushPerson(id); break;
      case 'fsev': S.f.sev = t.dataset.v; S.seenEv = null; hap.sel(); drawFilters(); if (S.refresh) S.refresh(); break;
      case 'fcam': S.f.cam = t.dataset.v; S.seenEv = null; hap.sel(); drawFilters(); if (S.refresh) S.refresh(); break;
      case 'pick': openPick(id); break;
      case 'bind': doBind(id, t); break;
      case 'unbind': doBind(null, t); break;
      case 'csv-send': csvSend(t.dataset.scope, t.dataset.person, t); break;
      case 'csv-dl': csvDownload(t.dataset.scope || 'shift', t.dataset.person); break;
      case 'close-shift': closeShift(); break;
      case 'roles': showRoles(false); break;
      case 'roles-close': hideRoles(); break;
      case 'more-go': closeSheet(); go(t.dataset.v); break;
      case 'goto': go(t.dataset.v); break;
      case 'step': stepMin(+t.dataset.v); break;
      case 'save-settings': saveSettings(t); break;
      case 'demo-reset': demoReset(t); break;
      case 'open-bot': { const u = 'https://t.me/' + (S.me.bot || {}).username; if (IN_TG && ver('6.1')) tg.openTelegramLink(u); else window.open(u, '_blank'); break; }
      case 'retry': location.reload(); break;
      default: break;
    }
  });
  $('#scrim').addEventListener('click', closeSheet);
  $('#roleBtn').addEventListener('click', () => { hap.tap(); showRoles(false); });
  $('#alarmPill').addEventListener('click', () => go('alarms'));
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') back();
    if (e.key === 'Enter' && e.target.getAttribute && e.target.getAttribute('role') === 'button') e.target.click();
  });

  // ------------------------------------------------------------ boot
  function startApp() {
    $('#gate').hidden = true;
    $('#app').hidden = false;
    if (!hdrArt) hdrArt = Art.header($('#hdrArt'));
    const ids = TABS[S.role].map((x) => x[0]).concat(S.role === 'director' ? MORE.map((x) => x[0]) : []);
    if (!S.tab || ids.indexOf(S.tab) < 0 || S.tab === 'more') S.tab = TABS[S.role][0][0];
    renderHeader();
    renderTabs();
    render();
    loadShift().catch(() => {});
    startWatch();
    handleDeepLink();
  }
  function playIntro() {
    return new Promise((res) => {
      const el = $('#intro');
      if (Art.reduced() || !once('lg.intro')) return res();
      el.hidden = false;
      // let the fonts arrive (or not) for a beat, then draw
      const go2 = () => Art.intro(el, res);
      if (document.fonts && document.fonts.load) Promise.race([document.fonts.load('800 50px "Fira Sans Extra Condensed"'), new Promise((r) => setTimeout(r, 350))]).then(go2, go2);
      else go2();
    });
  }
  async function boot() {
    applyTheme();
    Art.textures();
    if (tg) {
      try {
        tg.ready();
        if (IN_TG) {
          tg.expand();
          if (ver('7.7') && tg.disableVerticalSwipes) tg.disableVerticalSwipes();
          tg.onEvent('themeChanged', applyTheme);
          if (ver('6.1')) tg.BackButton.onClick(back);
          if (tg.MainButton) tg.MainButton.onClick(() => { if (mainFn) mainFn(); });
        }
      } catch (e) { /* old client */ }
    }
    if (window.matchMedia) { const mq = matchMedia('(prefers-color-scheme: dark)'); if (mq.addEventListener) mq.addEventListener('change', applyTheme); }
    const intro = playIntro();
    let me = null, err = null;
    try { me = await api('/api/tg/me'); } catch (e) { err = e; }
    await intro;
    if (err) return gate(err);
    S.me = me;
    if (!me.role) {
      if (me.demo) { $('#app').hidden = true; return showRoles(true); }
      return gate({ status: 403, message: 'Роль не назначена.' });
    }
    S.role = me.role;
    if (me.demo) keep('lg.role', me.role);
    startApp();
  }
  boot();
})();
