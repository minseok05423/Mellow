// World <-> screen mapping and basic 2D plot drawing (grid, axes, curves).

export interface Rect { x: number; y: number; w: number; h: number }
export interface Bounds { xmin: number; xmax: number; ymin: number; ymax: number }

export const COLORS = {
  bg: '#0c0d10',
  grid: 'rgba(88, 196, 221, 0.13)',
  axis: '#d8d8d8',
  text: '#ececec',
  dim: '#8a8f98',
  blue: '#58C4DD',
  yellow: '#F7D96F',
  green: '#83C167',
  red: '#FC6255',
};

export const MATH_FONT = 'KaTeX_Math, "Times New Roman", serif';
export const MAIN_FONT = 'KaTeX_Main, "Times New Roman", serif';

export class View {
  constructor(public rect: Rect, public b: Bounds) {}
  sx(x: number) { return this.rect.x + ((x - this.b.xmin) / (this.b.xmax - this.b.xmin)) * this.rect.w; }
  sy(y: number) { return this.rect.y + ((this.b.ymax - y) / (this.b.ymax - this.b.ymin)) * this.rect.h; }
  wy(py: number) { return this.b.ymax - ((py - this.rect.y) / this.rect.h) * (this.b.ymax - this.b.ymin); }
  /** Pixels per world unit, vertically. */
  get yScale() { return this.rect.h / (this.b.ymax - this.b.ymin); }
}

/** A "nice" tick spacing (1, 2 or 5 × 10^k) giving roughly `target` ticks over `range`. */
export function niceStep(range: number, target: number): number {
  const raw = range / target;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const n = raw / mag;
  return (n < 1.5 ? 1 : n < 3.5 ? 2 : n < 7.5 ? 5 : 10) * mag;
}

function ticks(lo: number, hi: number, step: number): number[] {
  const out: number[] = [];
  for (let v = Math.ceil(lo / step) * step; v <= hi + 1e-9; v += step) out.push(Math.abs(v) < step / 1e6 ? 0 : v);
  return out;
}

function fmt(v: number, step: number) {
  const digits = Math.max(0, -Math.floor(Math.log10(step) + 1e-9));
  return v.toFixed(digits).replace('-', '−');
}

export function drawGridAndAxes(ctx: CanvasRenderingContext2D, v: View, labels: boolean) {
  const { b, rect } = v;
  const xs = niceStep(b.xmax - b.xmin, rect.w / 90);
  const ys = niceStep(b.ymax - b.ymin, rect.h / 70);

  ctx.lineWidth = 1;
  ctx.strokeStyle = COLORS.grid;
  ctx.beginPath();
  for (const x of ticks(b.xmin, b.xmax, xs)) { ctx.moveTo(v.sx(x), rect.y); ctx.lineTo(v.sx(x), rect.y + rect.h); }
  for (const y of ticks(b.ymin, b.ymax, ys)) { ctx.moveTo(rect.x, v.sy(y)); ctx.lineTo(rect.x + rect.w, v.sy(y)); }
  ctx.stroke();

  // Axes sit at x=0 / y=0 when visible, else hug the edge.
  const ax = Math.min(Math.max(v.sx(0), rect.x), rect.x + rect.w);
  const ay = Math.min(Math.max(v.sy(0), rect.y), rect.y + rect.h);
  ctx.strokeStyle = COLORS.axis;
  ctx.lineWidth = 1.5;
  ctx.beginPath();
  ctx.moveTo(rect.x, ay); ctx.lineTo(rect.x + rect.w, ay);
  ctx.moveTo(ax, rect.y); ctx.lineTo(ax, rect.y + rect.h);
  ctx.stroke();

  if (!labels) return;
  ctx.fillStyle = COLORS.dim;
  ctx.font = `13px ${MAIN_FONT}`;
  ctx.textAlign = 'center';
  ctx.textBaseline = 'top';
  for (const x of ticks(b.xmin, b.xmax, xs)) if (x !== 0) ctx.fillText(fmt(x, xs), v.sx(x), ay + 6);
  ctx.textAlign = 'right';
  ctx.textBaseline = 'middle';
  for (const y of ticks(b.ymin, b.ymax, ys)) if (y !== 0) ctx.fillText(fmt(y, ys), ax - 6, v.sy(y));
}

/**
 * Stroke y = f(x) across the view. `colorAt(x, y)` picks each segment's color;
 * the path is broken at `jumps` (discontinuities) so no fake vertical line appears.
 */
export function drawCurve(
  ctx: CanvasRenderingContext2D, v: View, f: (x: number) => number,
  colorAt: (x: number, y: number) => string, jumps: number[] = [], width = 4,
) {
  const n = Math.max(200, Math.round(v.rect.w));
  const dx = (v.b.xmax - v.b.xmin) / n;
  ctx.lineWidth = width;
  ctx.lineCap = 'round';
  ctx.lineJoin = 'round';
  let color = '';
  let open = false;
  for (let i = 0; i < n; i++) {
    const x0 = v.b.xmin + i * dx;
    const x1 = x0 + dx;
    // Skip segments touching a jump (with float slack: x0 + dx may land a hair before it), leaving a gap.
    if (jumps.some((j) => j >= x0 - dx * 1e-6 && j <= x1 + dx * 1e-6)) {
      if (open) ctx.stroke();
      open = false;
      continue;
    }
    const xm = (x0 + x1) / 2;
    const c = colorAt(xm, f(xm));
    if (!open || c !== color) {
      if (open) ctx.stroke();
      ctx.beginPath();
      ctx.strokeStyle = c;
      ctx.moveTo(v.sx(x0), v.sy(f(x0)));
      color = c;
      open = true;
    }
    ctx.lineTo(v.sx(x1), v.sy(f(x1)));
  }
  if (open) ctx.stroke();
}

export function dot(ctx: CanvasRenderingContext2D, x: number, y: number, r: number, color: string, filled: boolean) {
  ctx.beginPath();
  ctx.arc(x, y, r, 0, Math.PI * 2);
  if (filled) { ctx.fillStyle = color; ctx.fill(); }
  else { ctx.fillStyle = COLORS.bg; ctx.fill(); ctx.lineWidth = 2.5; ctx.strokeStyle = color; ctx.stroke(); }
}
