/*
 * 10 violations — один шот на каждое нарушение смены (данные — FILM.SHIFT.events, по шоту на событие).
 * Слои: плита (бумага или чертёж для критичных) → шапка (номер нарушения, камера) и часы →
 * монитор с живым кадром камеры → рамка нарушителя и кольцо подтверждения (настоящий трек конвейера) →
 * подпись нарушения → сообщение бота с настоящей карточкой → статус «Принял» / «Устранено» → полоса смены.
 */
(function () {
  'use strict';
  const FILM = window.FILM;
  const L = FILM.lib;
  const K = FILM.K;
  const MON = { x: 96, y: 152, w: 1152, h: 648 };
  const SX = MON.w / 1280, SY = MON.h / 720;

  // рамка трека в момент t (линейно между кадрами анализа, 5 к/с); null — трека нет
  function boxAt(samples, t) {
    if (!samples || !samples.length) return null;
    if (t < samples[0][0] - 0.12 || t > samples[samples.length - 1][0] + 0.25) return null;
    let i = 0;
    while (i < samples.length - 1 && samples[i + 1][0] <= t) i++;
    const a = samples[i], b = samples[Math.min(samples.length - 1, i + 1)];
    if (b[0] - a[0] > 0.6 && t > a[0] + 0.25) return null; // разрыв трека
    const u = b[0] > a[0] ? L.clamp((t - a[0]) / (b[0] - a[0])) : 0;
    const v = [1, 2, 3, 4].map((k) => a[k] + (b[k] - a[k]) * u);
    return [MON.x + v[0] * SX, MON.y + v[1] * SY, MON.x + v[2] * SX, MON.y + v[3] * SY];
  }

  // скелет человека в момент t: 17 точек между кадрами анализа (5 к/с), в экранных координатах
  function kpAt(rows, t) {
    if (!rows || !rows.length || t < rows[0][0] - 0.12 || t > rows[rows.length - 1][0] + 0.25) return null;
    let i = 0;
    while (i < rows.length - 1 && rows[i + 1][0] <= t) i++;
    const a = rows[i], b = rows[Math.min(rows.length - 1, i + 1)];
    if (b[0] - a[0] > 0.6 && t > a[0] + 0.25) return null;
    const u = b[0] > a[0] ? L.clamp((t - a[0]) / (b[0] - a[0])) : 0;
    const kp = [];
    for (let j = 0; j < 17; j++) {
      const o = 5 + 3 * j;
      const ca = a[o + 2], cb = b[o + 2];
      const ok = ca >= 0.3 && cb >= 0.3;
      const x = ok ? a[o] + (b[o] - a[o]) * u : u < 0.5 ? a[o] : b[o];
      const y = ok ? a[o + 1] + (b[o + 1] - a[o + 1]) * u : u < 0.5 ? a[o + 1] : b[o + 1];
      const c = ok ? Math.min(ca, cb) : u < 0.5 ? ca : cb;
      kp.push([MON.x + x * SX, MON.y + y * SY, c]);
    }
    return kp;
  }
  const ITEM = {
    helmet: ['каска', '#3FAE7A'], head: ['голова', '#E5484D'], no_helmet: ['без каски', '#E5484D'],
    vest: ['жилет', '#3FAE7A'], phone: ['телефон', '#F2C230'], cigarette: ['сигарета', '#F2C230'],
  };

  // цифра по калибровке пола в момент t (расстояние до техники или скорость), ближайшая по времени
  function metricAt(ev, t) {
    const vs = ev.metric && ev.metric.values;
    if (!vs || !vs.length) return null;
    let best = vs[0];
    for (const v of vs) if (Math.abs(v[0] - t) < Math.abs(best[0] - t)) best = v;
    return Math.abs(best[0] - t) < 0.5 ? best[1] : null;
  }

  function makeScene(ev, idx, all) {
    const ID = ev.shot;
    const sev = K.SEV[ev.sev] || K.SEV.high;
    const prevMin = idx > 0 ? all[idx - 1].min : 0;
    FILM.scene({
      id: ID,
      draw(ctx, tIn, info) {
        const t = L.clamp(tIn, 0, info.dur);
        const T = info.T;
        const dark = ev.plate === 'navy';
        if (dark) K.navyPlate(ctx, T, { center: [1500, 420], seed: 5 + idx });
        else K.paperPlate(ctx, T);
        const ink = dark ? K.C.chalk : K.C.ink;
        const ink2 = dark ? K.C.pale : K.C.ink2;
        const alarm = t >= ev.tOpen;
        const resolved = t >= ev.tRes;

        // часы смены: подкручиваются от прошлого нарушения к этому
        const nowMin = L.lerp(prevMin, ev.min, L.seg(t, 0, 0.7, 'inOutCubic'));
        K.clock(ctx, { x: 1824, y: 112, min: nowMin, dark });

        // шапка
        K.text(ctx, `НАРУШЕНИЕ ${idx + 1} ИЗ ${all.length}`, 96, 70, { kind: 'mono', size: 22, weight: 700, color: dark ? K.C.orange : '#B5560C', track: 3 });
        K.text(ctx, ev.place, 96, 118, { kind: 'disp', size: 40, weight: 700, color: ink, alpha: L.seg(t, 0.05, 0.4) });

        // монитор
        const flash = alarm ? Math.max(0, 1 - (t - ev.tOpen) / 0.5) : 0;
        K.monitor(ctx, { shot: info.shot, T, x: MON.x, y: MON.y, w: MON.w, h: MON.h, dark, label: ev.cam, clock: ev.min * 60 + ev.sec + (t - ev.tOpen), flash, draw: L.seg(t, 0, 0.45) });

        // рамки нарушителей (только трек события) и предметы
        ctx.save();
        ctx.beginPath();
        ctx.rect(MON.x, MON.y, MON.w, MON.h);
        ctx.clip();
        // скелеты всех людей — как их видит модель позы; нарушитель после первого сигнала — цветом тревоги
        (ev.people || []).forEach((pp) => {
          const kp = kpAt(pp.s, t);
          if (!kp) return;
          const hot = pp.violator && t >= ev.tStart;
          K.skeleton(ctx, kp, { color: hot ? sev.color : K.C.cyan, width: hot ? 4 : 2.6, alpha: hot ? 1 : 0.8 * L.seg(t, 0.1, 0.5), joint: hot ? 4.5 : 3 });
        });
        // погрузчик и дистанция до пешехода
        const vb = ev.vehicle ? boxAt(ev.vehicle.boxes, t) : null;
        const pb = ev.vehicle && ev.tracks[0] ? boxAt(ev.tracks[0].boxes, t) : null;
        if (vb) {
          const pv = L.seg(t, ev.tStart - 0.05, ev.tStart + 0.3);
          K.violator(ctx, vb, { color: K.C.cyan, label: 'ПОГРУЗЧИК', p: pv, seed: K.sd(ID, 'veh'), minY: MON.y + 50, maxX: MON.x + MON.w - 8 });
          // опасная зона — эллипс у колёс погрузчика
          if (alarm) {
            const cx = (vb[0] + vb[2]) / 2, cy = vb[3];
            const rz = (vb[2] - vb[0]) * 1.25;
            ctx.save();
            ctx.globalAlpha = 0.9 * L.seg(t, ev.tOpen, ev.tOpen + 0.3);
            ctx.fillStyle = 'rgba(229,72,77,0.16)';
            ctx.beginPath();
            ctx.ellipse(cx, cy, rz, rz * 0.22, 0, 0, L.TAU);
            ctx.fill();
            ctx.restore();
            L.inkPath(ctx, L.ellipsePts(cx, cy, rz, rz * 0.22, 64), { closed: true, color: sev.color, width: 3, seed: K.sd(ID, 'zone'), draw: L.seg(t, ev.tOpen, ev.tOpen + 0.4) });
          }
        }
        if (vb && pb && t >= ev.tStart) {
          const px = pb[2], py = pb[3] - 12;
          const qx = vb[0], qy = vb[3] - 12;
          const m = metricAt(ev, t) || Math.max(1.1, Math.max(0, qx - px) / 125);
          const danger = alarm && m < 3.0; // порог камеры К-02: 3 м до движущегося погрузчика
          ctx.save();
          ctx.setLineDash([12, 9]);
          ctx.strokeStyle = danger ? sev.color : '#FFFFFF';
          ctx.lineWidth = 3.5;
          ctx.beginPath();
          ctx.moveTo(px, py);
          ctx.lineTo(qx, qy);
          ctx.stroke();
          ctx.setLineDash([]);
          const mx = (px + qx) / 2, my = (py + qy) / 2 - 20;
          const lbl = `${m.toFixed(1).replace('.', ',')} м`;
          const lw = K.measure(ctx, lbl, { kind: 'mono', size: 26, weight: 700 }) + 20;
          ctx.fillStyle = danger ? sev.color : 'rgba(10,14,30,0.85)';
          K.rrect(ctx, mx - lw / 2, my - 22, lw, 36, 6);
          ctx.fill();
          K.text(ctx, lbl, mx, my + 6, { kind: 'mono', size: 26, weight: 700, color: '#FFFFFF', align: 'center' });
          ctx.restore();
        }
        ev.tracks.forEach((tr, k) => {
          const b = boxAt(tr.boxes, t);
          if (!b) return;
          const t0 = tr.boxes[0][0];
          const p = L.seg(t, t0 - 0.05, t0 + 0.3);
          const conf = ev.tOpen > ev.tStart ? (t - ev.tStart) / (ev.tOpen - ev.tStart) : 1;
          K.violator(ctx, b, { color: sev.color, label: k === 0 ? tr.label : null, p, confirm: conf, alarm, seed: K.sd(ID, 'v', k), minY: MON.y + 50, maxX: MON.x + MON.w - 8 });
        });
        // скорость бегущего по калибровке пола — плашка под рамкой
        if (ev.type === 'running' && ev.tracks[0] && t >= ev.tStart) {
          const b = boxAt(ev.tracks[0].boxes, t);
          const v = metricAt(ev, t);
          if (b && v) {
            const lbl = v.toFixed(1).replace('.', ',') + ' м/с';
            const lw = K.measure(ctx, lbl, { kind: 'mono', size: 26, weight: 700 }) + 20;
            const cx = (b[0] + b[2]) / 2, cy = Math.min(b[3] + 30, MON.y + MON.h - 24);
            ctx.fillStyle = alarm ? sev.color : 'rgba(10,14,30,0.85)';
            K.rrect(ctx, cx - lw / 2, cy - 22, lw, 36, 6);
            ctx.fill();
            K.text(ctx, lbl, cx, cy + 6, { kind: 'mono', size: 26, weight: 700, color: '#FFFFFF', align: 'center' });
          }
        }
        // предметы у нарушителя — рамкой детектора: класс и уверенность модели
        (ev.items || []).forEach((it) => {
          const b = boxAt(it.boxes, t);
          if (!b || t < ev.tStart) return;
          const [label, color] = ITEM[it.cls] || [it.cls, K.C.yellow];
          const near = it.boxes.reduce((m, x) => (Math.abs(x[0] - t) < Math.abs(m[0] - t) ? x : m), it.boxes[0]);
          K.modelBox(ctx, b, { label, color, score: near[5], alpha: L.seg(t, ev.tStart, ev.tStart + 0.25), minY: MON.y + 50 });
        });
        ctx.restore();
        // ролик камеры кончился раньше шота — стоит кадр тревоги
        if (ev.hold != null && t > ev.hold) {
          const a = L.seg(t, ev.hold, ev.hold + 0.2);
          ctx.save();
          ctx.globalAlpha = a;
          ctx.fillStyle = sev.color;
          K.rrect(ctx, MON.x + MON.w - 252, MON.y + 58, 232, 36, 5);
          ctx.fill();
          K.text(ctx, 'СТОП-КАДР ТРЕВОГИ', MON.x + MON.w - 136, MON.y + 83, { kind: 'mono', size: 17, weight: 700, color: '#FFFFFF', align: 'center', track: 1.5 });
          ctx.restore();
        }

        // подпись под монитором
        const capA = L.seg(t, ev.tOpen - 0.05, ev.tOpen + 0.25);
        if (capA > 0) {
          const dy = (1 - L.ease.outCubic(capA)) * 18;
          ctx.save();
          ctx.globalAlpha = capA;
          const w0 = K.text(ctx, ev.title, 96, 886 + dy, { kind: 'disp', size: 64, weight: 800, color: sev.color });
          // чип уровня
          const cl = sev.label;
          const cw = K.measure(ctx, cl, { kind: 'mono', size: 18, weight: 700, track: 2 }) + 24;
          ctx.fillStyle = sev.color;
          K.rrect(ctx, 96 + w0 + 22, 846 + dy, cw, 34, 5);
          ctx.fill();
          K.text(ctx, cl, 96 + w0 + 34, 870 + dy, { kind: 'mono', size: 18, weight: 700, color: '#FFFFFF', track: 2 });
          ctx.restore();
          K.text(ctx, ev.sub, 98, 934, { kind: 'body', size: 28, weight: 500, color: ink2, p: L.seg(t, ev.tOpen + 0.2, ev.tOpen + 1.0) });
        } else {
          const look = L.seg(t, 0.3, 0.7);
          K.text(ctx, 'LinQ Guard смотрит камеру…', 98, 886, { kind: 'hand', size: 46, weight: 600, color: ink2, alpha: look * (1 - L.seg(t, ev.tOpen - 0.3, ev.tOpen)) });
        }

        // сообщение бота
        const pMsg = L.seg(t, ev.tOpen + 0.35, ev.tOpen + 0.85);
        const card = K.img(ev.card), cardRes = K.img(ev.cardRes);
        const m = K.botMessage(ctx, { x: 1300, y: 176, w: 500, img: card, p: pMsg, overlayImg: cardRes, overlayP: L.seg(t, ev.tRes, ev.tRes + 0.3) });
        if (m && pMsg > 0) {
          K.text(ctx, 'TELEGRAM · НАЧАЛЬНИКУ СМЕНЫ', 1300, 160, { kind: 'mono', size: 17, weight: 600, color: ink2, track: 2, alpha: pMsg });
          const yS = m.y + m.h + 46;
          const ack = L.seg(t, ev.tAck, ev.tAck + 0.25);
          if (ack > 0) {
            K.text(ctx, `«Принял» · ${K.hm(ev.min + ev.ackMin)}`, 1306, yS, { kind: 'body', size: 30, weight: 700, color: dark ? K.C.yellow : '#9A6A00', alpha: ack * (1 - 0.5 * L.seg(t, ev.tRes, ev.tRes + 0.3)) });
            K.text(ctx, 'начальник смены', 1306, yS + 34, { kind: 'body', size: 22, weight: 500, color: ink2, alpha: ack });
          }
          const rs = L.seg(t, ev.tRes, ev.tRes + 0.3);
          if (rs > 0) {
            const e = L.ease.outBack(rs);
            ctx.save();
            ctx.translate(1562, yS + 108);
            ctx.rotate(-0.06);
            ctx.scale(0.7 + 0.3 * e, 0.7 + 0.3 * e);
            ctx.globalAlpha = rs;
            const label = `✓ УСТРАНЕНО ЗА ${ev.resMin} МИН`;
            const lw = K.measure(ctx, label, { kind: 'disp', size: 40, weight: 800, track: 1 }) + 36;
            ctx.strokeStyle = K.C.greenLight;
            ctx.lineWidth = 4;
            K.rrect(ctx, -lw / 2, -34, lw, 62, 8);
            ctx.stroke();
            ctx.fillStyle = L.rgba(K.C.green, dark ? 0.22 : 0.12);
            ctx.fill();
            K.text(ctx, label, 0, 11, { kind: 'disp', size: 40, weight: 800, color: dark ? K.C.greenLight : K.C.green, align: 'center', track: 1 });
            ctx.restore();
          }
        }

        // полоса смены
        const marks = all.map((e, j) => ({ min: e.min, sev: e.sev, done: j < idx || (j === idx && resolved) }));
        K.strip(ctx, { x: 96, y: 1000, w: 1728, now: nowMin, marks, dark, hot: alarm && !resolved ? idx : null, T });
      },
    });
  }

  (FILM.SHIFT.events || []).forEach((ev, i, all) => makeScene(ev, i, all));
})();
