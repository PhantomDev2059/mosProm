"use strict";
const SEV = { critical: "критично", high: "высокий", medium: "средний" };
const ST = { new: "новое", confirmed: "подтверждено", false: "ложное", sent_to_master: "передано мастеру", resolved: "устранено" };
const DETAIL = {
  torso_angle: "угол корпуса, °", distance_m: "расстояние, м", gap_heights: "зазор, в ростах человека",
  speed_mps: "скорость, м/с", heights_per_s: "скорость, ростов/с", still_s: "неподвижен, с", vehicle: "техника",
  forklift: "погрузчик", machinery: "спецтехника",
};
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
const VIEWS = ["monitor", "review", "gallery", "journal", "analytics", "quality", "zones", "webcam", "linq"];
function show(view) {
  if (!VIEWS.includes(view)) view = "monitor";
  state.view = view;
  VIEWS.forEach((v) => { $("#view-" + v).hidden = v !== view; });
  document.querySelectorAll("#tabs a").forEach((a) => a.setAttribute("aria-current", a.dataset.view === view ? "page" : "false"));
  sendFrameWant();
  ({ review: loadReview, quality: loadQuality, gallery: loadGallery, journal: loadJournal, analytics: loadAnalytics, zones: loadZones, linq: loadLinq })[view]?.();
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
    box.innerHTML = cams.map((c, i) => `<div class="cam" id="cam-${esc(c.id)}" data-cam="${esc(c.id)}" title="Нажмите, чтобы развернуть">
      <img data-frame="${esc(c.id)}" alt="Камера ${esc(c.title)}">
      <div class="cam-top"><span class="cam-no">К-${String(i + 1).padStart(2, "0")}</span><b>${esc(c.title)}</b>
        <span class="cam-rec"><i></i>REC <span data-clock></span></span></div>
      <div class="cam-bottom"><span data-act></span><span class="cam-fps mono" data-fps></span></div></div>`).join("");
  }
  cams.forEach((c) => {
    const el = $("#cam-" + CSS.escape(c.id));
    if (!el) return;
    el.querySelector("[data-fps]").textContent = `${c.stats.fps} к/с · ${c.models.join(" + ")}`;
  });
  $("#sit-cams").textContent = cams.filter((c) => c.stats.fps > 0).length + " из " + cams.length;
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
  const all = [...state.events.values()];
  const active = all.filter((e) => !e.closed);
  const crit = new Set(active.filter((e) => e.severity === "critical").map((e) => e.camera));
  document.querySelectorAll(".cam").forEach((el) => {
    const cam = el.dataset.cam;
    el.classList.toggle("alarm", crit.has(cam));
    const mine = active.filter((e) => e.camera === cam);
    const byTitle = {};
    mine.forEach((e) => { (byTitle[e.title] ||= { n: 0, sev: e.severity }).n++; });
    el.querySelector("[data-act]").innerHTML = Object.entries(byTitle)
      .map(([t, v]) => `<span class="pill ${esc(v.sev)}">${esc(t)}${v.n > 1 ? " ×" + v.n : ""}</span>`).join("");
  });
  $("#sit-active").textContent = active.filter((e) => e.camera !== "webcam").length;
  $("#sit-crit").textContent = active.filter((e) => e.severity === "critical" && e.camera !== "webcam").length;
  $("#sit-crit").closest(".sit").classList.toggle("on", crit.size > 0);
  const since = Date.now() / 1000 - 12 * 3600;
  $("#sit-shift").textContent = all.filter((e) => e.started_at >= since && e.camera !== "webcam").length;
  const last = all.filter((e) => e.severity === "critical" && e.camera !== "webcam").sort((a, b) => b.started_at - a.started_at)[0];
  const btn = $("#sit-last");
  btn.hidden = !last;
  if (last) {
    btn.dataset.id = last.id;
    btn.innerHTML = `<span class="pill critical">последнее критичное</span><b>${esc(last.title)}</b><span class="muted">${esc(last.camera_title)} · ${fmtTime(last.started_at)}</span>`;
  }
}
$("#sit-last").addEventListener("click", (e) => openModal(e.currentTarget.dataset.id));
setInterval(() => {
  const t = new Date().toLocaleTimeString("ru-RU");
  document.querySelectorAll(".cam [data-clock]").forEach((el) => { el.textContent = t; });
}, 1000);

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
  loadSummary();
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
async function loadSummary() {
  const s = await api("/api/summary?hours=" + $("#sum-hours").value);
  $("#summary").innerHTML = s.text.split("\n").map((l) => `<p>${esc(l)}</p>`).join("");
}
$("#sum-hours").addEventListener("change", loadSummary);
$("#sum-copy").addEventListener("click", async (e) => {
  const text = $("#summary").innerText;
  try { await navigator.clipboard.writeText(text); e.target.textContent = "Скопировано"; }
  catch { const r = document.createRange(); r.selectNodeContents($("#summary")); getSelection().removeAllRanges(); getSelection().addRange(r); e.target.textContent = "Выделено"; }
  setTimeout(() => { e.target.textContent = "Скопировать"; }, 1500);
});

/* ---------- модалка события ---------- */
function openModal(id) {
  const ev = state.events.get(id);
  if (!ev) return;
  $("#modal").dataset.id = id;
  fillModal(ev);
  setMedia("img");
  $("#modal").hidden = false;
}
function setMedia(kind) {
  const ev = state.events.get($("#modal").dataset.id);
  const video = kind === "video" && ev?.clip;
  $("#m-img").hidden = !!video;
  $("#m-video").hidden = !video;
  if (video) { if (!$("#m-video").src.endsWith(ev.clip)) $("#m-video").src = ev.clip; $("#m-video").play().catch(() => {}); }
  else $("#m-video").pause();
  document.querySelectorAll("[data-media]").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.media === (video ? "video" : "img"))));
}
document.querySelectorAll("[data-media]").forEach((b) => b.addEventListener("click", () => setMedia(b.dataset.media)));
function fillModal(ev) {
  $("#m-img").src = ev.snapshot + "?v=" + Math.round(ev.duration_s);
  $("#m-video-btn").disabled = !ev.clip;
  $("#m-video-btn").title = ev.clip ? "" : "Фрагмент пишется: он появится через несколько секунд после тревоги";
  $("#m-sev").outerHTML = `<span class="pill ${esc(ev.severity)}" id="m-sev">${esc(SEV[ev.severity] || ev.severity)}</span>`;
  $("#m-status").outerHTML = `<span class="pill st-${esc(ev.status)}" id="m-status">${esc(ST[ev.status] || ev.status)}</span>`;
  $("#m-title").textContent = ev.title;
  const det = Object.entries(ev.details || {}).map(([k, v]) => `${DETAIL[k] || k}: ${DETAIL[v] || v}`).join(", ");
  $("#m-kv").innerHTML = `<dt>Камера</dt><dd>${esc(ev.camera_title)}</dd><dt>Зона</dt><dd>${esc(ev.zone_title)}</dd>
    <dt>Начало</dt><dd>${fmtTime(ev.started_at, true)}</dd><dt>Длительность</dt><dd>${fmtDur(ev.duration_s)}${ev.closed ? "" : " · идёт"}</dd>
    <dt>Тревога через</dt><dd>${ev.latency_s ?? "—"} с после начала</dd><dt>Уверенность</dt><dd>${Math.round((ev.score || 0) * 100)}%</dd>
    ${det ? `<dt>Детали</dt><dd>${esc(det)}</dd>` : ""}<dt>ID</dt><dd class="mono">${esc(ev.id)} · трек #${ev.track_id}</dd>`;
  $("#m-hist").innerHTML = (ev.history || []).map((h) => `<li>${fmtTime(h.at, true)} — ${esc(ST[h.status] || h.status)} (${esc(h.who)})</li>`).join("");
}
function closeModal() { $("#modal").hidden = true; $("#m-video").pause(); }
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
  Z.heat = [];
  api(`/api/cameras/${encodeURIComponent(Z.cam.id)}/heat`).then((pts) => { Z.heat = pts; drawZones(); });
  Z.img.onload = () => { const c = $("#z-canvas"); c.width = Z.img.width; c.height = Z.img.height; drawZones(); };
  Z.img.src = `/api/cameras/${encodeURIComponent(Z.cam.id)}/frame.jpg?t=${Date.now()}`;
  renderZoneSide();
}
$("#z-cam").addEventListener("change", pickZoneCam);
$("#z-heat").addEventListener("change", () => drawZones());
function drawZones() {
  const c = $("#z-canvas"), g = c.getContext("2d");
  if (!Z.img.complete || !Z.img.naturalWidth) return;
  g.drawImage(Z.img, 0, 0);
  if ($("#z-heat").checked && Z.heat?.length) {
    const r = Math.max(30, c.width / 22);
    Z.heat.forEach((p) => {
      const grad = g.createRadialGradient(p.x, p.y, 0, p.x, p.y, r);
      grad.addColorStop(0, p.severity === "critical" ? "rgba(255,60,50,.42)" : "rgba(255,150,30,.30)");
      grad.addColorStop(1, "rgba(255,120,30,0)");
      g.fillStyle = grad;
      g.fillRect(p.x - r, p.y - r, 2 * r, 2 * r);
    });
  }
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

/* ---------- качество ---------- */
const pct = (v) => (v == null ? "—" : Math.round(v * 100) + "%");
function qBlock(tag, m) {
  if (!m) return "";
  return `<div><span class="q-tag">${esc(tag)}</span><div class="q-num">
    <div><b>${pct(m.recall)}</b><span>найдено падений (recall)</span></div>
    <div><b>${pct(m.precision)}</b><span>тревог были настоящими (precision)</span></div>
    <div><b>${m.f1 == null ? "—" : m.f1.toFixed(2)}</b><span>F1</span></div></div>
    <p class="muted small">Ложных тревог на записях обычной активности: ${m.fp}</p></div>`;
}
async function loadQuality() {
  const q = await api("/api/quality");
  const f = q.fall, ml = q.fall_ml;
  $("#q-fall").innerHTML = f ? `<div class="q-cols">${qBlock("правило · проверка на чётных записях, система их не видела", f.test)}${ml ? qBlock("классификатор на позе · та же проверка", ml.test) : ""}</div>
    <p class="muted small">Оба метода настраивались только на нечётных записях. Правило (используется по умолчанию) ошибается реже, классификатор находит больше падений, но чаще зовёт зря.
    В кейсе точность важнее количества сценариев, поэтому в работе — правило. На настроечной половине правило дало F1 ${f.tune.f1.toFixed(2)}${ml ? `, классификатор — ${ml.tune.f1.toFixed(2)}` : ""}: разница с проверкой показывает, насколько мал набор (15 падений на половину).</p>
    <p class="muted small">По всему набору: ложных тревог в час — ${f.all_report.false_alarms_per_hour ?? "—"}, медиана времени до тревоги — ${f.all_report.latency_median_s ?? "—"} с,
    взвешенное покрытие времени — ${pct(f.all_report.weighted_time_coverage)}. Правило: резкое опускание головы ≥ ${Math.round(f.best_params.head_drop * 100)}% роста за ${f.best_params.drop_window_s} с,
    подтверждение ${f.confirm_s} с. Перебрано вариантов: ${f.grid_size}. Ограничение набора: камера в углу комнаты и разрешение 320×240 — на заводских камерах человек крупнее.</p>`
    : `<p class="empty">Оценка ещё не запускалась.</p>`;
  const rows = [];
  Object.entries(q.quant).sort().forEach(([name, vers]) => Object.entries(vers).sort().forEach(([v, m]) => rows.push(`<tr><td>${esc(name)}</td><td>${v}</td>
    <td class="num">${pct(m.recall_vs_fp32)}</td><td class="num">${m.precision_vs_fp32 == null ? "—" : pct(1 - m.precision_vs_fp32)}</td>
    <td class="num">${m.mean_iou ?? "—"}</td><td class="num">${m.keypoint_err_heights == null ? "—" : (m.keypoint_err_heights * 100).toFixed(1)}</td>
    <td class="num">${m.fp32_objects}</td><td class="small">${m.smart ? "с объектами" : "равномерная"}${m.finetune ? ` + дообучение ${m.finetune} эп.` : ""}</td></tr>`)));
  $("#q-quant").innerHTML = rows.join("") || `<tr><td colspan="8" class="empty">Проверка ещё не запускалась.</td></tr>`;
  const p = q.ppe;
  $("#q-ppe").innerHTML = p ? `<div class="q-num"><div><b>${pct(p.map50)}</b><span>mAP@0.5 на тестовой части</span></div>
    <div><b>${pct(p.map)}</b><span>mAP@0.5:0.95</span></div><div><b>${p.images}</b><span>тестовых изображений</span></div></div>
    <p class="muted small">По классам (mAP@0.5): ${Object.entries(p.per_class || {}).map(([k, v]) => `${k} ${pct(v)}`).join(", ")}</p>`
    : `<p class="empty">Обучение идёт: модель YOLOv8s на открытом наборе jhboyo/ppe-dataset (лицензия MIT) — каска, голова без каски, жилет.</p>`;
}

/* ---------- разбор смены ---------- */
const RV = { jobs: [], job: null, items: [], cat: "", verdict: "", cur: 0, timer: null };
const CAT_COLOR = { critical: "var(--crit)", high: "var(--high)", medium: "var(--medium)" };
const hms = (s) => { s = Math.max(0, Math.round(s)); return [Math.floor(s / 3600), Math.floor(s % 3600 / 60), s % 60].map((v) => String(v).padStart(2, "0")).join(":"); };

async function loadReview() {
  const [sources, presets] = await Promise.all([api("/api/review/sources"), api("/api/review/presets")]);
  const sel = $("#rv-source"), cur = sel.value;
  sel.innerHTML = sources.sort((a, b) => b.duration_s - a.duration_s)
    .map((s) => `<option value="${esc(s.path)}">${esc(s.name)} · ${hms(s.duration_s)}${s.uploaded ? " · загружено" : ""}</option>`).join("");
  if (cur) sel.value = cur;
  if (!$("#rv-preset").options.length) $("#rv-preset").innerHTML = presets.map((p) => `<option value="${esc(p.id)}">${esc(p.title)}</option>`).join("");
  await refreshJobs();
  if (!RV.job && RV.jobs.length) selectJob(RV.jobs[0].id);
}
async function refreshJobs() {
  RV.jobs = await api("/api/review/jobs");
  $("#rv-jobs").innerHTML = RV.jobs.map((j) => {
    const st = j.status === "done" ? `${j.items} роликов · проверено ${j.reviewed}` : j.status === "error" ? `ошибка: ${esc(j.error || "")}` : `обработка ${Math.round((j.progress || 0) * 100)}%`;
    return `<li data-job="${esc(j.id)}" class="${RV.job === j.id ? "sel" : ""}"><span class="t">${esc(j.title)}</span>
      <span class="m">${esc(j.preset_title)} · ${j.video_s ? hms(j.video_s) : "—"}</span><span class="m">${st}</span></li>`;
  }).join("") || `<li class="empty">Отчётов пока нет.</li>`;
  const job = RV.jobs.find((j) => j.id === RV.job);
  if (job) renderJobHead(job);
  clearTimeout(RV.timer);
  if (state.view === "review" && RV.jobs.some((j) => j.status === "running" || j.status === "queued")) {
    RV.timer = setTimeout(async () => { await refreshJobs(); if (RV.job) await loadItems(true); }, 2000);
  }
}
$("#rv-jobs").addEventListener("click", (e) => { const li = e.target.closest("li[data-job]"); if (li) selectJob(li.dataset.job); });
$("#rv-del").addEventListener("click", async (e) => {
  const btn = e.currentTarget;
  if (btn.dataset.confirm !== RV.job) {             // подтверждение вторым нажатием, без системных диалогов
    btn.dataset.confirm = RV.job; btn.textContent = "Точно удалить?";
    setTimeout(() => { btn.dataset.confirm = ""; btn.textContent = "Удалить"; }, 3000);
    return;
  }
  const r = await fetch(`/api/review/jobs/${encodeURIComponent(RV.job)}`, { method: "DELETE" });
  btn.dataset.confirm = ""; btn.textContent = "Удалить";
  if (!r.ok) { $("#rv-msg").textContent = "Разбор ещё идёт — удалить можно после окончания."; return; }
  RV.job = null; $("#rv-body").hidden = true; $("#rv-empty").hidden = false;
  await refreshJobs();
  if (RV.jobs.length) selectJob(RV.jobs[0].id);
});
async function selectJob(id) {
  RV.job = id; RV.cat = ""; RV.verdict = ""; RV.cur = 0;
  $("#rv-empty").hidden = true; $("#rv-body").hidden = false;
  document.querySelectorAll("#rv-jobs li").forEach((li) => li.classList.toggle("sel", li.dataset.job === id));
  $("#rv-report").href = `/static/report.html?job=${encodeURIComponent(id)}`;
  $("#rv-csv").href = `/api/review/jobs/${encodeURIComponent(id)}/report.csv`;
  const job = RV.jobs.find((j) => j.id === id);
  if (job) renderJobHead(job);
  await loadItems();
}
function renderJobHead(j) {
  $("#rv-h-title").textContent = j.title;
  const speed = j.processed_s && j.video_s && j.status === "done" ? ` · обработана за ${hms(j.processed_s)}, в ${(j.video_s / j.processed_s).toFixed(1)} раза быстрее реального времени` : "";
  $("#rv-h-meta").textContent = `Запись ${j.video_s ? hms(j.video_s) : "—"} · профиль «${j.preset_title}»${speed} · ${j.items} роликов вместо полной записи`;
  const running = j.status === "running" || j.status === "queued";
  $("#rv-progress").hidden = !running;
  $("#rv-bar").style.width = Math.round((j.progress || 0) * 100) + "%";
  $("#rv-ptext").textContent = running ? `обработано ${Math.round((j.progress || 0) * 100)}% · ролики появляются по ходу` : "";
}
async function loadItems(keep) {
  const curId = RV.items[RV.cur]?.id;
  RV.items = await api(`/api/review/jobs/${encodeURIComponent(RV.job)}/items`);
  const cats = {};
  RV.items.forEach((i) => { (cats[i.title] ||= { n: 0, sev: i.severity }).n++; });
  $("#rv-cats").innerHTML = `<button type="button" class="cat" data-cat="" aria-pressed="${!RV.cat}">Все <b>${RV.items.length}</b></button>` +
    Object.entries(cats).sort((a, b) => b[1].n - a[1].n).map(([t, c]) => `<button type="button" class="cat" data-cat="${esc(t)}" aria-pressed="${RV.cat === t}"><span class="dot" style="background:${CAT_COLOR[c.sev]}"></span>${esc(t)} <b>${c.n}</b></button>`).join("");
  $("#rv-people").innerHTML = [...new Set(RV.items.map((i) => i.person).filter(Boolean))].map((p) => `<option value="${esc(p)}">`).join("");
  renderList();
  const idx = keep && curId ? visibleItems().findIndex((i) => i.id === curId) : 0;
  showItem(Math.max(0, idx), keep);
}
function visibleItems() { return RV.items.filter((i) => (!RV.cat || i.title === RV.cat) && (!RV.verdict || i.verdict === RV.verdict)); }
function renderList() {
  const items = visibleItems();
  $("#rv-list").innerHTML = items.map((i, k) => `<li data-k="${k}" class="v-${esc(i.verdict)} ${k === RV.cur ? "cur" : ""}">
    <span class="n">${k + 1}</span><img src="${esc(i.snapshot)}" alt="" loading="lazy">
    <div><div class="t">${esc(i.title)}</div><div class="m">${hms(i.start_s)} · ${Math.round(i.end_s - i.start_s)} с · ${Math.round(i.score * 100)}%</div>
    <div class="m">${sevPill(i.severity)} ${i.verdict === "confirmed" ? "✓ подтверждено" : i.verdict === "rejected" ? "✕ ложное" : ""}${i.person ? " · " + esc(i.person) : ""}</div></div></li>`).join("")
    || `<li class="empty">${RV.items.length ? "Под фильтр ничего не попало." : "Нарушений пока не найдено."}</li>`;
}
$("#rv-list").addEventListener("click", (e) => { const li = e.target.closest("li[data-k]"); if (li) showItem(+li.dataset.k); });
$("#rv-cats").addEventListener("click", (e) => { const b = e.target.closest("[data-cat]"); if (!b) return; RV.cat = b.dataset.cat; document.querySelectorAll("#rv-cats .cat").forEach((c) => c.setAttribute("aria-pressed", String(c === b))); renderList(); showItem(0); });
$("#rv-verdicts").addEventListener("click", (e) => { const b = e.target.closest("[data-v]"); if (!b) return; RV.verdict = b.dataset.v; document.querySelectorAll("#rv-verdicts button").forEach((c) => c.setAttribute("aria-pressed", String(c === b))); renderList(); showItem(0); });
function showItem(k, keepVideo) {
  const items = visibleItems();
  if (!items.length) { $("#rv-video").removeAttribute("src"); $("#rv-i-title").textContent = ""; $("#rv-i-time").textContent = ""; $("#rv-pos").textContent = ""; return; }
  RV.cur = Math.min(Math.max(0, k), items.length - 1);
  const i = items[RV.cur];
  document.querySelectorAll("#rv-list li").forEach((li) => li.classList.toggle("cur", +li.dataset.k === RV.cur));
  $(`#rv-list li[data-k="${RV.cur}"]`)?.scrollIntoView({ block: "nearest" });
  const v = $("#rv-video");
  if (!keepVideo || !v.src.endsWith(i.clip)) { v.src = i.clip; v.play().catch(() => {}); }
  $("#rv-sev").outerHTML = `<span class="pill ${esc(i.severity)}" id="rv-sev">${esc(SEV[i.severity])}</span>`;
  $("#rv-verdict").outerHTML = `<span class="pill ${i.verdict === "confirmed" ? "ok" : i.verdict === "rejected" ? "st-false" : "st-new"}" id="rv-verdict">${i.verdict === "confirmed" ? "подтверждено" : i.verdict === "rejected" ? "ложное" : "не проверено"}</span>`;
  $("#rv-pos").textContent = `${RV.cur + 1} из ${items.length}`;
  $("#rv-i-title").textContent = i.title;
  const det = Object.entries(i.details || {}).map(([a, b]) => `${DETAIL[a] || a}: ${DETAIL[b] || b}`).join(", ");
  $("#rv-i-time").textContent = `${hms(i.start_s)} – ${hms(i.end_s)} в записи · ${Math.round(i.end_s - i.start_s)} с${det ? " · " + det : ""}`;
  $("#rv-conf").style.width = Math.round(i.score * 100) + "%";
  $("#rv-conf-v").textContent = Math.round(i.score * 100) + "%";
  $("#rv-person").value = i.person || "";
}
async function verdict(v) {
  const items = visibleItems(), i = items[RV.cur];
  if (!i) return;
  const upd = await api(`/api/review/items/${encodeURIComponent(i.id)}`, { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ verdict: v, person: $("#rv-person").value }) });
  Object.assign(i, upd);
  const next = RV.verdict ? RV.cur : RV.cur + 1;
  renderList(); showItem(next);
  refreshJobs();
}
$("#rv-ok").addEventListener("click", () => verdict("confirmed"));
$("#rv-no").addEventListener("click", () => verdict("rejected"));
$("#rv-prev").addEventListener("click", () => showItem(RV.cur - 1));
$("#rv-next").addEventListener("click", () => showItem(RV.cur + 1));
document.addEventListener("keydown", (e) => {
  if (state.view !== "review" || !RV.job || e.target.matches("input, select, textarea") || !$("#modal").hidden) return;
  const k = e.key.toLowerCase();
  if (k === "a" || k === "ф") { e.preventDefault(); verdict("confirmed"); }
  else if (k === "x" || k === "ч") { e.preventDefault(); verdict("rejected"); }
  else if (k === "arrowdown" || k === "j") { e.preventDefault(); showItem(RV.cur + 1); }
  else if (k === "arrowup" || k === "k") { e.preventDefault(); showItem(RV.cur - 1); }
});
$("#rv-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  let source = $("#rv-source").value;
  const file = $("#rv-file").files[0];
  try {
    if (file) {
      $("#rv-msg").textContent = `Загружаю ${file.name} (${Math.round(file.size / 1e6)} МБ)…`;
      const up = await fetch(`/api/review/upload?name=${encodeURIComponent(file.name)}`, { method: "POST", body: file }).then((r) => r.json());
      source = up.path;
    }
    const { id } = await api("/api/review/jobs", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ source, preset: $("#rv-preset").value, title: $("#rv-title").value || null }) });
    $("#rv-msg").textContent = "Разбор запущен. Ролики появятся по ходу обработки.";
    $("#rv-file").value = "";
    await refreshJobs(); selectJob(id); loadReview();
  } catch (err) {
    $("#rv-msg").textContent = "Не получилось запустить разбор. Проверьте файл и попробуйте ещё раз.";
  }
});

/* ---------- LinQ ---------- */
function quantCells(q) {
  if (!q) return `<td class="num muted">проверка идёт</td><td class="num">—</td><td class="num">—</td><td class="num">—</td>`;
  const pct = (v) => (v == null ? "—" : Math.round(v * 100) + "%");
  return `<td class="num">${pct(q.recall_vs_fp32)}</td><td class="num">${q.precision_vs_fp32 == null ? "—" : Math.round((1 - q.precision_vs_fp32) * 100) + "%"}</td>
    <td class="num">${q.mean_iou ?? "—"}</td><td class="num">${q.keypoint_err_heights == null ? "—" : (q.keypoint_err_heights * 100).toFixed(1)}</td>`;
}
async function loadLinq() {
  const m = await api("/api/metrics");
  $("#lq-backend").textContent = `Сейчас модели работают через ${m.gpu_backend}. На стенде LinQ тот же код переключается на бэкенд linq-tpu.`;
  const ms = (o, k) => (o && o[k] != null ? o[k] : "—");
  $("#lq-models").innerHTML = m.models.map((d) => `<tr><td>${esc(d.model)}</td><td class="mono">${esc(d.backend)}</td>
    <td class="num">${ms(d.pre, "p50")}</td><td class="num">${ms(d.infer, "p50")}</td><td class="num">${ms(d.infer, "p95")}</td><td class="num">${ms(d.post, "p50")}</td></tr>`).join("");
  const b = await api("/api/bench");
  const names = [...new Set(Object.values(b).flatMap((x) => Object.keys(x.models || {})))];
  const cell = (plat, n) => {
    const m = b[plat]?.models?.[n];
    if (!m || m.error) return `<td class="num muted">—</td><td class="num muted">—</td>`;
    const fps = m.fps_by_threads ? Math.max(...Object.values(m.fps_by_threads)) : null;
    return `<td class="num">${m.p50_ms}</td><td class="num">${fps ?? "—"}</td>`;
  };
  $("#lq-bench").innerHTML = names.map((n) => `<tr><td>${esc(n)}</td>${cell("tpu", n)}${cell("cuda", n)}${cell("cpu", n)}</tr>`).join("")
    || `<tr><td colspan="7" class="empty">Замеров ещё нет.</td></tr>`;
  $("#lq-bench-note").textContent = [
    b.tpu ? `LinQ: ${b.tpu.at}` : "LinQ TPU: замер на стенде после подключения VPN.",
    b.cuda ? `GPU: ${b.cuda.device || "CUDA"}, ${b.cuda.at}` : "",
    b.cpu ? `CPU: ${b.cpu.host}, ${b.cpu.at}` : "",
    "Задержка — p50 одного кадра; кадров/с — лучшая пропускная способность при 1–4 параллельных потоках. GPU и CPU мерились на том же сервере, что обслуживает живые камеры, — цифры немного занижены.",
  ].filter(Boolean).join(" · ");
  const progs = await api("/api/linq");
  $("#lq-programs").innerHTML = progs.length ? progs.map((p) => `<tr><td>${esc(p.name)}</td><td class="num">${p.head_nodes ?? "—"}</td>
    <td class="num">${p.tail_nodes ?? "—"}</td><td class="num">${p.calib_frames ?? "—"}</td>
    <td>${p.preset ? `<span class="pill ok">${esc(p.preset.toUpperCase())}</span>` : `<span class="pill high">не собрано</span>`}</td>
    <td class="num">${p.program_mb ?? "—"}</td><td class="num">${p.compile_s ?? "—"}</td>
    ${quantCells(p.quant_check)}
    <td class="mono small">${esc((p.program || "").split("/").pop())}</td></tr>`).join("")
    : `<tr><td colspan="12" class="empty">Компиляция идёт или ещё не запускалась.</td></tr>`;
  $("#lq-cams").innerHTML = m.cameras.map((c) => `<tr><td>${esc(c.title)}</td><td class="num">${c.fps}</td><td class="num">${c.proc_p50 ?? "—"}</td><td class="num">${c.proc_p95 ?? "—"}</td><td class="num">${c.active}</td></tr>`).join("");
}

/* ---------- старт ---------- */
(async function init() {
  try {
    state.rules = await api("/api/rules");
  } catch {                                    // сервис перезапускается — пробуем снова, не оставляя пустой экран
    setTimeout(init, 2000);
    return;
  }
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
