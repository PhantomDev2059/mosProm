/*
 * props.js : общий реквизит фильма «Смена» (LinQ Guard).
 *
 * Прелюдия после lib.js: шрифты проекта, кадры камер, карточки бота и повторяющиеся элементы —
 * монитор камеры, тушевая рамка нарушителя, плашка, часы смены, полоса смены внизу кадра.
 * Данные смены — FILM.SHIFT из photos.js (генерирует tools/shift_data.py из настоящего прогона конвейера).
 *
 * Кадры камер грузит рендер-драйвер (tools/vrender.cjs) через window.__prep(T) перед каждым кадром:
 * сцена берёт уже декодированную картинку и ничего не ждёт, поэтому кадр детерминирован.
 */
(function () {
  'use strict';
  const FILM = window.FILM;
  const L = FILM.lib;
  const K = (FILM.K = {});

  // ---- палитра фильма (как у бота, мини-аппа и презентации)
  K.C = {
    navy: '#0B1230', navyDeep: '#070B20', navy2: '#16224A', grid: '#2B3B6E',
    paper: '#EFE7D6', paper2: '#F7F2E7', ink: '#141A2E', ink2: '#3E4352', muted: '#6B6F7C',
    chalk: '#EEF1F8', pale: '#A7B2CC', cyan: '#7FD0E8',
    yellow: '#F2C230', orange: '#F08A24', red: '#E5484D', green: '#3FAE7A', greenLight: '#4FBF8A',
  };
  K.SEV = {
    critical: { color: '#E5484D', label: 'КРИТИЧНО' },
    high: { color: '#F08A24', label: 'ВЫСОКИЙ РИСК' },
    medium: { color: '#E0A91F', label: 'НАРУШЕНИЕ' },
  };

  // ---- шрифты: файлы проекта (OFL), грузятся до первого кадра
  const FACES = [
    ['LG Disp', 'FiraSansExtraCondensed-ExtraBold.ttf', '800'],
    ['LG Disp', 'FiraSansExtraCondensed-Bold.ttf', '700'],
    ['LG Body', 'GolosText-VF.ttf', '400 700'],
    ['LG Mono', 'JetBrainsMono-VF.ttf', '400 700'],
    ['LG Hand', 'Caveat-VF.ttf', '400 700'],
  ];
  const FAM = { disp: 'LG Disp', body: 'LG Body', mono: 'LG Mono', hand: 'LG Hand' };
  K.font = (kind, size, weight) => `${weight || (kind === 'disp' ? 800 : kind === 'mono' ? 500 : 500)} ${size}px "${FAM[kind]}"`;

  // текст: kind disp|body|mono|hand, size, weight, color, align, baseline, alpha, track (px), p (печать 0..1)
  K.text = (ctx, str, x, y, o = {}) => {
    let s = String(str);
    if (o.p != null) s = s.slice(0, Math.round(s.length * L.clamp(o.p)));
    if (!s) return 0;
    ctx.save();
    ctx.font = K.font(o.kind || 'body', o.size || 32, o.weight);
    ctx.fillStyle = o.color || K.C.ink;
    ctx.globalAlpha *= o.alpha != null ? o.alpha : 1;
    ctx.textAlign = o.align || 'left';
    ctx.textBaseline = o.baseline || 'alphabetic';
    ctx.letterSpacing = `${o.track || 0}px`;
    ctx.fillText(s, x, y);
    const w = ctx.measureText(s).width;
    ctx.restore();
    return w;
  };
  K.measure = (ctx, str, o = {}) => {
    ctx.save();
    ctx.font = K.font(o.kind || 'body', o.size || 32, o.weight);
    ctx.letterSpacing = `${o.track || 0}px`;
    const w = ctx.measureText(String(str)).width;
    ctx.restore();
    return w;
  };

  // ---- картинки (карточки бота) и кадры камер
  const imgs = {};
  const frames = new Map(); // `${shot}|${idx}` -> Image
  K.img = (id) => imgs[id] || null;

  const prevLoad = FILM.loadPhotos;
  let loading = null;
  FILM.loadPhotos = function () {
    if (loading) return loading;
    const S = FILM.SHIFT || {};
    const faces = FACES.map(([fam, file, weight]) => {
      const f = new FontFace(fam, `url("file://${S.fontsDir}/${file}")`, { weight });
      document.fonts.add(f);
      return f.load();
    });
    const pics = Object.keys(S.images || {}).map((id) => {
      const im = new Image();
      im.src = 'file://' + S.images[id];
      return im.decode().then(() => (imgs[id] = im));
    });
    loading = Promise.all([prevLoad ? prevLoad() : 0, ...faces, ...pics]).then(() => document.fonts.ready).then(() => pics.length);
    return loading;
  };

  // какой кадр ролика нужен шоту в момент T (кадры вырезаны ровно под окно шота, 25 к/с)
  function frameIdx(shot, T) {
    const v = shot.video;
    const t = L.clamp(T - shot.start + (v.lead || 0), 0, v.n / v.fps - 1e-6);
    return Math.min(v.n - 1, Math.max(0, Math.floor(t * v.fps + 1e-6)));
  }
  function framePath(shot, i) {
    return `${FILM.SHIFT.framesDir}/${shot.id}/f${String(i + 1).padStart(5, '0')}.jpg`;
  }
  // драйвер: загрузить кадры всех шотов, видимых в момент T (с запасом на переход)
  window.__prep = async function (T) {
    const shots = FILM.prepare().shots;
    const need = new Set();
    const jobs = [];
    for (const s of shots) {
      if (!s.video) continue;
      const tr = s.transitionIn ? s.transitionIn.dur : 0;
      const next = shots[s.index + 1];
      const tail = next && next.transitionIn ? next.transitionIn.dur : 0;
      if (T < s.start - tr - 0.05 || T > s.end + tail + 0.05) continue;
      const i = frameIdx(s, T);
      const key = `${s.id}|${i}`;
      need.add(key);
      if (!frames.has(key)) {
        const im = new Image();
        im.src = 'file://' + framePath(s, i);
        jobs.push(im.decode().then(() => frames.set(key, im)));
      }
    }
    await Promise.all(jobs);
    for (const k of [...frames.keys()]) if (!need.has(k)) frames.delete(k);
    return need.size;
  };
  K.vframe = (shot, T) => (shot.video ? frames.get(`${shot.id}|${frameIdx(shot, T)}`) || null : null);

  // ---- мелочи
  K.sd = (...k) => L.hash('linq-shift', ...k) & 0x7fffffff;
  K.hm = (min) => {
    const m = Math.round(min);
    const h = 8 + Math.floor(m / 60);
    return `${String(h).padStart(2, '0')}:${String(m % 60).padStart(2, '0')}`;
  };
  K.hms = (sec) => {
    const s = Math.floor(sec);
    const h = 8 + Math.floor(s / 3600);
    return `${String(h).padStart(2, '0')}:${String(Math.floor(s / 60) % 60).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`;
  };
  K.rrect = (ctx, x, y, w, h, r) => {
    ctx.beginPath();
    ctx.moveTo(x + r, y);
    ctx.arcTo(x + w, y, x + w, y + h, r);
    ctx.arcTo(x + w, y + h, x, y + h, r);
    ctx.arcTo(x, y + h, x, y, r);
    ctx.arcTo(x, y, x + w, y, r);
    ctx.closePath();
  };
  // тушевой прямоугольник (рамка), draw — прорисовка 0..1
  K.inkRect = (ctx, x, y, w, h, o = {}) =>
    L.inkPath(ctx, [[x, y], [x + w, y], [x + w, y + h], [x, y + h]], Object.assign({ closed: true, smooth: false, width: 3, wobble: 1.2, tremble: 0.35, taper: [6, 10] }, o));

  // ---- плиты
  K.paperPlate = (ctx, T, o = {}) => {
    L.paper(ctx, { color: '#EFE6D2', vignette: 0.25, seed: 3 });
    ctx.save();
    ctx.globalAlpha = 0.5;
    L.stripes(ctx, { colors: [o.c1 || '#F1E7D2', o.c2 || '#F2D9A0'], width: 150, angle: -0.52, offset: T * 18, seed: 21 });
    ctx.restore();
  };
  K.navyPlate = (ctx, T, o = {}) => {
    L.blueprint(ctx, { w: FILM.W, h: FILM.H, color: K.C.navy, grid: 48, major: 5, center: o.center || [FILM.W * 0.62, FILM.H * 0.48], circles: 4, diagonals: 4, seed: o.seed || 5, line: '#C8C1EF' });
  };

  // ---- монитор камеры: кадр, тушевая рамка, плашка камеры, REC и часы камеры
  // o: shot, T, t, x, y, w, h, dark (плита тёмная), label, clock (сек от 08:00), flash 0..1, draw 0..1
  K.monitor = (ctx, o) => {
    const { x, y, w, h } = o;
    const dark = !!o.dark;
    const draw = o.draw == null ? 1 : o.draw;
    ctx.save();
    // тень-подложка
    ctx.fillStyle = dark ? 'rgba(0,0,0,0.45)' : 'rgba(20,26,46,0.22)';
    ctx.fillRect(x + 10, y + 14, w, h);
    ctx.fillStyle = '#05070F';
    ctx.fillRect(x, y, w, h);
    const im = K.vframe(o.shot, o.T);
    if (im) {
      ctx.globalAlpha = L.clamp(draw * 1.6);
      ctx.drawImage(im, x, y, w, h);
      ctx.globalAlpha = 1;
    }
    // вспышка тревоги по краю кадра
    if (o.flash > 0) {
      ctx.strokeStyle = L.rgba(K.C.red, 0.85 * o.flash);
      ctx.lineWidth = 10;
      ctx.strokeRect(x + 5, y + 5, w - 10, h - 10);
    }
    // верхняя плашка: камера и время
    const bar = 46;
    const g = ctx.createLinearGradient(0, y, 0, y + bar + 30);
    g.addColorStop(0, 'rgba(5,7,15,0.78)');
    g.addColorStop(1, 'rgba(5,7,15,0)');
    ctx.fillStyle = g;
    ctx.fillRect(x, y, w, bar + 30);
    const blink = Math.floor(o.T * 2 + 1e-6) % 2 === 0;
    ctx.fillStyle = blink ? K.C.red : 'rgba(229,72,77,0.35)';
    ctx.beginPath();
    ctx.arc(x + 26, y + 26, 7, 0, L.TAU);
    ctx.fill();
    K.text(ctx, 'REC', x + 42, y + 33, { kind: 'mono', size: 20, weight: 700, color: '#FFFFFF', track: 2 });
    K.text(ctx, o.label || '', x + 104, y + 33, { kind: 'mono', size: 20, weight: 600, color: '#FFFFFF', track: 1.5 });
    if (o.clock != null) K.text(ctx, K.hms(o.clock), x + w - 20, y + 33, { kind: 'mono', size: 20, weight: 600, color: '#FFFFFF', align: 'right', track: 1 });
    ctx.restore();
    // тушевая рамка поверх
    const col = dark ? K.C.chalk : K.C.ink;
    K.inkRect(ctx, x - 6, y - 6, w + 12, h + 12, { color: col, width: 3.2, seed: K.sd('mon', o.shot.id), draw });
    // уголки-визиры
    const m = 22, s = 34;
    const corners = [[x - m, y - m, 1, 1], [x + w + m, y - m, -1, 1], [x - m, y + h + m, 1, -1], [x + w + m, y + h + m, -1, -1]];
    corners.forEach(([cx, cy, sx, sy], i) =>
      L.inkPath(ctx, [[cx, cy + sy * s], [cx, cy], [cx + sx * s, cy]], { color: dark ? K.C.cyan : K.C.orange, width: 2.6, smooth: false, seed: K.sd('vis', o.shot.id, i), taper: [4, 8], draw: L.clamp(draw * 1.4 - 0.2) })
    );
  };

  // ---- рамка нарушителя в экранных координатах: тушью, с плашкой нарушения
  // o: color, label, p (появление 0..1), confirm (0..1 — кольцо подтверждения), alarm (тревога открыта), seed
  K.violator = (ctx, bx, o) => {
    const [x1, y1, x2, y2] = bx;
    const w = x2 - x1, h = y2 - y1;
    const p = o.p == null ? 1 : o.p;
    if (p <= 0) return;
    const col = o.color;
    ctx.save();
    ctx.globalAlpha *= L.clamp(p * 1.5);
    // мягкая подсветка внутри
    ctx.fillStyle = L.rgba(col, o.alarm ? 0.14 : 0.08);
    ctx.fillRect(x1, y1, w, h);
    // рамка
    K.inkRect(ctx, x1, y1, w, h, { color: col, width: o.alarm ? 4.2 : 3, seed: o.seed, draw: L.clamp(p * 1.2), wobble: 1, tremble: 0.3 });
    // уголки
    const c = Math.min(28, w * 0.25, h * 0.25);
    [[x1, y1, 1, 1], [x2, y1, -1, 1], [x1, y2, 1, -1], [x2, y2, -1, -1]].forEach(([cx, cy, sx, sy], i) =>
      L.inkPath(ctx, [[cx - sx * 6, cy + sy * c], [cx - sx * 6, cy - sy * 6], [cx + sx * c, cy - sy * 6]], { color: col, width: 5, smooth: false, seed: o.seed + i + 7, taper: [3, 6] })
    );
    ctx.restore();
    // плашка над рамкой
    if (o.label && p > 0.3) {
      const size = 30;
      const tw = K.measure(ctx, o.label, { kind: 'disp', size, weight: 800, track: 1 });
      const ring = o.confirm != null && !o.alarm;
      const bw = tw + 28 + (ring ? 46 : 0);
      const bh = 46;
      let lx = x1 - 6, ly = y1 - bh - 14;
      if (ly < (o.minY || 0)) ly = y2 + 14;
      if (o.maxX && lx + bw > o.maxX) lx = o.maxX - bw;
      ctx.save();
      ctx.globalAlpha *= L.clamp((p - 0.3) * 3);
      ctx.fillStyle = o.alarm ? col : 'rgba(10,14,30,0.82)';
      K.rrect(ctx, lx, ly, bw, bh, 6);
      ctx.fill();
      if (!o.alarm) {
        ctx.strokeStyle = col;
        ctx.lineWidth = 2.5;
        ctx.stroke();
      }
      K.text(ctx, o.label, lx + 14, ly + 35, { kind: 'disp', size, weight: 800, color: '#FFFFFF', track: 1 });
      if (ring) {
        const cx = lx + bw - 26, cy = ly + bh / 2;
        ctx.strokeStyle = 'rgba(255,255,255,0.25)';
        ctx.lineWidth = 4;
        ctx.beginPath();
        ctx.arc(cx, cy, 13, 0, L.TAU);
        ctx.stroke();
        ctx.strokeStyle = col;
        ctx.beginPath();
        ctx.arc(cx, cy, 13, -Math.PI / 2, -Math.PI / 2 + L.TAU * L.clamp(o.confirm));
        ctx.stroke();
      }
      ctx.restore();
    }
  };

  // ---- скелет позы (17 точек COCO), как его видит модель: kp — 17×[x, y, уверенность] в экранных координатах
  const LIMBS = [[5, 7], [7, 9], [6, 8], [8, 10], [5, 6], [5, 11], [6, 12], [11, 12], [11, 13], [13, 15], [12, 14], [14, 16]];
  K.skeleton = (ctx, kp, o = {}) => {
    const ok = (j) => kp[j] && kp[j][2] >= 0.3;
    ctx.save();
    ctx.globalAlpha *= o.alpha == null ? 1 : o.alpha;
    ctx.lineCap = 'round';
    ctx.lineJoin = 'round';
    // подложка — чтобы линия читалась на любом фоне кадра
    for (const pass of [0, 1]) {
      ctx.strokeStyle = pass ? o.color : 'rgba(5,7,15,0.55)';
      ctx.lineWidth = pass ? o.width || 3 : (o.width || 3) + 3;
      ctx.beginPath();
      for (const [a, b] of LIMBS) {
        if (!ok(a) || !ok(b)) continue;
        ctx.moveTo(kp[a][0], kp[a][1]);
        ctx.lineTo(kp[b][0], kp[b][1]);
      }
      ctx.stroke();
    }
    const r = o.joint || 4;
    for (let j = 0; j < 17; j++) {
      if (!ok(j) || (j > 0 && j < 5)) continue; // лицо — одной точкой носа
      ctx.fillStyle = '#FFFFFF';
      ctx.beginPath();
      ctx.arc(kp[j][0], kp[j][1], r, 0, L.TAU);
      ctx.fill();
      ctx.strokeStyle = o.color;
      ctx.lineWidth = 2;
      ctx.stroke();
    }
    ctx.restore();
  };

  // рамка предмета, как у детектора: тонкая рамка и плашка «класс 0.91»
  K.modelBox = (ctx, bx, o) => {
    const [x1, y1, x2, y2] = bx;
    ctx.save();
    ctx.globalAlpha *= o.alpha == null ? 1 : o.alpha;
    ctx.strokeStyle = o.color;
    ctx.lineWidth = 3;
    ctx.strokeRect(x1, y1, x2 - x1, y2 - y1);
    const lbl = o.score != null ? `${o.label} ${o.score.toFixed(2)}` : o.label;
    const w = K.measure(ctx, lbl, { kind: 'mono', size: 17, weight: 700 }) + 14;
    const ly = y1 - 26 < (o.minY || 0) ? y2 + 2 : y1 - 26;
    ctx.fillStyle = o.color;
    ctx.fillRect(x1 - 1.5, ly, w, 24);
    K.text(ctx, lbl, x1 + 6, ly + 17, { kind: 'mono', size: 17, weight: 700, color: o.textColor || '#0B1230' });
    ctx.restore();
  };

  // небольшой круг вокруг предмета (телефон, сигарета, голова без каски)
  K.itemRing = (ctx, bx, o) => {
    const [x1, y1, x2, y2] = bx;
    const cx = (x1 + x2) / 2, cy = (y1 + y2) / 2;
    const r = Math.max(22, Math.hypot(x2 - x1, y2 - y1) * 0.62);
    L.inkPath(ctx, L.ellipsePts(cx, cy, r, r * 0.92, 48, 0.3), { closed: true, color: o.color, width: 3.4, seed: o.seed, draw: L.clamp(o.p == null ? 1 : o.p) });
  };

  // ---- полоса смены внизу кадра: 08:00–20:00, деления, отметки нарушений, бегунок «сейчас»
  // o: x, y, w, now (мин от 08:00), marks [{min, sev, label}], dark, hot (индекс отметки, что пульсирует), T, labels (подписи у отметок)
  K.strip = (ctx, o) => {
    const { x, y, w } = o;
    const dark = !!o.dark;
    const span = 12 * 60;
    const X = (m) => x + (w * L.clamp(m / span));
    const line = dark ? K.C.pale : K.C.ink2;
    // база
    ctx.save();
    ctx.fillStyle = dark ? 'rgba(127,208,232,0.10)' : 'rgba(20,26,46,0.07)';
    ctx.fillRect(x, y - 8, w, 16);
    ctx.fillStyle = dark ? 'rgba(127,208,232,0.55)' : 'rgba(240,138,36,0.55)';
    ctx.fillRect(x, y - 8, X(o.now) - x, 16);
    ctx.restore();
    L.inkLine(ctx, x, y + 8, x + w, y + 8, { color: line, width: 2, seed: K.sd('strip', 1), taper: [4, 4] });
    for (let hh = 0; hh <= 12; hh++) {
      const xx = X(hh * 60);
      const major = hh % 2 === 0;
      L.inkLine(ctx, xx, y + 8, xx, y + (major ? 22 : 16), { color: line, width: major ? 2 : 1.4, seed: K.sd('tick', hh), taper: [2, 2], wobble: 0.3 });
      if (major) K.text(ctx, `${String(8 + hh).padStart(2, '0')}:00`, xx, y + 46, { kind: 'mono', size: 18, weight: 500, color: line, align: hh === 0 ? 'left' : hh === 12 ? 'right' : 'center' });
    }
    (o.marks || []).forEach((m, i) => {
      if (m.min > o.now + 1e-6) return;
      const xx = X(m.min);
      const col = K.SEV[m.sev] ? K.SEV[m.sev].color : K.C.orange;
      const hot = o.hot === i;
      const r = hot ? 11 + 3 * Math.abs(Math.sin(o.T * 6)) : 9;
      ctx.fillStyle = col;
      ctx.beginPath();
      ctx.arc(xx, y, r, 0, L.TAU);
      ctx.fill();
      ctx.strokeStyle = dark ? K.C.navy : K.C.paper2;
      ctx.lineWidth = 3;
      ctx.stroke();
      if (m.done) {
        ctx.strokeStyle = '#FFFFFF';
        ctx.lineWidth = 2.6;
        ctx.beginPath();
        ctx.moveTo(xx - 4.5, y + 0.5);
        ctx.lineTo(xx - 1, y + 4);
        ctx.lineTo(xx + 5, y - 4);
        ctx.stroke();
      }
      if (o.labels && m.short) K.text(ctx, m.short, xx, y - 22 - (i % 3) * 28, { kind: 'disp', size: 22, weight: 700, color: dark ? K.C.chalk : K.C.ink, align: 'center' });
    });
    // бегунок «сейчас»
    const nx = X(o.now);
    ctx.fillStyle = dark ? K.C.chalk : K.C.ink;
    ctx.beginPath();
    ctx.moveTo(nx, y - 12);
    ctx.lineTo(nx - 8, y - 24);
    ctx.lineTo(nx + 8, y - 24);
    ctx.closePath();
    ctx.fill();
  };

  // ---- часы смены (правый верх)
  K.clock = (ctx, o) => {
    const dark = !!o.dark;
    K.text(ctx, 'СМЕНА 24.10 · 08:00–20:00', o.x, o.y - 70, { kind: 'mono', size: 18, weight: 500, color: dark ? K.C.pale : K.C.ink2, align: 'right', track: 2 });
    K.text(ctx, K.hm(o.min), o.x, o.y, { kind: 'mono', size: 64, weight: 700, color: dark ? K.C.chalk : K.C.ink, align: 'right', track: 1 });
  };

  // ---- сообщение бота: шапка «LinQ Guard», карточка-картинка, строка статуса
  // o: x, y, w, img, p (въезд 0..1), status [{t, text, color}], dark
  K.botMessage = (ctx, o) => {
    const im = o.img;
    if (!im || o.p <= 0) return;
    const w = o.w;
    const h = (im.height * w) / im.width;
    const e = L.ease.outBack ? L.ease.outBack(L.clamp(o.p)) : L.clamp(o.p);
    const dy = (1 - e) * 60;
    ctx.save();
    ctx.globalAlpha *= L.clamp(o.p * 2);
    const x = o.x, y = o.y + dy;
    const head = 54;
    // пузырь
    ctx.fillStyle = 'rgba(0,0,0,0.28)';
    K.rrect(ctx, x + 8, y + 12, w + 24, h + head + 24 + (o.foot || 0), 22);
    ctx.fill();
    ctx.fillStyle = '#17212B';
    K.rrect(ctx, x, y, w + 24, h + head + 24 + (o.foot || 0), 22);
    ctx.fill();
    // шапка: аватар и имя бота
    ctx.fillStyle = K.C.orange;
    ctx.beginPath();
    ctx.arc(x + 34, y + 28, 16, 0, L.TAU);
    ctx.fill();
    K.text(ctx, 'LG', x + 34, y + 34, { kind: 'disp', size: 17, weight: 800, color: K.C.navy, align: 'center' });
    K.text(ctx, 'LinQ Guard', x + 60, y + 35, { kind: 'body', size: 22, weight: 700, color: '#6AB3F3' });
    K.text(ctx, 'бот', x + 60 + K.measure(ctx, 'LinQ Guard', { kind: 'body', size: 22, weight: 700 }) + 10, y + 35, { kind: 'body', size: 18, weight: 500, color: '#7F91A4' });
    ctx.save();
    K.rrect(ctx, x + 12, y + head, w, h, 12);
    ctx.clip();
    ctx.drawImage(im, x + 12, y + head, w, h);
    if (o.overlayImg && o.overlayP > 0) {
      ctx.globalAlpha = L.clamp(o.overlayP);
      ctx.drawImage(o.overlayImg, x + 12, y + head, w, h);
    }
    ctx.restore();
    ctx.restore();
    return { x, y, w: w + 24, h: h + head + 24, cardY: y + head, cardH: h };
  };
})();
