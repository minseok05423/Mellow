// Readings under each line, and the "Check" flow: select a line -> confirm chip -> SymPy -> feedback card.

import katex from 'katex';
import { useState } from 'react';
import { EditorPortal, useEditor, useValue } from 'tldraw';
import { api, type CheckResult } from './api';
import { addFeedback } from './feedback';
import { Box } from 'tldraw';
import { Line, getLines, linesAbove, linesForStrokes } from './lines';
import { LineReading, getReadings, looksLikeWords, readingStatus, setReading, showReadings } from './readings';
import { selectedInkShapes } from './samples';

function Tex({ latex, className }: { latex: string; className?: string }) {
  return (
    <span className={className}
      dangerouslySetInnerHTML={{ __html: katex.renderToString(latex, { throwOnError: false }) }} />
  );
}

/** Faint readings under each line, in page space (mounted in tldraw's OnTheCanvas layer). */
export function ReadingsOverlay() {
  const editor = useEditor();
  const show = useValue(showReadings);
  const lines = useValue('lines', () => getLines(editor), [editor]);
  const readings = useValue('readings', () => getReadings(editor), [editor]);
  const status = useValue(readingStatus);
  // With the select tool, readings can be highlighted and copied; with the pen they never get in the way.
  const selectable = useValue('selectable readings', () => editor.getCurrentToolId() === 'select', [editor]);
  if (!show) return null;
  return (
    <>
      {lines.map((l) => {
        const r = readings[l.key];
        const st = status[l.key];
        const unsure = r && !r.confirmed && r.confidence !== null && r.confidence < 0.8;
        let body;
        if (st === 'reading') body = <span className="reading-busy">reading…</span>;
        else if (st) body = <span className="reading-err" title={st}>couldn't read</span>;
        else if (r?.latex) body = <Tex latex={r.latex} />;
        else return null;
        return (
          <div key={l.key}
            className={`reading ${r?.confirmed ? 'reading-confirmed' : ''} ${unsure ? 'reading-unsure' : ''} ${selectable ? 'reading-selectable' : ''}`}
            onPointerDown={selectable ? (e) => { e.stopPropagation(); editor.selectNone(); } : undefined}
            style={{ transform: `translate(${l.box.minX}px, ${l.box.maxY + 2}px)` }}>
            {body}{unsure && <span className="reading-q"> ?</span>}
          </div>
        );
      })}
    </>
  );
}

/** One editable reading in the confirm chip. */
function ReadingRow({ label, value, onChange, startEditing }: {
  label: string; value: string; onChange: (v: string) => void; startEditing: boolean;
}) {
  const [editing, setEditing] = useState(startEditing);
  return (
    <div className="chip-row">
      <div className="chip-label">{label}</div>
      {editing ? (
        <div className="chip-edit">
          <input autoFocus value={value} placeholder="Type what you wrote, in LaTeX"
            onChange={(e) => onChange(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && value.trim() && setEditing(false)} />
          {value.trim() && <Tex className="chip-preview" latex={value} />}
        </div>
      ) : (
        <div className="chip-read">
          <Tex latex={value} />
          <button className="chip-pen" title="Fix the reading" onClick={() => setEditing(true)}>✎</button>
        </div>
      )}
    </div>
  );
}

interface ChipState { lines: Line[]; texts: string[]; above: Line | undefined; previous: string }

// Same rule as the backend's verify.is_prose: words other than joining words make a line a statement.
const CONNECTORS = new Set(['so', 'thus', 'hence', 'then', 'therefore', 'and', 'i.e.', 'ie', 'which', 'gives', 'implies']);
const isProse = (latex: string) => looksLikeWords(latex.replace(/\\text\s*\{[^}]*\}/g, ' '))
  || [...latex.matchAll(/\\text\s*\{([^}]*)\}/g)]
    .flatMap((m) => m[1].toLowerCase().replace(/,/g, ' ').split(/\s+/))
    .some((w) => w && !CONNECTORS.has(w.replace(/[.:;]+$/, '')));

const RANK: Record<CheckResult['verdict'], number> = { refuted: 3, unverified: 2, answer: 2, verified: 1, noted: 0 };

/** One answer for one Check: the most serious verdict, with every line's problem in the message. */
function combine(results: { n: number; r: CheckResult }[]): CheckResult {
  if (results.length === 1) return results[0].r;
  const worst = results.map((x) => x.r).reduce((a, r) => (RANK[r.verdict] > RANK[a.verdict] ? r : a));
  const problems = results.filter((x) => x.r.verdict !== 'verified');
  const message = problems.length
    ? problems.map((x) => `Line ${x.n}: ${x.r.message}`).join('\n')
    : 'Every step follows from the line above it.';
  return { ...worst, message };
}

/** The Check button next to selected lines, and the confirm chip (screen space, InFrontOfTheCanvas). */
export function CheckUI() {
  const editor = useEditor();
  const [chip, setChip] = useState<ChipState | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  // Show the button only when the selection is handwriting and nothing is being dragged.
  const target = useValue('check target', () => {
    if (!editor.isIn('select.idle')) return null;
    const ids = new Set<string>(selectedInkShapes(editor).map((s) => s.id));
    if (ids.size === 0) return null;
    const lines = linesForStrokes(getLines(editor), ids);
    const bounds = editor.getSelectionScreenBounds();
    return lines.length && bounds ? { lines, x: bounds.maxX, y: bounds.minY } : null;
  }, [editor]);

  function open(sel: Line[]) {
    const readings = getReadings(editor);
    const above = linesAbove(getLines(editor), sel[0], 1)[0];
    setError('');
    setChip({ lines: sel, texts: sel.map((l) => readings[l.key]?.latex ?? ''), above,
      previous: above ? readings[above.key]?.latex ?? '' : '' });
  }

  async function confirmAndCheck() {
    if (!chip) return;
    const { lines: sel, above, previous } = chip;
    const texts = chip.texts.map((t) => t.trim());
    // Confirm-before-judge: store the student's confirmed readings first; only those are ever sent.
    const keep = (k: string, latex: string) => {
      const old: LineReading | undefined = getReadings(editor)[k];
      setReading(editor, k, { latex, confidence: old?.confidence ?? null, source: old?.source ?? 'student', confirmed: true });
    };
    sel.forEach((l, i) => keep(l.key, texts[i]));
    if (above && previous.trim()) keep(above.key, previous.trim());
    const readings = getReadings(editor);
    const contextFor = (l: Line) => linesAbove(getLines(editor), l)
      .map((a) => readings[a.key])
      .filter((r) => r?.confirmed && r.latex)
      .map((r) => r.latex);
    // A statement written over several lines is one statement; math lines are steps, each checked on its own.
    // Either way the student gets one card for one Check.
    const prose = texts.some(isProse);
    const jobs = prose
      ? [{ n: 1, line: sel[0], latex: texts.filter(Boolean).join(' \\text{ } ') }]
      : sel.map((l, i) => ({ n: i + 1, line: l, latex: texts[i] }))
        // The first of several selected lines is the starting point unless something confirmed sits above it.
        .filter((j, i) => j.latex && (i > 0 || sel.length === 1 || contextFor(j.line).length > 0));
    setBusy(true);
    try {
      const results = [];
      for (const j of jobs) {
        results.push({ n: j.n, r: await api.check({ current: j.latex, context: contextFor(j.line), confirmed: true }) });
      }
      const shown = prose ? jobs[0].latex : texts.filter(Boolean).join(' \\quad ');
      if (results.length) addFeedback(editor, Box.Common(sel.map((l) => l.box)), shown, combine(results));
      setChip(null);
      editor.selectNone();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const status = useValue(readingStatus);
  return (
    <EditorPortal>
      {target && !chip && (
        <button className="check-btn" style={{ left: target.x + 8, top: target.y - 4 }}
          onPointerDown={(e) => e.stopPropagation()} onClick={() => open(target.lines)}>
          Check ✓
        </button>
      )}
      {chip && (
        <div className="chip" style={{ left: Math.max(8, (target?.x ?? 200) - 120), top: (target?.y ?? 80) + 32 }}
          onPointerDown={(e) => e.stopPropagation()}>
          {chip.lines.some((l) => status[l.key] === 'reading') && <div className="chip-note">Still reading…</div>}
          {chip.lines.map((l, i) => (
            <ReadingRow key={l.key} label={i ? 'Next line' : chip.lines.length > 1 ? 'I read your lines as' : 'I read your line as'}
              value={chip.texts[i]} startEditing={!chip.texts[i]}
              onChange={(v) => setChip({ ...chip, texts: chip.texts.map((t, k) => (k === i ? v : t)) })} />
          ))}
          {chip.above && (
            <ReadingRow label="Checking it against the line above" value={chip.previous}
              startEditing={!chip.previous} onChange={(v) => setChip({ ...chip, previous: v })} />
          )}
          {error && <div className="chip-error">{error}</div>}
          <div className="chip-actions">
            <button onClick={() => setChip(null)}>Cancel</button>
            <button className="primary" disabled={busy || !chip.texts.some((t) => t.trim())} onClick={confirmAndCheck}>
              {busy ? 'Checking…' : 'Looks right, check it'}
            </button>
          </div>
        </div>
      )}
    </EditorPortal>
  );
}
