import { useState } from 'react';
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
import { CheckUI, ReadingsOverlay } from './CheckUI';
import { FeedbackShapeUtil, LegacySceneShapeUtil } from './feedback';
import { recordInk } from './ink';
import { showReadings, startLiveReading } from './readings';
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
  const readingsOn = useValue(showReadings);

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
      <button className={readingsOn ? 'on' : ''} onClick={() => showReadings.set(!readingsOn)}
        title="Show what the board reads under each line">
        Readings {readingsOn ? 'on' : 'off'}
      </button>
    </div>
  );
}

export function Notebook({ nb }: { nb: NotebookMeta }) {
  const [components] = useState<TLComponents>(() => ({
    PageMenu: null, // one tldraw page; our paper pages are shapes
    Toolbar,
    TopPanel: () => <TopBar nb={nb} />,
    OnTheCanvas: ReadingsOverlay,
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
          const stopReading = startLiveReading(editor);
          // Bump the library's "last edited" time, at most every few seconds.
          let last = 0;
          const stopListen = editor.store.listen(() => {
            if (Date.now() - last < 5000) return;
            last = Date.now();
            updateNotebook(nb.id, { updatedAt: last });
          }, { source: 'user', scope: 'document' });
          if (import.meta.env.DEV) (window as unknown as { editor: typeof editor }).editor = editor;
          return () => { stopPages(); stopInk(); stopReading(); stopListen(); };
        }}
      />
    </div>
  );
}
