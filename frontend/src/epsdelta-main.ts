import katex from 'katex';
import 'katex/dist/katex.min.css';
import './style.css';
import { Tween, lerp, smooth } from './anim';
import { DEMOS, EPS_MAX, EPS_MIN, type Demo } from './epsdelta';
import { COLORS, MAIN_FONT, MATH_FONT, View, dot, drawCurve, drawGridAndAxes, type Bounds, type Rect } from './plot';

const $ = <T extends HTMLElement>(id: string) => document.getElementById(id) as T;
const canvas = $<HTMLCanvasElement>('board');
const ctx = canvas.getContext('2d')!;
const slider = $<HTMLInputElement>('eps');

const tex = (el: HTMLElement, latex: string) => katex.render(latex, el, { throwOnError: false });

// ---- state ----
let demo: Demo = DEMOS.square;
let prevDemo: Demo | null = null;
const morph = new Tween(1);   // 0 → 1 while switching demos
let eps = 0.3;
let dragging = false;
let hovering = false;
let mainView = new View({ x: 0, y: 0, w: 1, h: 1 }, demo.bounds);

// ---- panel ----
function updatePanel() {
  slider.value = String(eps);
  $('epsOut').textContent = eps.toFixed(3);
  for (const b of document.querySelectorAll<HTMLButtonElement>('#tabs button')) {
    b.classList.toggle('active', b.dataset.demo === demo.id);
  }
  tex($('fdef'), demo.fLatex);

  const d = demo.delta(eps);
  const verdict = $('verdict');
  const ok = d !== null;
  verdict.className = ok ? 'ok' : 'bad';
  $('delta').classList.toggle('bad', !ok);
  if (demo.id === 'square') {
    tex($('delta'), `\\delta = \\sqrt{1+\\varepsilon} - 1 = ${d!.toFixed(4)}`);
    verdict.innerHTML = `✓ δ = ${d!.toFixed(4)} works: every <i>x</i> within δ of 1 has <i>x</i>² within ε of 1.`;
  } else if (ok) {
    tex($('delta'), `\\delta = \\text{anything}`);
    verdict.innerHTML = '✓ Any δ works: f only takes the values 0, ½, 1, and all are within ε of ½.';
  } else {
    tex($('delta'), `\\text{no } \\delta`);
    verdict.innerHTML = '<b>✗ No δ fits.</b> However small δ is, some <i>x</i> ∈ (−δ, 0) has f(<i>x</i>) = 0, '
      + 'which is ½ away from f(0) = ½, and ½ ≥ ε. Drag ε above ½ to fix it.';
  }
}

function setEps(v: number) {
  eps = Math.min(EPS_MAX, Math.max(EPS_MIN, Math.round(v * 1000) / 1000));
  updatePanel();
}

function setDemo(next: Demo) {
  if (next === demo) return;
  prevDemo = demo;
  demo = next;
  morph.set(0, 0);
  morph.set(1, 900);
  updatePanel();
}

// ---- drawing ----

/** When no δ works, cycle through shrinking candidates (0.8, 0.4, …) to show that every one fails. */
function candidateDelta(now: number) {
  const period = 1100;
  const k = Math.floor(now / period) % 6;
  const t = smooth(Math.min(1, ((now / period) % 1) * 2.5));
  return 0.8 * 2 ** -lerp(k, (k + 1) % 6, t);
}

function dashedLine(x0: number, y0: number, x1: number, y1: number, color: string) {
  ctx.save();
  ctx.setLineDash([7, 6]);
  ctx.strokeStyle = color;
  ctx.lineWidth = 1.5;
  ctx.beginPath(); ctx.moveTo(x0, y0); ctx.lineTo(x1, y1); ctx.stroke();
  ctx.restore();
}

function arrowHead(x: number, y: number, dx: number, dy: number, color: string) {
  const len = Math.hypot(dx, dy) || 1;
  const ux = dx / len, uy = dy / len, s = 7;
  ctx.fillStyle = color;
  ctx.beginPath();
  ctx.moveTo(x, y);
  ctx.lineTo(x - ux * s - uy * s * 0.6, y - uy * s + ux * s * 0.6);
  ctx.lineTo(x - ux * s + uy * s * 0.6, y - uy * s - ux * s * 0.6);
  ctx.fill();
}

function label(text: string, x: number, y: number, color: string, font: string, align: CanvasTextAlign = 'center', base: CanvasTextBaseline = 'middle') {
  ctx.fillStyle = color;
  ctx.font = font;
  ctx.textAlign = align;
  ctx.textBaseline = base;
  ctx.fillText(text, x, y);
}

/** Everything that depends on the demo: bands, curve, points, labels. */
function drawDemo(v: View, d: Demo, now: number, alpha: number, full: boolean) {
  const { rect } = v;
  const delta = d.delta(eps);
  const failing = delta === null;
  const shown = failing ? candidateDelta(now) : delta;
  const dColor = failing ? COLORS.red : COLORS.green;

  ctx.save();
  ctx.globalAlpha = alpha;

  // ε-band: horizontal strip |y − f(a)| < ε
  const top = v.sy(d.fa + eps), bot = v.sy(d.fa - eps);
  ctx.fillStyle = 'rgba(247, 217, 111, 0.13)';
  ctx.fillRect(rect.x, top, rect.w, bot - top);
  dashedLine(rect.x, top, rect.x + rect.w, top, COLORS.yellow);
  dashedLine(rect.x, bot, rect.x + rect.w, bot, COLORS.yellow);

  // δ-band: vertical strip |x − a| < δ
  const inf = shown === Infinity;
  const left = inf ? rect.x : v.sx(d.a - shown), right = inf ? rect.x + rect.w : v.sx(d.a + shown);
  ctx.fillStyle = failing ? 'rgba(252, 98, 85, 0.13)' : 'rgba(131, 193, 103, 0.13)';
  ctx.fillRect(left, rect.y, right - left, rect.h);
  if (!inf) {
    dashedLine(left, rect.y, left, rect.y + rect.h, dColor);
    dashedLine(right, rect.y, right, rect.y + rect.h, dColor);
  }

  // Graph: green where it stays in the ε-band inside the δ-window, red where it escapes.
  const colorAt = (x: number, y: number) =>
    Math.abs(x - d.a) < shown ? (Math.abs(y - d.fa) < eps ? COLORS.green : COLORS.red) : COLORS.blue;
  drawCurve(ctx, v, d.f, colorAt, d.jumps, full ? 4 : 3);
  for (const p of d.openDots) dot(ctx, v.sx(p.x), v.sy(p.y), full ? 6 : 5, colorAt(p.x, p.y), false);
  dot(ctx, v.sx(d.a), v.sy(d.fa), full ? 6 : 5, COLORS.yellow, true);

  if (full) {
    const ay = Math.min(Math.max(v.sy(0), rect.y), rect.y + rect.h);
    label('a', v.sx(d.a), ay + 24, COLORS.yellow, `italic 19px ${MATH_FONT}`, 'center', 'top');

    const yf = `italic 16px ${MATH_FONT}`;
    label('f(a) + ε', rect.x + rect.w - 6, top - 4, COLORS.yellow, yf, 'right', 'bottom');
    label('f(a) − ε', rect.x + rect.w - 6, bot + 4, COLORS.yellow, yf, 'right', 'top');

    // δ dimension line along the bottom.
    const by = rect.y + rect.h - 16;
    if (inf) {
      label('any δ works', (rect.x + rect.x + rect.w) / 2, by, COLORS.green, `italic 19px ${MATH_FONT}`);
    } else {
      const x0 = v.sx(d.a), x1 = right;
      ctx.strokeStyle = dColor;
      ctx.lineWidth = 2;
      ctx.beginPath(); ctx.moveTo(x0, by); ctx.lineTo(x1, by); ctx.stroke();
      if (x1 - x0 > 16) arrowHead(x1, by, 1, 0, dColor);
      const text = failing ? `δ = ${shown.toFixed(3)} fails` : 'δ';
      if (x1 - x0 > 40 && !failing) label(text, (x0 + x1) / 2, by - 6, dColor, `italic 19px ${MATH_FONT}`, 'center', 'bottom');
      else label(text, x1 + 8, by, dColor, `italic 17px ${MATH_FONT}`, 'left');
    }
  }
  ctx.restore();
}

function handlePos(v: View) {
  return { x: v.rect.x + 54, y: v.sy(demo.fa + eps) };
}

function drawHandle(v: View, alpha: number) {
  const { x, y } = handlePos(v);
  const y0 = v.sy(demo.fa);
  ctx.save();
  ctx.globalAlpha = alpha;
  // ε dimension arrow from f(a) up to the band edge.
  ctx.strokeStyle = COLORS.yellow;
  ctx.lineWidth = 2;
  ctx.beginPath(); ctx.moveTo(x, y0); ctx.lineTo(x, y); ctx.stroke();
  label('ε', x + 14, (y + y0) / 2, COLORS.yellow, `italic 21px ${MATH_FONT}`, 'left');
  const r = dragging || hovering ? 11 : 8;
  ctx.shadowColor = COLORS.yellow;
  ctx.shadowBlur = dragging || hovering ? 16 : 6;
  dot(ctx, x, y, r, COLORS.yellow, true);
  ctx.shadowBlur = 0;
  // little up/down chevrons so it reads as draggable
  ctx.strokeStyle = COLORS.bg;
  ctx.lineWidth = 2;
  ctx.beginPath();
  ctx.moveTo(x - 4, y - 1.5); ctx.lineTo(x, y - 5); ctx.lineTo(x + 4, y - 1.5);
  ctx.moveTo(x - 4, y + 1.5); ctx.lineTo(x, y + 5); ctx.lineTo(x + 4, y + 1.5);
  ctx.stroke();
  ctx.restore();
}

/** Magnified view around (a, f(a)), so tiny ε and δ stay readable. Only useful when δ is finite. */
function drawInset(now: number, alpha: number) {
  if (demo.id !== 'square' || alpha <= 0) return;
  const W = canvas.clientWidth, H = canvas.clientHeight;
  if (W < 560) return;
  const w = Math.min(300, W * 0.3), h = w * 0.72;
  const rect: Rect = { x: W - w - 18, y: H - h - 18, w, h };
  const yh = eps * 1.8, xh = yh * (w / h);
  const v = new View(rect, { xmin: demo.a - xh, xmax: demo.a + xh, ymin: demo.fa - yh, ymax: demo.fa + yh });
  const zoom = v.yScale / mainView.yScale;
  const fade = alpha * Math.min(1, Math.max(0, (zoom - 1.5) / 1.5));  // hide when it isn't magnifying
  if (fade <= 0) return;

  ctx.save();
  ctx.globalAlpha = fade;
  ctx.fillStyle = COLORS.bg;
  ctx.fillRect(rect.x, rect.y, rect.w, rect.h);
  ctx.beginPath(); ctx.rect(rect.x, rect.y, rect.w, rect.h); ctx.clip();
  drawGridAndAxes(ctx, v, false);
  drawDemo(v, demo, now, 1, false);
  ctx.restore();

  ctx.save();
  ctx.globalAlpha = fade;
  ctx.strokeStyle = COLORS.dim;
  ctx.lineWidth = 1;
  ctx.strokeRect(rect.x + 0.5, rect.y + 0.5, rect.w - 1, rect.h - 1);
  label(`zoom ×${zoom.toFixed(0)}`, rect.x + 8, rect.y + 8, COLORS.text, `13px ${MAIN_FONT}`, 'left', 'top');
  ctx.restore();
}

function lerpBounds(a: Bounds, b: Bounds, t: number): Bounds {
  return { xmin: lerp(a.xmin, b.xmin, t), xmax: lerp(a.xmax, b.xmax, t), ymin: lerp(a.ymin, b.ymin, t), ymax: lerp(a.ymax, b.ymax, t) };
}

function resize() {
  const dpr = window.devicePixelRatio || 1;
  const w = canvas.clientWidth, h = canvas.clientHeight;
  if (canvas.width !== Math.round(w * dpr) || canvas.height !== Math.round(h * dpr)) {
    canvas.width = Math.round(w * dpr);
    canvas.height = Math.round(h * dpr);
  }
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
}

// shortcut: redraws every frame even when idle. Upgrade path: only schedule frames while
// animating/dragging if battery or CPU use matters (M2's scene player will need the same loop).
function frame(now: number) {
  resize();
  const W = canvas.clientWidth, H = canvas.clientHeight;
  const t = morph.get(now);
  const morphing = prevDemo !== null && t < 1;
  const pad = 20;
  mainView = new View({ x: pad, y: pad, w: W - 2 * pad, h: H - 2 * pad }, morphing ? lerpBounds(prevDemo!.bounds, demo.bounds, t) : demo.bounds);

  ctx.fillStyle = COLORS.bg;
  ctx.fillRect(0, 0, W, H);
  drawGridAndAxes(ctx, mainView, true);
  if (morphing) drawDemo(mainView, prevDemo!, now, 1 - t, true);
  drawDemo(mainView, demo, now, morphing ? t : 1, true);
  drawHandle(mainView, morphing ? t : 1);
  drawInset(now, morphing ? t : 1);
  requestAnimationFrame(frame);
}

// ---- input ----

/** Is the pointer on the handle or on either ε-band edge? */
function onEpsTarget(px: number, py: number) {
  const h = handlePos(mainView);
  if (Math.hypot(px - h.x, py - h.y) < 16) return true;
  const top = mainView.sy(demo.fa + eps), bot = mainView.sy(demo.fa - eps);
  return Math.abs(py - top) < 9 || Math.abs(py - bot) < 9;
}

function local(e: PointerEvent) {
  const r = canvas.getBoundingClientRect();
  return { x: e.clientX - r.left, y: e.clientY - r.top };
}

canvas.addEventListener('pointerdown', (e) => {
  const p = local(e);
  if (!onEpsTarget(p.x, p.y)) return;
  dragging = true;
  canvas.setPointerCapture(e.pointerId);
  canvas.style.cursor = 'grabbing';
  setEps(Math.abs(mainView.wy(p.y) - demo.fa));
});

canvas.addEventListener('pointermove', (e) => {
  const p = local(e);
  if (dragging) {
    // Either edge works: ε is just the distance from f(a), so dragging the bottom edge down also grows ε.
    setEps(Math.abs(mainView.wy(p.y) - demo.fa));
  } else {
    hovering = onEpsTarget(p.x, p.y);
    canvas.style.cursor = hovering ? 'ns-resize' : 'default';
  }
});

const endDrag = () => {
  dragging = false;
  canvas.style.cursor = hovering ? 'ns-resize' : 'default';
};
canvas.addEventListener('pointerup', endDrag);
canvas.addEventListener('pointercancel', endDrag);
canvas.addEventListener('pointerleave', () => { if (!dragging) hovering = false; });

slider.min = String(EPS_MIN);
slider.max = String(EPS_MAX);
slider.addEventListener('input', () => setEps(Number(slider.value)));

for (const b of document.querySelectorAll<HTMLButtonElement>('#tabs button')) {
  const d = DEMOS[b.dataset.demo as keyof typeof DEMOS];
  tex(b, d.tab);
  b.addEventListener('click', () => setDemo(d));
}

tex($('def'), '\\forall \\varepsilon > 0\\ \\exists \\delta > 0:\\quad |x - a| < \\delta \\Rightarrow |f(x) - f(a)| < \\varepsilon');
updatePanel();
requestAnimationFrame(frame);
