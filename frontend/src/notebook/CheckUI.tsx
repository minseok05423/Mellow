// Readings under each line, and "Check my proof" (DECISIONS #32): select lines -> confirm the readings and which line
// is the problem -> the proof as a graph of steps, which the student confirms -> each step checked on its own (Lean,
// SymPy or three judges) -> one card on the page, every verdict with its tier.

import katex from 'katex';
import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { Box, EditorPortal, useEditor, useValue } from 'tldraw';
import { api, type Graph, type NodeVerdict, type Tier } from './api';
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
  step: 'readings' | 'graphing' | 'graph' | 'checking';
  graph: Graph | null;
  id: string; // ties this check's events together
}

const ICON: Record<NodeVerdict['status'], string> = { ok: '✓', wrong: '✗', unsure: '?' };
// Every verdict says how far to trust it: only Proved is certain.
const TIER: Record<Tier, string> = { proved: 'Proved by Lean', tested: 'Tested', judged: 'Judged by AI' };
const KIND = { given: 'Given', assume: 'Assume', choose: 'Choose', claim: 'Claim' } as const;

// The problem last checked in this tab, so the next check on it needn't be typed again.
let lastProblem = '';
// Graphs the student confirmed, by problem and proof: checking the same proof again skips that confirm and its wait.
const confirmedGraphs = new Map<string, Graph>();
const graphKey = (problem: string, proof: string[]) => JSON.stringify([problem, proof]);

/** The "Check my proof" button next to selected lines, and the confirm box (screen space, InFrontOfTheCanvas). */
export function CheckUI() {
  const editor = useEditor();
  const [chip, setChip] = useState<ChipState | null>(null);
  const [error, setError] = useState('');
  const [seconds, setSeconds] = useState(0);
  const chipRef = useRef<HTMLDivElement>(null);
  // After a ✗ or ?, one optional question: what the student does next is the friends test's key signal.
  const [ask, setAsk] = useState<string | null>(null);
  const busy = chip?.step === 'graphing' || chip?.step === 'checking';

  // Leaving during a wait is the strongest sign the wait is too long.
  useEffect(() => {
    if (!busy || !chip) return;
    const left = () => document.hidden && api.event('abandoned', chip.id, { step: chip.step });
    document.addEventListener('visibilitychange', left);
    return () => document.removeEventListener('visibilitychange', left);
  }, [busy, chip?.id, chip?.step]);

  // A live count while the checker works: a check takes 10 s to a minute.
  // shortcut: elapsed time only. Upgrade path: stream each step's verdict as it lands.
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
      typedProblem: lastProblem, step: 'readings', graph: null, id: crypto.randomUUID() });
    setAsk(null);
  }

  const texts = chip?.texts.map((t) => t.trim()) ?? [];
  const problem = chip
    ? (chip.roles.includes('problem') ? texts.filter((_, i) => chip.roles[i] === 'problem').join(' ') : chip.typedProblem.trim())
    : '';
  const proof = chip ? texts.filter((t, i) => chip.roles[i] === 'proof' && t) : [];

  async function makeGraph() {
    if (!chip) return;
    // Confirm-before-judge: store the student's confirmed readings; only those are ever checked.
    chip.lines.forEach((l, i) => {
      const old: LineReading | undefined = getReadings(editor)[l.key];
      setReading(editor, l.key, { latex: texts[i], confidence: old?.confidence ?? null, source: old?.source ?? 'student',
        confirmed: true });
    });
    setError('');
    const known = confirmedGraphs.get(graphKey(problem, proof));
    if (known) return check({ ...chip, graph: known });
    setChip({ ...chip, step: 'graphing' });
    try {
      const graph = await api.graph(problem, proof);
      lastProblem = problem;
      setChip((c) => c && { ...c, step: 'graph', graph });
    } catch (e) {
      setError((e as Error).message);
      setChip((c) => c && { ...c, step: 'readings' });
    }
  }

  async function check(c = chip) {
    if (!c?.graph) return;
    const graph = c.graph;
    setError('');
    setChip({ ...c, step: 'checking' });
    confirmedGraphs.set(graphKey(problem, proof), graph);
    api.event('check_started', c.id, { problem, lines: proof.length, steps: graph.nodes.length, again: c !== chip });
    try {
      const res = await api.check(graph);
      const rows = res.nodes.map((v) => `${ICON[v.status]} [${v.node}] ${graph.nodes[v.node].text} · ${TIER[v.tier]}`
        + (v.message ? ` — ${v.message}` : ''));
      const near = Box.Common(c.lines.filter((_, i) => c.roles[i] === 'proof').map((l) => l.box));
      addFeedback(editor, near, problem, res.complete ? 'graph_complete' : 'graph_issues', [...rows, res.message].join('\n'));
      const counts = res.nodes.reduce<Record<string, number>>((n, v) => {
        const k = `${v.status} ${v.tier}`;
        return { ...n, [k]: (n[k] ?? 0) + 1 };
      }, {});
      api.event('check_result', c.id, { verdicts: counts, complete: res.complete, seconds: res.seconds });
      if (res.nodes.some((v) => v.status !== 'ok')) setAsk(c.id);
      setChip(null);
      editor.selectNone();
    } catch (e) {
      setError((e as Error).message);
      setChip((c) => c && { ...c, step: 'graph' });
    }
  }

  const status = useValue(readingStatus);
  return (
    <EditorPortal>
      {target && !chip && (
        <div className="action-bar" style={{ left: target.x + 8, top: target.y - 4 }}
          onPointerDown={(e) => e.stopPropagation()}>
          <button className="action-btn" data-action="check" title="Check this proof, step by step"
            onClick={() => open(target.lines)}>Check my proof</button>
        </div>
      )}
      {chip && (
        // Beside the selection when there's room, so the ink being confirmed stays in view.
        <div className="chip" ref={chipRef} style={(target?.x ?? 0) + 404 <= window.innerWidth
          ? { left: target!.x + 16, top: target!.y } : { left: Math.max(8, (target?.x ?? 200) - 120), top: (target?.y ?? 80) + 32 }}
          onPointerDown={(e) => e.stopPropagation()}>
          {chip.step === 'readings' || chip.step === 'graphing' ? (
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
              {chip.step === 'graphing' && <div className="chip-note">Reading your proof's steps… {seconds} s</div>}
            </>
          ) : (
            <>
              <div className="chip-label">Your proof shows: <MathText text={chip.graph!.goal} /></div>
              <div className="chip-label">Its steps, each checked on its own from the steps it rests on:</div>
              <ol className="graph">
                {chip.graph!.nodes.map((n) => (
                  <li key={n.id} className={`graph-node graph-${n.kind}`}>
                    <span className="graph-id">[{n.id}]</span> <span className="graph-kind">{KIND[n.kind]}</span>{' '}
                    <MathText text={n.text} />
                    {n.premises.length > 0 && <span className="graph-from"> from {n.premises.map((p) => `[${p}]`).join(', ')}</span>}
                    {chip.graph!.concludes === n.id && <span className="graph-from"> (the goal)</span>}
                  </li>
                ))}
              </ol>
              {chip.step === 'checking' && (
                <div className="chip-note">Checking {chip.graph!.nodes.filter((n) => n.kind !== 'given').length} steps… {seconds} s</div>
              )}
            </>
          )}
          {error && <div className="chip-error">{error}</div>}
          <div className="chip-actions">
            <button onClick={() => setChip(null)}>Cancel</button>
            {chip.step === 'readings' || chip.step === 'graphing' ? (
              <button className="primary" disabled={busy || !problem || proof.length === 0} onClick={makeGraph}
                title={proof.length ? '' : 'Mark at least one line as proof'}>
                {chip.step === 'graphing' ? 'Reading…' : confirmedGraphs.has(graphKey(problem, proof)) ? 'Check again' : 'Looks right, next'}
              </button>
            ) : (
              <>
                <button disabled={busy} onClick={() => {
                  // Compact, under /event's size limit: which steps and premises the student said were wrong.
                  api.event('graph_rejected', chip.id, { problem, steps: chip.graph!.nodes
                    .map((n) => `[${n.id}] ${n.kind} from ${n.premises.join(',')}: ${n.text}`).join('\n').slice(0, 3000) });
                  setChip({ ...chip, step: 'readings', graph: null });
                }}>
                  Not what I meant
                </button>
                <button className="primary" disabled={busy} onClick={() => check()}>
                  {chip.step === 'checking' ? 'Checking…' : 'Yes, check my proof'}
                </button>
              </>
            )}
          </div>
        </div>
      )}
      {ask && !chip && (
        <div className="ask" onPointerDown={(e) => e.stopPropagation()}>
          <span>What will you do now?</span>
          {['Fix it', 'Ask ChatGPT or a friend', 'Not sure'].map((a) => (
            <button key={a} onClick={() => { api.event('next_step', ask, { answer: a }); setAsk(null); }}>{a}</button>
          ))}
          <button className="ask-close" aria-label="Close" onClick={() => setAsk(null)}>×</button>
        </div>
      )}
    </EditorPortal>
  );
}
