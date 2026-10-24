/*
 * 90 summary — бумага: итог смены. Полоса смены целиком со всеми отметками, четыре цифры, отчёт руководителю.
 * Слои: плита → заголовок и часы → карточки-цифры → большая полоса смены с подписями → строка отчёта.
 */
(function () {
  'use strict';
  const ID = 'summary';
  const FILM = window.FILM;
  const L = FILM.lib;
  const K = FILM.K;

  FILM.scene({
    id: ID,
    draw(ctx, tIn, info) {
      const t = L.clamp(tIn, 0, info.dur);
      const T = info.T;
      const S = FILM.SHIFT;
      const evs = S.events;
      K.paperPlate(ctx, T, { c1: '#F1E7D2', c2: '#E9DDC3' });
      K.clock(ctx, { x: 1824, y: 112, min: L.lerp(evs[evs.length - 1].min, 720, L.seg(t, 0, 0.8, 'inOutCubic')) });
      K.text(ctx, 'ИТОГ СМЕНЫ', 96, 70, { kind: 'mono', size: 22, weight: 700, color: '#B5560C', track: 3 });
      K.text(ctx, 'Смена закрыта: все тревоги устранены', 90, 150, { kind: 'disp', size: 76, weight: 800, color: K.C.ink, alpha: L.seg(t, 0.1, 0.5) });

      // цифры
      const st = S.stats;
      const cards = [
        { big: String(st.total), sub: 'нарушений за смену', note: `${st.types} видов`, color: K.C.ink },
        { big: String(st.critical), sub: 'критичных тревог', note: 'падения, огонь, дым', color: K.C.red },
        { big: `${st.resolved}/${st.total}`, sub: 'устранены в срок', note: 'срок — 30 мин', color: K.C.green },
        { big: st.avgAck, sub: 'среднее время до «Принял»', note: `устранение — ${st.avgRes}`, color: '#B5560C' },
      ];
      const cw = 405, gap = 36, y0 = 220;
      cards.forEach((c, i) => {
        const a = L.seg(t, 0.5 + i * 0.25, 0.9 + i * 0.25, 'outCubic');
        if (a <= 0) return;
        const x = 96 + i * (cw + gap);
        const dy = (1 - a) * 30;
        ctx.save();
        ctx.globalAlpha = a;
        ctx.fillStyle = 'rgba(247,242,231,0.92)';
        ctx.fillRect(x, y0 + dy, cw, 236);
        K.inkRect(ctx, x, y0 + dy, cw, 236, { color: K.C.ink, width: 2.6, seed: K.sd(ID, 'c', i) });
        K.text(ctx, c.big, x + 28, y0 + 120 + dy, { kind: 'disp', size: 104, weight: 800, color: c.color });
        K.text(ctx, c.sub, x + 30, y0 + 170 + dy, { kind: 'body', size: 26, weight: 700, color: K.C.ink });
        K.text(ctx, c.note, x + 30, y0 + 206 + dy, { kind: 'body', size: 22, weight: 500, color: K.C.ink2 });
        ctx.restore();
      });

      // полоса смены целиком
      const sa = L.seg(t, 1.6, 2.2);
      ctx.save();
      ctx.globalAlpha = sa;
      const marks = evs.map((e) => ({ min: e.min, sev: e.sev, done: true, short: e.short2 }));
      K.strip(ctx, { x: 120, y: 650, w: 1680, now: 720 * L.seg(t, 1.6, 3.2, 'inOutSine'), marks, labels: true, T });
      ctx.restore();

      // отчёт
      const ra = L.seg(t, 3.6, 4.1);
      if (ra > 0) {
        ctx.save();
        ctx.globalAlpha = ra;
        const x = 96, y = 800;
        ctx.fillStyle = '#17212B';
        K.rrect(ctx, x, y, 1000, 120, 20);
        ctx.fill();
        ctx.fillStyle = K.C.orange;
        ctx.beginPath();
        ctx.arc(x + 46, y + 60, 24, 0, L.TAU);
        ctx.fill();
        K.text(ctx, 'LG', x + 46, y + 69, { kind: 'disp', size: 22, weight: 800, color: K.C.navy, align: 'center' });
        K.text(ctx, 'Отчёт смены 24.10 → руководителю', x + 92, y + 52, { kind: 'body', size: 30, weight: 700, color: '#FFFFFF' });
        K.text(ctx, `${st.total} нарушений · время реакции по каждому · CSV-журнал`, x + 92, y + 92, { kind: 'body', size: 24, weight: 500, color: '#9FB0C2' });
        ctx.restore();
        K.text(ctx, 'рабочим — личные итоги смены', 1180, 852, { kind: 'hand', size: 44, weight: 600, color: K.C.ink2, alpha: L.seg(t, 4.4, 4.9) });
        K.text(ctx, 'по фото лица, без ФИО', 1180, 900, { kind: 'hand', size: 40, weight: 500, color: K.C.ink2, alpha: L.seg(t, 4.6, 5.1) });
      }
    },
  });
})();
