// Calls to our backend (via Vite's /api proxy). The browser never talks to an LLM or recognition vendor.

import { choices, feedbackMode } from './models';

function userId(): string {
  // shortcut: an anonymous per-browser id for daily caps and logs. Upgrade path: accounts in P4.
  let id = localStorage.getItem('mellow.user');
  if (!id) {
    id = `anon-${crypto.randomUUID()}`;
    localStorage.setItem('mellow.user', id);
  }
  return id;
}

async function post<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`/api${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-Mellow-User': userId() },
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

export interface CheckResult {
  // noted: words, no reasoning model available; answer: the AI's direct answer, not verified by SymPy
  verdict: 'verified' | 'refuted' | 'unverified' | 'noted' | 'answer';
  reason: string;
  message: string; // may contain $...$ math
  counterexample: Record<string, string>;
  values: Record<string, string>;
  compared_with: string | null;
}

export const api = {
  recognize: (body: { strokes: { t0: number; pts: number[][] }[]; image?: string; context: string[] }) => {
    const c = choices.get();
    return post<Reading>('/recognize', c ? { ...body, recognizer: c.recognizer, reader_model: c.reader } : body);
  },
  check: (body: { current: string; context: string[]; confirmed: true }) => {
    const c = choices.get();
    const withMode = { ...body, mode: feedbackMode.get() };
    return post<CheckResult>('/check', c ? { ...withMode, reasoning_model: c.reasoner } : withMode);
  },
};
