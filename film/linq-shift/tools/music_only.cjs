#!/usr/bin/env node
// music_only.cjs : только саундтрек партитуры фильма заданной длины (без кадров) — подложка под скринкаст.
//   node tools/music_only.cjs --dur 55 --out .tmp/bot_music.wav
'use strict';

const fs = require('fs');
const path = require('path');
const C = require('./common.cjs');

async function main() {
  const a = C.parseArgs(process.argv.slice(2), []);
  const dur = Number(a.dur || 55);
  const out = C.resolveOut(a.out || '.tmp/music.wav');
  // таймлайн без тревог: пульс смены, к концу — мажорный итог и финальный аккорд
  const tl = path.join(C.TMP, C.uniqueName('music-tl') + '.js');
  fs.mkdirSync(C.TMP, { recursive: true });
  fs.writeFileSync(tl, 'FILM.TIMELINE = ' + JSON.stringify({
    title: 'music', bpm: 120, fps: 24, width: 1920, height: 1080, duration: dur,
    shots: [{ id: 'm', start: 0, end: dur, mode: 'none' }],
    cues: [{ t: 0, kind: 'open' }, { t: Math.max(0, dur - 12), kind: 'summary' }, { t: Math.max(0, dur - 4), kind: 'close' }],
  }) + ';\nFILM.scene({ id: "m", draw() {} });\n');
  const files = [path.join(C.SRC, 'core.js'), path.join(C.SRC, 'lib.js'), tl, path.join(C.SRC, 'music.js')];
  const b = await C.launch();
  const pg = await C.openPage(b, files, { scale: 0.25, prefix: 'music' });
  const au = await pg.page.evaluate(([s, e, sr]) => window.__h.audio(s, e, sr), [0, dur, 48000]);
  C.writeWavFloat(out, au.b64, 2, 48000);
  console.log(`${out} ${dur}s peak ${au.peak.toFixed(3)}`);
  await pg.close();
  await b.close();
  fs.rmSync(tl, { force: true });
}

main().catch((e) => {
  console.error(e.stack || e.message);
  process.exit(1);
});
