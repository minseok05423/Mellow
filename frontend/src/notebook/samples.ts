// Bake-off sample recorder (SPEC §7). The student lassoes one handwritten line, types what it should
// read as, and we save its raw strokes plus a rendered PNG. Later, the same samples are replayed to
// every recognizer candidate (stroke recognizers get strokes; vision LLMs get the image).
// Stored in IndexedDB rather than localStorage because the images don't fit localStorage's ~5 MB.

import { openDB } from 'idb';
import { Editor, TLDrawShape } from 'tldraw';
import { getInk } from './ink';

export type SampleKind = 'math' | 'mixed' | 'messy';

export interface Sample {
  id: string;
  createdAt: number;
  notebookId: string;
  kind: SampleKind;
  truth: string;      // expected reading, LaTeX (prose as \text{...})
  pointer: string;    // input device of the first stroke
  /** Strokes in page units relative to the selection's top-left. Points: [x, y, pressure, ms since stroke start]. */
  strokes: { t0: number; pts: number[][] }[];
  image: string;      // PNG data URL of the selection, as tldraw renders it
}

const db = openDB('mellow-samples', 1, {
  upgrade(d) { d.createObjectStore('samples', { keyPath: 'id' }); },
});

/** The selected draw shapes that carry recorded ink. */
export function selectedInkShapes(editor: Editor): TLDrawShape[] {
  return editor
    .getSelectedShapes()
    .filter((s): s is TLDrawShape => s.type === 'draw' && !!getInk(s as TLDrawShape));
}

export async function saveSample(editor: Editor, notebookId: string, truth: string, kind: SampleKind) {
  const shapes = selectedInkShapes(editor).sort((a, b) => getInk(a)!.t0 - getInk(b)!.t0);
  if (shapes.length === 0) throw new Error('Select some handwriting first (lasso it).');
  const bounds = editor.getSelectionPageBounds()!;
  // shortcut: ignores scaleX/scaleY from resizing a stroke after writing. Upgrade path: apply the
  // shape's scale to dx/dy if resized samples ever matter.
  const strokes = shapes.map((s) => {
    const ink = getInk(s)!;
    return {
      t0: ink.t0,
      pts: ink.pts.map(([dx, dy, p, dt]) => [
        Math.round((s.x + dx - bounds.x) * 100) / 100, Math.round((s.y + dy - bounds.y) * 100) / 100, p, dt,
      ]),
    };
  });
  const { url: image } = await editor.toImageDataUrl(shapes, { format: 'png', background: true, padding: 16 });
  const sample: Sample = {
    id: crypto.randomUUID(),
    createdAt: Date.now(),
    notebookId,
    kind,
    truth,
    pointer: getInk(shapes[0])!.pointer,
    strokes,
    image,
  };
  await (await db).put('samples', sample);
  return sample;
}

export async function countSamples() {
  return (await db).count('samples');
}

/** Downloads every sample as one JSON file for the bake-off scripts. */
export async function downloadSamples() {
  const samples = (await (await db).getAll('samples')) as Sample[];
  const blob = new Blob([JSON.stringify({ schema_version: 1, samples }, null, 1)], { type: 'application/json' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = `mellow-samples-${new Date().toISOString().slice(0, 10)}.json`;
  a.click();
  URL.revokeObjectURL(a.href);
}
