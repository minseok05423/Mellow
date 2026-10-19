// Raw stroke log (SPEC §6). tldraw's draw shapes keep only x, y and pressure, without timestamps and
// at reduced precision. Stroke recognizers (and datasets like MathWriting) want full-rate points with
// times, so we record pointer events ourselves and attach them to the draw shape's `meta.ink`.
// Living in `meta` means the ink moves, deletes, undoes and syncs together with its shape.

import { Editor, TLDrawShape } from 'tldraw';

// A type alias (not an interface) so it is assignable to tldraw's JSON `meta` type.
export type Ink = {
  t0: number;        // epoch ms at pen-down
  pointer: string;   // 'pen' | 'mouse' | 'touch'
  /** [dx, dy, pressure, dt]: page units relative to the shape's origin, ms since t0. */
  pts: number[][];
};

export function getInk(shape: TLDrawShape): Ink | undefined {
  return shape.meta.ink as Ink | undefined;
}

const round = (v: number, digits: number) => Math.round(v * 10 ** digits) / 10 ** digits;

/** Starts recording strokes for the draw tool. Returns a disposer. */
export function recordInk(editor: Editor): () => void {
  const container = editor.getContainer();
  // A stroke being recorded. pts are absolute page-space [x, y, pressure, eventTimeMs].
  type Recording = { t0: number; start: number; pointer: string; pts: number[][] };
  let current: Recording | null = null;
  let finished: Recording | null = null;

  const accept = (e: PointerEvent) =>
    editor.getCurrentToolId() === 'draw' &&
    // In pen mode tldraw ignores fingers (palm rejection); so do we.
    !(editor.getInstanceState().isPenMode && e.pointerType !== 'pen');

  const push = (e: PointerEvent) => {
    const p = editor.screenToPage({ x: e.clientX, y: e.clientY });
    current!.pts.push([p.x, p.y, e.pressure, e.timeStamp]);
  };

  const onDown = (e: PointerEvent) => {
    if (e.button !== 0 || !accept(e)) return;
    current = { t0: Date.now(), start: e.timeStamp, pointer: e.pointerType, pts: [] };
    push(e);
  };
  const onMove = (e: PointerEvent) => {
    if (!current) return;
    // Coalesced events carry the full-rate samples (240 Hz on Apple Pencil) between frames.
    for (const ce of e.getCoalescedEvents?.() ?? [e]) push(ce);
  };
  const onUp = (e: PointerEvent) => {
    if (!current) return;
    push(e);
    finished = current;
    current = null;
  };

  // Capture phase on the container: we see each event before tldraw's canvas handles it.
  container.addEventListener('pointerdown', onDown, true);
  container.addEventListener('pointermove', onMove, true);
  container.addEventListener('pointerup', onUp, true);

  // When tldraw marks the draw shape complete, fold the recorded stroke into the same change,
  // so it lands in the same undo step as the stroke itself.
  const stop = editor.sideEffects.registerBeforeChangeHandler('shape', (prev, next) => {
    if (next.type !== 'draw' || prev.type !== 'draw') return next;
    if (prev.props.isComplete || !next.props.isComplete) return next;
    const stroke = finished ?? current;
    if (!stroke || stroke.pts.length === 0) return next;
    finished = null;
    const ink: Ink = {
      t0: stroke.t0,
      pointer: stroke.pointer,
      pts: stroke.pts.map(([x, y, p, t]) => [
        round(x - next.x, 2), round(y - next.y, 2), round(p, 3), Math.round(t - stroke.start),
      ]),
    };
    return { ...next, meta: { ...next.meta, ink } };
  });

  return () => {
    container.removeEventListener('pointerdown', onDown, true);
    container.removeEventListener('pointermove', onMove, true);
    container.removeEventListener('pointerup', onUp, true);
    stop();
  };
}
