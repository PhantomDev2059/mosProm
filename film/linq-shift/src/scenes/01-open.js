/*
 * 01 open — синий чертёж: план цеха прорисовывается тушью, на нём оживают семь камер, слева — «Смена 24.10».
 * Слои: плита → план (стены, участки, подписи) → камеры с конусами → заголовок → часы → полоса смены.
 */
(function () {
  'use strict';
  const ID = 'open';
  const FILM = window.FILM;
  const L = FILM.lib;
  const K = FILM.K;

  // план: участки в координатах кадра (правая часть), камера каждого участка
  const PX = 930, PY = 150, PW = 900, PH = 760;
  const ROOMS = [
    { id: 'К-01', name: 'ЦЕХ №1 · МАСТЕРСКАЯ', r: [0, 0, 0.36, 0.42], cam: [0.04, 0.05, 0.75] },
    { id: 'К-02', name: 'СКЛАД · ПРОЕЗД', r: [0.36, 0, 0.36, 0.42], cam: [0.69, 0.05, 2.3] },
    { id: 'К-07', name: 'СКЛАД ГОТ. ПРОДУКЦИИ', r: [0.72, 0, 0.28, 0.42], cam: [0.97, 0.06, 2.4] },
    { id: 'К-03', name: 'ЦЕХ №2', r: [0, 0.42, 0.46, 0.33], cam: [0.04, 0.47, 0.6] },
    { id: 'К-04', name: 'АБК · КОРИДОР', r: [0.46, 0.42, 0.26, 0.33], cam: [0.5, 0.46, 0.8] },
    { id: 'К-06', name: 'ПРОМПЛОЩАДКА', r: [0.72, 0.42, 0.28, 0.58], cam: [0.96, 0.96, -2.3] },
    { id: 'К-05', name: 'ТЕРРИТОРИЯ', r: [0, 0.75, 0.72, 0.25], cam: [0.42, 0.79, 1.3] },
  ];
  const X = (u) => PX + u * PW, Y = (v) => PY + v * PH;

  function plan(ctx, t, T) {
    const chalk = K.C.chalk;
    // внешний контур
    K.inkRect(ctx, PX, PY, PW, PH, { color: chalk, width: 3.4, seed: K.sd(ID, 'outer'), draw: L.seg(t, 0, 1.2, 'inOutSine') });
    ROOMS.forEach((rm, i) => {
      const [u, v, w, h] = rm.r;
      const d = L.seg(t, 0.3 + i * 0.16, 1.3 + i * 0.16, 'inOutSine');
      K.inkRect(ctx, X(u), Y(v), w * PW, h * PH, { color: L.rgba(chalk, 0.8), width: 2, seed: K.sd(ID, 'room', i), draw: d });
      // штриховка-«пол» участка
      if (d > 0.9) {
        ctx.save();
        ctx.globalAlpha = 0.18 * L.seg(t, 1.3 + i * 0.16, 2 + i * 0.16);
        L.hatch(ctx, L.rectPts(X(u) + 8, Y(v) + 8, w * PW - 16, h * PH - 16), { color: '#9CC2EA', spacing: 22, width: 1, angle: -0.8, seed: K.sd(ID, 'h', i) });
        ctx.restore();
      }
      const la = L.seg(t, 1.6 + i * 0.12, 2.1 + i * 0.12);
      K.text(ctx, rm.name, X(u) + 18, Y(v) + h * PH - 18, { kind: 'mono', size: 17, weight: 600, color: K.C.pale, alpha: la, track: 1.5 });
    });
  }

  function cameras(ctx, t, T) {
    ROOMS.forEach((rm, i) => {
      const a0 = 2.4 + i * 0.25; // по долям такта: камеры зажигаются одна за другой
      if (t < a0) return;
      const p = L.seg(t, a0, a0 + 0.35, 'outBack');
      const [cu, cv, ang] = rm.cam;
      const cx = X(cu), cy = Y(cv);
      const sweep = ang + 0.32 * Math.sin(T * 0.9 + i * 1.7);
      const R = 210 * p;
      const g = ctx.createRadialGradient(cx, cy, 0, cx, cy, R);
      g.addColorStop(0, 'rgba(127,208,232,0.34)');
      g.addColorStop(1, 'rgba(127,208,232,0)');
      ctx.fillStyle = g;
      ctx.beginPath();
      ctx.moveTo(cx, cy);
      ctx.arc(cx, cy, R, sweep - 0.36, sweep + 0.36);
      ctx.closePath();
      ctx.fill();
      ctx.fillStyle = K.C.chalk;
      ctx.beginPath();
      ctx.arc(cx, cy, 9 * p, 0, L.TAU);
      ctx.fill();
      ctx.fillStyle = K.C.red;
      ctx.beginPath();
      ctx.arc(cx, cy, 3.5 * p, 0, L.TAU);
      ctx.fill();
      const lx = cx + (cu > 0.5 ? -16 : 16);
      K.text(ctx, rm.id, lx, cy + (cv > 0.5 ? -16 : 30), { kind: 'mono', size: 18, weight: 700, color: K.C.cyan, align: cu > 0.5 ? 'right' : 'left', alpha: L.seg(t, a0 + 0.1, a0 + 0.4) });
    });
  }

  FILM.scene({
    id: ID,
    draw(ctx, tIn, info) {
      const t = L.clamp(tIn, 0, info.dur);
      const T = info.T;
      K.navyPlate(ctx, T, { center: [1380, 530] });
      plan(ctx, t, T);
      cameras(ctx, t, T);
      // заголовок
      K.text(ctx, 'LINQ GUARD · ДЕМО-СМЕНА', 96, 236, { kind: 'mono', size: 22, weight: 600, color: K.C.orange, track: 3, alpha: L.seg(t, 0.2, 0.6) });
      const up = (a) => (1 - L.seg(t, a, a + 0.5, 'outCubic')) * 30;
      ctx.save();
      ctx.globalAlpha = L.seg(t, 0.5, 0.9);
      K.text(ctx, 'Смена 24.10', 90, 380 + up(0.5), { kind: 'disp', size: 132, weight: 800, color: K.C.chalk });
      ctx.restore();
      K.text(ctx, '08:00 — 20:00 · 7 камер · 12 часов', 96, 456, { kind: 'body', size: 34, weight: 600, color: K.C.yellow, alpha: L.seg(t, 1.0, 1.4) });
      const lines = ['Камеры цеха смотрит LinQ Guard.', 'Нарушение → тревога начальнику смены', 'в Telegram → «Принял» → «Устранено».'];
      lines.forEach((s, i) => K.text(ctx, s, 96, 540 + i * 44, { kind: 'body', size: 30, weight: 500, color: K.C.pale, alpha: L.seg(t, 1.5 + i * 0.25, 1.9 + i * 0.25) }));
      // честная сноска
      K.text(ctx, 'Рамки и тревоги — настоящие срабатывания LinQ Guard', 96, 760, { kind: 'body', size: 22, weight: 500, color: K.C.cyan, alpha: L.seg(t, 3.2, 3.7) });
      K.text(ctx, 'на заводских записях; время смены условное', 96, 792, { kind: 'body', size: 22, weight: 500, color: K.C.cyan, alpha: L.seg(t, 3.3, 3.8) });
      // часы и полоса
      ctx.save();
      ctx.globalAlpha = L.seg(t, 0.8, 1.3);
      K.clock(ctx, { x: 1824, y: 112, min: 0, dark: true });
      ctx.restore();
      ctx.save();
      ctx.globalAlpha = L.seg(t, 1.2, 1.8);
      K.strip(ctx, { x: 96, y: 1000, w: 1728, now: L.seg(t, 4.2, info.dur, 'inQuad') * ((FILM.SHIFT.events[0] || { min: 0 }).min * 0.9), marks: [], dark: true, T });
      ctx.restore();
    },
  });
})();
