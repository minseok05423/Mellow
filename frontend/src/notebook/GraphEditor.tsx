// The proof's graph, for the student to confirm and fix before anything is judged (DECISIONS #40): each step is a box
// they can drag, each premise an arrow into the step that rests on it. Drag between two steps to link them, drag an
// arrow's end onto another step to move it, tap × to remove it. Only the premises change; steps and blocks stay.

import {
  BaseEdge, ConnectionMode, Controls, EdgeLabelRenderer, Handle, MarkerType, Position, ReactFlow, applyNodeChanges,
  getBezierPath, type Connection, type Edge, type EdgeProps, type Node, type NodeChange, type NodeProps,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import { useState, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import type { Graph, GraphNode } from './api';
import { MathText } from './feedback';

export const KIND = { given: 'Given', assume: 'Assume', choose: 'Choose', claim: 'Claim' } as const;

type StepData = { n: GraphNode; where: string };
type LinkData = { remove: () => void };

function StepNode({ data: { n, where } }: NodeProps<Node<StepData>>) {
  return (
    <div className={`gnode graph-${n.kind}`}>
      <Handle type="target" position={Position.Top} />
      {n.premises.length > 0 && (
        <span className="gnode-deps" title={`Rests on step${n.premises.length > 1 ? 's' : ''} ${n.premises.join(', ')}`}>
          {n.premises.join(', ')}
        </span>
      )}
      <div className="gnode-head">
        <span className="graph-id">[{n.id}]</span> <span className="graph-kind">{KIND[n.kind]}</span>
        {where && <span className="graph-kind"> · {where}</span>}
      </div>
      <MathText text={n.text} />
      <Handle type="source" position={Position.Bottom} />
    </div>
  );
}

function LinkEdge({ id, data, markerEnd, ...at }: EdgeProps<Edge<LinkData>>) {
  const [path, x, y] = getBezierPath(at);
  return (
    <>
      <BaseEdge id={id} path={path} markerEnd={markerEnd} />
      <EdgeLabelRenderer>
        {/* A button, not a delete key: on an iPad there is no keyboard. */}
        <button className="gedge-x nodrag nopan" data-edge={id} title="Remove this link" aria-label="Remove this link"
          style={{ transform: `translate(-50%, -50%) translate(${x}px, ${y}px)` }} onClick={data?.remove}>×</button>
      </EdgeLabelRenderer>
    </>
  );
}

const nodeTypes = { step: StepNode };
const edgeTypes = { link: LinkEdge };

/** Rows by depth: the givens and steps that rest on nothing on top, each step below the steps it rests on. */
function layout(g: Graph): Record<number, { x: number; y: number }> {
  const depth: number[] = [];
  for (const n of g.nodes) depth[n.id] = Math.max(0, ...n.premises.map((p) => depth[p] + 1));
  const rows: number[][] = [];
  for (const n of g.nodes) (rows[depth[n.id]] ??= []).push(n.id);
  const at: Record<number, { x: number; y: number }> = {};
  rows.forEach((row, y) => row.forEach((id, i) => { at[id] = { x: (i - (row.length - 1) / 2) * 260, y: y * 150 }; }));
  return at;
}

/** Link two steps: the later one rests on the earlier, whichever way the student dragged (premises are earlier nodes). */
export function link(g: Graph, a: number, b: number): Graph {
  const [p, n] = a < b ? [a, b] : [b, a];
  if (p === n || g.nodes[n].kind === 'given' || g.nodes[n].premises.includes(p)) return g;
  return { ...g, nodes: g.nodes.map((x) => (x.id === n ? { ...x, premises: [...x.premises, p].sort((u, v) => u - v) } : x)) };
}

export function unlink(g: Graph, p: number, n: number): Graph {
  return { ...g, nodes: g.nodes.map((x) => (x.id === n ? { ...x, premises: x.premises.filter((q) => q !== p) } : x)) };
}

export function GraphEditor({ graph, onChange, where, children }: {
  graph: Graph; onChange: (g: Graph) => void; where: (n: GraphNode) => string; children: ReactNode;
}) {
  // Positions live here; each box shows its step as it is now, so its numbers follow the student's edits.
  const [nodes, setNodes] = useState<Node<StepData>[]>(() => {
    const at = layout(graph);
    return graph.nodes.map((n) => ({ id: String(n.id), type: 'step', position: at[n.id], data: { n, where: where(n) } }));
  });
  const edges: Edge<LinkData>[] = graph.nodes.flatMap((n) => n.premises.map((p) => ({
    id: `${p}-${n.id}`, source: String(p), target: String(n.id), type: 'link', markerEnd: { type: MarkerType.ArrowClosed },
    data: { remove: () => onChange(unlink(graph, p, n.id)) },
  })));

  // In document.body, outside tldraw's canvas, so its gestures and shortcuts (Backspace deletes the selected ink)
  // never see what happens here; React still bubbles events to the canvas, hence the stopPropagation.
  return createPortal(
    <div className="graph-editor" onPointerDown={(e) => e.stopPropagation()} onKeyDown={(e) => e.stopPropagation()}>
      <div className="ge-panel">
        <div className="ge-canvas">
          <ReactFlow nodes={nodes.map((x) => ({ ...x, data: { ...x.data, n: graph.nodes[+x.id] } }))} edges={edges} nodeTypes={nodeTypes} edgeTypes={edgeTypes} fitView
            connectionMode={ConnectionMode.Loose} deleteKeyCode={null} minZoom={0.2}
            onNodesChange={(c: NodeChange<Node<StepData>>[]) => setNodes((ns) => applyNodeChanges(c, ns))}
            onConnect={(c: Connection) => onChange(link(graph, +c.source, +c.target))}
            onReconnect={(old: Edge, c: Connection) => onChange(link(unlink(graph, +old.source, +old.target), +c.source, +c.target))}>
            <Controls showInteractive={false} />
          </ReactFlow>
        </div>
        {children}
      </div>
    </div>,
    document.body,
  );
}
