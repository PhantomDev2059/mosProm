/*
 * 99 close — синий чертёж: «Увидели. Сообщили. Устранили в срок.» и знак LinQ Guard.
 * Слои: плита → три строки по долям такта → линия-подчёркивание тушью → знак и адреса.
 */
(function () {
  'use strict';
  const ID = 'close';
  const FILM = window.FILM;
  const L = FILM.lib;
  const K = FILM.K;

  FILM.scene({
    id: ID,
    draw(ctx, tIn, info) {
      const t = L.clamp(tIn, 0, info.dur);
      K.navyPlate(ctx, info.T, { center: [960, 540], seed: 9 });
      const rows = [
        ['Увидели.', K.C.cyan, 0.0],
        ['Сообщили.', K.C.yellow, 0.5],
        ['Устранили в срок.', K.C.greenLight, 1.0],
      ];
      rows.forEach(([s, c, a], i) => {
        const p = L.seg(t, a, a + 0.4, 'outCubic');
        if (p <= 0) return;
        ctx.save();
        ctx.globalAlpha = p;
        K.text(ctx, s, 128, 300 + i * 160 + (1 - p) * 30, { kind: 'disp', size: 150, weight: 800, color: c });
        ctx.restore();
      });
      L.inkLine(ctx, 132, 700, 132 + 1100 * L.seg(t, 1.6, 2.3, 'inOutSine'), 700, { color: K.C.orange, width: 5, seed: K.sd(ID, 'u') });
      K.text(ctx, 'LinQ Guard', 128, 830, { kind: 'disp', size: 72, weight: 800, color: K.C.chalk, alpha: L.seg(t, 2.0, 2.5) });
      K.text(ctx, 'видеоконтроль промышленной безопасности на ускорителе LinQ TPU', 132, 884, { kind: 'body', size: 30, weight: 500, color: K.C.pale, alpha: L.seg(t, 2.2, 2.7) });
      K.text(ctx, '@linq_control_bot · github.com/PhantomDev2059/mosProm', 132, 1000, { kind: 'mono', size: 24, weight: 500, color: K.C.cyan, alpha: L.seg(t, 2.6, 3.1) });
    },
  });
})();
