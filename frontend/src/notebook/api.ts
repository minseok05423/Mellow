// Calls to our backend (via Vite's /api proxy). The browser never talks to an LLM or recognition vendor.

import { choices } from './models';

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
  // verified / refuted / unverified come from SymPy; noted: nothing to judge (or no model);
  // ai_check / hint / answer / explain come from the reasoning model and are labelled as the AI's
  verdict: 'verified' | 'refuted' | 'unverified' | 'noted' | 'ai_check' | 'hint' | 'answer' | 'explain';
  reason: string;
  message: string; // may contain $...$ math
  counterexample: Record<string, string>;
  values: Record<string, string>;
  compared_with: string | null;
}

export type Role = 'problem' | 'definition' | 'claim' | 'proof_step' | 'algebra' | 'concept' | 'note';
export interface Structure { lines: { role: Role; segment: number }[]; segments: string[] }

export type Kind = 'problem' | 'steps' | 'concept';
export interface AssistBody {
  action: 'hint' | 'answer' | 'explain';
  kind: Kind;
  selection: string;
  problem: string | null;
  work: string[];
  definitions: string[];
  confirmed: true;
}

const reasoner = () => choices.get()?.reasoner;

export const api = {
  recognize: (body: { strokes: { t0: number; pts: number[][] }[]; image?: string; context: string[] }) => {
    const c = choices.get();
    return post<Reading>('/recognize', c ? { ...body, recognizer: c.recognizer, reader_model: c.reader } : body);
  },
  check: (body: { current: string; context: string[]; problem: string | null; confirmed: true }) =>
    post<CheckResult>('/check', { ...body, reasoning_model: reasoner() }),
  assist: (body: AssistBody) => post<CheckResult>('/assist', { ...body, reasoning_model: reasoner() }),
  structure: (lines: string[]) => post<Structure>('/structure', { lines, reader_model: choices.get()?.reader }),
};
