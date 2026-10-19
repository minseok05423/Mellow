// The notebook library: a small index in localStorage. Each notebook's content lives in its own
// tldraw IndexedDB database, keyed by `persistenceKey(id)`.
// shortcut: browser-only storage. Upgrade path: server storage + sync in P4 (SPEC §6).

export interface NotebookMeta {
  id: string;
  title: string;
  createdAt: number;
  updatedAt: number;
}

const KEY = 'mellow.notebooks.v1';

export const persistenceKey = (id: string) => `mellow-nb-${id}`;

export function listNotebooks(): NotebookMeta[] {
  try {
    const list = JSON.parse(localStorage.getItem(KEY) ?? '[]') as NotebookMeta[];
    return list.sort((a, b) => b.updatedAt - a.updatedAt);
  } catch {
    return [];
  }
}

function save(list: NotebookMeta[]) {
  localStorage.setItem(KEY, JSON.stringify(list));
}

export function getNotebook(id: string) {
  return listNotebooks().find((n) => n.id === id);
}

export function createNotebook(title: string): NotebookMeta {
  const now = Date.now();
  const nb = { id: crypto.randomUUID().slice(0, 8), title, createdAt: now, updatedAt: now };
  save([...listNotebooks(), nb]);
  return nb;
}

export function updateNotebook(id: string, patch: Partial<Pick<NotebookMeta, 'title' | 'updatedAt'>>) {
  save(listNotebooks().map((n) => (n.id === id ? { ...n, ...patch } : n)));
}

export function deleteNotebook(id: string) {
  save(listNotebooks().filter((n) => n.id !== id));
  // shortcut: relies on tldraw's internal IndexedDB name (TLDRAW_DOCUMENT_v2 + persistenceKey); tldraw
  // exports no delete API. Upgrade path: goes away with server storage in P4.
  indexedDB.deleteDatabase(`TLDRAW_DOCUMENT_v2${persistenceKey(id)}`);
}
