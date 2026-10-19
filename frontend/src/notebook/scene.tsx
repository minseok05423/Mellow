// Animated explanations (SPEC §4 board objects): a board on the page that plays scene JSON (scene-json skill)
// one step at a time, Manim-style. `write` draws LaTeX in from left to right; `transform` slides the symbols
// two equations share into their new places while new ones fade in and dropped ones fade out; `highlight`
// pulses a line (or its `part`) and turns it yellow; `fade` clears it. A step with a caption waits for a
// click; one without plays straight on. The scene comes from the backend, built from a SymPy-checked proof.
// Version 2 scenes add a figure (DECISIONS #24): `draw` makes a point, segment, polygon, circle, angle mark or
// curve draw itself on; `move` turns and slides a piece to its new place; highlight and fade work on shapes too.

import katex from 'katex';
import { useMemo, useRef, useState } from 'react';
import { Box, Editor, HTMLContainer, Rectangle2d, ShapeUtil, T, TLShape, createShapeId } from 'tldraw';
import { lerp, play } from '../anim';
import type { CheckResult } from './api';
import { MathText, lineText, placeNear } from './feedback';

const TYPE = 'mellow-scene';

declare module '@tldraw/tlschema' {
  interface TLGlobalShapePropsMap {
    [TYPE]: { w: number; h: number; scene: string; line: string; message: string };
  }
}
export type SceneShape = TLShape<typeof TYPE>;

export interface FigureShape {
  kind: 'point' | 'segment' | 'polygon' | 'circle' | 'angle' | 'curve';
  points: string[];
  label?: string;
  pts?: [number, number][]; // curve samples
}
export interface SceneStep {
  id: string;
  op: 'write' | 'fade' | 'transform' | 'highlight' | 'draw' | 'move';
  shape?: FigureShape;
  to?: string[];
  latex?: string;
  pos?: [number, number];
  from?: string;
  target?: string;
  part?: string;
  say?: string;
}
export interface Figure {
  points: Record<string, [number, number]>;
  view: [number, number, number, number]; // math coordinates shown, y up
  box: [number, number, number, number]; // where on the stage, normalized [x, y, w, h]
}
export interface Scene { schema_version: number; figure?: Figure; steps: SceneStep[] }

const SVG = 'http://www.w3.org/2000/svg';
const PALETTE = ['#58c4dd', '#83c167', '#fc6255', '#9a72ac', '#5cd0b3', '#f0ac5f']; // 3Blue1Brown's colours
const YELLOW = '#f7d94c';
type Pt = [number, number];
const centroid = (ps: Pt[]): Pt => [ps.reduce((s, p) => s + p[0], 0) / ps.length, ps.reduce((s, p) => s + p[1], 0) / ps.length];
const signedArea = (ps: Pt[]) => ps.reduce((s, p, i) => s + p[0] * ps[(i + 1) % ps.length][1] - ps[(i + 1) % ps.length][0] * p[1], 0) / 2;

/** SVG path for a shape at stage-pixel points. */
function pathFor(kind: FigureShape['kind'], ps: Pt[]): string {
  if (kind === 'circle') {
    const r = Math.hypot(ps[1][0] - ps[0][0], ps[1][1] - ps[0][1]);
    const [cx, cy] = ps[0];
    return `M ${cx - r} ${cy} a ${r} ${r} 0 1 0 ${2 * r} 0 a ${r} ${r} 0 1 0 ${-2 * r} 0`;
  }
  if (kind === 'angle') {
    const [a, b, c] = ps;
    const unit = (p: Pt): Pt => { const d = Math.hypot(p[0] - b[0], p[1] - b[1]) || 1; return [(p[0] - b[0]) / d, (p[1] - b[1]) / d]; };
    const u = unit(a), w = unit(c), r = 18;
    if (Math.abs(u[0] * w[0] + u[1] * w[1]) < 0.02) { // a right angle: the little square
      const k = r * 0.75;
      return `M ${b[0] + u[0] * k} ${b[1] + u[1] * k} L ${b[0] + (u[0] + w[0]) * k} ${b[1] + (u[1] + w[1]) * k} L ${b[0] + w[0] * k} ${b[1] + w[1] * k}`;
    }
    const sweep = u[0] * w[1] - u[1] * w[0] > 0 ? 1 : 0;
    return `M ${b[0] + u[0] * r} ${b[1] + u[1] * r} A ${r} ${r} 0 0 ${sweep} ${b[0] + w[0] * r} ${b[1] + w[1] * r}`;
  }
  const d = ps.map((p, i) => `${i ? 'L' : 'M'} ${p[0]} ${p[1]}`).join(' ');
  return kind === 'polygon' ? `${d} Z` : d;
}

interface ShapeObj { kind: FigureShape['kind']; path: SVGPathElement; dot?: SVGCircleElement; label?: HTMLDivElement; ps: Pt[] }

const W = 560;
const H = 580;
// KaTeX escapes everything it renders; \htmlClass is allowed only for our highlight class, so LaTeX written by
// the model can't add classes of its own.
const KATEX = { strict: false, trust: (c: { command: string; class?: string }) => c.command === '\\htmlClass' && c.class === 'scene-hl' };

/** KaTeX HTML for a line; with `part`, that piece is wrapped in .scene-hl (whole line if it isn't a subexpression). */
function render(latex: string, part?: string): string {
  const i = part ? latex.indexOf(part) : -1;
  if (part && i >= 0) {
    try {
      return katex.renderToString(`${latex.slice(0, i)}\\htmlClass{scene-hl}{${part}}${latex.slice(i + part.length)}`,
        { ...KATEX, throwOnError: true });
    } catch { /* fall through */ }
  }
  return katex.renderToString(latex, { ...KATEX, throwOnError: false });
}

/** The leaf spans KaTeX draws glyphs in: what a transform moves. */
const glyphs = (el: HTMLElement) => [...el.querySelectorAll<HTMLElement>('.katex-html span')]
  .filter((s) => s.childElementCount === 0 && s.textContent!.replace(/[\s\u200b]/g, '')); // not KaTeX's spacer spans

/** The shrink-to-fit scale of a line (1 unless it was too wide for the board). */
const fitScale = (el: HTMLElement) => Number((el.firstElementChild as HTMLElement).style.scale || 1);

/** Plays scene steps into a stage element. Positions are normalized to the stage, as the scene format says. */
class Player {
  private objects = new Map<string, { el: HTMLDivElement; latex: string; pos: [number, number] }>();
  private shapes = new Map<string, ShapeObj>();
  private svg: SVGSVGElement | null = null;
  private drawn = 0; // for the colour cycle

  constructor(private stage: HTMLDivElement, private figure?: Figure) {
    this.reset();
  }

  reset() {
    // Only the player's own nodes: the stage also holds React's "Click to start", which React removes itself.
    this.stage.querySelectorAll(':scope > .scene-obj, :scope > .scene-label, :scope > .scene-figure').forEach((n) => n.remove());
    this.objects.clear();
    this.shapes.clear();
    this.drawn = 0;
    this.svg = null;
    if (!this.figure) return;
    this.svg = document.createElementNS(SVG, 'svg');
    this.svg.setAttribute('class', 'scene-figure');
    this.svg.setAttribute('viewBox', `0 0 ${this.stage.offsetWidth} ${this.stage.offsetHeight}`);
    this.stage.append(this.svg);
  }

  /** Math coordinates (y up) -> stage pixels, keeping the aspect ratio, centred in the figure's box. */
  private px([x, y]: Pt): Pt {
    const f = this.figure!;
    const W = this.stage.offsetWidth, H = this.stage.offsetHeight;
    const [bx, by, bw, bh] = [f.box[0] * W, f.box[1] * H, f.box[2] * W, f.box[3] * H];
    const [x0, y0, x1, y1] = f.view;
    const s = Math.min(bw / (x1 - x0), bh / (y1 - y0));
    return [bx + (bw - s * (x1 - x0)) / 2 + (x - x0) * s, by + (bh - s * (y1 - y0)) / 2 + (y1 - y) * s];
  }

  private label(latex: string, at: Pt): HTMLDivElement {
    const el = document.createElement('div');
    el.className = 'scene-label';
    el.innerHTML = render(latex);
    this.stage.append(el);
    // Labels are centred on their spot; if one would sit on another label, nudge it up or down until clear.
    const others = [...this.stage.querySelectorAll<HTMLElement>(':scope > .scene-label')].filter((o) => o !== el && o.style.opacity !== '0')
      .map((o) => [parseFloat(o.style.left) - o.offsetWidth / 2, parseFloat(o.style.top) - o.offsetHeight / 2, o.offsetWidth, o.offsetHeight]);
    const w = el.offsetWidth, h = el.offsetHeight;
    const clear = (y: number) => others.every(([ox, oy, ow, oh]) =>
      at[0] - w / 2 >= ox + ow || at[0] + w / 2 <= ox || y - h / 2 >= oy + oh || y + h / 2 <= oy);
    const y = [0, -1, 1, -2, 2, -3, 3].map((k) => at[1] + k * (h + 2)).find(clear) ?? at[1];
    el.style.left = `${at[0]}px`;
    el.style.top = `${y}px`;
    return el;
  }

  /** Where a shape's label goes: inside a polygon, beside a segment or point (away from the figure's middle). */
  private labelAt(kind: FigureShape['kind'], ps: Pt[]): Pt {
    const mid = this.px(centroid(Object.values(this.figure!.points) as Pt[]));
    const away = (p: Pt, d: number): Pt => {
      const v = [p[0] - mid[0], p[1] - mid[1]], n = Math.hypot(v[0], v[1]) || 1;
      return [p[0] + (v[0] / n) * d, p[1] + (v[1] / n) * d];
    };
    if (kind === 'polygon') return centroid(ps);
    if (kind === 'segment') return away(centroid(ps), 14);
    if (kind === 'circle') return [ps[0][0], ps[0][1] - Math.hypot(ps[1][0] - ps[0][0], ps[1][1] - ps[0][1]) - 12];
    if (kind === 'curve') { const p = ps[Math.floor(ps.length * 0.8)]; return [p[0] + 10, p[1] - 16]; } // clear of the end point
    return away(ps[0], 13);
  }

  private async drawShape(s: SceneStep) {
    const sh = s.shape!;
    const f = this.figure!;
    const ps = (sh.kind === 'curve' ? sh.pts! : sh.points.map((n) => f.points[n])).map((p) => this.px(p as Pt));
    const colour = sh.kind === 'point' || sh.kind === 'angle' ? '#f1f1ee' : PALETTE[this.drawn++ % PALETTE.length];
    const path = document.createElementNS(SVG, 'path');
    path.setAttribute('d', sh.kind === 'point' ? '' : pathFor(sh.kind, ps));
    path.style.stroke = colour;
    path.style.fill = sh.kind === 'polygon' || sh.kind === 'circle' ? colour : 'none';
    path.style.fillOpacity = '0';
    this.svg!.append(path);
    const obj: ShapeObj = { kind: sh.kind, path, ps };
    this.shapes.set(s.id, obj);
    const text = sh.label ?? (sh.kind === 'point' ? sh.points[0] : '');
    if (sh.kind === 'point') {
      const dot = document.createElementNS(SVG, 'circle');
      dot.setAttribute('cx', String(ps[0][0]));
      dot.setAttribute('cy', String(ps[0][1]));
      dot.style.fill = colour;
      this.svg!.append(dot);
      obj.dot = dot;
    }
    if (text) obj.label = this.label(text, this.labelAt(sh.kind, ps));
    if (obj.label) obj.label.style.opacity = '0';
    const length = sh.kind === 'point' ? 0 : path.getTotalLength();
    path.style.strokeDasharray = `${length}`;
    await play(sh.kind === 'point' ? 350 : Math.min(1300, 500 + length * 1.5), (t) => {
      path.style.strokeDashoffset = String(length * (1 - Math.min(1, t * 1.25)));
      if (sh.kind === 'polygon' || sh.kind === 'circle') path.style.fillOpacity = String((sh.kind === 'circle' ? 0.12 : 0.3) * Math.max(0, t * 2 - 1));
      if (obj.dot) obj.dot.setAttribute('r', String(4.5 * t));
      if (obj.label) obj.label.style.opacity = String(Math.max(0, t * 2 - 1));
    });
    path.style.strokeDasharray = '';
    if (sh.kind === 'polygon') this.cutInto(s.id, ps);
  }

  /** A piece drawn inside an earlier polygon: that polygon becomes an outline, and labels the piece covers fade,
   * so a square cut into pieces reads as its pieces. */
  private cutInto(id: string, piece: Pt[]) {
    const inside = (p: Pt, poly: Pt[]) => { // point in polygon, boundary counts as inside
      let hit = false;
      for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
        const [xi, yi] = poly[i], [xj, yj] = poly[j];
        if (Math.abs((xj - xi) * (p[1] - yi) - (yj - yi) * (p[0] - xi)) < 1 && Math.min(xi, xj) - 1 <= p[0] && p[0] <= Math.max(xi, xj) + 1
          && Math.min(yi, yj) - 1 <= p[1] && p[1] <= Math.max(yi, yj) + 1) return true;
        if ((yi > p[1]) !== (yj > p[1]) && p[0] < ((xj - xi) * (p[1] - yi)) / (yj - yi) + xi) hit = !hit;
      }
      return hit;
    };
    for (const [other, o] of this.shapes) {
      if (other === id || o.kind !== 'polygon') continue;
      // A strictly smaller piece inside it (not a copy drawn on top) makes it an outline.
      if (Math.abs(signedArea(piece)) < Math.abs(signedArea(o.ps)) * 0.999 && piece.every((p) => inside(p, o.ps))) o.path.style.fillOpacity = '0.05';
      if (o.label && inside([parseFloat(o.label.style.left), parseFloat(o.label.style.top)], piece)) o.label.style.opacity = '0';
    }
  }

  /** Turn and slide a piece to its new place (a rotation when the piece isn't flipped; it can't be stretched). */
  private async moveShape(s: SceneStep) {
    const o = this.shapes.get(s.target!);
    if (!o) throw new Error(`step ${s.id}: nothing to move`);
    this.shapes.delete(s.target!);
    this.shapes.set(s.id, o);
    const from = o.ps, to = s.to!.map((n) => this.px(this.figure!.points[n] as Pt));
    const c0 = centroid(from), c1 = centroid(to);
    const rigid = from.length < 3 || Math.sign(signedArea(from)) === Math.sign(signedArea(to));
    let turn = Math.atan2(to[1][1] - to[0][1], to[1][0] - to[0][0]) - Math.atan2(from[1][1] - from[0][1], from[1][0] - from[0][0]);
    turn = Math.atan2(Math.sin(turn), Math.cos(turn)); // the short way round
    const at = (t: number): Pt[] => from.map((p, i) => {
      if (!rigid) return [lerp(p[0], to[i][0], t), lerp(p[1], to[i][1], t)];
      const a = turn * t, dx = p[0] - c0[0], dy = p[1] - c0[1];
      return [lerp(c0[0], c1[0], t) + dx * Math.cos(a) - dy * Math.sin(a), lerp(c0[1], c1[1], t) + dx * Math.sin(a) + dy * Math.cos(a)];
    });
    await play(1100, (t) => {
      const ps = at(t);
      o.path.setAttribute('d', pathFor(o.kind, ps));
      if (o.label) { const l = this.labelAt(o.kind, ps); o.label.style.left = `${l[0]}px`; o.label.style.top = `${l[1]}px`; }
    });
    o.ps = to;
  }

  /** Screen px per stage px (the tldraw camera may be zoomed); glyph moves are measured on screen. */
  private zoom() {
    return this.stage.getBoundingClientRect().width / this.stage.offsetWidth || 1;
  }

  private place(latex: string, pos: [number, number]): HTMLDivElement {
    const el = document.createElement('div');
    el.className = 'scene-obj';
    el.style.left = `${pos[0] * 100}%`;
    el.style.top = `${pos[1] * 100}%`;
    el.innerHTML = `<div class="scene-fit">${render(latex)}</div>`;
    this.stage.append(el);
    // Too wide for the board: shrink to fit instead of running off the edge.
    const fit = el.firstElementChild as HTMLElement;
    const room = this.stage.offsetWidth * (1 - pos[0]) - 16;
    if (fit.offsetWidth > room) fit.style.scale = String(room / fit.offsetWidth);
    return el;
  }

  async step(s: SceneStep) {
    const shape = s.target ? this.shapes.get(s.target) : undefined;
    if (s.op === 'draw') return this.drawShape(s);
    if (s.op === 'move') return this.moveShape(s);
    if (shape && s.op === 'fade') {
      this.shapes.delete(s.target!);
      const els = [shape.path, shape.dot, shape.label].filter(Boolean) as (HTMLElement | SVGElement)[];
      await play(400, (t) => els.forEach((e) => { e.style.opacity = String(1 - t); }));
      els.forEach((e) => e.remove());
      return;
    }
    if (shape && s.op === 'highlight') {
      shape.path.style.stroke = YELLOW;
      if (shape.kind === 'polygon' || shape.kind === 'circle') shape.path.style.fill = YELLOW;
      if (shape.dot) shape.dot.style.fill = YELLOW;
      shape.label?.classList.add('scene-lit');
      await play(800, (t) => { shape.path.style.strokeWidth = String(3 + 3 * Math.sin(Math.PI * t)); });
      return;
    }
    if (s.op === 'write') {
      const el = this.place(s.latex!, s.pos!);
      this.objects.set(s.id, { el, latex: s.latex!, pos: s.pos! });
      el.style.opacity = '0';
      await play(Math.min(1400, 500 + s.latex!.length * 14), (t) => {
        el.style.clipPath = `inset(-30% ${100 - t * 100}% -30% -4%)`; // room above/below for exponents
        el.style.opacity = String(Math.min(1, t * 2.5));
      });
      el.style.clipPath = '';
    } else if (s.op === 'fade') {
      const { el } = this.take(s.target!);
      await play(350, (t) => { el.style.opacity = String(1 - t); el.style.translate = `0 ${-8 * t}px`; });
      el.remove();
    } else if (s.op === 'transform') {
      await this.transform(s);
    } else {
      const o = this.objects.get(s.target!);
      if (!o) throw new Error(`step ${s.id}: nothing to highlight`);
      if (s.part) (o.el.firstElementChild as HTMLElement).innerHTML = render(o.latex, s.part);
      const parts = [...o.el.querySelectorAll<HTMLElement>('.scene-hl')];
      const lit = parts.length ? parts : [o.el.querySelector<HTMLElement>('.katex')!];
      lit.forEach((x) => { x.style.display = 'inline-block'; x.classList.add('scene-lit'); });
      await play(800, (t) => lit.forEach((x) => { x.style.scale = String(1 + 0.14 * Math.sin(Math.PI * t)); }));
    }
  }

  private take(id: string) {
    const o = this.objects.get(id);
    if (!o) throw new Error(`no object ${id} on the board`);
    this.objects.delete(id);
    return o;
  }

  /** Match glyphs by symbol, in reading order: matched ones fly from their old place, the rest cross-fade. */
  private async transform(s: SceneStep) {
    const old = this.take(s.from!);
    const pos = s.pos ?? old.pos;
    const el = this.place(s.latex!, pos);
    this.objects.set(s.id, { el, latex: s.latex!, pos });
    const before = glyphs(old.el);
    const after = glyphs(el);
    [...before, ...after].forEach((g) => { g.style.display = 'inline-block'; }); // transforms need a box
    const from = before.map((g) => g.getBoundingClientRect());
    const used = new Set<number>();
    const zoom = this.zoom() * fitScale(el); // screen px per px inside the new line's box
    const moves = after.map((g) => {
      const k = before.findIndex((b, j) => !used.has(j) && b.textContent === g.textContent);
      if (k < 0) return null;
      used.add(k);
      const a = from[k], b = g.getBoundingClientRect();
      return { g, dx: (a.x + a.width / 2 - b.x - b.width / 2) / zoom, dy: (a.y + a.height / 2 - b.y - b.height / 2) / zoom,
        s: b.height ? a.height / b.height : 1 };
    });
    before.forEach((g, j) => { if (used.has(j)) g.style.visibility = 'hidden'; }); // its twin flies instead
    const fresh = after.filter((_, k) => !moves[k]);
    const frame = (t: number) => {
      for (const m of moves) {
        if (!m) continue;
        m.g.style.translate = `${m.dx * (1 - t)}px ${m.dy * (1 - t)}px`;
        m.g.style.scale = String(lerp(m.s, 1, t));
      }
      fresh.forEach((g) => { g.style.opacity = String(Math.max(0, t * 2 - 1)); });
      old.el.style.opacity = String(Math.max(0, 1 - t * 2));
    };
    frame(0); // before the first paint, so nothing flashes in its final place
    await play(950, frame);
    old.el.remove();
    after.forEach((g) => { g.style.translate = g.style.scale = g.style.opacity = g.style.display = ''; });
  }
}

const CHECKED = /✓ (checked by SymPy|true in this picture \(checked by SymPy\))/;

function SceneBoard({ shape }: { shape: SceneShape }) {
  const stage = useRef<HTMLDivElement>(null);
  const player = useRef<Player | null>(null);
  const scene = useMemo<Scene>(() => {
    try { return JSON.parse(shape.props.scene); } catch { return { schema_version: 1, steps: [] }; } // never break the canvas
  }, [shape.props.scene]);
  const [at, setAt] = useState(0); // steps played so far
  const [say, setSay] = useState('');
  const [busy, setBusy] = useState(false);
  const steps = scene.steps;
  const total = steps.filter((s) => s.say).length;
  const done = steps.slice(0, at).filter((s) => s.say).length;

  async function next() {
    if (busy || at >= steps.length || !stage.current) return;
    setBusy(true);
    const p = (player.current ??= new Player(stage.current, scene.figure));
    let i = at;
    try {
      // Play on until a step with a caption has played: fades that clear the board don't need a click.
      let s: SceneStep;
      do {
        s = steps[i++];
        if (s.say) setSay(s.say);
        await p.step(s);
      } while (!s.say && i < steps.length);
    } catch (e) {
      setSay(`This explanation couldn't play (${(e as Error).message}). Copy has it as text.`);
      i = steps.length;
    }
    setAt(i);
    setBusy(false);
  }

  function replay() {
    player.current?.reset(); // also redraws the empty figure layer
    setAt(0);
    setSay('');
  }

  const stop = (e: React.PointerEvent) => e.stopPropagation(); // a press on the board is for the board, not tldraw
  return (
    <>
      <div className="feedback-head scene-head">
        <span><span className="feedback-icon">❋</span> Explanation · AI</span>
        <span className="scene-count">{done} / {total}</span>
        <button className="feedback-copy" title="Copy the explanation as text (math as LaTeX)" onPointerDown={stop}
          onClick={() => navigator.clipboard.writeText(`Explanation · AI: ${shape.props.line}\n${shape.props.message}`)}>Copy</button>
      </div>
      <MathText className="scene-line" text={lineText(shape.props.line)} />
      <div className="scene-stage" ref={stage} onPointerDown={stop} onClick={next}
        title={at < steps.length ? 'Click for the next step' : ''}>
        {at === 0 && !busy && <div className="scene-start">▶ Click to start</div>}
      </div>
      <div className="scene-say" onPointerDown={stop}>
        {say ? <MathText text={say.replace(CHECKED, '')} /> : null}
        {CHECKED.test(say) && <span className="scene-checked">{say.match(CHECKED)![0]}</span>}
      </div>
      <div className="scene-controls" onPointerDown={stop}>
        <button onClick={replay} disabled={busy || at === 0} title="Start again">↺ Replay</button>
        <button className="primary" onClick={next} disabled={busy || at >= steps.length}>
          {at >= steps.length ? 'Done' : at === 0 ? 'Start ▸' : 'Next ▸'}
        </button>
      </div>
    </>
  );
}

export class SceneShapeUtil extends ShapeUtil<SceneShape> {
  static override type = TYPE;
  static override props = { w: T.number, h: T.number, scene: T.string, line: T.string, message: T.string };

  getDefaultProps(): SceneShape['props'] {
    return { w: W, h: H, scene: '{"schema_version":1,"steps":[]}', line: '', message: '' };
  }

  getGeometry(shape: SceneShape) {
    return new Rectangle2d({ width: shape.props.w, height: shape.props.h, isFilled: true });
  }

  override getText(shape: SceneShape) {
    return `Explanation · AI: ${shape.props.line}\n${shape.props.message}`;
  }

  override canResize() { return false; }
  override hideRotateHandle() { return true; }

  component(shape: SceneShape) {
    return (
      <HTMLContainer className="feedback feedback-explain scene" style={{ width: shape.props.w, height: shape.props.h }}>
        <SceneBoard shape={shape} />
      </HTMLContainer>
    );
  }

  getIndicatorPath(shape: SceneShape) {
    const p = new Path2D();
    p.roundRect(0, 0, shape.props.w, shape.props.h, 8);
    return p;
  }
}

/** An animated explanation next to the selection; its text (Copy) is the same explanation as a card's. */
export function addScene(editor: Editor, near: Box, line: string, result: CheckResult) {
  const { x, y } = placeNear(editor, near, W, H);
  editor.createShape<SceneShape>({
    id: createShapeId(), type: TYPE, x, y,
    props: { w: W, h: H, scene: JSON.stringify(result.scene), line, message: result.message },
  });
}
