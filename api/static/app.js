"use strict";
const SEV = { critical: "критично", high: "высокий", medium: "средний" };
const ST = { new: "новое", confirmed: "подтверждено", false: "ложное", sent_to_master: "передано мастеру", resolved: "устранено" };
const $ = (s, r = document) => r.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const api = (p, o) => fetch(p, o).then((r) => { if (!r.ok) throw new Error(r.status); return r.json(); });

const state = { cameras: [], events: new Map(), rules: [], view: "monitor", sound: false, fresh: new Set() };

function fmtTime(ts, withDate) {
  const d = new Date(ts * 1000);
  const t = d.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  return withDate ? d.toLocaleDateString("ru-RU", { day: "2-digit", month: "2-digit" }) + " " + t : t;
}
function fmtDur(s) {
  s = Math.max(0, Math.round(s || 0));
  return String(Math.floor(s / 60)).padStart(2, "0") + ":" + String(s % 60).padStart(2, "0");
}
const sevPill = (s) => `<span class="pill ${esc(s)}">${esc(SEV[s] || s)}</span>`;
const stPill = (s) => `<span class="pill st-${esc(s)}">${esc(ST[s] || s)}</span>`;

/* ---------- маршрутизация ---------- */
const VIEWS = ["monitor", "gallery", "journal", "analytics", "zones", "webcam", "linq"];
function show(view) {
  if (!VIEWS.includes(view)) view = "monitor";
  state.view = view;
  VIEWS.forEach((v) => { $("#view-" + v).hidden = v !== view; });
  document.querySelectorAll("#tabs a").forEach((a) => a.setAttribute("aria-current", a.dataset.view === view ? "page" : "false"));
  sendFrameWant();
  ({ gallery: loadGallery, journal: loadJournal, analytics: loadAnalytics, zones: loadZones, linq: loadLinq })[view]?.();
}
window.addEventListener("hashchange", () => show(location.hash.slice(1)));

/* ---------- часы, звук, тосты ---------- */
setInterval(() => { $("#clock").textContent = new Date().toLocaleTimeString("ru-RU"); }, 1000);
let audioCtx = null;
$("#sound-btn").addEventListener("click", (e) => {
  state.sound = !state.sound;
  e.currentTarget.setAttribute("aria-pressed", String(state.sound));
  e.currentTarget.textContent = state.sound ? "Звук вкл." : "Звук выкл.";
  if (state.sound && !audioCtx) audioCtx = new (window.AudioContext || window.webkitAudioContext)();
});
function beep() {
  if (!state.sound || !audioCtx) return;
  [0, 0.25].forEach((t) => {
    const o = audioCtx.createOscillator(), g = audioCtx.createGain();
    o.frequency.value = 880; o.type = "square"; g.gain.value = 0.06;
    o.connect(g).connect(audioCtx.destination);
    o.start(audioCtx.currentTime + t); o.stop(audioCtx.currentTime + t + 0.15);
  });
}
function toast(ev) {
  const el = document.createElement("div");
  el.className = "toast";
  el.innerHTML = `<b>${esc(ev.title)}</b><span class="muted small">${esc(ev.camera_title)} · ${esc(ev.zone_title)} · ${fmtTime(ev.started_at)}</span>`;
  el.addEventListener("click", () => { openModal(ev.id); el.remove(); });
  $("#toasts").appendChild(el);
  setTimeout(() => el.remove(), 9000);
}

/* ---------- поток событий ---------- */
function connectWS() {
  const ws = new WebSocket((location.protocol === "https:" ? "wss://" : "ws://") + location.host + "/ws/events");
  ws.onmessage = (m) => {
    const msg = JSON.parse(m.data);
    const ev = msg.event;
    if (!ev) return;
    state.events.set(ev.id, ev);
    if (msg.kind === "event_open") {
      state.fresh.add(ev.id);
      setTimeout(() => { state.fresh.delete(ev.id); renderFeeds(); }, 6000);
      if (ev.severity === "critical") { beep(); toast(ev); }
    }
    renderFeeds();
    renderCamAlarms();
    if (!$("#modal").hidden && $("#modal").dataset.id === ev.id) fillModal(ev);
  };
  ws.onclose = () => setTimeout(connectWS, 2000);
}

function feedItem(ev) {
  return `<li data-id="${esc(ev.id)}" class="${state.fresh.has(ev.id) ? "fresh" : ""}">
    <img src="${esc(ev.snapshot)}?v=${Math.round(ev.duration_s)}" alt="" loading="lazy">
    <div><div class="row">${sevPill(ev.severity)}${stPill(ev.status)}</div>
    <div class="t">${esc(ev.title)}</div>
    <div class="m">${esc(ev.camera_title)} · ${esc(ev.zone_title)}</div>
    <div class="m">${fmtTime(ev.started_at)} · ${ev.closed ? "длилось" : "длится"} ${fmtDur(ev.duration_s)}</div></div></li>`;
}
function renderFeeds() {
  const all = [...state.events.values()].sort((a, b) => b.started_at - a.started_at);
  const main = all.filter((e) => e.camera !== "webcam").slice(0, 60);
  $("#feed").innerHTML = main.length ? main.map(feedItem).join("") : `<li class="empty">Событий пока нет. Камеры работают, лента обновится сама.</li>`;
  $("#feed-count").textContent = main.length ? `${main.length} последних` : "";
  const wc = all.filter((e) => e.camera === "webcam").slice(0, 30);
  $("#wc-feed").innerHTML = wc.length ? wc.map(feedItem).join("") : `<li class="empty">Здесь появятся нарушения с вебкамеры.</li>`;
  const active = all.filter((e) => !e.closed).length;
  $("#st-active").textContent = `${active} активных`;
  $("#st-active").classList.toggle("alert", all.some((e) => !e.closed && e.severity === "critical"));
}
document.addEventListener("click", (e) => {
  const li = e.target.closest(".feed li[data-id], .g-card[data-id], tbody tr[data-id]");
  if (li) openModal(li.dataset.id);
});

/* ---------- мониторинг ---------- */
async function loadCameras() {
  state.cameras = await api("/api/cameras");
  const cams = state.cameras.filter((c) => c.source !== "webcam");
  const box = $("#cams");
  if (box.children.length !== cams.length) {
    box.innerHTML = cams.map((c) => `<div class="cam" id="cam-${esc(c.id)}" data-cam="${esc(c.id)}" title="Нажмите, чтобы развернуть">
      <img data-frame="${esc(c.id)}" alt="Камера ${esc(c.title)}">
      <div class="cam-h"><b>${esc(c.title)}</b><span class="cam-meta"><span class="pill mono" data-fps></span><span data-act></span></span></div></div>`).join("");
  }
  cams.forEach((c) => {
    const el = $("#cam-" + CSS.escape(c.id));
    if (!el) return;
    el.querySelector("[data-fps]").textContent = `${c.stats.fps} к/с`;
    el.querySelector("[data-act]").innerHTML = c.stats.active ? `<span class="pill high">${c.stats.active} актив.</span>` : "";
  });
  renderCamAlarms();
  const m = await api("/api/metrics");
  $("#st-backend").textContent = m.gpu_backend === "linq-tpu" ? "LinQ TPU" : (m.gpu_backend || "").replace("onnx-", "ONNX · ").toUpperCase();
}
/* кадры всех камер приходят по одному WebSocket: 32 байта ID камеры + JPEG */
const FR = { ws: null, full: null };
function connectFrames() {
  FR.ws = new WebSocket((location.protocol === "https:" ? "wss://" : "ws://") + location.host + "/ws/frames");
  FR.ws.binaryType = "blob";
  FR.ws.onopen = sendFrameWant;
  FR.ws.onmessage = async (m) => {
    const id = (await m.data.slice(0, 32).text()).trim();
    const img = document.querySelector(`img[data-frame="${CSS.escape(id)}"]`);
    if (!img) return;
    const url = URL.createObjectURL(m.data.slice(32, m.data.size, "image/jpeg"));
    const old = img.src;
    img.src = url;
    if (old.startsWith("blob:")) URL.revokeObjectURL(old);
  };
  FR.ws.onclose = () => setTimeout(connectFrames, 2000);
}
function sendFrameWant() {
  if (FR.ws?.readyState !== 1) return;
  const cams = state.view === "monitor" ? state.cameras.filter((c) => c.source !== "webcam").map((c) => c.id) : [];
  FR.ws.send(JSON.stringify({ cams, full: FR.full }));
}
$("#cams").addEventListener("click", (e) => {
  const tile = e.target.closest(".cam");
  if (!tile) return;
  const big = !tile.classList.contains("big");
  document.querySelectorAll(".cam.big").forEach((t) => t.classList.remove("big"));
  tile.classList.toggle("big", big);
  FR.full = big ? tile.dataset.cam : null;
  sendFrameWant();
});
function renderCamAlarms() {
  const crit = new Set([...state.events.values()].filter((e) => !e.closed && e.severity === "critical").map((e) => e.camera));
  document.querySelectorAll(".cam").forEach((el) => el.classList.toggle("alarm", crit.has(el.id.slice(4))));
}

/* ---------- фильтры галереи и журнала ---------- */
function filtersHTML(prefix) {
  const types = state.rules.map((r) => `<option value="${esc(r.type)}">${esc(r.title)}</option>`).join("");
  const cams = state.cameras.map((c) => `<option value="${esc(c.id)}">${esc(c.title)}</option>`).join("");
  return `<label>Нарушение <select id="${prefix}-type"><option value="">все</option>${types}</select></label>
    <label>Камера <select id="${prefix}-cam"><option value="">все</option>${cams}</select></label>
    <label>Уровень <select id="${prefix}-sev"><option value="">все</option>${Object.entries(SEV).map(([k, v]) => `<option value="${k}">${v}</option>`).join("")}</select></label>
    <label>Статус <select id="${prefix}-st"><option value="">все</option>${Object.entries(ST).map(([k, v]) => `<option value="${k}">${v}</option>`).join("")}</select></label>
    <label>Период <select id="${prefix}-h"><option value="1">час</option><option value="24" selected>сутки</option><option value="168">неделя</option><option value="">всё время</option></select></label>`;
}
function setupFilters() {
  document.querySelectorAll("[data-filters]").forEach((bar) => {
    const p = bar.dataset.filters;
    const extra = bar.innerHTML;
    bar.innerHTML = filtersHTML(p) + extra;
    bar.addEventListener("change", () => (p === "gallery" ? loadGallery() : loadJournal()));
  });
}
function query(p) {
  const q = new URLSearchParams();
  [["type", "type"], ["camera", "cam"], ["severity", "sev"], ["status", "st"], ["hours", "h"]].forEach(([k, id]) => {
    const v = $(`#${p}-${id}`)?.value;
    if (v) q.set(k, v);
  });
  return q.toString();
}
async function loadGallery() {
  const rows = await api("/api/events?limit=240&" + query("gallery"));
  rows.forEach((r) => state.events.set(r.id, r));
  $("#gallery").innerHTML = rows.length ? rows.map((e) => `<article class="g-card" data-id="${esc(e.id)}">
    <div class="g-img"><img src="${esc(e.snapshot)}" alt="${esc(e.title)}" loading="lazy">${sevPill(e.severity)}<span class="dur">${fmtDur(e.duration_s)}</span></div>
    <div class="g-body"><div class="t">${esc(e.title)}</div><div class="m">${esc(e.camera_title)} · ${esc(e.zone_title)}</div>
    <div class="m">${fmtTime(e.started_at, true)} ${stPill(e.status)}</div></div></article>`).join("")
    : `<div class="empty">Под фильтр ничего не попало. Расширьте период или снимите фильтры.</div>`;
}
async function loadJournal() {
  const rows = await api("/api/events?limit=500&" + query("journal"));
  rows.forEach((r) => state.events.set(r.id, r));
  const h = $("#journal-h")?.value;
  $("#csv-link").href = "/api/events.csv" + (h ? "?hours=" + h : "");
  $("#journal").innerHTML = rows.map((e) => `<tr data-id="${esc(e.id)}"><td class="mono">${fmtTime(e.started_at, true)}</td>
    <td>${esc(e.camera_title)}</td><td>${esc(e.zone_title)}</td><td>${esc(e.title)}</td><td>${sevPill(e.severity)}</td>
    <td class="num">${e.duration_s}</td><td class="num">${e.latency_s ?? "—"}</td><td>${stPill(e.status)}</td>
    <td><img src="${esc(e.snapshot)}" alt="" loading="lazy"></td></tr>`).join("") || `<tr><td colspan="9" class="empty">Записей нет.</td></tr>`;
}

/* ---------- аналитика ---------- */
function bars(el, obj, color) {
  const entries = Object.entries(obj).sort((a, b) => b[1] - a[1]);
  const max = Math.max(1, ...entries.map((e) => e[1]));
  el.innerHTML = entries.length ? entries.map(([k, v]) => `<div class="bar"><span class="lbl" title="${esc(k)}">${esc(k)}</span>
    <span class="track"><span class="fill" style="width:${(v / max) * 100}%;${color ? "background:" + color : ""}"></span></span><span class="v">${v}</span></div>`).join("")
    : `<div class="muted small">Нет данных за период.</div>`;
}
async function loadAnalytics() {
  const s = await api("/api/stats?hours=" + $("#an-hours").value);
  const crit = s.by_severity.critical || 0;
  $("#kpis").innerHTML = `
    <div class="kpi"><b>${s.total}</b><span>событий за период</span></div>
    <div class="kpi"><b style="color:var(--crit)">${crit}</b><span>критичных</span></div>
    <div class="kpi"><b>${s.precision == null ? "—" : Math.round(s.precision * 100) + "%"}</b><span>точность по отметкам оператора</span></div>
    <div class="kpi"><b>${s.latency_p50 == null ? "—" : s.latency_p50.toFixed(1) + " с"}</b><span>медиана от начала нарушения до тревоги</span></div>`;
  bars($("#ch-type"), s.by_type);
  bars($("#ch-cam"), s.by_camera, "var(--medium)");
  bars($("#ch-status"), Object.fromEntries(Object.entries(s.by_status).map(([k, v]) => [ST[k] || k, v])), "var(--ok)");
  const days = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"];
  const max = Math.max(1, ...s.heat.flat());
  let html = `<span></span>` + Array.from({ length: 24 }, (_, h) => `<span class="h">${h % 3 === 0 ? h : ""}</span>`).join("");
  s.heat.forEach((row, d) => {
    html += `<span class="d">${days[d]}</span>` + row.map((v, h) => `<span class="c" title="${days[d]}, ${h}:00 — ${v}" style="${v ? `background:rgba(255,194,26,${0.15 + 0.85 * v / max})` : ""}"></span>`).join("");
  });
  $("#ch-heat").innerHTML = html;
}
$("#an-hours").addEventListener("change", loadAnalytics);

/* ---------- модалка события ---------- */
function openModal(id) {
  const ev = state.events.get(id);
  if (!ev) return;
  $("#modal").dataset.id = id;
  fillModal(ev);
  $("#modal").hidden = false;
}
function fillModal(ev) {
  $("#m-img").src = ev.snapshot + "?v=" + Math.round(ev.duration_s);
  $("#m-sev").outerHTML = `<span class="pill ${esc(ev.severity)}" id="m-sev">${esc(SEV[ev.severity] || ev.severity)}</span>`;
  $("#m-status").outerHTML = `<span class="pill st-${esc(ev.status)}" id="m-status">${esc(ST[ev.status] || ev.status)}</span>`;
  $("#m-title").textContent = ev.title;
  const det = Object.entries(ev.details || {}).map(([k, v]) => `${k}: ${v}`).join(", ");
  $("#m-kv").innerHTML = `<dt>Камера</dt><dd>${esc(ev.camera_title)}</dd><dt>Зона</dt><dd>${esc(ev.zone_title)}</dd>
    <dt>Начало</dt><dd>${fmtTime(ev.started_at, true)}</dd><dt>Длительность</dt><dd>${fmtDur(ev.duration_s)}${ev.closed ? "" : " · идёт"}</dd>
    <dt>Тревога через</dt><dd>${ev.latency_s ?? "—"} с после начала</dd><dt>Уверенность</dt><dd>${Math.round((ev.score || 0) * 100)}%</dd>
    ${det ? `<dt>Детали</dt><dd>${esc(det)}</dd>` : ""}<dt>ID</dt><dd class="mono">${esc(ev.id)} · трек #${ev.track_id}</dd>`;
  $("#m-hist").innerHTML = (ev.history || []).map((h) => `<li>${fmtTime(h.at, true)} — ${esc(ST[h.status] || h.status)} (${esc(h.who)})</li>`).join("");
}
function closeModal() { $("#modal").hidden = true; }
$("#modal-x").addEventListener("click", closeModal);
$("#modal").addEventListener("click", (e) => { if (e.target.id === "modal") closeModal(); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeModal(); });
document.querySelectorAll("[data-st]").forEach((b) => b.addEventListener("click", async () => {
  const id = $("#modal").dataset.id;
  const ev = await api(`/api/events/${encodeURIComponent(id)}/status`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ status: b.dataset.st }),
  });
  state.events.set(ev.id, ev);
  fillModal(ev); renderFeeds();
  if (state.view === "gallery") loadGallery();
  if (state.view === "journal") loadJournal();
}));

/* ---------- зоны ---------- */
const Z = { cam: null, zones: [], sel: -1, drawing: null, img: new Image() };
async function loadZones() {
  state.cameras = await api("/api/cameras");
  const sel = $("#z-cam");
  const cur = sel.value;
  sel.innerHTML = state.cameras.filter((c) => c.source !== "webcam").map((c) => `<option value="${esc(c.id)}">${esc(c.title)}</option>`).join("");
  if (cur) sel.value = cur;
  pickZoneCam();
}
function pickZoneCam() {
  Z.cam = state.cameras.find((c) => c.id === $("#z-cam").value);
  if (!Z.cam) return;
  Z.zones = JSON.parse(JSON.stringify(Z.cam.zones.filter((z) => z.polygon)));
  Z.frameRules = Z.cam.zones.filter((z) => !z.polygon);
  Z.sel = Z.zones.length ? 0 : -1;
  Z.drawing = null;
  Z.img = new Image();
  Z.img.onload = () => { const c = $("#z-canvas"); c.width = Z.img.width; c.height = Z.img.height; drawZones(); };
  Z.img.src = `/api/cameras/${encodeURIComponent(Z.cam.id)}/frame.jpg?t=${Date.now()}`;
  renderZoneSide();
}
$("#z-cam").addEventListener("change", pickZoneCam);
function drawZones() {
  const c = $("#z-canvas"), g = c.getContext("2d");
  g.drawImage(Z.img, 0, 0);
  const poly = (pts, i) => {
    if (!pts.length) return;
    g.beginPath(); pts.forEach(([x, y], k) => (k ? g.lineTo(x, y) : g.moveTo(x, y))); g.closePath();
    g.fillStyle = i === Z.sel ? "rgba(255,194,26,.28)" : "rgba(255,194,26,.14)"; g.fill();
    g.lineWidth = i === Z.sel ? 4 : 2; g.strokeStyle = "#FFC21A"; g.stroke();
  };
  Z.zones.forEach((z, i) => poly(z.polygon, i));
  if (Z.drawing) {
    g.strokeStyle = "#FF5A4E"; g.lineWidth = 3; g.beginPath();
    Z.drawing.forEach(([x, y], k) => (k ? g.lineTo(x, y) : g.moveTo(x, y))); g.stroke();
    Z.drawing.forEach(([x, y]) => { g.fillStyle = "#FF5A4E"; g.fillRect(x - 5, y - 5, 10, 10); });
  }
}
$("#z-canvas").addEventListener("click", (e) => {
  if (!Z.drawing) return;
  const c = e.currentTarget, r = c.getBoundingClientRect();
  Z.drawing.push([Math.round((e.clientX - r.left) * c.width / r.width), Math.round((e.clientY - r.top) * c.height / r.height)]);
  $("#z-finish").disabled = Z.drawing.length < 3;
  drawZones();
});
$("#z-new").addEventListener("click", () => {
  Z.drawing = [];
  $("#z-hint").textContent = "Кликайте по углам опасной зоны, затем «Замкнуть полигон».";
  drawZones();
});
$("#z-finish").addEventListener("click", () => {
  Z.zones.push({ id: "zone-" + Date.now().toString(36), title: "Новая зона", polygon: Z.drawing, exclude: [], rules: [{ type: "zone_intrusion", confirm_s: 2 }] });
  Z.drawing = null; Z.sel = Z.zones.length - 1;
  $("#z-finish").disabled = true;
  $("#z-hint").textContent = "Зона добавлена. Настройте правила справа и сохраните.";
  drawZones(); renderZoneSide();
});
function renderZoneSide() {
  $("#z-list").innerHTML = Z.zones.map((z, i) => `<li class="${i === Z.sel ? "sel" : ""}" data-i="${i}"><span>${esc(z.title)}</span><button type="button" data-del="${i}" aria-label="Удалить зону">✕</button></li>`).join("")
    || `<li class="muted">Зон нет: правила камеры действуют на весь кадр.</li>`;
  const z = Z.zones[Z.sel];
  if (!z) { $("#z-edit").innerHTML = ""; return; }
  const on = new Map(z.rules.map((r) => [r.type, r]));
  const sched = z.rules.find((r) => r.schedule)?.schedule || "";
  $("#z-edit").innerHTML = `<label>Название<input type="text" id="z-title" value="${esc(z.title)}"></label>
    <label>Действует (ЧЧ:ММ-ЧЧ:ММ, пусто — всегда)<input type="text" id="z-sched" value="${esc(sched)}" placeholder="07:55-12:00"></label>
    <div class="muted small">Правила в зоне · подтверждение, с</div>
    ${state.rules.filter((r) => r.target === "person" || r.target === "fire" || r.target === "smoke").map((r) => `<label class="rule-row">
      <input type="checkbox" data-rule="${esc(r.type)}" ${on.has(r.type) ? "checked" : ""}><span>${esc(r.title)}</span>
      <input type="number" min="0" step="0.5" data-conf="${esc(r.type)}" value="${on.get(r.type)?.confirm_s ?? r.confirm_s}"></label>`).join("")}`;
}
$("#z-list").addEventListener("click", (e) => {
  const del = e.target.closest("[data-del]");
  if (del) { Z.zones.splice(+del.dataset.del, 1); Z.sel = Math.min(Z.sel, Z.zones.length - 1); }
  else { const li = e.target.closest("li[data-i]"); if (li) { syncZoneForm(); Z.sel = +li.dataset.i; } }
  drawZones(); renderZoneSide();
});
function syncZoneForm() {
  const z = Z.zones[Z.sel];
  if (!z || !$("#z-title")) return;
  z.title = $("#z-title").value.trim() || z.title;
  const sched = $("#z-sched").value.trim();
  z.rules = [...document.querySelectorAll("#z-edit [data-rule]")].filter((c) => c.checked).map((c) => {
    const r = { type: c.dataset.rule, confirm_s: parseFloat($(`#z-edit [data-conf="${c.dataset.rule}"]`).value) || 0 };
    if (sched) r.schedule = sched;
    return r;
  });
}
$("#z-save").addEventListener("click", async () => {
  syncZoneForm();
  const frame = Z.frameRules.length ? [{ id: "frame", title: "Весь кадр", polygon: null, rules: Z.frameRules[0].rules }] : [];
  const zones = [...frame, ...Z.zones].map((z) => ({ id: z.id, title: z.title, polygon: z.polygon, exclude: z.exclude || [], rules: z.rules.map(({ title, ...r }) => r) }));
  try {
    await api(`/api/cameras/${encodeURIComponent(Z.cam.id)}/zones`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ zones }) });
    $("#z-msg").textContent = "Сохранено. Камера уже работает по новым правилам.";
  } catch (err) {
    $("#z-msg").textContent = "Не сохранилось: проверьте формат расписания, например 07:55-12:00.";
  }
});

/* ---------- вебкамера ---------- */
const WC = { stream: null, ws: null, timer: null, busy: false };
$("#wc-start").addEventListener("click", async () => {
  try {
    WC.stream = await navigator.mediaDevices.getUserMedia({ video: { width: 1280, height: 720 }, audio: false });
  } catch (err) {
    $("#wc-status").textContent = "Браузер не дал доступ к камере. Откройте страницу через localhost и разрешите камеру.";
    return;
  }
  const v = $("#wc-video");
  v.srcObject = WC.stream; await v.play();
  const canvas = document.createElement("canvas");
  WC.ws = new WebSocket((location.protocol === "https:" ? "wss://" : "ws://") + location.host + "/ws/webcam");
  WC.ws.binaryType = "blob";
  WC.ws.onmessage = (m) => { const url = URL.createObjectURL(m.data); const img = $("#wc-out"); const old = img.src; img.src = url; if (old.startsWith("blob:")) URL.revokeObjectURL(old); WC.busy = false; };
  WC.ws.onopen = () => {
    $("#wc-status").textContent = "Камера работает: кадры уходят на сервер 5 раз в секунду.";
    WC.timer = setInterval(() => {
      if (WC.busy || WC.ws.readyState !== 1 || !v.videoWidth) return;
      canvas.width = 960; canvas.height = Math.round(960 * v.videoHeight / v.videoWidth);
      canvas.getContext("2d").drawImage(v, 0, 0, canvas.width, canvas.height);
      canvas.toBlob((b) => { if (b) { WC.busy = true; WC.ws.send(b); } }, "image/jpeg", 0.8);
    }, 200);
  };
  $("#wc-start").disabled = true; $("#wc-stop").disabled = false;
});
$("#wc-stop").addEventListener("click", () => {
  clearInterval(WC.timer); WC.ws?.close(); WC.stream?.getTracks().forEach((t) => t.stop());
  $("#wc-start").disabled = false; $("#wc-stop").disabled = true;
  $("#wc-status").textContent = "Камера остановлена.";
});

/* ---------- LinQ ---------- */
async function loadLinq() {
  const m = await api("/api/metrics");
  $("#lq-backend").textContent = `Сейчас модели работают через ${m.gpu_backend}. На стенде LinQ тот же код переключается на бэкенд linq-tpu.`;
  const ms = (o, k) => (o && o[k] != null ? o[k] : "—");
  $("#lq-models").innerHTML = m.models.map((d) => `<tr><td>${esc(d.model)}</td><td class="mono">${esc(d.backend)}</td>
    <td class="num">${ms(d.pre, "p50")}</td><td class="num">${ms(d.infer, "p50")}</td><td class="num">${ms(d.infer, "p95")}</td><td class="num">${ms(d.post, "p50")}</td></tr>`).join("");
  $("#lq-cams").innerHTML = m.cameras.map((c) => `<tr><td>${esc(c.title)}</td><td class="num">${c.fps}</td><td class="num">${c.proc_p50 ?? "—"}</td><td class="num">${c.proc_p95 ?? "—"}</td><td class="num">${c.active}</td></tr>`).join("");
}

/* ---------- старт ---------- */
(async function init() {
  state.rules = await api("/api/rules");
  await loadCameras();
  setupFilters();
  const recent = await api("/api/events?limit=80&hours=24");
  recent.forEach((e) => state.events.set(e.id, e));
  renderFeeds();
  connectWS();
  connectFrames();
  show(location.hash.slice(1) || "monitor");
  setInterval(() => { loadCameras(); if (state.view === "linq") loadLinq(); }, 3000);
})();
