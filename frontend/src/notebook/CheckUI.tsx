// Readings under each line, and "Check with Lean" (SPEC §4, DECISIONS #27): select lines -> confirm the readings and
// which line is the problem -> Lean states the problem, the student confirms it in words -> Lean checks the proof
// line by line -> one card on the page.

import katex from 'katex';
import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { Box, EditorPortal, useEditor, useValue } from 'tldraw';
import { api, type LineVerdict, type Statement } from './api';
import { MathText, addFeedback, lineText } from './feedback';
import { Line, getLines, linesForStrokes, selectedInkShapes } from './lines';
import { LineReading, getReadings, readingStatus, setReading, showReadings } from './readings';

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
        else if (r?.latex) body = l.image // an image's reading is usually long text: let it wrap
          ? <MathText text={lineText(r.latex.replace(/\n/g, ' \\text{ } '))} /> : <Tex latex={r.latex} />;
        else return null;
        return (
          <div key={l.key}
            className={`reading ${r?.confirmed ? 'reading-confirmed' : ''} ${unsure ? 'reading-unsure' : ''} ${selectable ? 'reading-selectable' : ''}`}
            onPointerDown={selectable ? (e) => { e.stopPropagation(); editor.selectNone(); } : undefined}
            style={{ transform: `translate(${l.box.minX}px, ${l.box.maxY + 2}px)`,
              ...(l.image && { whiteSpace: 'normal', width: l.box.w }) }}>
            {body}{unsure && <span className="reading-q"> ?</span>}
          </div>
        );
      })}
    </>
  );
}

/** One editable reading in the confirm box. */
function ReadingRow({ label, value, onChange, startEditing, placeholder = 'Type what you wrote, in LaTeX', focus = true }: {
  label: React.ReactNode; value: string; onChange: (v: string) => void; startEditing: boolean; placeholder?: string;
  focus?: boolean;
}) {
  const [editing, setEditing] = useState(startEditing);
  return (
    <div className="chip-row">
      <div className="chip-label">{label}</div>
      {editing ? (
        <div className="chip-edit">
          <input autoFocus={focus && !value} value={value} placeholder={placeholder}
            onChange={(e) => onChange(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && value.trim() && setEditing(false)} />
          {value.trim() && <Tex className="chip-preview" latex={value} />}
        </div>
      ) : (
        <div className="chip-read">
          {/* Words as plain text so a long reading wraps; lines of a multi-line problem get a space between. */}
          <MathText text={lineText(value.replace(/\n/g, ' \\text{ } '))} />
          <button className="chip-pen" title="Fix the reading" onClick={() => setEditing(true)}>✎</button>
        </div>
      )}
    </div>
  );
}

type Role = 'problem' | 'proof';

interface ChipState {
  lines: Line[];
  texts: string[];
  roles: Role[];
  typedProblem: string; // the problem, when none of the selected lines is it
  step: 'readings' | 'stating' | 'statement' | 'checking';
  statement: Statement | null;
}

const ICON: Record<LineVerdict, string> = {
  proved: '✓', 'skipped step': '✓', 'wrong reason': '✗', 'not proved': '?', unchecked: '–',
};

// The problem last stated in this tab, so the next check on it needn't be typed again.
let lastProblem = '';

/** The "Check with Lean" button next to selected lines, and the confirm box (screen space, InFrontOfTheCanvas). */
export function CheckUI() {
  const editor = useEditor();
  const [chip, setChip] = useState<ChipState | null>(null);
  const [error, setError] = useState('');
  const [seconds, setSeconds] = useState(0);
  const chipRef = useRef<HTMLDivElement>(null);
  const busy = chip?.step === 'stating' || chip?.step === 'checking';

  // A live count while Lean works: a check takes 10 s to a minute.
  // shortcut: elapsed time only. Upgrade path: stream each line's verdict as Lean reaches it.
  useEffect(() => {
    if (!busy) return;
    setSeconds(0);
    const t = setInterval(() => setSeconds((s) => s + 1), 1000);
    return () => clearInterval(t);
  }, [busy]);

  // Keep the box on screen: lift it when it would run off the bottom (it grows as steps load).
  useLayoutEffect(() => {
    const el = chipRef.current;
    if (!el) return;
    const over = el.getBoundingClientRect().bottom - (window.innerHeight - 8);
    if (over > 0) el.style.top = `${Math.max(8, el.offsetTop - over)}px`;
  });

  // Show the button only when the selection is handwriting (or a pasted image) and nothing is being dragged.
  const target = useValue('check target', () => {
    if (!editor.isIn('select.idle')) return null;
    const ids = new Set<string>([...selectedInkShapes(editor), ...editor.getSelectedShapes().filter((s) => s.type === 'image')]
      .map((s) => s.id));
    if (ids.size === 0) return null;
    const lines = linesForStrokes(getLines(editor), ids);
    const bounds = editor.getSelectionScreenBounds();
    return lines.length && bounds ? { lines, x: bounds.maxX, y: bounds.minY } : null;
  }, [editor]);

  function open(sel: Line[]) {
    const readings = getReadings(editor);
    setError('');
    // Usually the problem is written first: with several lines selected, the first one starts as the problem.
    setChip({ lines: sel, texts: sel.map((l) => readings[l.key]?.latex ?? ''),
      roles: sel.map((_, i) => (i === 0 && sel.length > 1 ? 'problem' : 'proof')),
      typedProblem: lastProblem, step: 'readings', statement: null });
  }

  const texts = chip?.texts.map((t) => t.trim()) ?? [];
  const problem = chip
    ? (chip.roles.includes('problem') ? texts.filter((_, i) => chip.roles[i] === 'problem').join(' ') : chip.typedProblem.trim())
    : '';
  const proof = chip ? texts.filter((t, i) => chip.roles[i] === 'proof' && t) : [];

  async function state() {
    if (!chip) return;
    // Confirm-before-judge: store the student's confirmed readings; only those are ever checked.
    chip.lines.forEach((l, i) => {
      const old: LineReading | undefined = getReadings(editor)[l.key];
      setReading(editor, l.key, { latex: texts[i], confidence: old?.confidence ?? null, source: old?.source ?? 'student',
        confirmed: true });
    });
    setError('');
    setChip({ ...chip, step: 'stating' });
    try {
      const statement = await api.statement(problem);
      lastProblem = problem;
      setChip((c) => c && { ...c, step: 'statement', statement });
    } catch (e) {
      setError((e as Error).message);
      setChip((c) => c && { ...c, step: 'readings' });
    }
  }

  async function check() {
    if (!chip?.statement) return;
    setError('');
    setChip({ ...chip, step: 'checking' });
    try {
      const res = await api.leanCheck(chip.statement.statement, proof);
      const rows = res.lines.map((l, i) => `${ICON[l.verdict]} ${lineText(proof[i])} — ${l.message}`);
      const near = Box.Common(chip.lines.filter((_, i) => chip.roles[i] === 'proof').map((l) => l.box));
      addFeedback(editor, near, problem, res.complete ? 'lean_complete' : 'lean_issues',
        [...rows, res.message].join('\n'));
      setChip(null);
      editor.selectNone();
    } catch (e) {
      setError((e as Error).message);
      setChip((c) => c && { ...c, step: 'statement' });
    }
  }

  const status = useValue(readingStatus);
  return (
    <EditorPortal>
      {target && !chip && (
        <div className="action-bar" style={{ left: target.x + 8, top: target.y - 4 }}
          onPointerDown={(e) => e.stopPropagation()}>
          <button className="action-btn" data-action="lean" title="Check this proof with Lean, line by line"
            onClick={() => open(target.lines)}>Check with Lean</button>
        </div>
      )}
      {chip && (
        <div className="chip" ref={chipRef} style={{ left: Math.max(8, (target?.x ?? 200) - 120), top: (target?.y ?? 80) + 32 }}
          onPointerDown={(e) => e.stopPropagation()}>
          {chip.step === 'readings' || chip.step === 'stating' ? (
            <>
              {chip.lines.some((l) => status[l.key] === 'reading') && <div className="chip-note">Still reading…</div>}
              {chip.lines.map((l, i) => (
                <ReadingRow key={l.key} value={chip.texts[i]} startEditing={!chip.texts[i]}
                  label={(
                    <select aria-label={`Line ${i + 1} is`} value={chip.roles[i]}
                      onChange={(e) => setChip({ ...chip, roles: chip.roles.map((r, k) => (k === i ? e.target.value as Role : r)) })}>
                      <option value="problem">The problem</option>
                      <option value="proof">Proof, line {chip.roles.slice(0, i + 1).filter((r) => r === 'proof').length || 1}</option>
                    </select>
                  )}
                  onChange={(v) => setChip({ ...chip, texts: chip.texts.map((t, k) => (k === i ? v : t)) })} />
              ))}
              {!chip.roles.includes('problem') && (
                <ReadingRow label="The problem this proves" value={chip.typedProblem} startEditing={!chip.typedProblem}
                  placeholder="Type the problem, e.g. the sum of two even integers is even" focus={false}
                  onChange={(v) => setChip({ ...chip, typedProblem: v })} />
              )}
              {chip.step === 'stating' && <div className="chip-note">Stating the problem in Lean… {seconds} s</div>}
            </>
          ) : (
            <>
              <div className="chip-label">Lean will check that your proof shows:</div>
              <div className="chip-read chip-words"><MathText text={chip.statement!.in_words} /></div>
              <code className="chip-lean" title="The statement in Lean">{chip.statement!.statement}</code>
              {chip.step === 'checking' && (
                <div className="chip-note">Checking {proof.length} line{proof.length > 1 ? 's' : ''} with Lean… {seconds} s</div>
              )}
            </>
          )}
          {error && <div className="chip-error">{error}</div>}
          <div className="chip-actions">
            <button onClick={() => setChip(null)}>Cancel</button>
            {chip.step === 'readings' || chip.step === 'stating' ? (
              <button className="primary" disabled={busy || !problem || proof.length === 0} onClick={state}
                title={proof.length ? '' : 'Mark at least one line as proof'}>
                {chip.step === 'stating' ? 'Stating…' : 'Looks right, next'}
              </button>
            ) : (
              <>
                <button disabled={busy} onClick={() => setChip({ ...chip, step: 'readings', statement: null })}>
                  Not what I meant
                </button>
                <button className="primary" disabled={busy} onClick={check}>
                  {chip.step === 'checking' ? 'Checking…' : 'Yes, check my proof'}
                </button>
              </>
            )}
          </div>
        </div>
      )}
    </EditorPortal>
  );
}
