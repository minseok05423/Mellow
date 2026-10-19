// Page structure (SPEC §4, DECISIONS #21): each line's role and segment, labelled by a cheap model and kept
// in the notebook's document meta next to the readings. An action finds its context by lookup: the nearest
// problem above the selection, the work in between, and definitions earlier on the page.
// Pages are labelled when the student pauses (startLabelling) and again, if anything changed, when an action is
// pressed. An action looks back over its own page and the one before.

import { Editor, TLShapeId } from 'tldraw';
import { api, type Kind, type Role } from './api';
import { Line, getLines } from './lines';
import { getPages } from './pages';
import { getReadings, readingStatus } from './readings';

export interface PageStructure {
  sig: string; // the page's lines and readings when labelled; any change means relabel
  roles: Record<string, Role>; // line key -> role
  segment: Record<string, number>; // line key -> segment index
  segments: string[]; // one description per segment
}

type All = Record<string, PageStructure>;
const getAll = (editor: Editor) => (editor.getDocumentSettings().meta.structure ?? {}) as unknown as All;

/** The page's labels, from the notebook if still current, else from the labeller. Null if it can't label. */
export async function ensureStructure(editor: Editor, pageId: TLShapeId): Promise<PageStructure | null> {
  const readings = getReadings(editor);
  const lines = getLines(editor).filter((l) => l.pageId === pageId && readings[l.key]?.latex);
  if (!lines.length) return null;
  const texts = lines.map((l) => readings[l.key].latex);
  const sig = lines.map((l, i) => `${l.key}:${texts[i]}`).join('\n');
  const cached = getAll(editor)[pageId];
  if (cached?.sig === sig) return cached;
  try {
    const s = await api.structure(texts);
    const ps: PageStructure = { sig, roles: {}, segment: {}, segments: s.segments };
    lines.forEach((l, i) => { ps.roles[l.key] = s.lines[i].role; ps.segment[l.key] = s.lines[i].segment; });
    // Like readings: the board's labels, not the student's edits, so they stay out of undo history.
    editor.run(() => {
      const meta = editor.getDocumentSettings().meta;
      editor.updateDocumentSettings({ meta: { ...meta, structure: { ...getAll(editor), [pageId]: ps } as never } });
    }, { history: 'ignore' });
    return ps;
  } catch {
    return null; // no labelling model or it failed: the student sets the kind and problem in the confirm box
  }
}

/** A group of lines as one text, the way actions send it (so the background solve and the action match). */
export function joinReadings(editor: Editor, lines: Line[]): string {
  const r = getReadings(editor);
  return lines.map((l) => r[l.key]?.latex.trim() ?? '').filter(Boolean).join('\n');
}

const presolved = new Set<string>(); // problems already sent this session
const failed = new Map<string, string>(); // page id -> the page text the labeller failed on (not retried on pause)

/**
 * Label each page once its lines are read and the student pauses, and start solving any problem found,
 * so the first Hint / Answer / Explain on it is fast (DECISIONS #22). Returns a disposer.
 */
export function startLabelling(editor: Editor, delayMs = 2000): () => void {
  let timer: ReturnType<typeof setTimeout> | undefined;
  let running = false;
  const pass = async () => {
    if (running) { timer = setTimeout(pass, delayMs); return; } // one pass at a time; try again later
    running = true;
    try { await labelPages(); } finally { running = false; }
  };
  const labelPages = async () => {
    const lines = getLines(editor);
    const readings = getReadings(editor);
    const busy = readingStatus.get();
    for (const page of getPages(editor)) {
      const mine = lines.filter((l) => l.pageId === page.id);
      if (!mine.length || mine.some((l) => !readings[l.key] || busy[l.key])) continue; // still reading
      const text = mine.map((l) => readings[l.key].latex).join('\n');
      if (failed.get(page.id) === text) continue; // failed on this exact page (e.g. daily cap); wait for an edit
      const s = await ensureStructure(editor, page.id);
      if (!s) { failed.set(page.id, text); continue; }
      failed.delete(page.id);
      // Each run of consecutive problem lines is one problem.
      let block: Line[] = [];
      for (const l of [...mine, null]) {
        if (l && s.roles[l.key] === 'problem') { block.push(l); continue; }
        const problem = joinReadings(editor, block);
        block = [];
        if (problem && !presolved.has(problem)) {
          presolved.add(problem);
          api.presolve(problem).catch(() => presolved.delete(problem));
        }
      }
    }
  };
  // Readings land in the document record and strokes in shapes: any document change restarts the wait.
  // Our own label writes trigger one more pass, which finds everything current and does nothing.
  const stop = editor.store.listen(() => {
    clearTimeout(timer);
    timer = setTimeout(pass, delayMs);
  }, { scope: 'document' });
  return () => { stop(); clearTimeout(timer); };
}

export interface ActionContext {
  kind: Kind;
  problem: Line[]; // the nearest problem above the selection (its lines), if any
  work: Line[]; // lines between that problem and the selection (or just above, without a problem)
  definitions: Line[]; // definitions written above the selection
  description: string | null; // the selection's segment, e.g. "Problem: prove √2 is irrational"
}

/** What the selection is, and the lines an action needs around it. `structures` are the labels of the
 * selection's page first, then earlier pages, so a proof continued onto a new page still finds its problem. */
export function actionContext(editor: Editor, structures: (PageStructure | null)[], sel: Line[]): ActionContext {
  const roles: Record<string, Role> = Object.assign({}, ...structures.map((s) => s?.roles ?? {}));
  const role = (l: Line) => roles[l.key];
  const selRoles = sel.map(role);
  const kind: Kind = selRoles.includes('problem') ? 'problem'
    : selRoles.every((r) => r === 'concept' || r === 'definition') ? 'concept' : 'steps';
  const all = getLines(editor); // every page, top to bottom
  const above = all.slice(0, Math.max(0, all.findIndex((l) => l.key === sel[0].key)));
  let end = above.length - 1;
  while (end >= 0 && role(above[end]) !== 'problem') end--;
  let start = end;
  while (start > 0 && role(above[start - 1]) === 'problem') start--;
  const s = structures[0];
  const seg = s?.segment[sel[0].key];
  return {
    kind,
    problem: kind === 'steps' && end >= 0 ? above.slice(start, end + 1) : [],
    work: kind === 'steps' ? above.slice(end + 1).slice(-30) : [],
    definitions: above.filter((l) => role(l) === 'definition').slice(-10),
    description: s && seg !== undefined ? s.segments[seg] ?? null : null,
  };
}
