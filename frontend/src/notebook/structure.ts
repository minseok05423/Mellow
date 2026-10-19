// Page structure (SPEC §4, DECISIONS #21): each line's role and segment, labelled by a cheap model and kept
// in the notebook's document meta next to the readings. An action finds its context by lookup: the nearest
// problem above the selection, the work in between, and definitions earlier on the page.
// shortcut: labelled per page, when an action is pressed (then cached until the page's readings change), so a
// proof continued on the next page doesn't find its problem. Upgrade path: label on pause, across pages.

import { Editor, TLShapeId } from 'tldraw';
import { api, type Kind, type Role } from './api';
import { Line, getLines } from './lines';
import { getReadings } from './readings';

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

export interface ActionContext {
  kind: Kind;
  problem: Line[]; // the nearest problem above the selection (its lines), if any
  work: Line[]; // lines between that problem and the selection (or just above, without a problem)
  definitions: Line[]; // definitions written above the selection on the page
  description: string | null; // the selection's segment, e.g. "Problem: prove √2 is irrational"
}

/** What the selection is, and the lines an action needs around it. */
export function actionContext(editor: Editor, s: PageStructure | null, sel: Line[]): ActionContext {
  const role = (l: Line) => s?.roles[l.key];
  const roles = sel.map(role);
  const kind: Kind = roles.includes('problem') ? 'problem'
    : roles.every((r) => r === 'concept' || r === 'definition') ? 'concept' : 'steps';
  const above = getLines(editor).filter((l) => l.pageId === sel[0].pageId && l.box.maxY <= sel[0].box.minY + 1);
  let end = above.length - 1;
  while (end >= 0 && role(above[end]) !== 'problem') end--;
  let start = end;
  while (start > 0 && role(above[start - 1]) === 'problem') start--;
  const seg = s?.segment[sel[0].key];
  return {
    kind,
    problem: kind === 'steps' && end >= 0 ? above.slice(start, end + 1) : [],
    work: kind === 'steps' ? above.slice(end + 1).slice(-30) : [],
    definitions: above.filter((l) => role(l) === 'definition').slice(-10),
    description: s && seg !== undefined ? s.segments[seg] ?? null : null,
  };
}
