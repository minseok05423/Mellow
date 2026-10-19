import { useState } from 'react';
import { createNotebook, deleteNotebook, listNotebooks, updateNotebook } from './library';

const fmt = new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' });

export function Library() {
  const [notebooks, setNotebooks] = useState(listNotebooks);
  const refresh = () => setNotebooks(listNotebooks());

  function onCreate() {
    const nb = createNotebook(`Notebook ${notebooks.length + 1}`);
    location.hash = `#/nb/${nb.id}`;
  }

  function onRename(id: string, title: string) {
    const next = prompt('Rename notebook', title)?.trim();
    if (next) { updateNotebook(id, { title: next }); refresh(); }
  }

  function onDelete(id: string, title: string) {
    if (confirm(`Delete "${title}"? This cannot be undone.`)) { deleteNotebook(id); refresh(); }
  }

  return (
    <main className="library">
      <header>
        <h1>Notebooks</h1>
        <button className="primary" onClick={onCreate}>+ New notebook</button>
      </header>
      {notebooks.length === 0 && <p className="empty">No notebooks yet. Create one to start writing.</p>}
      <ul>
        {notebooks.map((nb) => (
          <li key={nb.id}>
            <a href={`#/nb/${nb.id}`} className="cover">
              <span className="cover-title">{nb.title}</span>
              <span className="cover-date">Edited {fmt.format(nb.updatedAt)}</span>
            </a>
            <div className="actions">
              <button onClick={() => onRename(nb.id, nb.title)}>Rename</button>
              <button onClick={() => onDelete(nb.id, nb.title)}>Delete</button>
            </div>
          </li>
        ))}
      </ul>
    </main>
  );
}
