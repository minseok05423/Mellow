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

async function post<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`/api${path}`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-Mellow-User': userId(),
      'X-Mellow-Key': localStorage.getItem('mellow.key') ?? '',
    },
    body: JSON.stringify(body),
  });
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

/** The problem as Lean states it, and what that says in words (from a second model that sees only the Lean). */
export interface Statement { statement: string; in_words: string }

export type LineVerdict = 'proved' | 'skipped step' | 'wrong reason' | 'not proved' | 'unchecked';
export interface LeanCheck {
  lines: { verdict: LineVerdict; message: string; lean: string[] }[];
  complete: boolean;
  message: string;
  seconds: number;
}

export const api = {
  recognize: (body: { strokes: { t0: number; pts: number[][] }[]; image?: string; context: string[] }) =>
    post<Reading>('/recognize', body),
  statement: (problem: string) => post<Statement>('/lean/statement', { problem, confirmed: true }),
  leanCheck: (statement: string, lines: string[]) =>
    post<LeanCheck>('/lean/check', { statement, lines, confirmed: true }),
};
