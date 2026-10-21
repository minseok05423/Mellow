// Feedback cards: the board's reply to "Check my proof", drawn on the page as an ordinary shape the student can move
// or delete (SPEC §4 board objects). Placement is deterministic code, never the LLM.

import katex from 'katex';
import { Box, Editor, HTMLContainer, Rectangle2d, ShapeUtil, T, TLShape, createShapeId } from 'tldraw';
import { getPages } from './pages';

const TYPE = 'mellow-feedback';
// Cards from removed features (SymPy checks, hints, answers, explanations) keep their verdicts, so old notebooks load.
const VERDICTS = ['verified', 'refuted', 'unverified', 'noted', 'answer', 'ai_check', 'hint', 'explain',
  'lean_complete', 'lean_issues', 'graph_complete', 'graph_issues'] as const;
export type Verdict = (typeof VERDICTS)[number];

declare module '@tldraw/tlschema' {
  interface TLGlobalShapePropsMap {
    [TYPE]: { w: number; h: number; verdict: Verdict; message: string; line: string };
  }
}
export type FeedbackShape = TLShape<typeof TYPE>;

const ICON: Record<Verdict, string> = { verified: '✓', refuted: '✗', unverified: '?', noted: '•', ai_check: '◇', hint: '?',
  answer: '✦', explain: '❋', lean_complete: '✓', lean_issues: '?',
  graph_complete: '✓', graph_issues: '?' };
const TITLE: Record<Verdict, string> = { verified: 'Follows', refuted: 'Counterexample', unverified: 'Question', noted: 'Noted',
  ai_check: 'Check · AI, not verified', hint: 'Hint', answer: 'Answer · AI', explain: 'Explanation · AI',
  lean_complete: 'Checked by Lean', lean_issues: 'Lean check', graph_complete: 'Every step checks out',
  graph_issues: 'Step by step' };

/** Renders text with $...$ math segments via KaTeX. Models also write \(...\), \[...\] and $$...$$
 * despite the prompt, so those become $...$ first. */
export function MathText({ text, className }: { text: string; className?: string }) {
  const html = text
    .replace(/\$\$|\\\(|\\\)|\\\[|\\\]/g, '$')
    .split('$')
    .map((part, i) =>
      i % 2 ? katex.renderToString(part, { throwOnError: false }) : part.replace(/[&<>]/g, (c) => `&#${c.charCodeAt(0)};`))
    .join('');
  return <span className={className} dangerouslySetInnerHTML={{ __html: html }} />;
}

/** A reading as MathText: `\text{...}` becomes plain text so a long statement wraps instead of clipping.
 * split() with a capture group alternates math, text, math, ...; blank math stays out (no empty `$$`). */
export const lineText = (latex: string) => latex
  .split(/\\text\s*\{([^}]*)\}/)
  .map((part, i) => (i % 2 || !part.trim() ? part : `$${part}$`))
  .join('');

const cardText = (shape: FeedbackShape) =>
  `${TITLE[shape.props.verdict]}: ${shape.props.line}\n${shape.props.message}`;

export class FeedbackShapeUtil extends ShapeUtil<FeedbackShape> {
  static override type = TYPE;
  static override props = {
    w: T.number,
    h: T.number,
    // New values only ever get added here, so notebooks saved earlier still load.
    verdict: T.literalEnum(...VERDICTS),
    message: T.string,
    line: T.string,
  };

  getDefaultProps(): FeedbackShape['props'] {
    return { w: 280, h: 96, verdict: 'unverified', message: '', line: '' };
  }

  getGeometry(shape: FeedbackShape) {
    return new Rectangle2d({ width: shape.props.w, height: shape.props.h, isFilled: true });
  }

  // Plain-text clipboard when the card itself is copied (tldraw otherwise puts nothing readable there).
  override getText(shape: FeedbackShape) {
    return cardText(shape);
  }

  override canResize() { return false; }
  override hideRotateHandle() { return true; }

  component(shape: FeedbackShape) {
    const { verdict, message, line, w, h } = shape.props;
    // Pressing on the text selects text (to copy) instead of grabbing the card; drag the card by its title.
    const selectText = (e: React.PointerEvent) => { e.stopPropagation(); this.editor.selectNone(); };
    return (
      <HTMLContainer className={`feedback feedback-${verdict}`} style={{ width: w, height: h }}>
        <div className="feedback-head">
          <span><span className="feedback-icon">{ICON[verdict]}</span> {TITLE[verdict]}</span>
          <button className="feedback-copy" title="Copy as text (math as LaTeX)"
            onPointerDown={(e) => e.stopPropagation()}
            onClick={() => navigator.clipboard.writeText(cardText(shape))}>Copy</button>
        </div>
        <div className="feedback-body" onPointerDown={selectText}>
          <MathText className="feedback-line" text={lineText(line)} />
          <MathText className="feedback-msg" text={message} />
        </div>
      </HTMLContainer>
    );
  }

  getIndicatorPath(shape: FeedbackShape) {
    const p = new Path2D();
    p.roundRect(0, 0, shape.props.w, shape.props.h, 8);
    return p;
  }
}

/** Free spot for a w×h card next to `near`: to the right on the same page, else below, stepping down. */
export function placeNear(editor: Editor, near: Box, w: number, h: number): { x: number; y: number } {
  const page = getPages(editor).find((p) => near.center.y >= p.y && near.center.y <= p.y + p.props.h);
  const pageRight = page ? page.x + page.props.w : near.maxX + w + 24;
  const occupied = editor
    .getCurrentPageShapes()
    .filter((s) => s.type !== 'mellow-page')
    .map((s) => editor.getShapePageBounds(s)!)
    .filter(Boolean);
  const free = (b: Box) => !occupied.some((o) => Box.Collides(o, b));
  const starts = [
    { x: near.maxX + 24, y: near.minY },
    { x: Math.max(page?.x ?? 0, near.minX), y: near.maxY + 12 },
  ].filter((p) => p.x + w <= pageRight - 8);
  // Prefer a spot that stays on the page (a tall card would otherwise hang off its bottom):
  // stepping down first, then up; failing that, anywhere free below.
  const pageBottom = page ? page.y + page.props.h - 8 : Infinity;
  const down = Array.from({ length: 38 }, (_, i) => i * 16);
  const up = Array.from({ length: 25 }, (_, i) => -(i + 1) * 16);
  // Every downward spot (right of the selection, then below it) before any upward one, as before this change.
  for (const [onPage, offsets] of [[true, down], [true, up], [false, down]] as const) {
    for (const start of starts) {
      for (const dy of offsets) {
        const b = new Box(start.x, start.y + dy, w, h);
        if (free(b) && (!onPage || (b.maxY <= pageBottom && b.minY >= (page?.y ?? -Infinity) + 8))) return { x: b.x, y: b.y };
      }
    }
  }
  return { x: near.minX, y: near.maxY + 12 };
}

export function addFeedback(editor: Editor, near: Box, line: string, verdict: Verdict, message: string) {
  // Height estimate from text length, per line of the message (the card can't measure KaTeX before it exists).
  const long = message.length > 600;
  const w = long ? 380 : 300;
  const perRow = long ? 50 : 38;
  const rows = message.split('\n').reduce((n, part) => n + Math.max(1, Math.ceil(part.length / perRow)), 0);
  const h = 44 + Math.ceil(line.length / 45) * 20 + rows * 20;
  const { x, y } = placeNear(editor, near, w, h);
  editor.createShape<FeedbackShape>({
    id: createShapeId(),
    type: TYPE,
    x,
    y,
    props: { w, h, verdict, message, line },
  });
}

const SCENE = 'mellow-scene';
declare module '@tldraw/tlschema' {
  interface TLGlobalShapePropsMap {
    [SCENE]: { w: number; h: number; scene: string; line: string; message: string };
  }
}
type SceneShape = TLShape<typeof SCENE>;

/** Animated explanations were removed (DECISIONS #27); old notebooks still hold them, and tldraw won't open a document
 * with a shape type it doesn't know. They show as a plain card with their text. */
export class LegacySceneShapeUtil extends ShapeUtil<SceneShape> {
  static override type = SCENE;
  static override props = { w: T.number, h: T.number, scene: T.string, line: T.string, message: T.string };

  getDefaultProps(): SceneShape['props'] {
    return { w: 300, h: 96, scene: '', line: '', message: '' };
  }

  getGeometry(shape: SceneShape) {
    return new Rectangle2d({ width: shape.props.w, height: shape.props.h, isFilled: true });
  }

  override canResize() { return false; }
  override hideRotateHandle() { return true; }

  component(shape: SceneShape) {
    return (
      <HTMLContainer className="feedback feedback-explain" style={{ width: shape.props.w, height: shape.props.h }}>
        <div className="feedback-head"><span>Explanation (no longer animated)</span></div>
        <div className="feedback-body" style={{ overflow: 'auto' }}>
          <MathText className="feedback-line" text={lineText(shape.props.line)} />
          <MathText className="feedback-msg" text={shape.props.message} />
        </div>
      </HTMLContainer>
    );
  }

  getIndicatorPath(shape: SceneShape) {
    const p = new Path2D();
    p.roundRect(0, 0, shape.props.w, shape.props.h, 8);
    return p;
  }
}
