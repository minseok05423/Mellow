// Line readings (SPEC §4): what the board thinks each handwritten line says, kept with the notebook
// (tldraw document meta) so readings are never redone just to answer an action. Writes skip the undo
// history: undoing a stroke should undo the stroke, not the board's reading of it.

import { Editor, atom } from 'tldraw';
import { api } from './api';
import { Line, getLines, linesAbove, lineStrokes } from './lines';

export interface LineReading {
  latex: string;
  confidence: number | null;
  source: string;
  confirmed: boolean; // the student confirmed or corrected it; only confirmed readings are ever judged
}

/** Per-line status that shouldn't be saved: in flight, or failed (with the reason). */
export const readingStatus = atom<Record<string, 'reading' | string>>('reading status', {});

/** Same rule as the backend's verify.looks_like_words: MyScript's math mode reads words as letters ("l e t f"). */
const looksLikeWords = (latex: string) =>
  /[a-zA-Z]{3,}/.test(latex.replace(/\\[a-zA-Z]+|[\s{}]/g, ''));

/** A line needs reading unless the student confirmed it or it has a usable reading (not empty, not letter by letter). */
export const needsReading = (r: LineReading | undefined) =>
  !r || (!r.confirmed && (!r.latex || (r.source === 'myscript' && looksLikeWords(r.latex))));

export function getReadings(editor: Editor): Record<string, LineReading> {
  return (editor.getDocumentSettings().meta.readings ?? {}) as unknown as Record<string, LineReading>;
}

function writeReadings(editor: Editor, readings: Record<string, LineReading>) {
  editor.run(() => {
    const meta = editor.getDocumentSettings().meta;
    editor.updateDocumentSettings({ meta: { ...meta, readings: readings as never } });
  }, { history: 'ignore' });
}

export function setReading(editor: Editor, key: string, r: LineReading) {
  writeReadings(editor, { ...getReadings(editor), [key]: r });
}

function setStatus(key: string, status: string | null) {
  const next = { ...readingStatus.get() };
  if (status === null) delete next[key];
  else next[key] = status;
  readingStatus.set(next);
}

async function readLine(editor: Editor, line: Line, lines: Line[]) {
  setStatus(line.key, 'reading');
  try {
    const readings = getReadings(editor);
    const context = linesAbove(lines, line).map((l) => readings[l.key]?.latex).filter(Boolean) as string[];
    // An image is sent at up to twice its size on the page, so small print in a screenshot stays readable, but no
    // more than 1568 px on its long side: Claude scales anything larger down, and a bigger PNG can pass its 5 MB limit.
    const scale = line.image ? Math.min(2, 1568 / Math.max(line.box.w, line.box.h)) : 1;
    const { url: image } = await editor.toImageDataUrl(line.image ? [line.image] : line.strokes,
      { format: 'png', background: true, padding: 16, scale });
    const r = await api.recognize({ strokes: lineStrokes(line), image, context });
    // The line may have changed while we waited; only store if it still exists.
    if (getLines(editor).some((l) => l.key === line.key)) {
      setReading(editor, line.key, { latex: r.latex, confidence: r.confidence, source: r.source, confirmed: false });
    }
    setStatus(line.key, null);
  } catch (e) {
    setStatus(line.key, (e as Error).message || 'failed');
  }
}

/**
 * Read the selected lines that need it, all at once, when the student asks for a check: nothing is read while they
 * write (DECISIONS #39). A failed line keeps its reason in readingStatus and is retried on the next check.
 * shortcut: in parallel, so a line's context is only the readings already stored above it, not the selection's
 * other lines. Upgrade path: read top to bottom if context turns out to matter more than the wait.
 */
export async function readLines(editor: Editor, sel: Line[]) {
  const lines = getLines(editor);
  const readings = getReadings(editor);
  // Drop readings of lines that no longer exist (their strokes changed or were erased).
  const live = new Set(lines.map((l) => l.key));
  if (Object.keys(readings).some((k) => !live.has(k))) {
    writeReadings(editor, Object.fromEntries(Object.entries(readings).filter(([k]) => live.has(k))));
  }
  const busy = readingStatus.get();
  await Promise.all(sel.filter((l) => busy[l.key] !== 'reading' && needsReading(readings[l.key]))
    .map((l) => readLine(editor, l, lines)));
}
