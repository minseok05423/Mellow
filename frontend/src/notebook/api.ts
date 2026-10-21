// Calls to our backend (via Vite's /api proxy). The browser never talks to an LLM or recognition vendor.

function userId(): string {
  // shortcut: an anonymous per-browser id for daily caps and logs. Upgrade path: accounts in P4.
  let id = localStorage.getItem('mellow.user');
  if (!id) {
    id = `anon-${crypto.randomUUID()}`;
    localStorage.setItem('mellow.user', id);
  }
  return id;
}

// The server's access code arrives once in the invite link (?key=...) and is kept for later visits.
// On iPad, Add to Home Screen keeps the link's ?key= as the app's start URL.
const urlKey = new URLSearchParams(location.search).get('key');
if (urlKey) localStorage.setItem('mellow.key', urlKey);

const headers = () => ({
  'Content-Type': 'application/json',
  'X-Mellow-User': userId(),
  'X-Mellow-Key': localStorage.getItem('mellow.key') ?? '',
});

async function post<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`/api${path}`, { method: 'POST', headers: headers(), body: JSON.stringify(body) });
  if (!res.ok) {
    const detail = await res.json().then((d) => d.detail, () => res.statusText);
    throw new Error(typeof detail === 'string' ? detail : `Request failed (${res.status})`);
  }
  return res.json();
}

export interface Reading {
  latex: string;
  confidence: number | null;
  source: string; // mathpix | myscript | llm | none
  has_prose: boolean;
}

/** The proof as a graph of steps (DECISIONS #32): the student confirms it before anything is judged. */
export interface GraphNode {
  id: number;
  kind: 'given' | 'assume' | 'choose' | 'claim';
  line: number | null; // index into the proof lines sent; null for the problem's givens
  text: string;
  premises: number[];
}
export interface Graph {
  goal: string;
  goal_order: { name: string; quantifier: 'forall' | 'exists' }[];
  nodes: GraphNode[];
  variables: { name: string; kind: 'given' | 'any' | 'chosen' | 'obtained'; introduced_at: number | null; depends_on: string[] }[];
  concludes: number | null;
}

/** How far a verdict can be trusted: Proved by Lean, Tested by SymPy at random values, Judged by three models. */
export type Tier = 'proved' | 'tested' | 'judged';
export interface NodeVerdict { node: number; status: 'ok' | 'wrong' | 'unsure'; tier: Tier; message: string; detail: string }
export interface GraphCheck { nodes: NodeVerdict[]; complete: boolean; message: string; seconds: number }

export const api = {
  recognize: (body: { strokes: { t0: number; pts: number[][] }[]; image?: string; context: string[] }) =>
    post<Reading>('/recognize', body),
  graph: (problem: string, lines: string[]) => post<Graph>('/graph', { problem, lines, confirmed: true }),
  check: (graph: Graph) => post<GraphCheck>('/check', { graph, confirmed: true }),
  /** What the student does around a check, for the friends test (PROJECT.md §11). Fire and forget; `keepalive`
   * lets it leave while the tab closes (sendBeacon can't send our headers). */
  event: (event: string, checkId: string, data: Record<string, unknown> = {}) => {
    fetch('/api/event', { method: 'POST', headers: headers(), keepalive: true,
      body: JSON.stringify({ event, check_id: checkId, data }) }).catch(() => {});
  },
};
