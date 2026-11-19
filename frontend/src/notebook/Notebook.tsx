import { useRef, useState } from 'react';
import {
  DefaultToolbar,
  DrawToolbarItem,
  EraserToolbarItem,
  HandToolbarItem,
  SelectToolbarItem,
  TLComponents,
  TLUiOverrides,
  Tldraw,
  TldrawUiMenuItem,
  useEditor,
  useIsToolSelected,
  useTools,
  useValue,
} from 'tldraw';
import 'katex/dist/katex.min.css';
import 'katex/contrib/copy-tex'; // copying rendered math gives its LaTeX source
import 'tldraw/tldraw.css';
import { api } from './api';
import { CheckUI } from './CheckUI';
import { FeedbackShapeUtil, LegacySceneShapeUtil } from './feedback';
import { recordInk } from './ink';
import { LassoOverlayUtil, LassoSelectTool } from './lasso';
import { NotebookMeta, persistenceKey, updateNotebook } from './library';
import { PAPERS, PageShapeUtil, Paper, addPage, getPageInView, installPages, setPaper } from './pages';

// Module-level constants, so <Tldraw> doesn't see new objects on every render.
const shapeUtils = [PageShapeUtil, FeedbackShapeUtil, LegacySceneShapeUtil];
const tools = [LassoSelectTool];
const overlayUtils = [LassoOverlayUtil];

const overrides: TLUiOverrides = {
  tools(editor, tools) {
    tools['lasso-select'] = {
      id: 'lasso-select',
      icon: 'blob',
      label: 'Lasso select',
      kbd: 'w',
      onSelect: () => editor.setCurrentTool('lasso-select'),
    };
    return tools;
  },
};

/** Only what a handwritten proof needs: pen, eraser, lasso (to select lines for Lean), select and hand. */
function Toolbar() {
  const tools = useTools();
  const lassoSelected = useIsToolSelected(tools['lasso-select']);
  return (
    <DefaultToolbar>
      <DrawToolbarItem />
      <EraserToolbarItem />
      <TldrawUiMenuItem {...tools['lasso-select']} isSelected={lassoSelected} />
      <SelectToolbarItem />
      <HandToolbarItem />
    </DefaultToolbar>
  );
}

function TopBar({ nb }: { nb: NotebookMeta }) {
  const editor = useEditor();
  const [title, setTitle] = useState(nb.title);
  const page = useValue('page in view', () => getPageInView(editor), [editor]);
  const fileRef = useRef<HTMLInputElement>(null);

  return (
    <div className="topbar">
      <a href="#" className="back" title="All notebooks">←</a>
      <input
        className="title"
        value={title}
        aria-label="Notebook title"
        onChange={(e) => setTitle(e.target.value)}
        onBlur={() => title.trim() && updateNotebook(nb.id, { title: title.trim() })}
        onKeyDown={(e) => e.key === 'Enter' && (e.target as HTMLInputElement).blur()}
      />
      <button onClick={() => addPage(editor)} title="Add a page at the end">+ Page</button>
      {page && (
        <select
          aria-label="Paper"
          value={page.props.paper}
          onChange={(e) => setPaper(editor, page, e.target.value as Paper)}
        >
          {PAPERS.map((p) => <option key={p} value={p}>{p}</option>)}
        </select>
      )}
      {/* A screenshot or photo (a problem sheet, typed work) goes on the page selected, ready for Check my proof. */}
      <button onClick={() => fileRef.current?.click()} title="Add a screenshot or photo to the page">Image</button>
      <input ref={fileRef} type="file" accept="image/*" hidden onChange={async (e) => {
        const files = [...(e.target.files ?? [])];
        e.target.value = '';
        if (!files.length) return;
        await editor.putExternalContent({ type: 'files', files, point: editor.getViewportPageBounds().center });
        editor.setCurrentTool('select');
      }} />
      <button title="Tell us what worked or didn't" onClick={() => {
        const text = window.prompt('What worked, what didn\'t, what confused you?')?.trim();
        if (text) api.event('feedback', '', { text: text.slice(0, 3000) });
      }}>Feedback</button>
    </div>
  );
}

export function Notebook({ nb }: { nb: NotebookMeta }) {
  const [components] = useState<TLComponents>(() => ({
    PageMenu: null, // one tldraw page; our paper pages are shapes
    Toolbar,
    TopPanel: () => <TopBar nb={nb} />,
    InFrontOfTheCanvas: CheckUI,
  }));

  return (
    <div className="notebook">
      <Tldraw
        persistenceKey={persistenceKey(nb.id)}
        licenseKey={import.meta.env.VITE_TLDRAW_LICENSE_KEY}
        shapeUtils={shapeUtils}
        tools={tools}
        overlayUtils={overlayUtils}
        overrides={overrides}
        components={components}
        onMount={(editor) => {
          editor.setCurrentTool('draw');
          const stopPages = installPages(editor);
          const stopInk = recordInk(editor);
          // Bump the library's "last edited" time, at most every few seconds.
          let last = 0;
          const stopListen = editor.store.listen(() => {
            if (Date.now() - last < 5000) return;
            last = Date.now();
            updateNotebook(nb.id, { updatedAt: last });
          }, { source: 'user', scope: 'document' });
          if (import.meta.env.DEV) (window as unknown as { editor: typeof editor }).editor = editor;
          return () => { stopPages(); stopInk(); stopListen(); };
        }}
      />
    </div>
  );
}
