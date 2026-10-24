/* LinQ Guard · procedural art for the Telegram Mini App.
 * Everything here is drawn on <canvas> in plain JS: no images, no libraries.
 * Approach and helpers (hash / h3 / rng / noise1, the wobbly ink line, hatching,
 * the navy blueprint plate, glow dots, tick rings, lines that "boil" on twos,
 * fills printed slightly off-register from their outline) are adapted from
 * procedural-film, MIT: https://github.com/kuhnhomeuk-cell/procedural-film
 *
 * Rule of the house: the drawing is white/cyan ink on navy; only safety things
 * (helmets, vests, floor markings, vehicles, alarms) get colour.
 * Public API: window.Art = { intro, header, role, still, textures, reduced }.
 */
(function () {
  'use strict';
  const TAU = Math.PI * 2;
  const MQ = window.matchMedia ? matchMedia('(prefers-reduced-motion: reduce)') : { matches: false };
  const reduced = () => !!MQ.matches;
  const clamp = (v, a = 0, b = 1) => (v < a ? a : v > b ? b : v);
  const lerp = (a, b, t) => a + (b - a) * t;
  const seg = (t, a, b) => clamp((t - a) / (b - a));
  const eOut = (p) => 1 - Math.pow(1 - clamp(p), 3);
  const eSnap = (p) => ((p = clamp(p)), 1 + 2.2 * Math.pow(p - 1, 3) + 1.2 * Math.pow(p - 1, 2));
  const tri = (x) => 1 - Math.abs(((x % 2) + 2) % 2 - 1);

  const PAL = {
    navy: '#0B1230', navyDeep: '#060A1C', navyLight: '#18234D', grid: '#3A4A86',
    lavender: '#C8C1EF', white: '#EEF0FF', pale: '#9CC2EA', cyan: '#7FE3FF',
    ink: '#2A1C13', hazard: '#F2C230', signal: '#FF7A1A', alarm: '#FF4A3D',
    reflect: '#E4ECF6', helmetW: '#F4F6FB', ok: '#4FD1A1',
  };
  const RGB = {};
  function rgba(hex, a) {
    let c = RGB[hex];
    if (!c) {
      const h = hex.replace('#', '');
      c = RGB[hex] = [parseInt(h.slice(0, 2), 16), parseInt(h.slice(2, 4), 16), parseInt(h.slice(4, 6), 16)];
    }
    return 'rgba(' + c[0] + ',' + c[1] + ',' + c[2] + ',' + a + ')';
  }

  // ---------------------------------------------------------------- hash, rng, noise (procedural-film lib.js)
  function mix32(h) {
    h ^= h >>> 16; h = Math.imul(h, 0x7feb352d); h ^= h >>> 15; h = Math.imul(h, 0x846ca68b); h ^= h >>> 16;
    return h >>> 0;
  }
  function hash() {
    let h = 0x811c9dc5 ^ arguments.length;
    for (let a = 0; a < arguments.length; a++) {
      const s = String(arguments[a]);
      for (let i = 0; i < s.length; i++) h = Math.imul(h ^ s.charCodeAt(i), 0x01000193);
      h = mix32(h ^ s.length);
    }
    return h >>> 0;
  }
  function h3(a, b, c) {
    let h = Math.imul(a | 0, 0x27d4eb2d) ^ Math.imul(b | 0, 0x165667b1) ^ Math.imul(c | 0, 0x9e3779b1);
    h ^= h >>> 15; h = Math.imul(h, 0x85ebca6b); h ^= h >>> 13; h = Math.imul(h, 0xc2b2ae35); h ^= h >>> 16;
    return (h >>> 0) / 4294967296;
  }
  function rng(seed) {
    let a = hash(seed) || 0x9e3779b9;
    const r = () => {
      a = (a + 0x6d2b79f5) | 0;
      let t = Math.imul(a ^ (a >>> 15), 1 | a);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
    r.range = (lo, hi) => lo + (hi - lo) * r();
    return r;
  }
  const fade5 = (f) => f * f * f * (f * (f * 6 - 15) + 10);
  function noise1(x, s) {
    s |= 0;
    const i = Math.floor(x), f = x - i;
    const g0 = h3(i, 71, s) * 2 - 1, g1 = h3(i + 1, 71, s) * 2 - 1;
    return clamp(lerp(g0 * f, g1 * (f - 1), fade5(f)) * 2, -1, 1);
  }

  // ---------------------------------------------------------------- canvas plumbing
  function fit(cv, maxDpr) {
    const r = cv.getBoundingClientRect();
    const dpr = Math.min(maxDpr || 2, window.devicePixelRatio || 1);
    const w = Math.max(1, Math.round(r.width)), h = Math.max(1, Math.round(r.height));
    const pw = Math.round(w * dpr), ph = Math.round(h * dpr);
    if (cv.width !== pw || cv.height !== ph) { cv.width = pw; cv.height = ph; }
    const ctx = cv.getContext('2d');
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    return { ctx, w, h, dpr };
  }
  function off(w, h, dpr) {
    const cv = document.createElement('canvas');
    cv.width = Math.max(1, Math.round(w * dpr));
    cv.height = Math.max(1, Math.round(h * dpr));
    const ctx = cv.getContext('2d');
    ctx.scale(dpr, dpr);
    return { cv, ctx };
  }

  // One rAF loop for every living canvas: runs only while something is on screen and the tab is visible.
  const actors = new Set();
  let raf = 0;
  function frame(now) {
    raf = 0;
    if (document.hidden) return;
    let alive = false;
    actors.forEach((a) => {
      if (!a.cv.isConnected) { actors.delete(a); if (a.io) a.io.disconnect(); if (a.ro) a.ro.disconnect(); return; }
      if (!a.vis) return;
      if (now - a.last >= a.every - 2) { a.last = now; a.paint(now / 1000); }
      alive = true;
    });
    if (alive && !reduced()) raf = requestAnimationFrame(frame);
  }
  function kick() { if (!raf && !document.hidden) raf = requestAnimationFrame(frame); }
  document.addEventListener('visibilitychange', kick);

  function actor(cv, fps, draw, setup) {
    const a = { cv, every: 1000 / fps, last: -1e9, vis: true, st: null };
    a.paint = (T) => {
      const f = fit(cv);
      if (!a.st || a.st.w !== f.w || a.st.h !== f.h || a.st.dpr !== f.dpr) {
        a.st = Object.assign(setup ? setup(f) : {}, { w: f.w, h: f.h, dpr: f.dpr });
      }
      draw(f.ctx, a.st, reduced() ? 4.2 : T);
    };
    a.wake = () => { a.last = -1e9; kick(); };
    if ('IntersectionObserver' in window) {
      a.io = new IntersectionObserver((es) => { a.vis = es[es.length - 1].isIntersecting; if (a.vis) a.wake(); });
      a.io.observe(cv);
    }
    if ('ResizeObserver' in window) { a.ro = new ResizeObserver(a.wake); a.ro.observe(cv); }
    actors.add(a);
    a.paint(performance.now() / 1000); // first frame now, not on the next rAF
    a.last = performance.now();
    kick();
    return a;
  }

  // ---------------------------------------------------------------- drawing helpers
  const RS = new WeakMap();
  function resample(pts, step, closed) {
    const hit = RS.get(pts);
    if (hit && hit.step === step && hit.closed === closed) return hit;
    const src = closed ? pts.concat([pts[0]]) : pts;
    const X = [src[0][0]], Y = [src[0][1]], S = [0];
    let acc = 0;
    for (let i = 1; i < src.length; i++) {
      const ax = src[i - 1][0], ay = src[i - 1][1], bx = src[i][0], by = src[i][1];
      const L = Math.hypot(bx - ax, by - ay);
      const n = Math.max(1, Math.ceil(L / step));
      for (let k = 1; k <= n; k++) { X.push(ax + ((bx - ax) * k) / n); Y.push(ay + ((by - ay) * k) / n); acc += L / n; S.push(acc); }
    }
    const m = X.length, NX = new Float32Array(m), NY = new Float32Array(m);
    for (let k = 0; k < m; k++) {
      const a = Math.max(0, k - 1), b = Math.min(m - 1, k + 1);
      const tx = X[b] - X[a], ty = Y[b] - Y[a], l = Math.hypot(tx, ty) || 1;
      NX[k] = -ty / l; NY[k] = tx / l;
    }
    const c = { X, Y, S, NX, NY, m, len: acc, step, closed };
    RS.set(pts, c);
    return c;
  }
  function trace(ctx, C, seed, bi, wob, trem, from, to, offset) {
    let started = false;
    for (let k = 0; k < C.m; k++) {
      const s = C.S[k];
      if (s < from) continue;
      let x = C.X[k], y = C.Y[k], last = false;
      if (s > to && k > 0) {
        const p = (to - C.S[k - 1]) / (s - C.S[k - 1] || 1);
        x = lerp(C.X[k - 1], x, p); y = lerp(C.Y[k - 1], y, p); last = true;
      }
      // low-frequency drift whose phase jumps with each boil drawing, plus a hair of tremble
      const d = noise1(s / 70 + seed * 7.31 + bi * 2.17, seed) * wob + (h3(k, bi, seed) - 0.5) * 2 * trem + offset;
      x += C.NX[k] * d; y += C.NY[k] * d;
      if (!started) { ctx.moveTo(x, y); started = true; } else ctx.lineTo(x, y);
      if (last) break;
    }
  }
  /** ink(ctx, pts, o): hand-inked polyline (a light cousin of procedural-film's inkPath). */
  function ink(ctx, pts, o) {
    o = o || {};
    const dp = o.draw == null ? 1 : clamp(o.draw);
    if (dp <= 0 || !pts || pts.length < 2) return;
    const C = resample(pts, o.step || 4, !!o.closed);
    const seed = o.seed || 1, bi = o.boil || 0;
    const wob = o.wobble != null ? o.wobble : 1.1, trem = o.tremble != null ? o.tremble : 0.22;
    ctx.save();
    ctx.lineCap = 'round'; ctx.lineJoin = 'round';
    if (o.fill && dp >= 1) {
      // the wash is printed a touch off-register from its outline
      ctx.save();
      ctx.translate(o.offX != null ? o.offX : 0.9, o.offY != null ? o.offY : 0.7);
      ctx.beginPath(); trace(ctx, C, seed, bi, wob, trem, 0, C.len, 0); ctx.closePath();
      ctx.globalAlpha *= o.fillAlpha != null ? o.fillAlpha : 1;
      ctx.fillStyle = o.fill; ctx.fill();
      ctx.restore();
    }
    ctx.beginPath();
    trace(ctx, C, seed, bi, wob, trem, 0, C.len * dp, 0);
    ctx.lineWidth = o.w || 1.4;
    ctx.strokeStyle = o.color || PAL.white;
    ctx.globalAlpha *= o.alpha != null ? o.alpha : 1;
    if (o.dash) ctx.setLineDash(o.dash);
    ctx.stroke();
    if (o.double && dp >= 1) {
      ctx.beginPath();
      trace(ctx, C, seed + 97, bi, wob * 1.4, trem, C.len * 0.12, C.len * 0.82, (o.w || 1.4) * 0.6 + 1.1);
      ctx.lineWidth = Math.max(0.5, (o.w || 1.4) * 0.45);
      ctx.globalAlpha *= 0.4;
      ctx.stroke();
    }
    ctx.restore();
  }
  function poly(ctx, pts) {
    ctx.beginPath();
    pts.forEach((p, i) => (i ? ctx.lineTo(p[0], p[1]) : ctx.moveTo(p[0], p[1])));
    ctx.closePath();
  }
  function ell(cx, cy, rx, ry, a0, a1, n) {
    a0 = a0 == null ? 0 : a0; a1 = a1 == null ? TAU : a1; n = n || 28;
    const out = [];
    for (let i = 0; i <= n; i++) { const a = a0 + ((a1 - a0) * i) / n; out.push([cx + Math.cos(a) * rx, cy + Math.sin(a) * ry]); }
    if (a1 - a0 >= TAU - 1e-6) out.pop();
    return out;
  }
  /** hatch(ctx, pts, o): parallel hand-hatching clipped to a polygon. */
  function hatch(ctx, pts, o) {
    let x0 = 1e9, y0 = 1e9, x1 = -1e9, y1 = -1e9;
    pts.forEach((p) => { x0 = Math.min(x0, p[0]); y0 = Math.min(y0, p[1]); x1 = Math.max(x1, p[0]); y1 = Math.max(y1, p[1]); });
    const a = o.angle != null ? o.angle : -0.8, gap = o.gap || 3, s = o.seed || 3;
    const ca = Math.cos(a), sa = Math.sin(a), cx = (x0 + x1) / 2, cy = (y0 + y1) / 2, R = Math.hypot(x1 - x0, y1 - y0) / 2 + 2;
    ctx.save();
    poly(ctx, pts); ctx.clip();
    if (o.clipX != null) { ctx.beginPath(); ctx.rect(o.clipX, y0 - 2, x1 - o.clipX + 4, y1 - y0 + 4); ctx.clip(); }
    ctx.beginPath();
    for (let d = -R, i = 0; d <= R; d += gap, i++) {
      const j = (h3(i, s, 7) - 0.5) * gap * 0.45, px = cx - sa * (d + j), py = cy + ca * (d + j);
      const e0 = R * (0.75 + 0.25 * h3(i, 1, s)), e1 = R * (0.75 + 0.25 * h3(i, 2, s));
      ctx.moveTo(px - ca * e0, py - sa * e0); ctx.lineTo(px + ca * e1, py + sa * e1);
    }
    ctx.strokeStyle = o.color || PAL.white; ctx.globalAlpha *= o.alpha != null ? o.alpha : 0.4; ctx.lineWidth = o.w || 0.7;
    ctx.stroke();
    ctx.restore();
  }
  function glow(ctx, x, y, r, color, k) {
    k = k == null ? 1 : k;
    const g = ctx.createRadialGradient(x, y, 0, x, y, r * 4);
    g.addColorStop(0, rgba(color, 0.55 * k)); g.addColorStop(0.25, rgba(color, 0.18 * k)); g.addColorStop(1, rgba(color, 0));
    ctx.save();
    ctx.globalCompositeOperation = 'lighter';
    ctx.fillStyle = g; ctx.fillRect(x - r * 4, y - r * 4, r * 8, r * 8);
    ctx.globalAlpha *= Math.min(1, k); ctx.fillStyle = '#fff';
    ctx.beginPath(); ctx.arc(x, y, r * 0.55, 0, TAU); ctx.fill();
    ctx.restore();
  }
  function ticks(ctx, x, y, r, n, len, color, alpha, major, a0, span) {
    const p = new Path2D(), pm = new Path2D();
    a0 = a0 || 0; span = span == null ? TAU : span;
    const full = Math.abs(span - TAU) < 1e-6;
    for (let i = 0; i < n; i++) {
      const a = a0 + (i / (full ? n : n - 1)) * span, M = major && i % major === 0, l = M ? len * 1.8 : len;
      (M ? pm : p).moveTo(x + Math.cos(a) * r, y + Math.sin(a) * r);
      (M ? pm : p).lineTo(x + Math.cos(a) * (r + l), y + Math.sin(a) * (r + l));
    }
    ctx.save(); ctx.strokeStyle = color; ctx.globalAlpha *= alpha; ctx.lineCap = 'round';
    ctx.lineWidth = 0.8; ctx.stroke(p); ctx.lineWidth = 1.1; ctx.stroke(pm); ctx.restore();
  }
  function text(ctx, s, x, y, size, color, alpha, align) {
    ctx.save();
    ctx.font = '500 ' + size + 'px "JetBrains Mono", ui-monospace, monospace';
    ctx.fillStyle = color; ctx.globalAlpha *= alpha; ctx.textAlign = align || 'center'; ctx.textBaseline = 'middle';
    ctx.fillText(s, x, y); ctx.restore();
  }

  // ---------------------------------------------------------------- the blueprint plate (cached per size)
  let grainTile = null;
  function grain() {
    if (grainTile) return grainTile;
    const c = document.createElement('canvas'); c.width = c.height = 96;
    const g = c.getContext('2d'), im = g.createImageData(96, 96);
    for (let i = 0; i < 96 * 96; i++) {
      const v = h3(i % 96, (i / 96) | 0, 5), v2 = h3((i % 96) >> 2, ((i / 96) | 0) >> 2, 9);
      im.data[i * 4] = 225; im.data[i * 4 + 1] = 232; im.data[i * 4 + 2] = 255;
      im.data[i * 4 + 3] = Math.max(0, (v - 0.55) * 40 + (v2 - 0.5) * 10);
    }
    g.putImageData(im, 0, 0);
    return (grainTile = c);
  }
  const PLATES = new Map();
  function plate(w, h, dpr, o) {
    const key = [w, h, dpr, o.grid, o.cx | 0, o.cy | 0, o.circles].join('|');
    if (PLATES.has(key)) return PLATES.get(key);
    if (PLATES.size > 6) PLATES.clear();
    const { cv, ctx } = off(w, h, dpr);
    const cx = o.cx != null ? o.cx : w / 2, cy = o.cy != null ? o.cy : h * 0.45, R = Math.hypot(w, h) * 0.62;
    const g = ctx.createRadialGradient(cx, cy, 0, cx, cy, R);
    g.addColorStop(0, PAL.navyLight); g.addColorStop(0.38, PAL.navy); g.addColorStop(1, PAL.navyDeep);
    ctx.fillStyle = g; ctx.fillRect(0, 0, w, h);
    ctx.fillStyle = ctx.createPattern(grain(), 'repeat'); ctx.fillRect(0, 0, w, h);
    const gs = o.grid || 20, minor = new Path2D(), major = new Path2D();
    for (let x = (w / 2) % gs, i = 0; x <= w; x += gs, i++) {
      const p = Math.round((x - w / 2) / gs) % 5 === 0 ? major : minor; p.moveTo(x, 0); p.lineTo(x, h);
    }
    for (let y = (h / 2) % gs; y <= h; y += gs) {
      const p = Math.round((y - h / 2) / gs) % 5 === 0 ? major : minor; p.moveTo(0, y); p.lineTo(w, y);
    }
    ctx.lineWidth = 0.6; ctx.strokeStyle = rgba(PAL.grid, 0.32); ctx.stroke(minor);
    ctx.lineWidth = 0.9; ctx.strokeStyle = rgba(PAL.grid, 0.55); ctx.stroke(major);
    const r = rng('plate' + key);
    for (let i = 0; i < 3; i++) {
      const a = r.range(0, Math.PI), ox = cx + r.range(-0.3, 0.3) * w, oy = cy + r.range(-0.3, 0.3) * h, L = Math.hypot(w, h);
      ctx.beginPath(); ctx.moveTo(ox - Math.cos(a) * L, oy - Math.sin(a) * L); ctx.lineTo(ox + Math.cos(a) * L, oy + Math.sin(a) * L);
      ctx.setLineDash(i === 1 ? [7, 8] : []); ctx.lineWidth = 0.8; ctx.strokeStyle = rgba(PAL.lavender, 0.08); ctx.stroke();
    }
    ctx.setLineDash([]);
    if (o.circles) {
      const md = Math.min(w, h);
      for (let i = 0; i < 4; i++) {
        ctx.beginPath(); ctx.arc(cx, cy, md * (0.22 + i * 0.17), 0, TAU);
        ctx.setLineDash(i === 1 ? [2, 7] : []); ctx.lineWidth = 1; ctx.strokeStyle = rgba(PAL.lavender, i % 2 ? 0.08 : 0.12); ctx.stroke();
      }
      ctx.setLineDash([]);
      ticks(ctx, cx, cy, md * 0.73, 120, 5, PAL.lavender, 0.16, 10);
    }
    ctx.strokeStyle = rgba(PAL.lavender, 0.3); ctx.lineWidth = 1;
    const m = 10, s = 12;
    [[m, m, 1, 1], [w - m, m, -1, 1], [m, h - m, 1, -1], [w - m, h - m, -1, -1]].forEach(([x, y, sx, sy]) => {
      ctx.beginPath(); ctx.moveTo(x, y + sy * s); ctx.lineTo(x, y); ctx.lineTo(x + sx * s, y); ctx.stroke();
    });
    ctx.fillStyle = rgba(PAL.white, 0.2);
    for (let i = 0; i < (w * h) / 4000; i++) { ctx.beginPath(); ctx.arc(r() * w, r() * h, r.range(0.3, 0.9), 0, TAU); ctx.fill(); }
    PLATES.set(key, cv);
    return cv;
  }

  // ---------------------------------------------------------------- the workshop plan (shared by intro and header)
  function buildPlan(W, H, seed, o) {
    const r = rng('plan' + seed);
    const m = o.margin || 14, x0 = m, y0 = m, x1 = W - m, y1 = H - m;
    const nx = Math.max(2, Math.round((x1 - x0) / (o.bay || 64))), ny = Math.max(1, Math.round((y1 - y0) / (o.bay || 64)));
    const bx = (x1 - x0) / nx, by = (y1 - y0) / ny;
    const P = { W, H, x0, y0, x1, y1, strokes: [], cols: [], labels: [], machines: [], conveyors: [], cams: [], workers: [], hot: 0 };
    const add = (k, pts, x) => P.strokes.push(Object.assign({ k, pts, seed: P.strokes.length * 13 + 5, w: 1, a: 0.8 }, x));
    if (o.axes) {
      const AX = 'АБВГДЕЖИКЛМН';
      for (let i = 0; i <= nx; i++) {
        const x = x0 + i * bx;
        add('axis', [[x, y0 - m * 0.55], [x, y1 + m * 0.4]], { w: 0.6, a: 0.32, dash: [9, 3, 2, 3], t0: 0.02 + i * 0.03, t1: 0.4 + i * 0.03 });
        P.labels.push({ x, y: y0 - m * 0.55 - 7, s: String(i + 1), t0: 0.25 + i * 0.03 });
      }
      for (let j = 0; j <= ny; j++) {
        const y = y0 + j * by;
        add('axis', [[x0 - m * 0.55, y], [x1 + m * 0.4, y]], { w: 0.6, a: 0.32, dash: [9, 3, 2, 3], t0: 0.05 + j * 0.03, t1: 0.42 + j * 0.03 });
        P.labels.push({ x: x0 - m * 0.55 - 7, y, s: AX[j % AX.length], t0: 0.28 + j * 0.03 });
      }
    }
    // outer walls with a gate in the bottom wall, the gate's swing arc, an office partition
    const g0 = x0 + Math.floor(nx / 2) * bx + bx * 0.22, gw = Math.min(30, bx * 0.5), g1 = g0 + gw;
    add('wall', [[g0, y1], [x0, y1], [x0, y0], [x1, y0], [x1, y1], [g1, y1]], { w: 2, a: 0.95, t0: 0.12, t1: 0.72, double: true });
    add('door', ell(g0, y1, gw, gw, -Math.PI / 2, 0, 10), { w: 0.7, a: 0.5, dash: [2, 3], t0: 0.6, t1: 0.8 });
    add('door', [[g0, y1], [g0, y1 - gw]], { w: 1, a: 0.7, t0: 0.6, t1: 0.75 });
    if (nx >= 3) {
      const ox = x0 + bx * 0.92, oy = y0 + Math.min(by, (y1 - y0) * 0.45) * 0.62;
      add('wall', [[ox, y0], [ox, oy], [x0 + bx * 0.38, oy]], { w: 1.3, a: 0.8, t0: 0.42, t1: 0.7 });
    }
    for (let i = 0; i <= nx; i++) for (let j = 0; j <= ny; j++) P.cols.push({ x: x0 + i * bx, y: y0 + j * by, t0: 0.38 + (i + j) * 0.025 });
    // conveyors with rollers, flanked by hazard-yellow walkway markings and a forklift lane
    const rows = ny >= 5 ? [Math.floor(ny * 0.3) + 0.5, Math.floor(ny * 0.72) + 0.5] : [Math.floor(ny / 2) + (ny > 1 ? 0.5 : 0.5)];
    rows.forEach((row, ci) => {
      const cy = y0 + by * Math.min(row, ny - 0.5), a = x0 + bx * 0.55, b = x1 - bx * 0.55, hw = 4.5;
      const band = [[a, cy - hw], [b, cy - hw]].concat(ell(b, cy, hw, hw, -Math.PI / 2, Math.PI / 2, 6), [[a, cy + hw]], ell(a, cy, hw, hw, Math.PI / 2, Math.PI * 1.5, 6));
      add('conv', band, { closed: true, w: 1.1, a: 0.85, t0: 0.55 + ci * 0.1, t1: 0.95 + ci * 0.1 });
      add('aisle', [[a, cy - hw - 9], [b, cy - hw - 9]], { w: 1.1, a: 0.6, c: PAL.hazard, dash: [6, 4], t0: 0.85, t1: 1.15 });
      add('aisle', [[a, cy + hw + 9], [b, cy + hw + 9]], { w: 1.1, a: 0.6, c: PAL.hazard, dash: [6, 4], t0: 0.88, t1: 1.18 });
      P.conveyors.push({ a, b, y: cy, hw, t0: 0.85 + ci * 0.1, lane: cy + hw + 19 < y1 - 6 ? cy + hw + 19 : cy - hw - 19 });
    });
    // machines in the bays away from the conveyors
    let mi = 0;
    for (let i = 0; i < nx; i++) for (let j = 0; j < ny; j++) {
      const cx = x0 + (i + 0.5) * bx, cy = y0 + (j + 0.5) * by;
      if (P.conveyors.some((c) => Math.abs(cy - c.y) < by * 0.55 || Math.abs(cy - c.lane) < 14)) continue;
      if (i === 0 && j === 0 && nx >= 3) continue;
      if (r() > 0.78) continue;
      const w = bx * r.range(0.34, 0.6), h = by * r.range(0.26, 0.46), x = cx - w / 2 + r.range(-4, 4), y = cy - h / 2 + r.range(-4, 4);
      const pts = [[x, y], [x + w, y], [x + w, y + h], [x, y + h]], t0 = 0.6 + mi * 0.035;
      P.machines.push({ pts, t0, round: r() < 0.4 ? { x: x + w * 0.7, y: y + h / 2, r: Math.min(w, h) * 0.28 } : null });
      add('mach', pts, { closed: true, w: 0.9, a: 0.75, t0, t1: t0 + 0.3, smooth: false });
      mi++;
    }
    // cameras spaced around the walls, looking inwards
    const per = 2 * (x1 - x0 + y1 - y0), n = o.cams || 4;
    for (let i = 0; i < n; i++) {
      let u = (((i + 0.5) / n + r.range(-0.04, 0.04)) % 1) * per, x, y, dir;
      if (u < x1 - x0) { x = x0 + u; y = y0 + 3; dir = Math.PI / 2; }
      else if ((u -= x1 - x0) < y1 - y0) { x = x1 - 3; y = y0 + u; dir = Math.PI; }
      else if ((u -= y1 - y0) < x1 - x0) { x = x1 - u; y = y1 - 3; dir = -Math.PI / 2; }
      else { u -= x1 - x0; x = x0 + 3; y = y1 - u; dir = 0; }
      const toC = Math.atan2((y0 + y1) / 2 - y, (x0 + x1) / 2 - x);
      dir = lerp(dir, dir + Math.atan2(Math.sin(toC - dir), Math.cos(toC - dir)), 0.45);
      P.cams.push({ x, y, dir, fov: r.range(0.85, 1.1), sweep: r.range(0.12, 0.25), range: Math.min(W, H) * r.range(0.42, 0.62), t0: 0.95 + i * 0.07 });
    }
    P.hot = Math.min(n - 1, 1);
    for (let i = 0; i < (o.workers || 3); i++) P.workers.push({ s: 11 + i * 7 });
    return P;
  }

  function drawPlan(ctx, P, bi, t, alpha) {
    const pr = (a, b) => (t == null ? 1 : seg(t, a, b));
    P.strokes.forEach((s) => {
      const p = pr(s.t0, s.t1);
      if (p > 0) ink(ctx, s.pts, { w: s.w, alpha: s.a * alpha, color: s.c || PAL.white, seed: s.seed, boil: bi, dash: s.dash, closed: s.closed, draw: eOut(p), double: s.double, wobble: s.k === 'axis' ? 0.5 : 0.9 });
    });
    P.labels.forEach((l) => {
      const p = pr(l.t0, l.t0 + 0.2);
      if (p <= 0) return;
      ctx.save(); ctx.globalAlpha = alpha * p;
      ctx.beginPath(); ctx.arc(l.x, l.y, 6, 0, TAU); ctx.strokeStyle = rgba(PAL.lavender, 0.55); ctx.lineWidth = 0.8; ctx.stroke();
      text(ctx, l.s, l.x, l.y + 0.5, 7, PAL.lavender, 0.85);
      ctx.restore();
    });
    P.machines.forEach((mc, i) => {
      const p = pr(mc.t0 + 0.15, mc.t0 + 0.45);
      if (p <= 0) return;
      hatch(ctx, mc.pts, { angle: i % 2 ? 0.8 : -0.8, gap: 3.2, color: PAL.lavender, alpha: 0.22 * p * alpha, w: 0.6, seed: i + 3 });
      if (mc.round) ink(ctx, ell(mc.round.x, mc.round.y, mc.round.r, mc.round.r), { closed: true, w: 0.8, alpha: 0.7 * alpha, seed: 40 + i, boil: bi, draw: eOut(p), fill: PAL.navy, fillAlpha: 0.8, offX: 0, offY: 0 });
    });
    P.conveyors.forEach((c) => {
      const p = pr(c.t0 - 0.05, c.t0 + 0.2);
      if (p <= 0) return;
      ctx.save(); ctx.beginPath();
      for (let x = c.a + 4; x < lerp(c.a, c.b, p) - 3; x += 6) { ctx.moveTo(x, c.y - c.hw + 1.2); ctx.lineTo(x, c.y + c.hw - 1.2); }
      ctx.strokeStyle = rgba(PAL.lavender, 0.45 * alpha); ctx.lineWidth = 0.6; ctx.stroke();
      ctx.restore();
    });
    P.cols.forEach((c) => {
      const p = pr(c.t0, c.t0 + 0.18);
      if (p <= 0) return;
      const s = 3.2 * eSnap(p);
      ctx.save(); ctx.globalAlpha = alpha;
      ctx.fillStyle = PAL.navy; ctx.fillRect(c.x - s, c.y - s, s * 2, s * 2);
      ctx.strokeStyle = rgba(PAL.white, 0.85); ctx.lineWidth = 0.9; ctx.strokeRect(c.x - s, c.y - s, s * 2, s * 2);
      ctx.beginPath(); ctx.moveTo(c.x - s, c.y - s); ctx.lineTo(c.x + s, c.y + s); ctx.stroke();
      ctx.restore();
    });
  }

  function drawLive(ctx, P, T, o) {
    const pulse = 0.5 + 0.5 * Math.sin(T * 5);
    ctx.save();
    ctx.beginPath(); ctx.rect(P.x0, P.y0, P.x1 - P.x0, P.y1 - P.y0); ctx.clip();
    // 1. camera cones
    P.cams.forEach((c, i) => {
      const k = o.open ? o.open(i) : 1;
      if (k <= 0) return;
      const a = c.dir + Math.sin(T * 0.45 + i * 1.7) * c.sweep, half = (c.fov / 2) * eOut(k), R = c.range * (0.3 + 0.7 * eOut(k));
      const hot = o.scanX == null ? 0 : Math.max(0, 1 - Math.abs(o.scanX - c.x) / 46);
      const al = o.alarm && i === P.hot, col = al ? PAL.alarm : PAL.cyan;
      const g = ctx.createRadialGradient(c.x, c.y, 0, c.x, c.y, R);
      g.addColorStop(0, rgba(col, 0.15 + hot * 0.22 + (al ? 0.22 * pulse : 0))); g.addColorStop(1, rgba(col, 0));
      ctx.fillStyle = g;
      ctx.beginPath(); ctx.moveTo(c.x, c.y); ctx.arc(c.x, c.y, R, a - half, a + half); ctx.closePath(); ctx.fill();
      ctx.strokeStyle = rgba(col, 0.4 + hot * 0.45); ctx.lineWidth = 0.7;
      ctx.beginPath(); ctx.moveTo(c.x + Math.cos(a - half) * R, c.y + Math.sin(a - half) * R); ctx.lineTo(c.x, c.y);
      ctx.lineTo(c.x + Math.cos(a + half) * R, c.y + Math.sin(a + half) * R); ctx.stroke();
      ctx.setLineDash([2, 3]); ctx.beginPath(); ctx.arc(c.x, c.y, R, a - half, a + half); ctx.stroke(); ctx.setLineDash([]);
    });
    // 2. crates riding the conveyors
    const bk = o.boxes == null ? 1 : o.boxes;
    if (bk > 0) P.conveyors.forEach((c, ci) => {
      const L = c.b - c.a - 10;
      for (let i = 0; i < Math.floor(L / 34); i++) {
        const x = c.a + 5 + ((T * 16 * (ci ? -1 : 1) + i * 34) % L + L) % L;
        ctx.globalAlpha = bk; ctx.fillStyle = PAL.navyLight; ctx.strokeStyle = PAL.white; ctx.lineWidth = 0.8;
        ctx.fillRect(x - 3, c.y - 3, 6, 6); ctx.strokeRect(x - 3, c.y - 3, 6, 6);
      }
      ctx.globalAlpha = 1;
    });
    // 3. forklift on its lane, with its safety radius
    const fk = o.fork == null ? 1 : o.fork, c0 = P.conveyors[0];
    let fx = 0, fy = 0;
    if (c0 && fk > 0) {
      const u = tri(T / 7), d = Math.cos((T / 7) * Math.PI) >= 0 ? 1 : -1;
      fx = lerp(c0.a + 10, c0.b - 10, u); fy = c0.lane;
      ctx.save(); ctx.globalAlpha = fk; ctx.translate(fx, fy);
      ctx.setLineDash([2, 3]); ctx.strokeStyle = rgba(PAL.signal, 0.55); ctx.lineWidth = 0.8;
      ctx.beginPath(); ctx.arc(0, 0, 15, 0, TAU); ctx.stroke(); ctx.setLineDash([]);
      ctx.scale(d, 1);
      ctx.fillStyle = PAL.signal; ctx.fillRect(-6, -3.5, 9, 7);
      ctx.strokeStyle = PAL.white; ctx.lineWidth = 0.8; ctx.strokeRect(-6, -3.5, 9, 7);
      ctx.beginPath(); ctx.moveTo(3, -2.5); ctx.lineTo(8, -2.5); ctx.moveTo(3, 2.5); ctx.lineTo(8, 2.5); ctx.stroke();
      ctx.restore();
    }
    // 4. people: a helmet seen from above is a yellow dot
    const wk = o.people == null ? 1 : o.people;
    if (wk > 0) P.workers.forEach((w, i) => {
      let x = lerp(P.x0 + 10, P.x1 - 10, 0.5 + 0.5 * noise1(T * 0.05 + w.s * 3.1, w.s));
      let y = lerp(P.y0 + 10, P.y1 - 10, 0.5 + 0.5 * noise1(T * 0.06 + w.s * 5.7, w.s + 9));
      const al = o.alarm && i === 0 && c0;
      if (al) { x = fx + 11; y = fy - 9; }
      ctx.save(); ctx.globalAlpha = wk;
      if (al) { ctx.strokeStyle = rgba(PAL.alarm, 0.4 + 0.6 * pulse); ctx.lineWidth = 1.2; ctx.beginPath(); ctx.arc(x, y, 6 + pulse * 5, 0, TAU); ctx.stroke(); }
      ctx.fillStyle = al ? PAL.alarm : PAL.hazard; ctx.beginPath(); ctx.arc(x, y, 2.6, 0, TAU); ctx.fill();
      ctx.strokeStyle = rgba(PAL.white, 0.8); ctx.lineWidth = 0.6; ctx.beginPath(); ctx.arc(x, y, 3.6, 0, TAU); ctx.stroke();
      ctx.restore();
    });
    ctx.restore();
    // 5. camera bodies with blinking record lights (outside the clip so they sit on the wall)
    P.cams.forEach((c, i) => {
      const k = o.open ? o.open(i) : 1;
      if (k <= 0) return;
      const s = eSnap(Math.min(1, k * 2.5));
      ctx.save(); ctx.translate(c.x, c.y); ctx.rotate(c.dir); ctx.scale(s, s);
      ctx.fillStyle = PAL.navy; ctx.fillRect(-4, -2.4, 8, 4.8);
      ctx.strokeStyle = PAL.white; ctx.lineWidth = 0.9; ctx.strokeRect(-4, -2.4, 8, 4.8);
      ctx.beginPath(); ctx.moveTo(4, -1.6); ctx.lineTo(6.5, -2.8); ctx.lineTo(6.5, 2.8); ctx.lineTo(4, 1.6); ctx.stroke();
      ctx.restore();
      if ((T * 1.1 + i * 0.37) % 1 < 0.5) { ctx.fillStyle = o.alarm && i === P.hot ? PAL.alarm : '#FF5A4D'; ctx.beginPath(); ctx.arc(c.x, c.y, 1.1, 0, TAU); ctx.fill(); }
    });
  }

  // ---------------------------------------------------------------- 1. intro: a workshop drawing itself
  function intro(root, onDone) {
    const cv = root.querySelector('canvas');
    const f = fit(cv);
    const W = f.w, H = f.h;
    const pw = Math.min(W - 44, 440), ph = Math.min(H * 0.56, pw * 1.35), ox = (W - pw) / 2, oy = Math.max(46, H * 0.075);
    const P = buildPlan(pw, ph, 3, { bay: 56, margin: 26, cams: 5, axes: true, workers: 4 });
    const bg = plate(W, H, f.dpr, { grid: 24, cx: W / 2, cy: oy + ph / 2, circles: true });
    const DUR = 1.95;
    let raf2 = 0, t0 = 0, done = false;
    function draw(t) {
      const ctx = f.ctx;
      ctx.drawImage(bg, 0, 0, W, H);
      ctx.save(); ctx.translate(ox, oy);
      drawPlan(ctx, P, Math.floor(t * 12), t, 1);
      const live = seg(t, 1.0, 1.3);
      drawLive(ctx, P, t, { open: (i) => seg(t, P.cams[i].t0, P.cams[i].t0 + 0.4), boxes: live, fork: live, people: live, scanX: t > 1.25 ? lerp(-40, pw + 40, seg(t, 1.25, 1.95)) : null });
      // dimension lines under and beside the plan
      const pd = eOut(seg(t, 0.7, 1.05));
      if (pd > 0) {
        ctx.strokeStyle = rgba(PAL.lavender, 0.6); ctx.lineWidth = 0.8;
        const yb = P.y1 + 18, xm = pw / 2, hw = (P.x1 - P.x0) / 2 * pd;
        ctx.beginPath(); ctx.moveTo(xm - hw, yb); ctx.lineTo(xm + hw, yb);
        ctx.moveTo(xm - hw, yb - 4); ctx.lineTo(xm - hw, yb + 4); ctx.moveTo(xm + hw, yb - 4); ctx.lineTo(xm + hw, yb + 4); ctx.stroke();
        if (pd > 0.8) text(ctx, '72 000', xm, yb - 7, 8, PAL.lavender, (pd - 0.8) * 5);
        const xr = P.x1 + 14, hh = (P.y1 - P.y0) / 2 * pd, ym = (P.y0 + P.y1) / 2;
        ctx.beginPath(); ctx.moveTo(xr, ym - hh); ctx.lineTo(xr, ym + hh); ctx.stroke();
      }
      ctx.restore();
    }
    function finish() {
      if (done) return;
      done = true; cancelAnimationFrame(raf2);
      root.classList.add('out');
      setTimeout(() => { root.hidden = true; root.classList.remove('out', 'on'); if (onDone) onDone(); }, 420);
    }
    function step(now) {
      if (!t0) t0 = now;
      const t = (now - t0) / 1000;
      draw(Math.min(t, DUR));
      if (t > 1.1) root.classList.add('on');
      if (t < DUR + 0.55) raf2 = requestAnimationFrame(step); else finish();
    }
    root.addEventListener('click', finish);
    raf2 = requestAnimationFrame(step);
    return { skip: finish };
  }

  // ---------------------------------------------------------------- 3. living header
  function header(cv) {
    const S = { alarm: false };
    const a = actor(cv, 20, (ctx, st, T) => {
      const { w, h } = st;
      ctx.drawImage(st.bg, 0, 0, w, h);
      const dx = -30 + Math.sin(T / 23) * 22, dy = -12 + Math.cos(T / 31) * 6;
      const scan = ((T % 7) / 7) * (w + 140) - 70;
      ctx.save(); ctx.translate(dx, dy);
      ctx.drawImage(st.layers[Math.floor(T * 8) % 3], 0, 0, st.P.W, st.P.H);
      drawLive(ctx, st.P, T, { alarm: S.alarm, scanX: scan - dx });
      ctx.restore();
      // the scanning line with its fading wake
      const col = S.alarm ? PAL.alarm : PAL.cyan;
      const g = ctx.createLinearGradient(scan - 70, 0, scan, 0);
      g.addColorStop(0, rgba(col, 0)); g.addColorStop(1, rgba(col, 0.16));
      ctx.fillStyle = g; ctx.fillRect(scan - 70, 0, 70, h);
      ctx.fillStyle = rgba(col, 0.75); ctx.fillRect(scan, 0, 1, h);
      if (S.alarm) {
        const p = 0.5 + 0.5 * Math.sin(T * 4.2), c = st.P.cams[st.P.hot];
        ctx.fillStyle = rgba(PAL.alarm, 0.06 + 0.08 * p); ctx.fillRect(0, 0, w, h);
        if (c) {
          const x = c.x + dx, y = c.y + dy, R = 26 + p * 40, rg = ctx.createRadialGradient(x, y, 0, x, y, R);
          rg.addColorStop(0, rgba(PAL.alarm, 0.45 * p)); rg.addColorStop(1, rgba(PAL.alarm, 0));
          ctx.fillStyle = rg; ctx.fillRect(x - R, y - R, R * 2, R * 2);
        }
        ctx.fillStyle = rgba(PAL.alarm, 0.5 + 0.5 * p); ctx.fillRect(0, h - 2, w, 2);
      }
    }, (f) => {
      const P = buildPlan(f.w + 60, f.h + 24, 7, { bay: 66, margin: 12, cams: Math.max(4, Math.round(f.w / 80)), workers: 4 });
      const bg = plate(f.w, f.h, f.dpr, { grid: 16, cx: f.w * 0.72, cy: f.h * 0.5 });
      const layers = [0, 1, 2].map((bi) => { const o = off(P.W, P.H, f.dpr); drawPlan(o.ctx, P, bi, null, 0.5); return o.cv; });
      return { P, bg, layers };
    });
    return { setAlarm(v) { if (S.alarm !== !!v) { S.alarm = !!v; a.wake(); } } };
  }

  // ---------------------------------------------------------------- 2. role illustrations (100×100 units)
  function bust(ctx, T, bi, o) {
    const b = Math.sin(T * 1.7 + o.seed) * 0.7, W = PAL.white, sd = o.seed * 20;
    ctx.save(); ctx.translate(o.cx - 50, 0);
    const torso = [[9, 101], [12, 86], [21, 77.5], [37, 72.5 + b * 0.3], [50, 71.5 + b * 0.3], [63, 72.5 + b * 0.3], [79, 77.5], [88, 86], [91, 101]];
    ctx.fillStyle = PAL.navyLight; poly(ctx, torso); ctx.fill();
    const vl = [[19, 101], [20, 85], [27.5, 78], [40, 73.5], [46, 87], [45, 101]], vr = vl.map((p) => [100 - p[0], p[1]]);
    [vl, vr].forEach((v, i) => {
      ink(ctx, v, { closed: true, fill: o.vest, w: 0.9, color: W, seed: sd + i, boil: bi, wobble: 0.45, alpha: 0.95 });
      ctx.save(); poly(ctx, v); ctx.clip(); ctx.fillStyle = PAL.reflect; ctx.fillRect(0, 87.5, 100, 2.4); ctx.fillRect(0, 94.5, 100, 2.4); ctx.restore();
      hatch(ctx, v, { angle: i ? -0.95 : 0.95, gap: 2.3, color: PAL.navyDeep, alpha: i ? 0.4 : 0.14, w: 0.5, seed: 3 + i });
    });
    ink(ctx, torso, { w: 1.3, color: W, seed: sd + 3, boil: bi, wobble: 0.55 });
    ink(ctx, [[41, 73.5], [50, 82], [59, 73.5]], { w: 1, color: W, seed: sd + 4, boil: bi, wobble: 0.3 });
    ink(ctx, [[45, 62], [45.5, 72]], { w: 1.1, color: W, seed: sd + 5, boil: bi, wobble: 0.2 });
    ink(ctx, [[55, 62], [54.5, 72]], { w: 1.1, color: W, seed: sd + 6, boil: bi, wobble: 0.2 });
    ctx.translate(0, b * 0.5);
    ink(ctx, ell(50, 52, 11.5, 13.2), { closed: true, w: 1.4, color: W, seed: sd + 7, boil: bi, wobble: 0.45, fill: PAL.navyLight, offX: 0, offY: 0 });
    ink(ctx, ell(38.8, 54, 1.8, 3, Math.PI * 0.5, Math.PI * 1.5, 8), { w: 1, color: W, seed: sd + 8, boil: bi, wobble: 0.2 });
    ink(ctx, ell(61.2, 54, 1.8, 3, -Math.PI * 0.5, Math.PI * 0.5, 8), { w: 1, color: W, seed: sd + 9, boil: bi, wobble: 0.2 });
    const blink = (T + o.seed * 1.3) % 4.3 < 0.14;
    ctx.fillStyle = W; ctx.strokeStyle = W; ctx.lineWidth = 0.9;
    [45.5, 54.5].forEach((x) => { if (blink) { ctx.beginPath(); ctx.moveTo(x - 1.4, 53.5); ctx.lineTo(x + 1.4, 53.5); ctx.stroke(); } else { ctx.beginPath(); ctx.arc(x, 53.5, 1, 0, TAU); ctx.fill(); } });
    ink(ctx, ell(50, 57.5, 3.2, 1.8, 0.35, Math.PI - 0.35, 8), { w: 0.9, color: W, seed: sd + 10, boil: bi, wobble: 0.15 });
    const dome = ell(50, 46, 14.5, 12.8, Math.PI, TAU, 22);
    ink(ctx, dome, { closed: true, fill: o.helmet, w: 1.3, color: W, seed: sd + 11, boil: bi, wobble: 0.4 });
    hatch(ctx, dome, { angle: -0.7, gap: 2, color: PAL.navyDeep, alpha: 0.35, w: 0.5, seed: 9, clipX: 54 });
    const brim = [[32, 45.2], [68, 45.2], [69.5, 47.6], [30.5, 47.6]];
    ink(ctx, brim, { closed: true, fill: o.helmet, w: 1.1, color: W, seed: sd + 12, boil: bi, wobble: 0.3, smooth: false });
    ink(ctx, [[50, 33.6], [50, 45]], { w: 1.4, color: PAL.navyDeep, alpha: 0.5, seed: sd + 13, boil: bi, wobble: 0.2 });
    ink(ctx, ell(50, 46, 11, 9.6, Math.PI * 1.12, Math.PI * 1.38, 8), { w: 1.1, color: '#fff', alpha: 0.75, seed: sd + 14, boil: bi, wobble: 0.15 });
    ctx.restore();
  }

  const SCENES = {
    worker(ctx, T, bi) {
      ink(ctx, ell(50, 47, 31, 31), { closed: true, w: 0.7, color: PAL.lavender, alpha: 0.3, dash: [2, 4], seed: 2, boil: bi, wobble: 0.3 });
      ticks(ctx, 50, 47, 34, 48, 1.6, PAL.lavender, 0.35, 6, Math.PI * 1.05, Math.PI * 0.9);
      ctx.strokeStyle = rgba(PAL.lavender, 0.3); ctx.lineWidth = 0.6; ctx.beginPath(); ctx.moveTo(6, 47); ctx.lineTo(94, 47); ctx.moveTo(50, 6); ctx.lineTo(50, 14); ctx.stroke();
      bust(ctx, T, bi, { cx: 50, seed: 1, helmet: PAL.hazard, vest: PAL.signal });
      // a dimension bracket checks the helmet: the PPE is on
      ink(ctx, [[80, 33], [80, 47]], { w: 0.8, color: PAL.cyan, alpha: 0.8, seed: 31, boil: bi, wobble: 0.2 });
      ink(ctx, [[77.5, 33], [82.5, 33]], { w: 0.8, color: PAL.cyan, alpha: 0.8, seed: 32, boil: bi, wobble: 0.1 });
      ink(ctx, [[77.5, 47], [82.5, 47]], { w: 0.8, color: PAL.cyan, alpha: 0.8, seed: 33, boil: bi, wobble: 0.1 });
      const p = (T % 3.2) / 3.2;
      ink(ctx, [[84.5, 40], [86.5, 42.5], [91, 36.5]], { w: 1.4, color: PAL.ok, seed: 34, boil: bi, wobble: 0.15, draw: eOut(seg(p, 0.05, 0.3)) });
    },
    operator(ctx, T, bi) {
      // a wall of camera feeds; one of them catches a violation and blinks red
      const x0 = 11, y0 = 6, w = 24.5, h = 19, g = 2.5, hot = 5, al = T % 1.6 < 1;
      for (let r = 0; r < 2; r++) for (let c = 0; c < 3; c++) {
        const i = r * 3 + c, x = x0 + c * (w + g), y = y0 + r * (h + g), isHot = i === hot;
        ink(ctx, [[x, y], [x + w, y], [x + w, y + h], [x, y + h]], { closed: true, fill: PAL.navyDeep, w: isHot && al ? 1.3 : 0.9, color: isHot && al ? PAL.alarm : PAL.white, seed: 90 + i, boil: bi, wobble: 0.25, smooth: false, offX: 0, offY: 0 });
        ctx.save(); ctx.beginPath(); ctx.rect(x + 1, y + 1, w - 2, h - 2); ctx.clip();
        ctx.strokeStyle = rgba(PAL.cyan, 0.32); ctx.lineWidth = 0.4; ctx.beginPath();
        for (let k = -2; k <= 2; k++) { ctx.moveTo(x + w / 2 + k * 2, y + h * 0.42); ctx.lineTo(x + w / 2 + k * 7, y + h); }
        ctx.moveTo(x, y + h * 0.42); ctx.lineTo(x + w, y + h * 0.42); ctx.stroke();
        const px = x + 3 + ((T * (0.1 + i * 0.025) + h3(i, 3, 5)) % 1) * (w - 6), py = y + h * 0.74;
        ctx.fillStyle = isHot ? PAL.alarm : PAL.hazard; ctx.beginPath(); ctx.arc(px, py - 3.4, 1.1, 0, TAU); ctx.fill();
        ctx.fillStyle = rgba(PAL.white, 0.8); ctx.fillRect(px - 0.8, py - 2.2, 1.6, 3.6);
        if (isHot) { ctx.strokeStyle = al ? PAL.alarm : rgba(PAL.alarm, 0.45); ctx.lineWidth = 0.7; ctx.strokeRect(px - 2.6, py - 5.4, 5.2, 9); }
        ctx.fillStyle = rgba(PAL.cyan, 0.16); ctx.fillRect(x, y + ((T * 0.45 + i * 0.17) % 1) * h, w, 1.1);
        ctx.restore();
        ctx.fillStyle = (T * 1.1 + i * 0.37) % 1 < 0.5 ? '#FF5A4D' : rgba(PAL.white, 0.25);
        ctx.beginPath(); ctx.arc(x + 2.4, y + 2.4, 0.7, 0, TAU); ctx.fill();
      }
      ink(ctx, [[5, 78], [95, 78]], { w: 1.2, color: PAL.white, seed: 101, boil: bi, wobble: 0.3 });
      ink(ctx, [[61, 75], [82, 75], [83.5, 78], [59.5, 78]], { closed: true, fill: PAL.navyLight, w: 0.8, color: PAL.white, seed: 102, boil: bi, wobble: 0.15, smooth: false, offX: 0, offY: 0 });
      // the operator, seen from behind, glancing towards the red feed
      const turn = 1.2 + Math.sin(T * 0.7) * 1.2 + (al ? 0.8 : 0);
      ink(ctx, [[25, 101], [29, 90], [39, 83.5], [50, 82], [61, 83.5], [71, 90], [75, 101]], { w: 1.3, color: PAL.white, seed: 103, boil: bi, wobble: 0.45, fill: PAL.navyLight, offX: 0, offY: 0 });
      const head = ell(50 + turn, 71, 8, 9.2);
      ink(ctx, head, { closed: true, w: 1.3, color: PAL.white, seed: 104, boil: bi, wobble: 0.35, fill: PAL.navyLight, offX: 0, offY: 0 });
      hatch(ctx, head, { angle: 1.2, gap: 1.6, color: PAL.lavender, alpha: 0.4, w: 0.45, seed: 7 });
      ink(ctx, ell(50 + turn, 71, 9.6, 10.4, Math.PI * 1.08, Math.PI * 1.92, 14), { w: 1.5, color: PAL.white, seed: 105, boil: bi, wobble: 0.15 });
      ctx.fillStyle = PAL.signal;
      ctx.fillRect(40.6 + turn, 69, 2.6, 6); ctx.fillRect(56.8 + turn, 69, 2.6, 6);
      ink(ctx, [[59.4 + turn, 73.5], [63 + turn, 77], [61.5 + turn, 80]], { w: 0.9, color: PAL.cyan, seed: 106, boil: bi, wobble: 0.1 });
    },
    supervisor(ctx, T, bi) {
      const cx = 79, cy = 23, r = 13;
      ink(ctx, ell(cx, cy, r, r), { closed: true, w: 1.1, color: PAL.white, seed: 41, boil: bi, wobble: 0.35, fill: PAL.navy, offX: 0, offY: 0 });
      ticks(ctx, cx, cy, r - 3, 12, 1.4, PAL.white, 0.8, 3);
      const sp = 0.25 + ((T * 0.02) % 0.75);
      ctx.strokeStyle = PAL.signal; ctx.lineWidth = 1.8; ctx.beginPath(); ctx.arc(cx, cy, r + 2.6, -Math.PI / 2, -Math.PI / 2 + sp * TAU); ctx.stroke();
      const am = T * 0.9, ah = T * 0.075 + 1.2;
      ctx.strokeStyle = PAL.white; ctx.lineCap = 'round';
      ctx.lineWidth = 1.1; ctx.beginPath(); ctx.moveTo(cx, cy); ctx.lineTo(cx + Math.cos(am) * (r - 4), cy + Math.sin(am) * (r - 4)); ctx.stroke();
      ctx.lineWidth = 1.6; ctx.beginPath(); ctx.moveTo(cx, cy); ctx.lineTo(cx + Math.cos(ah) * (r - 7), cy + Math.sin(ah) * (r - 7)); ctx.stroke();
      glow(ctx, cx, cy, 1.4, PAL.cyan, 0.9);
      bust(ctx, T, bi, { cx: 44, seed: 2, helmet: PAL.helmetW, vest: PAL.hazard });
      // radio on the shoulder, waves ripple out
      ink(ctx, [[17, 66], [22, 66], [22, 79], [17, 79]], { closed: true, fill: PAL.navyDeep, w: 0.9, color: PAL.white, seed: 51, boil: bi, wobble: 0.2, smooth: false });
      ink(ctx, [[19, 66], [19, 57]], { w: 1, color: PAL.white, seed: 52, boil: bi, wobble: 0.1 });
      for (let i = 0; i < 3; i++) {
        const k = (T * 0.9 + i / 3) % 1;
        ctx.strokeStyle = rgba(PAL.cyan, (1 - k) * 0.8); ctx.lineWidth = 0.8;
        ctx.beginPath(); ctx.arc(19, 57, 2 + k * 9, -Math.PI * 0.85, -Math.PI * 0.15); ctx.stroke();
      }
      // tablet with the alarm feed, one line blinking red
      ctx.save(); ctx.translate(66, 88); ctx.rotate(-0.16);
      ink(ctx, [[-12, -8.5], [12, -8.5], [12, 8.5], [-12, 8.5]], { closed: true, fill: PAL.navyDeep, w: 1.1, color: PAL.white, seed: 61, boil: bi, wobble: 0.25, smooth: false, offX: 0, offY: 0 });
      for (let i = 0; i < 4; i++) {
        const al = i === 1 && (T % 1.4) < 0.8;
        ctx.fillStyle = al ? PAL.alarm : rgba(PAL.cyan, 0.7);
        ctx.fillRect(-9, -5.5 + i * 3.6, al ? 14 : 8 + ((i * 5) % 7), 1.6);
      }
      ctx.restore();
      ink(ctx, [[52, 93], [55, 90.5], [57.5, 93]], { w: 1, color: PAL.white, seed: 62, boil: bi, wobble: 0.15 });
    },
    director(ctx, T, bi) {
      ink(ctx, [[4, 86], [96, 86]], { w: 1.2, color: PAL.white, seed: 71, boil: bi, wobble: 0.4 });
      for (let x = 6; x < 95; x += 4) { ctx.strokeStyle = rgba(PAL.lavender, 0.35); ctx.lineWidth = 0.5; ctx.beginPath(); ctx.moveTo(x, 86.5); ctx.lineTo(x - 2.5, 90); ctx.stroke(); }
      const shed = [[8, 86], [8, 63], [16, 54], [16, 63], [24, 54], [24, 63], [32, 54], [32, 63], [40, 54], [40, 86]];
      ink(ctx, shed, { closed: true, fill: PAL.navyLight, w: 1.2, color: PAL.white, seed: 72, boil: bi, wobble: 0.35, smooth: false, offX: 0, offY: 0 });
      hatch(ctx, shed, { angle: 0.9, gap: 2.6, color: PAL.lavender, alpha: 0.18, w: 0.5, seed: 4 });
      const blk = [[40, 86], [40, 67], [62, 67], [62, 86]];
      ink(ctx, blk, { closed: true, fill: PAL.navyLight, w: 1.2, color: PAL.white, seed: 73, boil: bi, wobble: 0.3, smooth: false, offX: 0, offY: 0 });
      ink(ctx, [[47, 67], [47, 43], [53, 43], [53, 67]], { w: 1.1, color: PAL.white, seed: 74, boil: bi, wobble: 0.25, smooth: false });
      ctx.fillStyle = PAL.alarm; ctx.fillRect(47.2, 47, 5.6, 1.6); ctx.fillRect(47.2, 52, 5.6, 1.6);
      for (let i = 0; i < 3; i++) for (let j = 0; j < 2; j++) {
        const lit = h3(i, j, Math.floor(T / 2.5)) > 0.45;
        ctx.fillStyle = lit ? rgba(PAL.hazard, 0.85) : rgba(PAL.lavender, 0.25); ctx.fillRect(12 + i * 9, 68 + j * 8, 4.5, 4);
      }
      ctx.fillStyle = rgba(PAL.white, 0.9); ctx.fillRect(44, 75, 6, 11);
      for (let i = 0; i < 4; i++) {
        const k = ((T * 0.22 + i / 4) % 1), x = 50 + k * 16 + Math.sin(k * 6 + i) * 2, y = 41 - k * 26;
        ctx.strokeStyle = rgba(PAL.lavender, 0.7 * (1 - k)); ctx.lineWidth = 0.8;
        ctx.beginPath(); ctx.arc(x, y, 2 + k * 5, 0, TAU); ctx.stroke();
      }
      // the chart: violations falling over the shift; a glowing dot rides the line
      const ax = 64, ay = 52, aw = 30, ah = 36;
      ink(ctx, [[ax, ay - ah], [ax, ay], [ax + aw, ay]], { w: 1, color: PAL.white, seed: 75, boil: bi, wobble: 0.2, smooth: false });
      ctx.strokeStyle = rgba(PAL.lavender, 0.4); ctx.lineWidth = 0.5;
      for (let i = 1; i < 4; i++) { ctx.beginPath(); ctx.moveTo(ax, ay - (i * ah) / 4); ctx.lineTo(ax + aw, ay - (i * ah) / 4); ctx.stroke(); }
      const vals = [0.82, 0.9, 0.64, 0.7, 0.42, 0.35, 0.22];
      const pts = vals.map((v, i) => [ax + 2 + (i * (aw - 4)) / (vals.length - 1), ay - v * ah * (0.96 + 0.04 * Math.sin(T * 1.3 + i))]);
      vals.forEach((v, i) => { ctx.fillStyle = rgba(PAL.cyan, 0.16); ctx.fillRect(pts[i][0] - 1.6, pts[i][1], 3.2, ay - pts[i][1]); });
      ink(ctx, pts, { w: 1.4, color: PAL.cyan, seed: 76, boil: bi, wobble: 0.25 });
      ink(ctx, [[ax, ay - 0.5 * ah], [ax + aw, ay - 0.5 * ah]], { w: 0.9, color: PAL.hazard, dash: [2, 2], seed: 77, boil: bi, wobble: 0.1 });
      const u = (T * 0.25) % 1, k = u * (pts.length - 1), i0 = Math.floor(k);
      const p0 = pts[i0], p1 = pts[Math.min(pts.length - 1, i0 + 1)];
      glow(ctx, lerp(p0[0], p1[0], k - i0), lerp(p0[1], p1[1], k - i0), 1.6, PAL.cyan, 1);
      ink(ctx, ell(29, 52, 30, 30, Math.PI * 1.1, Math.PI * 1.9, 20), { w: 0.7, color: PAL.lavender, alpha: 0.4, dash: [2, 3], seed: 78, boil: bi, wobble: 0.2 });
    },
  };

  function role(cv, kind) {
    return actor(cv, 12, (ctx, st, T) => {
      const { w, h } = st, s = Math.min(w, h) / 100;
      ctx.clearRect(0, 0, w, h);
      ctx.save(); ctx.translate((w - 100 * s) / 2, (h - 100 * s) / 2); ctx.scale(s, s);
      SCENES[kind](ctx, T, Math.floor(T * 12) % 4);
      ctx.restore();
    });
  }

  // ---------------------------------------------------------------- empty-state stills (120×80 units, drawn once)
  function still(cv, kind, o) {
    o = o || {};
    const paint = () => {
      const f = fit(cv), s = Math.min(f.w / 120, f.h / 80), c = f.ctx, I = o.ink || PAL.white, A = o.line || PAL.cyan;
      c.clearRect(0, 0, f.w, f.h);
      c.save(); c.translate((f.w - 120 * s) / 2, (f.h - 80 * s) / 2); c.scale(s, s);
      const L = (pts, x) => ink(c, pts, Object.assign({ w: 1.3, color: I, seed: 3, wobble: 0.5 }, x));
      L([[14, 70], [106, 70]], { w: 1, alpha: 0.6, seed: 1 });
      for (let x = 18; x < 104; x += 5) L([[x, 71], [x - 3, 75]], { w: 0.6, alpha: 0.4, seed: x, wobble: 0.1 });
      if (kind === 'ok' || kind === 'alarm') {
        const dome = ell(52, 58, 24, 22, Math.PI, TAU, 22);
        L(dome, { closed: true, fill: PAL.hazard, seed: 5 });
        hatch(c, dome, { angle: -0.7, gap: 2.6, color: o.hatch || '#5a4100', alpha: 0.35, w: 0.6, clipX: 58 });
        L([[24, 57], [80, 57], [83, 62], [21, 62]], { closed: true, fill: PAL.hazard, smooth: false, seed: 6 });
        L([[52, 36], [52, 57]], { w: 2, color: '#000', alpha: 0.25, seed: 7 });
        L(ell(92, 34, 11, 11), { closed: true, color: kind === 'ok' ? PAL.ok : A, seed: 8 });
        L(kind === 'ok' ? [[87, 34], [91, 38.5], [98, 29]] : [[87, 29], [97, 39]], { w: 1.8, color: kind === 'ok' ? PAL.ok : A, seed: 9 });
        if (kind === 'alarm') L([[97, 29], [87, 39]], { w: 1.8, color: A, seed: 10 });
      } else if (kind === 'cam') {
        L([[24, 22], [24, 40], [36, 40]], { w: 1.4, smooth: false, seed: 11 });
        c.save(); c.translate(48, 34); c.rotate(0.3);
        L([[-14, -7], [14, -7], [14, 7], [-14, 7]], { closed: true, fill: o.fill || PAL.navyLight, smooth: false, seed: 12 });
        L([[14, -5], [20, -8], [20, 8], [14, 5]], { closed: true, smooth: false, seed: 13 });
        c.fillStyle = PAL.alarm; c.beginPath(); c.arc(-9, -2, 1.6, 0, TAU); c.fill();
        c.restore();
        L([[67, 41], [108, 52]], { w: 0.9, color: A, dash: [3, 3], seed: 14 });
        L([[66, 46], [96, 68]], { w: 0.9, color: A, dash: [3, 3], seed: 15 });
      } else if (kind === 'people') {
        [[34, 0.9, PAL.helmetW], [60, 1.05, PAL.hazard], [86, 0.9, PAL.helmetW]].forEach(([x, k, hc], i) => {
          L(ell(x, 50, 9 * k, 10.5 * k), { closed: true, seed: 20 + i });
          L(ell(x, 45, 11 * k, 10 * k, Math.PI, TAU, 16), { closed: true, fill: hc, seed: 30 + i });
          L([[x - 16 * k, 70], [x - 12 * k, 63], [x, 60 + 2 * k], [x + 12 * k, 63], [x + 16 * k, 70]], { seed: 40 + i });
        });
      } else {
        L([[38, 18], [82, 18], [82, 62], [38, 62]], { closed: true, smooth: false, seed: 50 });
        for (let y = 22; y < 60; y += 8) { L([[41, y], [45, y]], { w: 1, seed: 51 + y }); L([[75, y], [79, y]], { w: 1, seed: 52 + y }); }
        L([[60, 50], [60, 28]], { w: 1.8, color: A, seed: 53 });
        L([[52, 35], [60, 27], [68, 35]], { w: 1.8, color: A, seed: 54 });
      }
      c.restore();
    };
    paint();
    if ('ResizeObserver' in window) { const ro = new ResizeObserver(() => { if (!cv.isConnected) ro.disconnect(); else paint(); }); ro.observe(cv); }
  }

  // ---------------------------------------------------------------- paper grain tiles for CSS
  function textures() {
    const make = (dark) => {
      const c = document.createElement('canvas'); c.width = c.height = 160;
      const g = c.getContext('2d'), r = rng(dark ? 'chalk' : 'paper');
      const col = dark ? '238,240,255' : '60,40,20';
      for (let i = 0; i < 1500; i++) { g.fillStyle = 'rgba(' + col + ',' + r.range(0.03, dark ? 0.09 : 0.12) + ')'; g.fillRect(r() * 160, r() * 160, r.range(0.6, 1.4), r.range(0.6, 1.4)); }
      g.lineWidth = 0.5;
      for (let i = 0; i < 26; i++) {
        const x = r() * 160, y = r() * 160, a = r() * TAU, L = r.range(4, 12);
        g.strokeStyle = 'rgba(' + col + ',' + r.range(0.04, 0.09) + ')';
        g.beginPath(); g.moveTo(x, y); g.quadraticCurveTo(x + Math.cos(a) * L * 0.5 + r.range(-2, 2), y + Math.sin(a) * L * 0.5 + r.range(-2, 2), x + Math.cos(a) * L, y + Math.sin(a) * L); g.stroke();
      }
      return 'url(' + c.toDataURL('image/png') + ')';
    };
    const root = document.documentElement.style;
    root.setProperty('--grain-ink', make(false));
    root.setProperty('--grain-chalk', make(true));
  }

  window.Art = { intro, header, role, still, textures, reduced, PAL };
})();
