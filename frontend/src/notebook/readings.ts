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
export const showReadings = atom('show readings', true);

/** Same rule as the backend's verify.looks_like_words: MyScript's math mode reads words as letters ("l e t f"). */
export const looksLikeWords = (latex: string) =>
  /[a-zA-Z]{3,}/.test(latex.replace(/\\[a-zA-Z]+|[\s{}]/g, ''));

// Readings that came back letter by letter before the backend sent those lines to the LLM reader.
// Each is re-read once per session (the retry can return the same text if no LLM is configured).
const stale = (r: LineReading) => r.source === 'myscript' && looksLikeWords(r.latex);
const reread = new Set<string>();

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
    const { url: image } = await editor.toImageDataUrl(line.strokes, { format: 'png', background: true, padding: 16 });
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
 * Live reading: shortly after the student stops writing, read every line that has no reading yet.
 * Returns a disposer.
 */
export function startLiveReading(editor: Editor, delayMs = 900): () => void {
  let timer: ReturnType<typeof setTimeout> | undefined;

  const pass = () => {
    const lines = getLines(editor);
    const readings = getReadings(editor);
    const status = readingStatus.get();
    // Drop readings of lines that no longer exist (their strokes changed or were erased).
    const live = new Set(lines.map((l) => l.key));
    if (Object.keys(readings).some((k) => !live.has(k))) {
      writeReadings(editor, Object.fromEntries(Object.entries(readings).filter(([k]) => live.has(k))));
    }
    for (const line of lines) {
      const r = readings[line.key];
      if (r && stale(r) && !reread.has(line.key)) reread.add(line.key);
      else if (r || status[line.key]) continue;
      readLine(editor, line, lines);
    }
  };

  const stop = editor.store.listen(
    (entry) => {
      // Only shape changes matter (not our own readings in the document record).
      const touchesShapes = [entry.changes.added, entry.changes.updated, entry.changes.removed]
        .some((m) => Object.keys(m).some((id) => id.startsWith('shape:')));
      if (!touchesShapes) return;
      clearTimeout(timer);
      timer = setTimeout(pass, delayMs);
    },
    { scope: 'document', source: 'user' },
  );
  timer = setTimeout(pass, delayMs); // read anything left unread from last time
  return () => { stop(); clearTimeout(timer); };
}
