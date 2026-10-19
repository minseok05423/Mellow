import { useEffect, useState } from 'react';
import {
  ArrowToolbarItem,
  AssetToolbarItem,
  DefaultToolbar,
  DrawToolbarItem,
  EllipseToolbarItem,
  EraserToolbarItem,
  HandToolbarItem,
  HighlightToolbarItem,
  LineToolbarItem,
  RectangleToolbarItem,
  SelectToolbarItem,
  TLComponents,
  TLUiOverrides,
  TextToolbarItem,
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
import { FeedbackShapeUtil } from './feedback';
import { recordInk } from './ink';
import {
  Catalog, Choices, ModelOption, catalog, choices, loadModels, setChoice,
} from './models';
import { showReadings, startLiveReading } from './readings';
import { LassoOverlayUtil, LassoSelectTool } from './lasso';
import { NotebookMeta, persistenceKey, updateNotebook } from './library';
import { PAPERS, PageShapeUtil, Paper, addPage, getPageInView, installPages, setPaper } from './pages';
import { SampleKind, countSamples, downloadSamples, saveSample, selectedInkShapes } from './samples';

// Module-level constants, so <Tldraw> doesn't see new objects on every render.
const shapeUtils = [PageShapeUtil, FeedbackShapeUtil];
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

/** GoodNotes-like tool set: pen, highlighter, eraser, lasso first; shapes, text and images after. */
function Toolbar() {
  const tools = useTools();
  const lassoSelected = useIsToolSelected(tools['lasso-select']);
  return (
    <DefaultToolbar>
      <DrawToolbarItem />
      <HighlightToolbarItem />
      <EraserToolbarItem />
      <TldrawUiMenuItem {...tools['lasso-select']} isSelected={lassoSelected} />
      <SelectToolbarItem />
      <HandToolbarItem />
      <TextToolbarItem />
      <RectangleToolbarItem />
      <EllipseToolbarItem />
      <LineToolbarItem />
      <ArrowToolbarItem />
      <AssetToolbarItem />
    </DefaultToolbar>
  );
}

const MODEL_ROWS: { kind: keyof Choices; group: keyof Omit<Catalog, 'defaults'>; label: string; hint: string }[] = [
  { kind: 'recognizer', group: 'recognizers', label: 'Handwriting reader', hint: 'Reads your strokes, live' },
  { kind: 'reader', group: 'readers', label: 'Image reader (fallback)', hint: 'Re-reads prose and unsure lines; labels the page' },
  { kind: 'reasoner', group: 'reasoners', label: 'Reasoning', hint: 'Check, Hint, Answer and Explain' },
];

function optionLabel(o: ModelOption) {
  return [o.label, o.price && `${o.price} per M tokens`, !o.available && 'no key'].filter(Boolean).join(' · ');
}

/** Lets the student choose which recognizer and models the board uses. */
function ModelsMenu() {
  const cat = useValue(catalog);
  const ch = useValue(choices);
  const [open, setOpen] = useState(false);
  if (!cat || !ch) return null;
  return (
    <span className="models">
      <button className={open ? 'on' : ''} onClick={() => setOpen(!open)} title="Choose models">Models ▾</button>
      {open && (
        <div className="models-panel">
          {MODEL_ROWS.map((r) => {
            const current = cat[r.group].find((o) => o.id === ch[r.kind]);
            return (
              <label key={r.kind} className="models-row">
                <span className="models-label">{r.label}<small>{r.hint}</small></span>
                <select aria-label={r.label} value={ch[r.kind]} onChange={(e) => setChoice(r.kind, e.target.value)}>
                  {cat[r.group].map((o) => (
                    <option key={o.id} value={o.id} disabled={!o.available}>{optionLabel(o)}</option>
                  ))}
                </select>
                {current?.note && <small className="models-note">{current.note}</small>}
              </label>
            );
          })}
          <small className="models-foot">Applies to lines read from now on. Prices: input / output.</small>
        </div>
      )}
    </span>
  );
}

function TopBar({ nb }: { nb: NotebookMeta }) {
  const editor = useEditor();
  const [title, setTitle] = useState(nb.title);
  const page = useValue('page in view', () => getPageInView(editor), [editor]);
  const inkSelected = useValue('ink selected', () => selectedInkShapes(editor).length, [editor]);
  const [sampleForm, setSampleForm] = useState(false);
  const [truth, setTruth] = useState('');
  const [kind, setKind] = useState<SampleKind>('math');
  const [sampleCount, setSampleCount] = useState(0);
  const [status, setStatus] = useState('');
  const readingsOn = useValue(showReadings);

  useEffect(() => { countSamples().then(setSampleCount); }, []);

  async function onSaveSample() {
    try {
      await saveSample(editor, nb.id, truth.trim(), kind);
      setSampleCount(await countSamples());
      setStatus('Sample saved');
      setSampleForm(false);
      setTruth('');
    } catch (e) {
      setStatus(String((e as Error).message));
    }
  }

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
      <ModelsMenu />
      <span className="sep" />
      {/* Bake-off tooling (SPEC §7): record handwritten lines with their expected reading. */}
      {sampleForm ? (
        <form className="sample-form" onSubmit={(e) => { e.preventDefault(); onSaveSample(); }}>
          <input
            autoFocus
            placeholder="Expected LaTeX, e.g. 2q^2=p^2"
            value={truth}
            onChange={(e) => setTruth(e.target.value)}
          />
          <select value={kind} onChange={(e) => setKind(e.target.value as SampleKind)} aria-label="Sample kind">
            <option value="math">math</option>
            <option value="mixed">mixed prose</option>
            <option value="messy">messy</option>
          </select>
          <button type="submit" disabled={!truth.trim()}>Save</button>
          <button type="button" onClick={() => setSampleForm(false)}>Cancel</button>
        </form>
      ) : (
        <button
          disabled={inkSelected === 0}
          title={inkSelected ? 'Save the selected line as a bake-off sample' : 'Lasso a handwritten line first'}
          onClick={() => { setStatus(''); setSampleForm(true); }}
        >
          Save sample
        </button>
      )}
      <button onClick={downloadSamples} disabled={sampleCount === 0} title="Download all samples as JSON">
        Samples ({sampleCount}) ⤓
      </button>
      {status && <span className="status">{status}</span>}
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
          loadModels();
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
