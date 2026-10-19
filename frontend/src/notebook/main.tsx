import { useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { Library } from './Library';
import { Notebook } from './Notebook';
import { getNotebook } from './library';
import './app.css';

// Two screens, routed by the URL hash: "#" (library) and "#/nb/<id>" (a notebook).
function useHash() {
  const [hash, setHash] = useState(location.hash);
  useEffect(() => {
    const on = () => setHash(location.hash);
    addEventListener('hashchange', on);
    return () => removeEventListener('hashchange', on);
  }, []);
  return hash;
}

function App() {
  const id = useHash().match(/^#\/nb\/([\w-]+)$/)?.[1];
  const nb = id ? getNotebook(id) : undefined;
  // key: remount tldraw when switching notebooks, so each loads its own store.
  return nb ? <Notebook key={nb.id} nb={nb} /> : <Library />;
}

createRoot(document.getElementById('root')!).render(<App />);
