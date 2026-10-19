// Lasso select, adapted from tldraw's lasso-select-tool example (v5.5.2).
// Difference: a shape is selected when *most* of it is inside the loop, because handwritten
// strokes often poke out of a hand-drawn lasso. Locked shapes (the paper pages) are never selected.

import {
  OverlayUtil,
  StateNode,
  TLOverlay,
  TLShape,
  VecModel,
  atom,
  getStrokePoints,
  getSvgPathFromStrokePoints,
  pointInPolygon,
} from 'tldraw';

const INSIDE_FRACTION = 0.75;

export class LassoSelectTool extends StateNode {
  static override id = 'lasso-select';
  static override initial = 'idle';
  static override children() {
    return [IdleState, LassoingState];
  }
}

class IdleState extends StateNode {
  static override id = 'idle';
  override onPointerDown() {
    this.editor.selectNone();
    this.parent.transition('lassoing');
  }
}

export class LassoingState extends StateNode {
  static override id = 'lassoing';
  // An atom, so the overlay re-renders as points are added.
  points = atom<VecModel[]>('lasso points', []);

  override onEnter() {
    this.points.set([]);
    this.editor.markHistoryStoppingPoint('lasso start');
  }

  override onPointerMove() {
    const { x, y } = this.editor.inputs.getCurrentPagePoint();
    this.points.set([...this.points.get(), { x, y }]);
  }

  override onPointerUp() { this.complete(); }
  override onComplete() { this.complete(); }

  private isMostlyInside(shape: TLShape, lasso: VecModel[]) {
    const { editor } = this;
    if (editor.isShapeOrAncestorLocked(shape)) return false;
    const vertices = editor.getShapePageTransform(shape).applyToPoints(editor.getShapeGeometry(shape).vertices);
    if (vertices.length === 0) return false;
    const inside = vertices.filter((v) => pointInPolygon(v, lasso)).length;
    return inside / vertices.length >= INSIDE_FRACTION;
  }

  private complete() {
    const { editor } = this;
    const lasso = this.points.get();
    const picked = lasso.length < 3
      ? []
      : editor.getCurrentPageRenderingShapesSorted().filter((s) => this.isMostlyInside(s, lasso));
    editor.setSelectedShapes(picked);
    // Hand over to the select tool so the selection can be dragged/resized right away.
    editor.setCurrentTool('select');
  }
}

interface LassoOverlay extends TLOverlay {
  props: { svgPath: string };
}

export class LassoOverlayUtil extends OverlayUtil<LassoOverlay> {
  static override type = 'lasso';

  override isActive() {
    return this.editor.isIn('lasso-select.lassoing');
  }

  override getOverlays(): LassoOverlay[] {
    const state = this.editor.getStateDescendant<LassoingState>('lasso-select.lassoing');
    const points = state?.points.get() ?? [];
    if (points.length === 0) return [];
    const svgPath = getSvgPathFromStrokePoints(getStrokePoints(points), true);
    return svgPath ? [{ id: 'lasso', type: 'lasso', props: { svgPath } }] : [];
  }

  override render(ctx: CanvasRenderingContext2D, overlays: LassoOverlay[]) {
    const overlay = overlays[0];
    if (!overlay) return;
    const colors = this.editor.getCurrentTheme().colors[this.editor.getColorMode()];
    const path = new Path2D(overlay.props.svgPath);
    ctx.globalAlpha = 0.5;
    ctx.fillStyle = colors.selectionFill;
    ctx.fill(path);
    ctx.globalAlpha = 1;
    ctx.setLineDash([6 / this.editor.getZoomLevel(), 4 / this.editor.getZoomLevel()]);
    ctx.lineWidth = 2 / this.editor.getZoomLevel();
    ctx.strokeStyle = colors.selectionStroke;
    ctx.stroke(path);
    ctx.setLineDash([]);
  }
}
