// "Check my proof" (DECISIONS #32): select lines -> they are read (#39) -> confirm the readings and which line
// is the problem -> the proof as a graph of steps, which the student confirms -> each step checked on its own (Lean,
// SymPy or three judges) -> when the judges aren't sure of a step, the student weighs it with their comments -> one
// card on the page, every verdict with its tier.

import katex from 'katex';
import { Fragment, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { Box, EditorPortal, useEditor, useValue } from 'tldraw';
import { api, type BlockKind, type Graph, type GraphCheck, type NodeVerdict, type Tier } from './api';
import { MathText, addFeedback, lineText } from './feedback';
import { GraphEditor, KIND } from './GraphEditor';
import { Line, getLines, linesForStrokes, selectedInkShapes } from './lines';
import { LineReading, getReadings, needsReading, readLines, readingStatus, setReading } from './readings';

function Tex({ latex, className }: { latex: string; className?: string }) {
  return (
    <span className={className}
      dangerouslySetInnerHTML={{ __html: katex.renderToString(latex, { throwOnError: false }) }} />
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
  step: 'reading' | 'readings' | 'graphing' | 'graph' | 'checking' | 'review';
  graph: Graph | null;
  fromModel: Graph | null; // the graph as the model linked it, to log what the student changed
  result: GraphCheck | null; // in review: the check, before it goes on the page
  decisions: Record<number, 'accepted' | 'rejected'>; // the student's call on each ? step
  id: string; // ties this check's events together
}

const ICON: Record<NodeVerdict['status'], string> = { ok: '✓', wrong: '✗', unsure: '?' };
// Every verdict says how far to trust it: only Proved is certain.
const TIER: Record<Tier, string> = { proved: 'Proved by Lean', tested: 'Tested', judged: 'Judged by AI' };

const BLOCK: Record<BlockKind, string> = { proof: 'The proof', let: 'Subproof', contradiction: 'Suppose, for contradiction',
  cases: 'Proof by cases', case: 'Case', induction: 'Induction', base: 'Base case', step: 'Inductive step', chain: 'Chain' };

/** A block and the blocks around it, innermost first, ending with block 0 (the whole proof). */
function inside(g: Graph, b: number): number[] {
  const out = [b];
  for (let p = g.blocks[b].parent; p !== null; p = g.blocks[p].parent) out.push(p);
  return out;
}
const depth = (g: Graph, b: number) => inside(g, b).length - 1;

/** The blocks that start at this step, outermost first, for their headers: "Proof by cases", "Case 1", ... */
function opensAt(g: Graph, id: number): number[] {
  return g.blocks.filter((b) => b.id && g.nodes.find((n) => inside(g, n.block).includes(b.id))?.id === id).map((b) => b.id);
}

function blockName(g: Graph, b: number): string {
  const x = g.blocks[b];
  return x.kind === 'case' ? `Case ${g.blocks.filter((y) => y.parent === x.parent).findIndex((y) => y.id === b) + 1}` : BLOCK[x.kind];
}

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
  const busy = chip?.step === 'reading' || chip?.step === 'graphing' || chip?.step === 'checking';

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
    const id = crypto.randomUUID();
    const texts = () => sel.map((l) => getReadings(editor)[l.key]?.latex ?? '');
    const unread = sel.some((l) => needsReading(getReadings(editor)[l.key]));
    setError('');
    // Usually the problem is written first: with several lines selected, the first one starts as the problem.
    setChip({ lines: sel, texts: texts(), roles: sel.map((_, i) => (i === 0 && sel.length > 1 ? 'problem' : 'proof')),
      typedProblem: lastProblem, step: unread ? 'reading' : 'readings', graph: null, fromModel: null, result: null,
      decisions: {}, id });
    setAsk(null);
    // The rows appear once every line is read, so a reading never lands under what the student is typing.
    if (unread) readLines(editor, sel).then(() => setChip((c) => c?.id === id ? { ...c, step: 'readings', texts: texts() } : c));
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
      setChip((c) => c && { ...c, step: 'graph', graph, fromModel: graph });
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
    // The links the student changed, "[step] model's premises -> theirs": training data for the linker.
    const edits = c.fromModel ? graph.nodes.filter((n, i) => n.premises.join() !== c.fromModel!.nodes[i].premises.join())
      .map((n) => `[${n.id}] ${c.fromModel!.nodes[n.id].premises.join(',')} -> ${n.premises.join(',')}`) : [];
    api.event('check_started', c.id, { problem, lines: proof.length, steps: graph.nodes.length, again: c !== chip,
      edits: edits.join('; ').slice(0, 1500) });
    try {
      const res = await api.check(graph);
      const counts = res.nodes.reduce<Record<string, number>>((n, v) => {
        const k = `${v.status} ${v.tier}`;
        return { ...n, [k]: (n[k] ?? 0) + 1 };
      }, {});
      api.event('check_result', c.id, { verdicts: counts, complete: res.complete, seconds: res.seconds });
      // A ? is the student's call: show the whole graph with what the doubting judges said.
      if (res.nodes.some((v) => v.status === 'unsure')) setChip({ ...c, step: 'review', result: res, decisions: {} });
      else finish(c, res, {});
    } catch (e) {
      setError((e as Error).message);
      setChip((c) => c && { ...c, step: 'graph' });
    }
  }

  // The card on the page. A ? the student accepted stays a ?, marked as their call: it never becomes a ✓.
  function finish(c: ChipState, res: GraphCheck, decisions: ChipState['decisions']) {
    const graph = c.graph!;
    const indent = (n: number) => ' '.repeat(n); // an em space: pre-line doesn't collapse it
    const rows = res.nodes.flatMap((v) => [
      ...opensAt(graph, v.node).map((b) => `${indent(depth(graph, b) - 1)}${blockName(graph, b)}:`),
      `${indent(depth(graph, graph.nodes[v.node].block))}${ICON[v.status]} [${v.node}] ${graph.nodes[v.node].text} · ${TIER[v.tier]}`
      + (decisions[v.node] ? ` — ${decisions[v.node] === 'accepted' ? 'you accepted it' : "you didn't accept it"}`
        : v.message ? ` — ${v.message}` : '')]);
    const open = res.nodes.filter((v) => v.status !== 'ok' && decisions[v.node] !== 'accepted');
    const accepted = res.nodes.filter((v) => decisions[v.node] === 'accepted').map((v) => v.node);
    const summary = accepted.length && !open.length && res.reaches_goal
      ? `You accepted step${accepted.length > 1 ? 's' : ''} ${accepted.join(', ')}, which the judges weren't sure about; every other step checks out.`
      : res.message;
    const near = Box.Common(c.lines.filter((_, i) => c.roles[i] === 'proof').map((l) => l.box));
    addFeedback(editor, near, problem, res.complete ? 'graph_complete' : 'graph_issues', [...rows, summary].join('\n'));
    if (Object.keys(decisions).length) api.event('review', c.id, { decisions });
    if (open.length) setAsk(c.id);
    setChip(null);
    editor.selectNone();
  }

  const status = useValue(readingStatus);
  // The error and the buttons, in the box or under the graph editor.
  const actions = chip && (
    <>
      {error && <div className="chip-error">{error}</div>}
      <div className="chip-actions">
        {chip.step !== 'review' && <button onClick={() => setChip(null)}>Cancel</button>}
        {chip.step === 'review' ? (
          <button className="primary" onClick={() => finish(chip, chip.result!, chip.decisions)}>Put it on the page</button>
        ) : chip.step === 'reading' || chip.step === 'readings' || chip.step === 'graphing' ? (
          <button className="primary" disabled={busy || !problem || proof.length === 0} onClick={makeGraph}
            title={proof.length ? '' : 'Mark at least one line as proof'}>
            {busy ? 'Reading…' : confirmedGraphs.has(graphKey(problem, proof)) ? 'Check again' : 'Looks right, next'}
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
    </>
  );
  return (
    <EditorPortal>
      {target && !chip && (
        <div className="action-bar" style={{ left: target.x + 8, top: target.y - 4 }}
          onPointerDown={(e) => e.stopPropagation()}>
          <button className="action-btn" data-action="check" title="Check this proof, step by step"
            onClick={() => open(target.lines)}>Check my proof</button>
        </div>
      )}
      {chip?.step === 'graph' && (
        <GraphEditor graph={chip.graph!} onChange={(graph) => setChip((c) => c && { ...c, graph })}
          where={(n) => (n.block ? blockName(chip.graph!, n.block) : '')}>
          <div className="chip-label">Your proof shows: <MathText text={chip.graph!.goal} /></div>
          <div className="chip-label">Each arrow goes from a step to a step that rests on it. Fix the arrows before the
            check: drag from one step to another to link them, drag an arrow's end to move it, tap × to remove it.</div>
          {!chip.graph!.reaches_goal && <div className="chip-note">No step reaches the goal yet.</div>}
          {actions}
        </GraphEditor>
      )}
      {chip && chip.step !== 'graph' && (
        // Beside the selection when there's room, so the ink being confirmed stays in view.
        <div className="chip" ref={chipRef} style={(target?.x ?? 0) + 404 <= window.innerWidth
          ? { left: target!.x + 16, top: target!.y } : { left: Math.max(8, (target?.x ?? 200) - 120), top: (target?.y ?? 80) + 32 }}
          onPointerDown={(e) => e.stopPropagation()}>
          {chip.step === 'reading' ? (
            <div className="chip-note">Reading your handwriting… {seconds} s</div>
          ) : chip.step === 'readings' || chip.step === 'graphing' ? (
            <>
              {chip.lines.some((l, i) => status[l.key] && !chip.texts[i]) && (
                // The reason in the note itself: a tablet has no hover to show a tooltip.
                <div className="chip-note">
                  Some lines couldn't be read ({chip.lines.map((l) => status[l.key]).find(Boolean)}): type what you
                  wrote, or try again.
                </div>
              )}
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
              <div className="chip-label">{chip.step === 'review'
                ? "The judges weren't sure about the steps marked ?. Read what they said and decide whether each is acceptable:"
                : 'Its steps, each checked on its own from the steps it rests on:'}</div>
              <ol className="graph">
                {chip.graph!.nodes.map((n) => {
                  const v = chip.result?.nodes.find((x) => x.node === n.id);
                  const decided = chip.decisions[n.id];
                  const g = chip.graph!;
                  return (
                    <Fragment key={n.id}>
                    {opensAt(g, n.id).map((b) => (
                      <li key={`b${b}`} className="graph-block" style={{ marginLeft: (depth(g, b) - 1) * 14 }}>{blockName(g, b)}</li>
                    ))}
                    <li className={`graph-node graph-${n.kind}`} style={{ marginLeft: depth(g, n.block) * 14 }}>
                      {v && <span className={`graph-verdict verdict-${v.status}`}>{ICON[v.status]}</span>}
                      <span className="graph-id">[{n.id}]</span> <span className="graph-kind">{KIND[n.kind]}</span>{' '}
                      <MathText text={n.text} />
                      {n.premises.length > 0 && <span className="graph-from"> from {n.premises.map((p) => `[${p}]`).join(', ')}</span>}
                      {v && <span className="graph-from"> · {TIER[v.tier]}</span>}
                      {v && v.status !== 'ok' && !v.comments.length && v.message && <div className="graph-msg"><MathText text={v.message} /></div>}
                      {v?.status === 'unsure' && (
                        <div className="judge-review">
                          {v.comments.length > 0 && (
                            <ul className="judge-comments">
                              {v.comments.map((m, i) => <li key={i}><MathText text={m} /></li>)}
                            </ul>
                          )}
                          {(['accepted', 'rejected'] as const).map((d) => (
                            <button key={d} className={`decide${decided === d ? ' on' : ''}`} aria-pressed={decided === d}
                              onClick={() => setChip({ ...chip, decisions: { ...chip.decisions, [n.id]: d } })}>
                              {d === 'accepted' ? 'Acceptable' : 'Not acceptable'}
                            </button>
                          ))}
                        </div>
                      )}
                    </li>
                    </Fragment>
                  );
                })}
              </ol>
              {!chip.graph!.reaches_goal && <div className="chip-note">No step reaches the goal yet.</div>}
              {chip.step === 'checking' && (
                <div className="chip-note">Checking {chip.graph!.nodes.filter((n) => n.kind !== 'given').length} steps… {seconds} s</div>
              )}
            </>
          )}
          {actions}
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
