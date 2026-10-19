// Group handwritten strokes into lines of writing. A line is the unit that gets read, confirmed and checked.
// shortcut: geometric grouping only (vertical overlap + small gaps). Tall fractions or a line squeezed
// between two others can be split or merged wrongly. Upgrade path: use stroke timing from meta.ink, or
// let the recognizer segment a page region.

import { Box, Editor, TLDrawShape, TLShapeId } from 'tldraw';
import { getInk } from './ink';
import { getPages } from './pages';

export interface Line {
  key: string; // changes whenever the line's strokes change, so a stale reading is never reused
  pageId: TLShapeId;
  strokes: TLDrawShape[];
  box: Box;
}

const JOIN_OVERLAP = 0.3; // a stroke joins a line if this fraction of its height overlaps the line
const MERGE_GAP = 6; // lines closer than this (page units) that also overlap horizontally merge

function hash(s: string): string {
  let h = 5381;
  for (let i = 0; i < s.length; i++) h = ((h << 5) + h + s.charCodeAt(i)) | 0;
  return (h >>> 0).toString(36);
}

export function getLines(editor: Editor): Line[] {
  const pages = getPages(editor);
  const strokes = editor
    .getCurrentPageShapes()
    .filter((s): s is TLDrawShape => s.type === 'draw' && !!getInk(s as TLDrawShape));
  const lines: Line[] = [];

  for (const page of pages) {
    const pageBox = new Box(page.x, page.y, page.props.w, page.props.h);
    const mine = strokes
      .map((s) => ({ s, b: editor.getShapePageBounds(s)! }))
      .filter(({ b }) => pageBox.containsPoint(b.center))
      .sort((a, b) => a.b.minY - b.b.minY);

    const groups: { strokes: TLDrawShape[]; box: Box }[] = [];
    for (const { s, b } of mine) {
      const g = groups.find((g) => {
        const overlap = Math.min(g.box.maxY, b.maxY) - Math.max(g.box.minY, b.minY);
        return overlap >= JOIN_OVERLAP * Math.max(1, Math.min(b.h, g.box.h));
      });
      if (g) { g.strokes.push(s); g.box = Box.Common([g.box, b]); }
      else groups.push({ strokes: [s], box: b.clone() });
    }
    // Merge stacked pieces of one expression (e.g. a fraction's numerator, bar and denominator).
    for (let i = 0; i < groups.length; i++) {
      for (let j = i + 1; j < groups.length; j++) {
        const a = groups[i].box, b = groups[j].box;
        const gap = Math.max(a.minY, b.minY) - Math.min(a.maxY, b.maxY);
        const xOverlap = Math.min(a.maxX, b.maxX) - Math.max(a.minX, b.minX);
        if (gap < MERGE_GAP && xOverlap > 0) {
          groups[i] = { strokes: [...groups[i].strokes, ...groups[j].strokes], box: Box.Common([a, b]) };
          groups.splice(j, 1);
          j = i;
        }
      }
    }
    for (const g of groups.sort((a, b) => a.box.minY - b.box.minY)) {
      const sig = g.strokes
        .map((s) => `${s.id}@${Math.round(s.x)},${Math.round(s.y)}`)
        .sort()
        .join('|');
      lines.push({ key: hash(sig), pageId: page.id, strokes: g.strokes, box: g.box });
    }
  }
  return lines;
}

/** Lines holding any of the given strokes (e.g. the current selection), top to bottom. */
export function linesForStrokes(lines: Line[], ids: Set<string>): Line[] {
  return lines.filter((l) => l.strokes.some((s) => ids.has(s.id)));
}

/** Lines above `line` on the same page, nearest last (the order the checker wants as context). */
export function linesAbove(lines: Line[], line: Line, max = 4): Line[] {
  return lines.filter((l) => l.pageId === line.pageId && l.box.maxY <= line.box.minY + 1).slice(-max);
}

/** Strokes of a line in page units relative to its top-left, as the backend expects. */
export function lineStrokes(line: Line) {
  return [...line.strokes]
    .sort((a, b) => getInk(a)!.t0 - getInk(b)!.t0)
    .map((s) => {
      const ink = getInk(s)!;
      return {
        t0: ink.t0,
        pts: ink.pts.map(([dx, dy, p, dt]) => [
          Math.round((s.x + dx - line.box.x) * 100) / 100, Math.round((s.y + dy - line.box.y) * 100) / 100, p, dt,
        ]),
      };
    });
}
