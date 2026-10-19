// Animated explanations (SPEC §4 board objects): a board on the page that plays scene JSON (scene-json skill)
// one step at a time, Manim-style. `write` draws LaTeX in from left to right; `transform` slides the symbols
// two equations share into their new places while new ones fade in and dropped ones fade out; `highlight`
// pulses a line (or its `part`) and turns it yellow; `fade` clears it. A step with a caption waits for a
// click; one without plays straight on. The scene comes from the backend, built from a SymPy-checked proof.

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

export interface SceneStep {
  id: string;
  op: 'write' | 'fade' | 'transform' | 'highlight';
  latex?: string;
  pos?: [number, number];
  from?: string;
  target?: string;
  part?: string;
  say?: string;
}
export interface Scene { schema_version: number; steps: SceneStep[] }

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

  constructor(private stage: HTMLDivElement) {}

  reset() {
    this.stage.replaceChildren();
    this.objects.clear();
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
    const p = (player.current ??= new Player(stage.current));
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
    player.current?.reset();
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
        {say ? <MathText text={say.replace(/✓ checked by SymPy/, '')} /> : null}
        {/✓ checked by SymPy/.test(say) && <span className="scene-checked">✓ checked by SymPy</span>}
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
