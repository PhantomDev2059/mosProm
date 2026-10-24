#!/usr/bin/env python3
"""Screencast of LinQ Guard for the hackathon backup video.

Runs on the server next to the service (local network, no tunnel), headless Chromium via Playwright.
Frames come from the CDP screencast (JPEG q90, real timestamps), ffmpeg turns them into H.264 30 fps:
Playwright's own recordVideo caps VP8 at 1 Mbit/s, which smears the dashboard text.

    uv venv /workspace/screencast/.venv && uv pip install --python /workspace/screencast/.venv/bin/python playwright pillow
    /workspace/screencast/.venv/bin/playwright install --with-deps chromium
    nice -n 10 /workspace/screencast/.venv/bin/python tools/screencast.py --out /workspace/screencast/out            # both
    nice -n 10 /workspace/screencast/.venv/bin/python tools/screencast.py --only dashboard                            # one

Read-only for the product except one thing: the mini-app tour accepts and resolves ONE alarm
(«Принял» → «Устранено»). Nothing else is changed: no shift close, no demo reset, no verdicts in the review.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import math
import shutil
import subprocess
import time
from pathlib import Path

from playwright.async_api import async_playwright

BASE = "http://localhost:8000"

# ---------------------------------------------------------------- injected overlay: cursor, click ripple, key badge
OVERLAY_JS = r"""
(() => {
  if (window.__pw) return;
  const TOUCH = __TOUCH__;
  const S = { x: -100, y: -100 };
  window.__pw = S;
  function setup() {
    if (document.getElementById('pw-style')) return;
    const st = document.createElement('style');
    st.id = 'pw-style';
    st.textContent = `
      #pw-cursor{position:fixed;left:0;top:0;width:26px;height:26px;z-index:2147483647;pointer-events:none;
        transform:translate(-100px,-100px);filter:drop-shadow(0 2px 3px rgba(0,0,0,.45))}
      .pw-ripple{position:fixed;z-index:2147483646;pointer-events:none;border-radius:50%;
        border:3px solid #ffb020;background:rgba(255,176,32,.28);animation:pwr .65s ease-out forwards}
      .pw-ripple.t{border-color:rgba(255,255,255,.95);background:rgba(255,255,255,.35)}
      @keyframes pwr{from{transform:scale(.35);opacity:1}to{transform:scale(1.35);opacity:0}}
      #pw-key{position:fixed;left:50%;bottom:44px;transform:translateX(-50%) translateY(8px);z-index:2147483647;pointer-events:none;
        font:600 26px/1 'JetBrains Mono',ui-monospace,monospace;color:#fff;background:rgba(11,21,48,.9);
        border:2px solid #ffb020;border-radius:12px;padding:12px 20px;opacity:0;transition:opacity .2s,transform .2s;white-space:nowrap}
      #pw-key.on{opacity:1;transform:translateX(-50%) translateY(0)}
      #pw-key small{font:500 15px/1 'Golos Text',sans-serif;color:#cfd6ea;margin-left:12px}
      #pw-hl{position:fixed;z-index:2147483645;pointer-events:none;border:3px solid #ffb020;border-radius:8px;
        box-shadow:0 0 0 6px rgba(255,176,32,.25);opacity:0;transition:opacity .3s}
    `;
    document.documentElement.appendChild(st);
    if (!TOUCH) {
      const c = document.createElement('div');
      c.id = 'pw-cursor';
      c.innerHTML = '<svg viewBox="0 0 26 26" width="26" height="26"><path d="M4 2.5 L4 21 L9 16.6 L12.3 24 L15.6 22.6 L12.4 15.3 L19 15.3 Z" fill="#fff" stroke="#111" stroke-width="1.6" stroke-linejoin="round"/></svg>';
      document.documentElement.appendChild(c);
    }
    const k = document.createElement('div'); k.id = 'pw-key'; document.documentElement.appendChild(k);
    const h = document.createElement('div'); h.id = 'pw-hl'; document.documentElement.appendChild(h);
    place();
  }
  function place() { const c = document.getElementById('pw-cursor'); if (c) c.style.transform = `translate(${S.x - 4}px,${S.y - 2}px)`; }
  function ripple(x, y) {
    const r = document.createElement('div');
    const d = TOUCH ? 46 : 42;
    r.className = 'pw-ripple' + (TOUCH ? ' t' : '');
    Object.assign(r.style, { left: (x - d / 2) + 'px', top: (y - d / 2) + 'px', width: d + 'px', height: d + 'px' });
    document.documentElement.appendChild(r);
    setTimeout(() => r.remove(), 700);
  }
  let kt = 0;
  function key(label, hint) {
    const k = document.getElementById('pw-key'); if (!k) return;
    k.innerHTML = label + (hint ? '<small>' + hint + '</small>' : '');
    k.classList.add('on'); clearTimeout(kt); kt = setTimeout(() => k.classList.remove('on'), 1100);
  }
  window.__pwRipple = ripple;
  window.__pwKey = key;
  window.__pwHighlight = (sel, on) => {
    const h = document.getElementById('pw-hl'), el = document.querySelector(sel);
    if (!h || !el) return;
    const r = el.getBoundingClientRect();
    Object.assign(h.style, { left: (r.left - 6) + 'px', top: (r.top - 6) + 'px', width: (r.width + 12) + 'px', height: (r.height + 12) + 'px' });
    h.style.opacity = on ? '1' : '0';
  };
  const KEYS = { ArrowDown: '↓', ArrowUp: '↑', Escape: 'Esc', Enter: 'Enter' };
  document.addEventListener('mousemove', (e) => { S.x = e.clientX; S.y = e.clientY; place(); }, true);
  document.addEventListener('mousedown', (e) => ripple(e.clientX, e.clientY), true);
  document.addEventListener('keydown', (e) => { if (KEYS[e.key] || e.key.length === 1 && /[a-zA-Z]/.test(e.key) && !e.target.matches('input,textarea')) key(KEYS[e.key] || e.key.toUpperCase(), window.__pwKeyHint || ''); }, true);
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', setup); else setup();
  new MutationObserver(() => { if (!document.getElementById('pw-style') && document.documentElement) setup(); }).observe(document, { childList: true, subtree: false });
})();
"""

SCROLL_JS = r"""
async ({ sel, to, by, ms, el: elSel, offset }) => {
  const sc = elSel ? document.querySelector(elSel) : document.scrollingElement;
  const start = sc.scrollTop;
  let target = start;
  if (sel) {
    const t = document.querySelector(sel);
    if (t) {
      const top = t.getBoundingClientRect().top;
      const base = elSel ? sc.getBoundingClientRect().top : 0;
      target = start + top - base - (offset || 0);
    }
  } else if (to != null) target = to;
  else if (by != null) target = start + by;
  target = Math.max(0, Math.min(target, sc.scrollHeight - sc.clientHeight));
  const t0 = performance.now();
  await new Promise((res) => {
    function step(now) {
      const k = Math.min(1, (now - t0) / ms);
      const e = k < 0.5 ? 4 * k * k * k : 1 - Math.pow(-2 * k + 2, 3) / 2;
      sc.scrollTop = start + (target - start) * e;
      if (k < 1) requestAnimationFrame(step); else res();
    }
    requestAnimationFrame(step);
  });
  return sc.scrollTop;
}
"""


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


class Recorder:
    """CDP screencast → numbered JPEGs with timestamps → ffconcat list."""

    def __init__(self, page, cdp, frame_dir: Path, w: int, h: int, quality: int = 90):
        self.page, self.cdp, self.dir, self.w, self.h, self.q = page, cdp, frame_dir, w, h, quality
        self.frames: list[tuple[float, Path]] = []
        self.marks: list[tuple[str, float]] = []
        self.stopped_at = None
        self.paused_at = None
        self.gaps: list[tuple[float, float]] = []      # cut out of the video (loading spinners)
        if self.dir.exists():
            shutil.rmtree(self.dir)
        self.dir.mkdir(parents=True)

    async def _ack(self, sid):
        try:
            await self.cdp.send("Page.screencastFrameAck", {"sessionId": sid})
        except Exception:
            pass                                  # page already closed

    def _on_frame(self, p):
        asyncio.ensure_future(self._ack(p["sessionId"]))
        if self.stopped_at or self.paused_at:
            return
        path = self.dir / f"{len(self.frames):06d}.jpg"
        path.write_bytes(base64.b64decode(p["data"]))
        self.frames.append((float(p["metadata"]["timestamp"]), path))

    async def start(self):
        self.cdp.on("Page.screencastFrame", self._on_frame)
        await self.cdp.send("Page.startScreencast", {"format": "jpeg", "quality": self.q,
                                                     "maxWidth": self.w, "maxHeight": self.h, "everyNthFrame": 1})
        self.t_start = time.time()
        self.mark("start")

    def mark(self, name):
        self.marks.append((name, time.time()))
        log(f"mark {name} +{time.time() - self.marks[0][1]:.1f}s, frames {len(self.frames)}")

    def pause(self):
        self.paused_at = time.time()

    def resume(self):
        if self.paused_at:
            self.gaps.append((self.paused_at, time.time()))
            log(f"cut {time.time() - self.paused_at:.1f}s of loading")
        self.paused_at = None

    def eff(self, t):
        return t - sum(min(e, t) - b for b, e in self.gaps if b < t)

    async def stop(self):
        self.stopped_at = time.time()
        try:
            await self.cdp.send("Page.stopScreencast")
        except Exception:
            pass

    def write_concat(self, trim_mark: str | None = None, lead: float = 0.3) -> tuple[Path, float, dict]:
        fr = [(self.eff(t), p) for t, p in self.frames]
        assert len(fr) > 10, f"too few frames: {len(fr)}"
        marks_abs = {n: self.eff(t) for n, t in self.marks}
        t_start = fr[0][0]
        if trim_mark and trim_mark in marks_abs:
            t_start = max(fr[0][0], marks_abs[trim_mark] - lead)
            before = [i for i, (t, _) in enumerate(fr) if t <= t_start]
            i0 = before[-1] if before else 0
            fr = [(max(t, t_start), p) for t, p in fr[i0:]]
        end = self.eff(self.stopped_at)
        lines = ["ffconcat version 1.0"]
        total = 0.0
        for i, (t, p) in enumerate(fr):
            nxt = fr[i + 1][0] if i + 1 < len(fr) else max(end, t + 0.5)
            d = max(0.001, nxt - t)
            total += d
            lines += [f"file '{p.name}'", f"duration {d:.4f}"]
        lines.append(f"file '{fr[-1][1].name}'")
        lst = self.dir / "list.ffconcat"
        lst.write_text("\n".join(lines) + "\n")
        marks = {n: max(0.0, t - fr[0][0]) for n, t in marks_abs.items()}
        return lst, total, marks


class Director:
    """Deliberate human-like actions with a visible cursor."""

    def __init__(self, page, touch=False):
        self.page, self.touch = page, touch
        self.x, self.y = (960, 600) if not touch else (195, 600)

    async def sleep(self, s):
        await asyncio.sleep(s)

    async def move(self, x, y, ms=650):
        if self.touch:                       # a finger does not glide: jump, the tap ripple shows where
            self.x, self.y = x, y
            await self.page.mouse.move(x, y)
            return
        # time-based: a busy renderer (7 live streams) makes each mouse.move slow, so the glide never overruns `ms`
        x0, y0 = self.x, self.y
        arc_h = min(40, abs(x - x0) * 0.06)
        t0 = time.monotonic()
        while True:
            k = min(1.0, (time.monotonic() - t0) / (ms / 1000))
            e = 1 - (1 - k) ** 3
            await self.page.mouse.move(x0 + (x - x0) * e, y0 + (y - y0) * e - math.sin(k * math.pi) * arc_h)
            if k >= 1:
                break
            await asyncio.sleep(0.012)
        self.x, self.y = x, y

    async def box(self, sel, timeout=15000):
        loc = self.page.locator(sel).first
        await loc.wait_for(state="visible", timeout=timeout)
        b = await loc.bounding_box()
        return loc, b

    async def hover(self, sel, ms=650, dx=0.5, dy=0.5):
        _, b = await self.box(sel)
        await self.move(b["x"] + b["width"] * dx, b["y"] + b["height"] * dy, ms)

    async def click(self, sel, ms=650, pause=0.25, dx=0.5, dy=0.5):
        _, b = await self.box(sel)
        await self.move(b["x"] + b["width"] * dx, b["y"] + b["height"] * dy, ms)
        await asyncio.sleep(pause)
        await self.page.mouse.down()
        await asyncio.sleep(0.09)
        await self.page.mouse.up()

    async def fake_click(self, sel, ms=650):
        """Cursor + ripple without a real click (native <select> popups are not drawn headless)."""
        _, b = await self.box(sel)
        await self.move(b["x"] + b["width"] * 0.5, b["y"] + b["height"] * 0.5, ms)
        await asyncio.sleep(0.2)
        await self.page.evaluate("([x, y]) => window.__pwRipple && window.__pwRipple(x, y)", [self.x, self.y])

    async def scroll(self, ms=1800, **kw):
        kw.setdefault("sel", None); kw.setdefault("to", None); kw.setdefault("by", None)
        kw.setdefault("el", None); kw.setdefault("offset", 90)
        kw["ms"] = ms
        return await self.page.evaluate(SCROLL_JS, kw)

    async def key(self, k, hint=""):
        await self.page.evaluate("(h) => { window.__pwKeyHint = h; }", hint)
        await self.page.keyboard.press(k)


async def wait_fn(page, js, timeout=20000, arg=None):
    await page.wait_for_function(js, arg=arg, timeout=timeout, polling=200)


def encode(lst: Path, out: Path, vf: str, crf: int, extra_inputs=(), filter_complex=None, duration=None):
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst)]
    for i in extra_inputs:
        cmd += ["-loop", "1", "-i", str(i)]
    if filter_complex:
        cmd += ["-filter_complex", filter_complex, "-map", "[out]"]
    else:
        cmd += ["-vf", vf]
    if duration:
        cmd += ["-t", f"{duration:.2f}"]
    cmd += ["-r", "30", "-c:v", "libx264", "-preset", "slow", "-crf", str(crf), "-pix_fmt", "yuv420p",
            "-profile:v", "high", "-movflags", "+faststart", "-an", "-threads", "8", str(out)]
    log("ffmpeg →", out.name)
    subprocess.run(cmd, check=True)


# ---------------------------------------------------------------- 1. dashboard
async def dashboard(pw, out: Path, work: Path, crf: int):
    browser = await pw.chromium.launch(args=["--disable-gpu", "--hide-scrollbars", "--autoplay-policy=no-user-gesture-required"])
    ctx = await browser.new_context(viewport={"width": 1920, "height": 1080}, device_scale_factor=1,
                                    locale="ru-RU", timezone_id="Europe/Moscow", accept_downloads=False)
    await ctx.add_init_script(OVERLAY_JS.replace("__TOUCH__", "false"))
    page = await ctx.new_page()
    cdp = await ctx.new_cdp_session(page)
    d = Director(page)

    await page.goto(BASE + "/#monitor", wait_until="domcontentloaded")
    await wait_fn(page, "() => { const t = document.querySelectorAll('#cams .cam'); return t.length >= 7 && document.querySelectorAll('#cams img[src^=\"blob:\"]').length >= t.length; }", 40000)
    await page.evaluate("document.fonts && document.fonts.ready")
    await page.mouse.move(d.x, d.y)
    await asyncio.sleep(2.0)

    rec = Recorder(page, cdp, work / "frames_dashboard", 1920, 1080)
    await rec.start()

    # Мониторинг: 7 live cameras with overlays + event feed
    rec.mark("monitor")
    await asyncio.sleep(2.6)
    await d.hover("#cams .cam:nth-child(1)", 700)
    await asyncio.sleep(0.6)
    await d.click("#cams .cam:nth-child(4)", 600)          # enlarge one camera
    await asyncio.sleep(2.8)
    await d.click("#cams .cam.big", 400)
    await asyncio.sleep(0.6)
    await d.hover(".feed-panel .feed li:nth-child(2)", 700)
    await asyncio.sleep(1.4)

    # Галерея нарушений
    rec.mark("gallery")
    await d.click('#tabs a[data-view="gallery"]', 600)
    await wait_fn(page, "() => document.querySelectorAll('#gallery .g-card img').length > 0 && [...document.querySelectorAll('#gallery .g-card img')].slice(0, 4).every(i => i.complete)", 15000)
    await asyncio.sleep(1.8)
    await d.click("#gallery .g-card:nth-child(1) .g-img", 600)
    await page.wait_for_selector("#modal:not([hidden])", timeout=8000)
    await asyncio.sleep(1.6)
    if await page.locator("#m-video-btn").is_visible():
        await d.click("#m-video-btn", 500)
        await asyncio.sleep(2.8)
    await d.click("#modal-x", 500)
    await asyncio.sleep(0.3)

    # Журнал: zone filter + time range + CSV export
    rec.mark("journal")
    await d.click('#tabs a[data-view="journal"]', 500)
    await wait_fn(page, "() => document.querySelectorAll('#journal tr').length > 0", 15000)
    await asyncio.sleep(1.4)
    zone = await page.evaluate("() => { const o = [...document.querySelectorAll('#journal-zone option')].find(o => o.value); return o ? o.value : ''; }")
    if zone:
        await d.fake_click("#journal-zone", 500)
        await page.select_option("#journal-zone", zone)
        await asyncio.sleep(1.1)
    rng = await page.evaluate("""() => {
      const f = (ms) => new Date(ms - new Date(ms).getTimezoneOffset() * 60000).toISOString().slice(0, 16);
      const now = Date.now();
      return [f(now - 3 * 3600e3), f(now + 60e3)];
    }""")
    await d.fake_click("#journal-from", 450)
    await page.fill("#journal-from", rng[0])
    await asyncio.sleep(0.8)
    await d.fake_click("#journal-to", 450)
    await page.fill("#journal-to", rng[1])
    await asyncio.sleep(1.1)
    await d.hover("#csv-link", 600)
    await page.evaluate("window.__pwHighlight('#csv-link', true)")
    await asyncio.sleep(1.8)
    await page.evaluate("window.__pwHighlight('#csv-link', false)")

    # Аналитика
    rec.mark("analytics")
    await d.click('#tabs a[data-view="analytics"]', 600)
    await wait_fn(page, "() => document.querySelector('#kpis').children.length > 0 && document.querySelector('#ch-heat').children.length > 0", 15000)
    await asyncio.sleep(2.4)
    await d.scroll(sel=".charts", ms=1800)
    await asyncio.sleep(2.2)
    await d.scroll(to=0, ms=700)

    # Качество: UR Fall + PPE comparison
    rec.mark("quality")
    await d.click('#tabs a[data-view="quality"]', 600)
    await wait_fn(page, "() => document.querySelector('#q-fall').children.length > 0", 15000)
    await asyncio.sleep(1.0)
    await d.scroll(sel="#q-fall", offset=150, ms=1500)
    await d.hover("#q-fall", 500, dy=0.3)
    await asyncio.sleep(2.4)
    await d.scroll(sel="#q-ppe-compare", offset=260, ms=1800)
    await d.hover("#q-ppe-compare table", 500, dy=0.3)
    await asyncio.sleep(2.6)
    await d.scroll(to=0, ms=700)

    # LinQ: programs table, platform comparison, device calculator
    rec.mark("linq")
    await d.click('#tabs a[data-view="linq"]', 600)
    await wait_fn(page, "() => document.querySelectorAll('#lq-programs tr').length > 0 && document.querySelectorAll('#lq-bench tr').length > 0", 15000)
    await asyncio.sleep(1.0)
    await d.scroll(sel="#lq-programs", offset=230, ms=1900)
    await d.hover("#lq-programs tr:nth-child(1)", 500, dx=0.2)
    await asyncio.sleep(2.4)
    await d.scroll(sel="#lq-bench", offset=200, ms=1600)
    await d.hover("#lq-bench tr:nth-child(1)", 500, dx=0.3)
    await asyncio.sleep(2.2)
    await d.scroll(sel="#calc", offset=90, ms=1300)
    await asyncio.sleep(0.6)
    await d.click("#calc-cams", 500)
    await page.keyboard.press("Control+A")
    await asyncio.sleep(0.2)
    await page.keyboard.type("100", delay=240)
    await asyncio.sleep(1.0)
    await d.hover("#calc-rows tr:nth-child(1)", 600, dx=0.5)
    await asyncio.sleep(2.4)

    # Разбор смены: open the report, play a clip, keyboard review
    rec.mark("review")
    await d.scroll(to=0, ms=700)
    await d.click('#tabs a[data-view="review"]', 600)
    await wait_fn(page, "() => document.querySelectorAll('#rv-jobs li[data-job]').length > 0", 15000)
    await asyncio.sleep(0.8)
    await d.click("#rv-jobs li[data-job]", 600)
    await wait_fn(page, "() => !document.querySelector('#rv-body').hidden && document.querySelectorAll('#rv-list li[data-k]').length > 0", 15000)
    await wait_fn(page, "() => { const v = document.querySelector('#rv-video'); return v && v.readyState >= 3 && v.currentTime > 0.3; }", 15000)
    await asyncio.sleep(2.8)
    await d.click("#rv-list li[data-k='2']", 700)
    await asyncio.sleep(2.8)
    await d.move(1250, 980, 500)                  # out of the way, keys do the work now
    for _ in range(2):
        await d.key("ArrowDown", "следующий ролик")
        await asyncio.sleep(2.2)
    await d.hover("#rv-ok", 600)
    await asyncio.sleep(1.6)
    await rec.stop()
    await browser.close()

    lst, total, marks = rec.write_concat()
    log("dashboard frames", len(rec.frames), f"{total:.1f}s", json.dumps({k: round(v, 1) for k, v in marks.items()}, ensure_ascii=False))
    encode(lst, out / "dashboard_tour.mp4", "fps=30,scale=1920:1080:flags=lanczos,format=yuv420p", crf,
           duration=min(total, 89.8))                     # ≤ 90 s for the slot; the tail is a hover over «Подтвердить»
    (work / "dashboard_marks.json").write_text(json.dumps(marks, ensure_ascii=False, indent=1))


# ---------------------------------------------------------------- 2. mini app in a phone frame
PHONE_W, PHONE_H = 390, 844
SCR_H = 944
SCR_W = round(SCR_H * PHONE_W / PHONE_H)       # 436
BEZEL = 14


def make_phone_assets(work: Path, captions: dict[str, tuple[str, str, str]]):
    from PIL import Image, ImageDraw, ImageFilter, ImageFont

    W, H = 1920, 1080
    sx, sy = (W - SCR_W) // 2, (H - SCR_H) // 2
    fb = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    fr = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    fc = "/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed-Bold.ttf"

    # background: navy, fine + coarse blueprint grid, soft vignette, phone shadow
    bg = Image.new("RGB", (W, H), "#0b1530")
    dr = ImageDraw.Draw(bg)
    for x in range(0, W, 24):
        dr.line([(x, 0), (x, H)], fill="#101d3e")
    for y in range(0, H, 24):
        dr.line([(0, y), (W, y)], fill="#101d3e")
    for x in range(0, W, 120):
        dr.line([(x, 0), (x, H)], fill="#16264f")
    for y in range(0, H, 120):
        dr.line([(0, y), (W, y)], fill="#16264f")
    # dimension line under the phone like on a drawing
    dl_y = sy + SCR_H + BEZEL + 26
    dr.line([(sx - BEZEL, dl_y), (sx + SCR_W + BEZEL, dl_y)], fill="#3b5590", width=2)
    for xx in (sx - BEZEL, sx + SCR_W + BEZEL):
        dr.line([(xx, dl_y - 8), (xx, dl_y + 8)], fill="#3b5590", width=2)
    shadow = Image.new("L", (W, H), 0)
    ImageDraw.Draw(shadow).rounded_rectangle([sx - BEZEL, sy - BEZEL + 18, sx + SCR_W + BEZEL, sy + SCR_H + BEZEL + 18], 62, fill=170)
    shadow = shadow.filter(ImageFilter.GaussianBlur(28))
    bg = Image.composite(Image.new("RGB", (W, H), "#02060f"), bg, shadow)
    bg.save(work / "phone_bg.png")

    # top layer: phone body with a rounded hole for the screen
    top = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    dt = ImageDraw.Draw(top)
    dt.rounded_rectangle([sx - BEZEL, sy - BEZEL, sx + SCR_W + BEZEL, sy + SCR_H + BEZEL], 60, fill="#1a1f2c", outline="#4a5573", width=3)
    dt.rounded_rectangle([sx - BEZEL + 5, sy - BEZEL + 5, sx + SCR_W + BEZEL - 5, sy + SCR_H + BEZEL - 5], 56, outline="#0c0f17", width=3)
    hole = Image.new("L", (W, H), 0)
    ImageDraw.Draw(hole).rounded_rectangle([sx, sy, sx + SCR_W, sy + SCR_H], 46, fill=255)
    top.putalpha(Image.eval(Image.merge("L", [top.getchannel("A")]).point(lambda a: a), lambda a: a))
    a = top.getchannel("A")
    a = Image.composite(Image.new("L", (W, H), 0), a, hole)
    top.putalpha(a)
    # side buttons
    dt = ImageDraw.Draw(top)
    dt.rounded_rectangle([sx + SCR_W + BEZEL - 1, sy + 190, sx + SCR_W + BEZEL + 4, sy + 290], 2, fill="#2b3245")
    dt.rounded_rectangle([sx - BEZEL - 4, sy + 160, sx - BEZEL + 1, sy + 220], 2, fill="#2b3245")
    dt.rounded_rectangle([sx - BEZEL - 4, sy + 240, sx - BEZEL + 1, sy + 300], 2, fill="#2b3245")
    top.save(work / "phone_top.png")

    # captions on the left: product line + role (one PNG per role segment)
    F_k = ImageFont.truetype(fr, 22)
    F_t = ImageFont.truetype(fc, 50)
    F_r = ImageFont.truetype(fb, 40)
    F_d = ImageFont.truetype(fr, 25)
    CX, CW = 150, sx - BEZEL - 150 - 90
    for key, (step, role, desc) in captions.items():
        im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        c = ImageDraw.Draw(im)
        y = 330
        c.text((CX, y), "LINQ GUARD · TELEGRAM MINI APP", font=F_k, fill="#8fa3d6")
        y += 44
        c.line([(CX, y), (CX + 64, y)], fill="#ffb020", width=4)
        y += 26
        c.text((CX, y), "Мини-приложение", font=F_t, fill="#ffffff")
        y += 62
        c.text((CX, y), "для смены", font=F_t, fill="#ffffff")
        y += 104
        c.text((CX, y), "РОЛЬ" + ("  " + step if step != "—" else ""), font=F_k, fill="#8fa3d6")
        y += 34
        c.text((CX, y), role, font=F_r, fill="#ffb020")
        y += 62
        line, lines = "", []
        for wd in desc.split():
            t = (line + " " + wd).strip()
            if c.textlength(t, font=F_d) > CW:
                lines.append(line); line = wd
            else:
                line = t
        lines.append(line)
        for ln in lines:
            c.text((CX, y), ln, font=F_d, fill="#d5dcf0")
            y += 36
        im.save(work / f"cap_{key}.png")
    return sx, sy


async def miniapp(pw, out: Path, work: Path, crf: int):
    """Four short role segments: worker → operator → supervisor → director.
    State changes (asked for): the worker binds to one face, the operator passes one non-critical event
    (that creates an alarm), the supervisor accepts and resolves exactly that alarm. The director only looks."""
    browser = await pw.chromium.launch(args=["--disable-gpu", "--hide-scrollbars"])
    ctx = await browser.new_context(viewport={"width": PHONE_W, "height": PHONE_H}, device_scale_factor=2, is_mobile=True,
                                    locale="ru-RU", timezone_id="Europe/Moscow", color_scheme="light",
                                    user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1")
    await ctx.add_init_script(OVERLAY_JS.replace("__TOUCH__", "true"))
    page = await ctx.new_page()
    cdp = await ctx.new_cdp_session(page)
    d = Director(page, touch=True)

    async def switch_role(role):
        rec.mark("pick_" + role)
        await d.scroll(to=0, ms=500, offset=0)
        await d.click("#roleBtn", pause=0.2)
        await page.wait_for_selector("#roles:not([hidden]) .role-card", timeout=8000)
        await asyncio.sleep(1.0)
        await d.scroll(el="#roles", sel=f'.role-card[data-role="{role}"]', offset=300, ms=700)
        await asyncio.sleep(0.3)
        await d.click(f'.role-card[data-role="{role}"]', pause=0.2)

    await page.set_content("<html><body style='margin:0;background:#0B1230'></body></html>")
    rec = Recorder(page, cdp, work / "frames_miniapp", PHONE_W * 2, PHONE_H * 2)
    await rec.start()
    await asyncio.sleep(0.3)
    await page.goto(BASE + "/app/", wait_until="domcontentloaded")
    try:
        await page.wait_for_selector("#intro:not([hidden])", timeout=8000)
        rec.mark("intro")
    except Exception:
        log("intro not seen")

    # ---- 01 Рабочий: intro → role chooser → «Найдите себя» → «Это я» → Моя смена
    await page.wait_for_selector("#roles:not([hidden]) .role-card", timeout=30000)
    rec.mark("roles")
    await asyncio.sleep(2.2)
    rec.mark("pick_worker")
    await d.click('.role-card[data-role="worker"]', pause=0.3)
    rec.mark("worker")
    await asyncio.sleep(0.6)
    rec.pause()                                   # faces come from a busy server: cut the skeleton wait
    await page.wait_for_selector("#view .faces .face-card[data-act=pick]", timeout=20000)
    await page.evaluate("Promise.all([...document.querySelectorAll('#view .face-card img')].map(i => i.complete ? 0 : new Promise(r => { i.onload = i.onerror = r; setTimeout(r, 8000); })))")
    await asyncio.sleep(0.3)
    rec.resume()
    await asyncio.sleep(1.8)
    pid = await page.evaluate("""async () => {
      const h = { 'X-Demo-User': localStorage.getItem('lg.demo') || '', 'X-Demo-Role': 'supervisor' };
      try {
        const list = await (await fetch('/api/tg/people', { headers: h })).json();
        const ok = list.filter(p => !p.tg_bound).sort((a, b) => (b.events || 0) - (a.events || 0));
        return ok.length ? String(ok[0].id) : null;
      } catch (e) { return null; }
    }""")
    face = f'#view .face-card[data-act=pick][data-id="{pid}"]' if pid else "#view .face-card[data-act=pick]"
    if not await page.locator(face).count():
        face = "#view .face-card[data-act=pick]"
    await d.scroll(sel=face, offset=260, ms=700)
    await d.click(face)
    await page.wait_for_selector("#sheet:not([hidden]) [data-act=bind]", timeout=8000)
    await asyncio.sleep(1.5)
    await d.click("#sheet [data-act=bind]")
    await page.wait_for_selector("#view .tl, #view .empty, #view .sec", timeout=15000)
    await asyncio.sleep(2.2)
    await d.scroll(by=380, ms=1400, offset=0)
    await asyncio.sleep(1.2)

    # ---- 02 Оператор: Лента → «Передать начальнику смены» on a non-critical event, with a comment
    await switch_role("operator")
    rec.mark("operator")
    await page.wait_for_selector("#view .ev", timeout=15000)
    await asyncio.sleep(2.2)
    btn = None
    for sel in ("#view .ev.medium [data-act=pass]", "#view .ev.high [data-act=pass]", "#view .ev:not(.critical) [data-act=pass]"):
        if await page.locator(sel).count():
            btn = sel
            break
    passed = None
    if btn:
        passed = await page.locator(btn).first.get_attribute("data-id")
        await d.scroll(sel=btn, offset=330, ms=900)
        await asyncio.sleep(0.6)
        await d.click(btn)
        await page.wait_for_selector("#sheet:not([hidden]) #passNote", timeout=8000)
        await asyncio.sleep(1.2)
        await d.click("#passNote")
        await page.keyboard.type("человек у станка без жилета, проверьте", delay=45)
        await asyncio.sleep(0.5)
        await page.evaluate("document.activeElement && document.activeElement.blur()")
        await d.click("#sheet [data-act=do-pass]")
        await page.wait_for_selector("#sheet:not([hidden]) [data-act=close].btn", timeout=10000)
        await asyncio.sleep(2.6)
        await d.click("#sheet [data-act=close].btn")
        await asyncio.sleep(0.6)
    else:
        log("no event to pass — operator segment shows the feed only")
        await d.scroll(by=420, ms=1600, offset=0)
        await asyncio.sleep(1.0)
    log("passed event:", passed)

    # ---- 03 Начальник смены: Тревоги (countdown) → «Принял» → «Устранено» → Отчёт (reaction time)
    await switch_role("supervisor")
    rec.mark("supervisor")
    await page.wait_for_selector("#view article.alarm", timeout=20000)
    await asyncio.sleep(0.8)
    aid = await page.evaluate("""(want) => {
      const cards = [...document.querySelectorAll('#view article.alarm')];
      const mine = want && cards.find(c => c.id === 'al-' + want && c.querySelector('[data-act=ack]'));
      const withTimer = cards.find(c => c.querySelector('.timer:not(.late)') && c.querySelector('[data-act=ack]'));
      const any = mine || withTimer || cards.find(c => c.querySelector('[data-act=ack]')) || cards[0];
      return any ? any.id.slice(3) : null;
    }""", passed)
    log("alarm to handle:", aid)
    card = f'[id="al-{aid}"]'
    title = await page.evaluate("(s) => (document.querySelector(s + ' h3') || {}).textContent || ''", card)
    await d.scroll(sel=card, offset=150, ms=1000)
    await asyncio.sleep(2.4)                     # the countdown ticks
    await d.click(f"{card} [data-act=ack]")
    await asyncio.sleep(2.4)
    await d.scroll(sel=card, offset=150, ms=500)
    await d.click(f"{card} [data-act=resolve]")
    await page.wait_for_selector("#sheet:not([hidden]) [data-act=do-resolve]", timeout=8000)
    await asyncio.sleep(1.0)
    chip = "Помощь оказана" if "Паден" in title else "Очаг потушен" if ("огонь" in title.lower() or "дым" in title.lower()) else "Опасность устранена"
    await d.click(f'#sheet .notes .chip:has-text("{chip}")')
    await asyncio.sleep(0.5)
    await d.click("#resNote")
    await page.keyboard.type("выдали жилет", delay=55)
    await asyncio.sleep(0.4)
    await page.evaluate("document.activeElement && document.activeElement.blur()")
    await d.click("#sheet [data-act=do-resolve]")
    await asyncio.sleep(2.4)
    await d.click('#tabs [data-tab="report"]')
    await page.wait_for_selector("#view .paper", timeout=15000)
    await asyncio.sleep(1.4)
    if await page.locator("#view .sla").count():
        await d.scroll(sel="#view .sla", offset=140, ms=1500)
    else:
        await d.scroll(by=600, ms=1500, offset=0)
    await asyncio.sleep(3.0)

    # ---- 04 Руководитель: Обзор (KPIs + reaction time) → Тревоги with «Запросить отчёт»
    await switch_role("director")
    rec.mark("director")
    await page.wait_for_selector("#view .kpis .kpi .v", timeout=15000)
    await asyncio.sleep(2.6)
    await d.scroll(sel="#view .sla", offset=120, ms=1400)
    await asyncio.sleep(2.6)
    await d.click('#tabs [data-tab="alarms"]')
    await page.wait_for_selector("#view article.alarm, #view .empty", timeout=15000)
    await asyncio.sleep(1.4)
    if await page.locator("#view article.alarm [data-act=ask]").count():
        await d.scroll(sel="#view article.alarm [data-act=ask]", offset=520, ms=900)
        await asyncio.sleep(0.4)
        await page.evaluate("window.__pwHighlight('#view article.alarm [data-act=ask]', true)")
        await asyncio.sleep(2.4)
        await page.evaluate("window.__pwHighlight('#view article.alarm [data-act=ask]', false)")
    await asyncio.sleep(1.0)
    await rec.stop()
    await browser.close()

    lst, total, marks = rec.write_concat(trim_mark="intro")
    log("miniapp frames", len(rec.frames), f"{total:.1f}s", json.dumps({k: round(v, 1) for k, v in marks.items()}, ensure_ascii=False))
    (work / "miniapp_marks.json").write_text(json.dumps(marks, ensure_ascii=False, indent=1))

    captions = {
        "start": ("—", "Выбор роли", "Открыто без Telegram — демо-вход. Интерфейс подстраивается под роль на смене"),
        "worker": ("01 / 04", "Рабочий", "Находит себя по фото с камер и видит свои нарушения за смену"),
        "operator": ("02 / 04", "Оператор", "Смотрит ленту всех камер и передаёт нарушение начальнику смены — с комментарием"),
        "supervisor": ("03 / 04", "Начальник смены", "Тревога со сроком: «Принял» → «Устранено» с отметкой, что сделано. Отчёт — время реакции"),
        "director": ("04 / 04", "Руководитель", "Показатели смены, время реакции, по просроченным — «Запросить отчёт»"),
    }
    sx, sy = make_phone_assets(work, captions)
    order = ["start", "worker", "operator", "supervisor", "director"]
    ts = [0.0, marks["pick_worker"], marks["pick_operator"] + 1.0, marks["pick_supervisor"] + 1.0, marks["pick_director"] + 1.0, total + 1]
    seg = [(k, ts[i], ts[i + 1]) for i, k in enumerate(order)]
    inputs = [work / "phone_bg.png", work / "phone_top.png"] + [work / f"cap_{k}.png" for k, _, _ in seg]
    fc = (f"[0:v]fps=30,scale={SCR_W}:{SCR_H}:flags=lanczos,format=rgba[scr];"
          f"[1:v]format=rgba[bg];[bg][scr]overlay={sx}:{sy}:shortest=1[a];[a][2:v]overlay=0:0[b0]")
    prev = "b0"
    for i, (k, t0, t1) in enumerate(seg):
        nxt = f"c{i}"
        fc += f";[{prev}][{3 + i}:v]overlay=0:0:enable='between(t,{t0:.2f},{t1:.2f})'[{nxt}]"
        prev = nxt
    fc += f";[{prev}]format=yuv420p[out]"
    encode(lst, out / "miniapp_tour.mp4", None, crf, extra_inputs=inputs, filter_complex=fc, duration=total)


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="/workspace/screencast/out")
    ap.add_argument("--work", default="/workspace/screencast/work")
    ap.add_argument("--only", choices=["dashboard", "miniapp"])
    ap.add_argument("--crf", type=int, default=24)
    a = ap.parse_args()
    out, work = Path(a.out), Path(a.work)
    out.mkdir(parents=True, exist_ok=True); work.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as pw:
        if a.only in (None, "dashboard"):
            await dashboard(pw, out, work, a.crf)
        if a.only in (None, "miniapp"):
            await miniapp(pw, out, work, a.crf)


if __name__ == "__main__":
    asyncio.run(main())
