// Readings under each line, and the selection actions (SPEC §4): select lines -> Check / Hint / Answer / Explain
// -> confirm chip (readings, what the selection is, its problem) -> /check or /assist -> feedback card.

import katex from 'katex';
import { useState } from 'react';
import { EditorPortal, useEditor, useValue } from 'tldraw';
import { api, type CheckResult, type Kind } from './api';
import { addFeedback } from './feedback';
import { Box } from 'tldraw';
import { Line, getLines, linesAbove, linesForStrokes } from './lines';
import { LineReading, getReadings, looksLikeWords, readingStatus, setReading, showReadings } from './readings';
import { selectedInkShapes } from './samples';
import { ActionContext, actionContext, ensureStructure } from './structure';

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
function ReadingRow({ label, value, onChange, startEditing, placeholder = 'Type what you wrote, in LaTeX', focus = true }: {
  label: string; value: string; onChange: (v: string) => void; startEditing: boolean; placeholder?: string; focus?: boolean;
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
          <Tex latex={value} />
          <button className="chip-pen" title="Fix the reading" onClick={() => setEditing(true)}>✎</button>
        </div>
      )}
    </div>
  );
}

type Action = 'check' | 'hint' | 'answer' | 'explain';
const ACTIONS: { id: Action; label: string; title: string; go: string }[] = [
  { id: 'check', label: 'Check ✓', title: 'Is what I wrote sound?', go: 'check it' },
  { id: 'hint', label: 'Hint', title: 'A question toward the next step', go: 'give a hint' },
  { id: 'answer', label: 'Answer', title: 'The answer, or whether these steps hold and how to fix them', go: 'answer' },
  { id: 'explain', label: 'Explain with AI', title: 'A worked explanation', go: 'explain' },
];
const KINDS: { id: Kind; label: string }[] = [
  { id: 'steps', label: 'proof steps' }, { id: 'problem', label: 'a problem' }, { id: 'concept', label: 'a concept' },
];

interface ChipState {
  action: Action;
  lines: Line[];
  texts: string[];
  above: Line | undefined; // Check: the line an algebra step is checked against
  previous: string;
  labelling: boolean; // waiting for the page's labels
  ctx: ActionContext | null;
  kind: Kind;
  problem: string; // the problem the selection works on (steps only); the student confirms or fixes it
  problemRead: string; // the problem as read from the page, to tell whether the student changed it
}

// Same rule as the backend's verify.is_prose: words other than joining words make a line a statement.
const CONNECTORS = new Set(['so', 'thus', 'hence', 'then', 'therefore', 'and', 'i.e.', 'ie', 'which', 'gives', 'implies']);
const isProse = (latex: string) => looksLikeWords(latex.replace(/\\text\s*\{[^}]*\}/g, ' '))
  || [...latex.matchAll(/\\text\s*\{([^}]*)\}/g)]
    .flatMap((m) => m[1].toLowerCase().replace(/,/g, ' ').split(/\s+/))
    .some((w) => w && !CONNECTORS.has(w.replace(/[.:;]+$/, '')));

const RANK: Record<CheckResult['verdict'], number> = {
  refuted: 3, unverified: 2, ai_check: 2, hint: 2, answer: 2, explain: 2, verified: 1, noted: 0,
};

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

const NOTHING_TO_CHECK: Record<Exclude<Kind, 'steps'>, string> = {
  problem: 'This is a problem, not a step, so there is nothing to check yet. Try Hint or Answer.',
  concept: 'This is a concept, so there is nothing to check. Try Explain with AI.',
};
const noted = (message: string): CheckResult =>
  ({ verdict: 'noted', reason: 'nothing_to_check', message, counterexample: {}, values: {}, compared_with: null });

/** The action buttons next to selected lines, and the confirm chip (screen space, InFrontOfTheCanvas). */
export function CheckUI() {
  const editor = useEditor();
  const [chip, setChip] = useState<ChipState | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  // Show the buttons only when the selection is handwriting and nothing is being dragged.
  const target = useValue('check target', () => {
    if (!editor.isIn('select.idle')) return null;
    const ids = new Set<string>(selectedInkShapes(editor).map((s) => s.id));
    if (ids.size === 0) return null;
    const lines = linesForStrokes(getLines(editor), ids);
    const bounds = editor.getSelectionScreenBounds();
    return lines.length && bounds ? { lines, x: bounds.maxX, y: bounds.minY } : null;
  }, [editor]);

  function open(sel: Line[], action: Action) {
    const readings = getReadings(editor);
    const above = linesAbove(getLines(editor), sel[0], 1)[0];
    setError('');
    setChip({ action, lines: sel, texts: sel.map((l) => readings[l.key]?.latex ?? ''), above,
      previous: above ? readings[above.key]?.latex ?? '' : '', labelling: true, ctx: null, kind: 'steps',
      problem: '', problemRead: '' });
    // Label the page while the student looks over the readings.
    ensureStructure(editor, sel[0].pageId).then((s) => {
      const ctx = actionContext(editor, s, sel);
      const r = getReadings(editor);
      const problem = ctx.problem.map((l) => r[l.key]?.latex ?? '').filter(Boolean).join(' ');
      setChip((c) => (c?.lines === sel ? { ...c, labelling: false, ctx, kind: ctx.kind, problem, problemRead: problem } : c));
    });
  }

  async function confirmAndRun() {
    if (!chip) return;
    const { action, lines: sel, above, previous, kind, ctx } = chip;
    const texts = chip.texts.map((t) => t.trim());
    const problem = kind === 'steps' ? chip.problem.trim() || null : null;
    // Confirm-before-judge: store the student's confirmed readings first; only those are judged.
    const keep = (k: string, latex: string) => {
      const old: LineReading | undefined = getReadings(editor)[k];
      setReading(editor, k, { latex, confidence: old?.confidence ?? null, source: old?.source ?? 'student', confirmed: true });
    };
    sel.forEach((l, i) => keep(l.key, texts[i]));
    if (action === 'check' && above && previous.trim()) keep(above.key, previous.trim());
    if (problem && problem === chip.problemRead.trim()) {
      ctx?.problem.forEach((l) => { const r = getReadings(editor)[l.key]; if (r?.latex) keep(l.key, r.latex); });
    }
    const readings = getReadings(editor);
    const box = Box.Common(sel.map((l) => l.box));
    const prose = texts.some(isProse);
    const shown = prose ? texts.filter(Boolean).join(' \\text{ } ') : texts.filter(Boolean).join(' \\quad ');
    setBusy(true);
    try {
      if (action === 'check') {
        if (kind !== 'steps') addFeedback(editor, box, shown, noted(NOTHING_TO_CHECK[kind]));
        else {
          const contextFor = (l: Line) => linesAbove(getLines(editor), l)
            .map((a) => readings[a.key])
            .filter((r) => r?.confirmed && r.latex)
            .map((r) => r.latex);
          // A statement written over several lines is one statement; math lines are steps, each checked on its
          // own. Either way the student gets one card for one Check.
          const jobs = prose
            ? [{ n: 1, line: sel[0], latex: texts.filter(Boolean).join(' \\text{ } ') }]
            : sel.map((l, i) => ({ n: i + 1, line: l, latex: texts[i] }))
              // The first of several selected lines is the starting point unless something confirmed sits above it.
              .filter((j, i) => j.latex && (i > 0 || sel.length === 1 || contextFor(j.line).length > 0));
          const results = [];
          for (const j of jobs) {
            results.push({ n: j.n, r: await api.check({ current: j.latex, context: contextFor(j.line), problem, confirmed: true }) });
          }
          if (results.length) addFeedback(editor, box, shown, combine(results));
        }
      } else {
        // An answer is a verdict, so its context is confirmed lines only; hints and explanations may use any reading.
        const usable = (ls: Line[] = []) => ls.map((l) => readings[l.key])
          .filter((r) => r?.latex && (action !== 'answer' || r.confirmed)).map((r) => r.latex);
        const result = await api.assist({ action, kind, selection: texts.filter(Boolean).join('\n'), problem,
          work: kind === 'steps' ? usable(ctx?.work) : [], definitions: usable(ctx?.definitions), confirmed: true });
        addFeedback(editor, box, shown, result);
      }
      setChip(null);
      editor.selectNone();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const status = useValue(readingStatus);
  const verb = chip && ACTIONS.find((a) => a.id === chip.action)!.go;
  return (
    <EditorPortal>
      {target && !chip && (
        <div className="action-bar" style={{ left: target.x + 8, top: target.y - 4 }}
          onPointerDown={(e) => e.stopPropagation()}>
          {ACTIONS.map((a) => (
            <button key={a.id} className="action-btn" data-action={a.id}
              title={a.title} onClick={() => open(target.lines, a.id)}>{a.label}</button>
          ))}
        </div>
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
          {chip.labelling ? <div className="chip-note">Working out what this is…</div> : (
            <div className="chip-row chip-kind">
              <label className="chip-label" htmlFor="chip-kind">This is</label>
              <select id="chip-kind" aria-label="This is" value={chip.kind}
                onChange={(e) => setChip({ ...chip, kind: e.target.value as Kind })}>
                {KINDS.map((k) => <option key={k.id} value={k.id}>{k.label}</option>)}
              </select>
              {chip.ctx?.description && <span className="chip-note">in “{chip.ctx.description}”</span>}
            </div>
          )}
          {!chip.labelling && chip.kind === 'steps' && (
            <ReadingRow key={`problem-${chip.problemRead}`} label="The problem these steps work on" value={chip.problem}
              startEditing={!chip.problem} placeholder="No problem found above. Type it, or leave empty" focus={false}
              onChange={(v) => setChip({ ...chip, problem: v })} />
          )}
          {chip.action === 'check' && chip.kind === 'steps' && chip.above && (
            <ReadingRow label="Checking it against the line above" value={chip.previous}
              startEditing={!chip.previous} onChange={(v) => setChip({ ...chip, previous: v })} />
          )}
          {error && <div className="chip-error">{error}</div>}
          <div className="chip-actions">
            <button onClick={() => setChip(null)}>Cancel</button>
            <button className="primary" disabled={busy || chip.labelling || !chip.texts.some((t) => t.trim())}
              onClick={confirmAndRun}>
              {busy ? (chip.action === 'check' ? 'Checking…' : 'Thinking…') : `Looks right, ${verb}`}
            </button>
          </div>
        </div>
      )}
    </EditorPortal>
  );
}
