#!/usr/bin/env node
// vrender.cjs : рендер фильма с живыми кадрами камер. Как render.cjs, но перед каждым кадром
// ждёт window.__prep(T) — загрузку кадров роликов, видимых в момент T (src/props.js).
//
//   node tools/vrender.cjs --at 1,2.5,7 --out .tmp/snap        PNG выбранных моментов (+ sheet.jpg)
//   node tools/vrender.cjs --out exports/linq-shift.mp4 [--from s --to s --scale 0.5 --workers 4 --silent]
'use strict';

const fs = require('fs');
const path = require('path');
const { spawn, spawnSync } = require('child_process');
const C = require('./common.cjs');

const FFMPEG = process.env.FFMPEG || 'ffmpeg';
const SR = 48000;

function files() {
  const src = C.SRC;
  const scenes = fs.readdirSync(path.join(src, 'scenes')).filter((f) => f.endsWith('.js')).sort().map((f) => path.join(src, 'scenes', f));
  const list = [path.join(src, 'core.js'), path.join(src, 'lib.js'), path.join(src, 'photos.js'), path.join(src, 'props.js'), path.join(src, 'timeline.js'), ...scenes];
  if (fs.existsSync(path.join(src, 'music.js'))) list.push(path.join(src, 'music.js'));
  return list;
}

function run(args, stdin) {
  const proc = spawn(FFMPEG, args, { stdio: [stdin ? 'pipe' : 'ignore', 'ignore', 'pipe'] });
  let err = '';
  proc.stderr.on('data', (d) => (err += d));
  const done = new Promise((res, rej) => proc.on('close', (c) => (c === 0 ? res() : rej(new Error('ffmpeg ' + c + '\n' + err)))));
  done.catch(() => {});
  return { proc, done };
}

async function main() {
  const a = C.parseArgs(process.argv.slice(2), ['silent', 'keep']);
  const scale = a.scale ? Number(a.scale) : 1;
  const workers = Math.max(1, Number(a.workers || 1));
  const list = files();
  const browsers = await Promise.all(Array.from({ length: workers }, () => C.launch()));
  const pages = await Promise.all(browsers.map((b, i) => C.openPage(b, list, { scale, prefix: 'vr' + i })));
  for (const pg of pages) {
    const probs = [...pg.loadErrors.map((e) => `${e.file}:${e.line} ${e.message}`), ...pg.state.regErrors, ...pg.pageErrors];
    if (probs.length || !pg.info) throw new Error('load failed:\n  ' + (probs.join('\n  ') || 'no FILM'));
  }
  const info = pages[0].info;
  const FPS = 24;
  const one = async (pg, T) => {
    await pg.page.evaluate((t) => window.__prep(t), T);
    const r = await pg.page.evaluate((t) => window.__h.render(t), T);
    if (r.errors.length) throw new Error(`T=${T.toFixed(3)} ${r.shot}: ${r.errors.map((e) => e.message + '\n' + (e.stack || '')).join('; ')}`);
    return Buffer.from(await pg.page.evaluate(() => window.__h.png()), 'base64');
  };

  if (a.at) {
    const ts = String(a.at).split(',').map(Number);
    const out = C.resolveOut(a.out || '.tmp/snap');
    fs.mkdirSync(out, { recursive: true });
    const names = [];
    for (let i = 0; i < ts.length; i++) {
      const f = path.join(out, `s${String(i).padStart(3, '0')}_${ts[i].toFixed(2)}.png`);
      fs.writeFileSync(f, await one(pages[0], ts[i]));
      names.push(f);
    }
    if (names.length > 1) {
      const py = `import sys\nfrom PIL import Image, ImageDraw\nfs=sys.argv[3:]\ncols=int(sys.argv[2])\nims=[Image.open(f).convert('RGB') for f in fs]\nw=640;h=round(ims[0].height*w/ims[0].width)\nrows=(len(ims)+cols-1)//cols\ns=Image.new('RGB',(cols*(w+8)+8,rows*(h+30)+8),(20,20,20))\nd=ImageDraw.Draw(s)\nfor i,(f,im) in enumerate(zip(fs,ims)):\n  x=8+(i%cols)*(w+8);y=8+(i//cols)*(h+30)\n  s.paste(im.resize((w,h)),(x,y));d.text((x+4,y+h+6),f.split('/')[-1],fill=(220,220,220))\ns.save(sys.argv[1],quality=88)\n`;
      spawnSync('python3', ['-c', py, path.join(out, 'sheet.jpg'), String(a.cols || 3), ...names], { stdio: 'inherit' });
    }
    console.log(out);
  } else {
    const dur = info.duration;
    const from = a.from != null ? Number(a.from) : 0;
    const to = a.to != null ? Number(a.to) : dur;
    const f0 = Math.round(from * FPS), f1 = Math.round(to * FPS);
    const frames = f1 - f0;
    const out = C.resolveOut(a.out || 'exports/linq-shift.mp4');
    fs.mkdirSync(path.dirname(out), { recursive: true });
    const work = path.join(C.TMP, C.uniqueName('vrender'));
    fs.mkdirSync(work, { recursive: true });
    const wav = path.join(work, 'audio.wav');
    if (a.silent || !pages[0].state.hasAudio) {
      const n = Math.round((frames / FPS) * SR);
      C.writeWavFloat(wav, Buffer.alloc(n * 8).toString('base64'), 2, SR);
    } else {
      const au = await pages[0].page.evaluate(([s, e, sr]) => window.__h.audio(s, e, sr), [f0 / FPS, f1 / FPS, SR]);
      C.writeWavFloat(wav, au.b64, 2, SR);
      console.log(`audio peak ${au.peak.toFixed(3)}`);
    }
    const t0 = Date.now();
    let done = 0;
    const per = Math.ceil(frames / workers);
    await Promise.all(pages.map(async (pg, i) => {
      for (let f = f0 + i * per; f < Math.min(f1, f0 + (i + 1) * per); f++) {
        fs.writeFileSync(path.join(work, `f${String(f - f0).padStart(6, '0')}.png`), await one(pg, f / FPS));
        if (++done % 48 === 0) console.log(`frames ${done}/${frames}  ${(done / ((Date.now() - t0) / 1000)).toFixed(1)} fps`);
      }
    }));
    const enc = run(['-y', '-hide_banner', '-loglevel', 'error', '-framerate', String(FPS), '-start_number', '0', '-i', path.join(work, 'f%06d.png'), '-i', wav,
      '-map', '0:v:0', '-map', '1:a:0', '-vf', 'scale=in_range=full:out_range=tv:out_color_matrix=bt709:flags=lanczos,format=yuv420p',
      '-c:v', 'libx264', '-preset', 'medium', '-crf', String(a.crf || 17), '-pix_fmt', 'yuv420p', '-r', String(FPS),
      '-colorspace', 'bt709', '-color_primaries', 'bt709', '-color_trc', 'bt709', '-color_range', 'tv',
      '-c:a', 'aac', '-b:a', '192k', '-ar', String(SR), '-ac', '2', '-t', (frames / FPS).toFixed(6), '-movflags', '+faststart', out]);
    await enc.done;
    if (!a.keep) fs.rmSync(work, { recursive: true, force: true });
    console.log(`${out} (${frames} frames, ${((Date.now() - t0) / 1000).toFixed(0)} s)`);
  }
  await Promise.all(pages.map((p) => p.close()));
  await Promise.all(browsers.map((b) => b.close()));
}

main().catch((e) => {
  console.error(e.stack || e.message);
  process.exit(1);
});
