const $ = (s) => document.querySelector(s);
const state = { mode: 'tpu', conf: 0.3, video: null, statsTimer: null, seen: new Set(), audio: null };
const SKELETON = [[15, 13], [13, 11], [16, 14], [14, 12], [11, 12], [5, 11], [6, 12], [5, 6], [5, 7],
  [6, 8], [7, 9], [8, 10], [1, 2], [0, 1], [0, 2], [1, 3], [2, 4], [3, 5], [4, 6]];
const poseOn = () => ($('#pose').checked ? 1 : 0);
$('#pose').onchange = () => state.video && startVideo(state.video);
const PALETTE = {};
const colorFor = (name) => PALETTE[name] ||= name === 'person' ? '#ff5d5d' : `hsl(${[...name].reduce((a, c) => a + c.charCodeAt(0) * 37, 0) % 360} 75% 60%)`;

// ---------- вкладки ----------
document.querySelectorAll('.tab').forEach((t) => t.onclick = () => {
  document.querySelectorAll('.tab').forEach((x) => x.classList.toggle('active', x === t));
  document.querySelectorAll('.panel').forEach((p) => p.classList.toggle('active', p.id === t.dataset.tab));
});

// ---------- настройки ----------
document.querySelectorAll('#mode button').forEach((b) => b.onclick = () => {
  state.mode = b.dataset.mode;
  document.querySelectorAll('#mode button').forEach((x) => x.classList.toggle('on', x === b));
  if (state.video) startVideo(state.video);
});
$('#conf').oninput = (e) => { state.conf = +e.target.value; $('#confv').textContent = state.conf.toFixed(2); };

// ---------- статус ----------
async function refreshStatus() {
  const el = $('#status');
  try {
    const s = await (await fetch('/api/status')).json();
    el.className = 'status ' + (s.tpu.ok ? 'ok' : 'bad');
    el.lastElementChild.textContent = s.tpu.ok ? `TPU relay онлайн · ${s.model}` : `TPU недоступен: ${s.tpu.error}`;
    $('#modelinfo').textContent = `модель ${s.model}: вход ${s.meta.imgsz}×${s.meta.imgsz}, ${s.meta.num_classes} классов COCO, программа ${s.meta.program}`;
    $('#tpulive').innerHTML = [
      ['Relay', s.tpu.ok ? `онлайн, pid ${s.tpu.pid}, аптайм ${s.tpu.uptime} с, в очереди ${s.tpu.pending}` : s.tpu.error],
      ['Модель', `${s.model} → <code>${s.meta.program}</code>`],
      ['Выходы на TPU', s.meta.outputs.map((o, i) => `<code>${o}</code> ${JSON.stringify(s.meta.output_shapes[i])}`).join('<br>')],
      ['На CPU', 'DFL (softmax 16 бинов), sigmoid классов, NMS'],
    ].map(([k, v]) => `<div><span>${k}</span><b>${v}</b></div>`).join('');
  } catch (e) {
    el.className = 'status bad'; el.lastElementChild.textContent = 'сервер сайта недоступен';
  }
}
refreshStatus(); setInterval(refreshStatus, 15000);

// ---------- картинки ----------
function drawDetections(canvas, img, dets) {
  canvas.width = img.naturalWidth; canvas.height = img.naturalHeight;
  const ctx = canvas.getContext('2d');
  ctx.drawImage(img, 0, 0);
  const lw = Math.max(2, Math.round(canvas.width / 400)), fs = Math.max(12, Math.round(canvas.width / 60));
  ctx.font = `600 ${fs}px system-ui`;
  for (const d of dets) {
    if (d.kpts) {
      const p = d.kpts.map(([x, y, v]) => (v > 0.5 ? [x, y] : null));
      ctx.strokeStyle = '#50ffaa'; ctx.lineWidth = lw + 1;
      for (const [a, b] of SKELETON) if (p[a] && p[b]) { ctx.beginPath(); ctx.moveTo(...p[a]); ctx.lineTo(...p[b]); ctx.stroke(); }
      p.forEach((q, i) => { if (q) { ctx.fillStyle = [0, 9, 10].includes(i) ? '#ffc800' : '#fff'; ctx.beginPath(); ctx.arc(q[0], q[1], lw * 2, 0, 7); ctx.fill(); } });
    }
    const [x1, y1, x2, y2] = d.box, c = colorFor(d.name);
    ctx.strokeStyle = c; ctx.lineWidth = lw; ctx.strokeRect(x1, y1, x2 - x1, y2 - y1);
    const label = `${d.name} ${d.conf.toFixed(2)}`, tw = ctx.measureText(label).width + 8;
    ctx.fillStyle = c; ctx.fillRect(x1, Math.max(0, y1 - fs - 6), tw, fs + 6);
    ctx.fillStyle = '#000'; ctx.fillText(label, x1 + 4, Math.max(fs, y1 - 4));
  }
}

const chipsHtml = (pairs) => pairs.map(([k, v]) => `<span class="chip">${k} × ${v}</span>`).join('');
const evHtml = (evs) => evs.map((e) => `<span class="ev ${e.level}">п.${e.code} ${e.title}</span>`).join('');

async function detectFile(file) {
  const node = $('#imgcard').content.firstElementChild.cloneNode(true);
  const canvas = node.querySelector('canvas');
  node.querySelector('.fname').textContent = file.name;
  node.querySelector('.loading').textContent = state.mode === 'tpu' ? 'LinQ TPU…' : 'CPU…';
  $('#imgresults').prepend(node);
  const img = new Image();
  img.src = URL.createObjectURL(file);
  await img.decode();
  drawDetections(canvas, img, []);
  const fd = new FormData(); fd.append('file', file);
  try {
    const r = await fetch(`/api/detect?mode=${state.mode}&conf=${state.conf}&pose=${poseOn()}`, { method: 'POST', body: fd });
    const res = await r.json();
    if (!r.ok) throw new Error(res.detail || r.statusText);
    drawDetections(canvas, img, res.detections);
    const t = res.timing;
    node.querySelector('.timing').innerHTML = state.mode === 'tpu'
      ? `<b>${t.roundtrip_ms} мс</b> всего · TPU ${t.tpu_ms} мс · препроц. ${t.pre_ms} · постпроц. ${t.post_ms} · отправлено ${res.upload_kb} КБ`
      : `<b>${t.roundtrip_ms} мс</b> всего · CPU ${t.cpu_ms} мс · препроц. ${t.pre_ms} · постпроц. ${t.post_ms}`;
    const counts = {};
    res.detections.forEach((d) => counts[d.name] = (counts[d.name] || 0) + 1);
    node.querySelector('.chips').innerHTML = chipsHtml(Object.entries(counts)) || '<span class="muted">ничего не найдено</span>';
    node.querySelector('.evs').innerHTML = evHtml(res.events);
  } catch (e) {
    node.querySelector('.timing').innerHTML = `<span class="err">${e.message}</span>`;
  } finally {
    node.querySelector('.loading').remove();
  }
}

function setupDrop(label, input, onFiles) {
  input.onchange = () => { onFiles([...input.files]); input.value = ''; };
  label.ondragover = (e) => { e.preventDefault(); label.classList.add('over'); };
  label.ondragleave = () => label.classList.remove('over');
  label.ondrop = (e) => { e.preventDefault(); label.classList.remove('over'); onFiles([...e.dataTransfer.files]); };
}
setupDrop($('#imgdrop'), $('#imgfile'), (files) => files.filter((f) => f.type.startsWith('image/')).forEach(detectFile));
setupDrop($('#viddrop'), $('#vidfile'), async (files) => {
  const f = files.find((x) => x.type.startsWith('video/') || /\.(mp4|avi|mov|mkv)$/i.test(x.name));
  if (!f) return;
  const fd = new FormData(); fd.append('file', f);
  $('#vidtitle').textContent = `Загрузка ${f.name}…`; $('#videocard').hidden = false;
  const info = await (await fetch('/api/video', { method: 'POST', body: fd })).json();
  startVideo(info);
});

// ---------- видео ----------
function startVideo(info) {
  stopVideo(false);
  state.video = info;
  state.seen = new Set();
  try { state.audio ||= new AudioContext(); state.audio.resume(); } catch (err) { /* без звука */ }
  $('#videocard').hidden = false;
  $('#vidtitle').textContent = 'webcam' in info
    ? `${info.name} · ${$('#fps').value} к/с на ${state.mode.toUpperCase()}`
    : `${info.name} · ${info.width}×${info.height}, ${info.fps} к/с, ${info.frames} кадров`;
  $('#s_tpu_l').textContent = state.mode === 'tpu' ? 'мс на TPU (из relay)' : 'мс CPU-инференса';
  $('#s_events').innerHTML = '<li class="muted">пока нет</li>'; $('#s_classes').innerHTML = ''; $('#s_err').textContent = '';
  $('#vidstream').src = `/api/video/${info.id}/stream?mode=${state.mode}&fps=${$('#fps').value}&conf=${state.conf}&pose=${poseOn()}&_=${Date.now()}`;
  state.statsTimer = setInterval(pollStats, 1000);
  $('#videocard').scrollIntoView({ behavior: 'smooth', block: 'start' });
}
function stopVideo(clear = true) {
  clearInterval(state.statsTimer);
  if (state.video) fetch(`/api/video/${state.video.id}/stop`, { method: 'POST' });
  $('#vidstream').removeAttribute('src');
  if (clear) state.video = null;
}
async function pollStats() {
  if (!state.video) return;
  const s = await (await fetch(`/api/video/${state.video.id}/stats`)).json();
  $('#s_fps').textContent = s.fps ?? '–';
  $('#s_lat').textContent = s.latency_ms ?? '–';
  $('#s_tpu').textContent = s.tpu_ms ?? '–';
  $('#s_n').textContent = s.processed ?? 0;
  $('#s_classes').innerHTML = chipsHtml(s.classes || []);
  if (s.events?.length) {
    $('#s_events').innerHTML = s.events.map((e) => `<li class="${e.level}">` +
      (e.snap ? `<a href="/api/snap/${e.snap}" target="_blank"><img src="/api/snap/${e.snap}" alt=""></a>` : '') +
      `<span>${e.time || ''} · п.${e.code} ${e.title}</span></li>`).join('');
    for (const e of [...s.events].reverse()) {
      const key = `${e.code}@${e.t}`;
      if (!state.seen.has(key)) { state.seen.add(key); alarm(e); }
    }
  }
  $('#s_err').textContent = s.errors ? `ошибок: ${s.errors} (${s.last_error})` : '';
  if (s.done) clearInterval(state.statsTimer);
}
// ---------- оповещения: карточка с кадром, камерой, временем и типом + звук ----------
function beep(critical) {
  try {
    state.audio ||= new AudioContext();
    const ctx = state.audio;
    [0, 0.3].concat(critical ? [0.6] : []).forEach((dt) => {
      const o = ctx.createOscillator(), g = ctx.createGain();
      o.frequency.value = critical ? 990 : 740; o.type = 'square';
      g.gain.setValueAtTime(0.08, ctx.currentTime + dt); g.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + dt + 0.22);
      o.connect(g).connect(ctx.destination); o.start(ctx.currentTime + dt); o.stop(ctx.currentTime + dt + 0.25);
    });
  } catch (err) { /* звук не обязателен */ }
}
function alarm(e) {
  const critical = e.level === 'critical';
  const el = document.createElement('div');
  el.className = 'toast ' + (critical ? '' : 'warn');
  el.innerHTML = `<header><span>${critical ? '⛔ КРИТИЧНО' : '⚠ НАРУШЕНИЕ'} · п.${e.code}</span><button title="закрыть">×</button></header>
    <div class="body"><b>${e.title}</b>${e.camera || ''} · ${e.time || ''} (t=${e.t} с)</div>
    ${e.snap ? `<img src="/api/snap/${e.snap}" alt="кадр нарушения">` : ''}`;
  el.querySelector('button').onclick = () => el.remove();
  $('#toasts').prepend(el);
  setTimeout(() => el.remove(), critical ? 20000 : 10000);
  const wrap = document.querySelector('.videowrap');
  wrap.classList.remove('alarm'); void wrap.offsetWidth; wrap.classList.add('alarm');
  beep(critical);
}

$('#vidstop').onclick = () => stopVideo(false);
$('#webcam').onclick = async () => startVideo(await (await fetch('/api/video/webcam', { method: 'POST' })).json());
$('#vidrestart').onclick = () => state.video && startVideo(state.video);

// ---------- примеры ----------
(async () => {
  const s = await (await fetch('/api/samples')).json();
  $('#imgsamples').innerHTML = s.images.map((n) => `<img src="/static/samples/${n}" title="${n}" data-n="${n}">`).join('');
  $('#imgsamples').onclick = async (e) => {
    const n = e.target.dataset.n; if (!n) return;
    const blob = await (await fetch(`/static/samples/${n}`)).blob();
    detectFile(new File([blob], n, { type: blob.type }));
  };
  $('#vidsamples').innerHTML = s.videos.map((n) => `<button data-n="${n}">▶ ${n}</button>`).join('');
  $('#vidsamples').onclick = async (e) => {
    const n = e.target.dataset.n; if (!n) return;
    startVideo(await (await fetch(`/api/video/sample?name=${encodeURIComponent(n)}`, { method: 'POST' })).json());
  };
})();

// ---------- материалы организаторов ----------
(async () => {
  const groups = await (await fetch('/api/org')).json();
  const icon = { pdf: '📄', notebook: '📓', image: '🖼', code: '⌨' };
  const enc = (p) => p.split('/').map(encodeURIComponent).join('/');
  $('#orglist').innerHTML = groups.map((g) => `<h4>${g.title}</h4>` + g.files.map((f) =>
    `<a href="/org/${enc(f.path)}" data-p="${f.path}">${icon[f.kind] || ''} ${f.name}<small>${f.kb} КБ</small></a>`).join('')).join('');
  const open = (a) => {
    document.querySelectorAll('#orglist a').forEach((x) => x.classList.toggle('on', x === a));
    $('#orgframe').src = a.getAttribute('href');
  };
  $('#orglist').onclick = (e) => { const a = e.target.closest('a'); if (a) { e.preventDefault(); open(a); } };
  const first = [...document.querySelectorAll('#orglist a')].find((a) => /LinQ\.pdf$/.test(a.dataset.p));
  if (first) open(first);
})();

// ---------- замеры ----------
document.querySelectorAll('[data-bench]').forEach((b) => b.onclick = async () => {
  const [mode, inflight] = b.dataset.bench.split(',');
  document.querySelectorAll('[data-bench]').forEach((x) => x.disabled = true);
  $('#benchout').innerHTML = `<span class="muted">замер ${mode.toUpperCase()}, ${inflight} в полёте…</span>`;
  try {
    const r = await (await fetch(`/api/bench?mode=${mode}&n=${mode === 'cpu' ? 10 : 24}&inflight=${inflight}`)).json();
    const inner = mode === 'tpu' ? 'relay → TPU → relay' : 'onnxruntime CPU';
    $('#benchout').innerHTML = `<table>
      <tr><th>режим</th><td>${mode.toUpperCase()}, ${r.inflight} одновременно, ${r.ok}/${r.n} ок</td></tr>
      <tr><th>пропускная способность</th><td><b>${r.fps} FPS</b></td></tr>
      <tr><th>задержка (ноутбук → ответ)</th><td>p50 ${r.latency_ms.p50} · p90 ${r.latency_ms.p90} · min ${r.latency_ms.min} мс</td></tr>
      <tr><th>${inner}</th><td>p50 ${r.inner_ms.p50} · p90 ${r.inner_ms.p90} · min ${r.inner_ms.min} мс</td></tr>
      <tr><th>JPEG на кадр</th><td>${r.upload_kb} КБ</td></tr>
      ${r.errors.length ? `<tr><th>ошибки</th><td class="err">${r.errors.join('<br>')}</td></tr>` : ''}
    </table>`;
  } catch (e) {
    $('#benchout').innerHTML = `<span class="err">${e.message}</span>`;
  } finally {
    document.querySelectorAll('[data-bench]').forEach((x) => x.disabled = false);
  }
});
