// Paper pages. tldraw is an infinite canvas, so each notebook page is a locked shape at the bottom
// of the z-order, stacked vertically (the same approach as tldraw's PDF-editor example).
// Ink and other shapes sit on top. Locked shapes are ignored by select, eraser and our lasso.

import {
  Box,
  Editor,
  HTMLContainer,
  Rectangle2d,
  ShapeUtil,
  T,
  TLShape,
  createShapeId,
  getIndicesBetween,
  react,
  sortByIndex,
} from 'tldraw';

export type Paper = 'blank' | 'lined' | 'grid';
export const PAPERS: Paper[] = ['blank', 'lined', 'grid'];

const PAGE_TYPE = 'mellow-page';

declare module '@tldraw/tlschema' {
  interface TLGlobalShapePropsMap {
    [PAGE_TYPE]: { w: number; h: number; paper: Paper };
  }
}

export type PageShape = TLShape<typeof PAGE_TYPE>;

// A4 portrait at 96 dpi, so exports and a future PDF import line up with real paper.
export const PAGE_W = 794;
export const PAGE_H = 1123;
const GAP = 40;
const LINE = 32; // ruled-line and grid spacing in page units

export class PageShapeUtil extends ShapeUtil<PageShape> {
  static override type = PAGE_TYPE;
  static override props = {
    w: T.number,
    h: T.number,
    paper: T.literalEnum('blank', 'lined', 'grid'),
  };

  getDefaultProps(): PageShape['props'] {
    return { w: PAGE_W, h: PAGE_H, paper: 'lined' };
  }

  getGeometry(shape: PageShape) {
    return new Rectangle2d({ width: shape.props.w, height: shape.props.h, isFilled: true });
  }

  override canBind() { return false; }
  override canResize() { return false; }
  override canSnap() { return false; }
  override hideResizeHandles() { return true; }
  override hideRotateHandle() { return true; }

  component(shape: PageShape) {
    return (
      <HTMLContainer className={`mellow-page paper-${shape.props.paper}`}
        style={{ width: shape.props.w, height: shape.props.h, '--line': `${LINE}px` } as React.CSSProperties} />
    );
  }

  getIndicatorPath(shape: PageShape) {
    const p = new Path2D();
    p.rect(0, 0, shape.props.w, shape.props.h);
    return p;
  }
}

export function getPages(editor: Editor): PageShape[] {
  return editor
    .getCurrentPageShapes()
    .filter((s): s is PageShape => s.type === PAGE_TYPE)
    .sort((a, b) => a.y - b.y);
}

export function addPage(editor: Editor, paper?: Paper) {
  const pages = getPages(editor);
  const last = pages.at(-1);
  editor.createShape<PageShape>({
    id: createShapeId(),
    type: PAGE_TYPE,
    x: 0,
    y: last ? last.y + last.props.h + GAP : 0,
    isLocked: true,
    props: { w: PAGE_W, h: PAGE_H, paper: paper ?? last?.props.paper ?? 'lined' },
  });
}

/** The page filling most of the viewport, i.e. the one the student is looking at. */
export function getPageInView(editor: Editor): PageShape | undefined {
  const vp = editor.getViewportPageBounds();
  let best: PageShape | undefined;
  let bestOverlap = 0;
  // Pages share one column, so vertical overlap is enough.
  for (const p of getPages(editor)) {
    const overlap = Math.min(vp.maxY, p.y + p.props.h) - Math.max(vp.y, p.y);
    if (overlap > bestOverlap) { best = p; bestOverlap = overlap; }
  }
  return best;
}

export function setPaper(editor: Editor, page: PageShape, paper: Paper) {
  // Locked shapes can only be changed with ignoreShapeLock.
  editor.run(() => editor.updateShape<PageShape>({ id: page.id, type: PAGE_TYPE, props: { paper } }), {
    ignoreShapeLock: true,
  });
}

/** Keeps pages locked and underneath everything, and the camera on the page column. Returns a disposer. */
export function installPages(editor: Editor): () => void {
  if (getPages(editor).length === 0) addPage(editor, 'lined');

  const stopLock = editor.sideEffects.registerBeforeChangeHandler('shape', (prev, next) =>
    next.type === PAGE_TYPE && !next.isLocked ? { ...prev, isLocked: true } : next,
  );

  function keepPagesAtBottom() {
    const pages = getPages(editor).sort(sortByIndex);
    const siblings = editor.getSortedChildIdsForParent(editor.getCurrentPageId());
    if (pages.every((p, i) => siblings[i] === p.id)) return;
    const firstOther = siblings.find((id) => editor.getShape(id)?.type !== PAGE_TYPE);
    const below = firstOther ? editor.getShape(firstOther)!.index : undefined;
    const indexes = getIndicesBetween(undefined, below, pages.length);
    editor.run(
      () => editor.updateShapes(pages.map((p, i) => ({ id: p.id, type: PAGE_TYPE, index: indexes[i] }))),
      { ignoreShapeLock: true },
    );
  }
  keepPagesAtBottom();
  const stopCreate = editor.sideEffects.registerAfterCreateHandler('shape', keepPagesAtBottom);
  const stopChange = editor.sideEffects.registerAfterChangeHandler('shape', keepPagesAtBottom);

  // Re-run whenever the page list or the viewport width class changes.
  const stopCamera = react('page camera bounds', () => {
    const pages = getPages(editor);
    if (pages.length === 0) return;
    const bounds = pages.reduce(
      (acc, p) => acc.union(new Box(p.x, p.y, p.props.w, p.props.h)),
      new Box(pages[0].x, pages[0].y, pages[0].props.w, pages[0].props.h),
    );
    const narrow = editor.getViewportScreenBounds().width < 840;
    editor.setCameraOptions({
      constraints: {
        bounds,
        padding: { x: narrow ? 12 : 64, y: 48 },
        origin: { x: 0.5, y: 0 },
        initialZoom: 'fit-x-100',
        baseZoom: 'default',
        behavior: 'contain',
      },
    });
  });
  editor.setCamera(editor.getCamera(), { reset: true });

  return () => { stopLock(); stopCreate(); stopChange(); stopCamera(); };
}
