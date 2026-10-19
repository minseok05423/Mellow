// The student's model choices (Models menu). Saved per browser; sent with each request; the backend
// only accepts models it has keys for, so this never exposes a key or bypasses the daily caps.

import { atom } from 'tldraw';

export interface ModelOption {
  id: string;
  label: string;
  available: boolean;
  price?: string;
  note?: string;
}

export interface Catalog {
  recognizers: ModelOption[];
  readers: ModelOption[];
  reasoners: ModelOption[];
  defaults: { recognizer: string; reader: string; reasoner: string };
}

export type Choices = Catalog['defaults'];

const KEY = 'mellow.models.v1';

export const catalog = atom<Catalog | null>('model catalog', null);
export const choices = atom<Choices | null>('model choices', null);

/** Saved choice if still available, else the server default. */
function pick(options: ModelOption[], saved: string | undefined, fallback: string) {
  return options.find((o) => o.id === saved && o.available)?.id ?? fallback;
}

export async function loadModels() {
  try {
    const c: Catalog = await fetch('/api/models').then((r) => r.json());
    let saved: Partial<Choices> = {};
    try { saved = JSON.parse(localStorage.getItem(KEY) ?? '{}'); } catch { /* ignore a corrupt entry */ }
    catalog.set(c);
    choices.set({
      recognizer: pick(c.recognizers, saved.recognizer, c.defaults.recognizer),
      reader: pick(c.readers, saved.reader, c.defaults.reader),
      reasoner: pick(c.reasoners, saved.reasoner, c.defaults.reasoner),
    });
  } catch {
    // Backend down: requests go out without choices and the server defaults apply.
  }
}

export function setChoice(kind: keyof Choices, id: string) {
  const next = { ...choices.get()!, [kind]: id };
  choices.set(next);
  localStorage.setItem(KEY, JSON.stringify(next));
}

/** What the student wants for proof steps in words: a guiding question, or the AI's direct answer. */
export type FeedbackMode = 'hint' | 'answer';
const MODE_KEY = 'mellow.feedback.v1';
const savedMode = (() => { try { return localStorage.getItem(MODE_KEY); } catch { return null; } })();
export const feedbackMode = atom<FeedbackMode>('feedback mode', savedMode === 'answer' ? 'answer' : 'hint');

export function setFeedbackMode(mode: FeedbackMode) {
  feedbackMode.set(mode);
  try { localStorage.setItem(MODE_KEY, mode); } catch { /* not saved; still applies this session */ }
}
