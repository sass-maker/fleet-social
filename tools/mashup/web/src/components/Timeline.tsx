import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { getEdl, getSegment, putEdl } from '../lib/api';
import { duration, signed } from '../lib/format';
import type { Candidate, Clip, EDL } from '../lib/types';
import { ClipCard, type PanelKind } from './ClipCard';
import { ScoreHeader } from './ScoreHeader';

const UNDO_DEPTH = 50;
const DRIFT_WARN = 0.1;

type StatusKind = 'idle' | 'busy' | 'ok' | 'error';
interface Status {
  kind: StatusKind;
  message: string;
}

function totalDuration(clips: Clip[]): number {
  return clips.reduce((sum, clip) => sum + (clip.render_end - clip.render_start), 0);
}

function appendNote(note: string | null, addition: string): string {
  return note ? `${note}; ${addition}` : addition;
}

function reindex(clips: Clip[]): Clip[] {
  return clips.map((clip, index) => (clip.index === index ? clip : { ...clip, index }));
}

export default function Timeline() {
  const [doc, setDoc] = useState<EDL | null>(null);
  const [undoStack, setUndoStack] = useState<EDL[]>([]);
  const [status, setStatus] = useState<Status>({ kind: 'busy', message: 'loading EDL…' });
  const [scoreMode, setScoreMode] = useState('partial');
  const [recomputed, setRecomputed] = useState<string[]>([]);
  const [selected, setSelected] = useState(0);
  const [panel, setPanel] = useState<{ index: number; kind: Exclude<PanelKind, null> } | null>(null);
  const [preview, setPreview] = useState<number | null>(null);
  const [fatal, setFatal] = useState('');

  const cards = useRef(new Map<number, HTMLLIElement>());
  const busy = status.kind === 'busy';

  useEffect(() => {
    getEdl()
      .then((loaded) => {
        setDoc(loaded);
        setStatus({ kind: 'idle', message: 'loaded' });
      })
      .catch((exc) => setFatal(String((exc as Error).message)));
  }, []);

  /** Save through the backend so the score comes back recomputed. */
  const save = useCallback(
    async (next: EDL, label: string, { undoable = true } = {}) => {
      const previous = doc;
      setDoc(next);
      setStatus({ kind: 'busy', message: `${label}…` });
      try {
        const result = await putEdl(next);
        setDoc(result.doc);
        setScoreMode(result.mode);
        setRecomputed(result.recomputed);
        if (undoable && previous) {
          setUndoStack((stack) => [...stack, previous].slice(-UNDO_DEPTH));
        }
        setStatus({ kind: 'ok', message: `${label} · saved` });
      } catch (exc) {
        // Roll back rather than leave the screen disagreeing with the file.
        setDoc(previous);
        setStatus({ kind: 'error', message: `${label} failed — ${(exc as Error).message}` });
      }
    },
    [doc],
  );

  const withClips = useCallback(
    (label: string, mutate: (clips: Clip[]) => Clip[]) => {
      // One edit in flight at a time: a second edit built on the pre-save
      // document would silently undo the first when it lands.
      if (!doc || busy) return;
      void save({ ...doc, clips: reindex(mutate(doc.clips)) }, label);
    },
    [doc, busy, save],
  );

  // ---- edit operations --------------------------------------------------

  const removeClip = useCallback(
    (index: number) => {
      if (!doc || !doc.clips[index]) return;
      setPanel(null);
      setPreview(null);
      setSelected(Math.max(0, Math.min(index, doc.clips.length - 2)));
      withClips(`removed clip ${index + 1}`, (clips) => clips.filter((_, i) => i !== index));
    },
    [doc, withClips],
  );

  const moveClip = useCallback(
    (index: number, delta: -1 | 1) => {
      if (!doc) return;
      const target = index + delta;
      if (target < 0 || target >= doc.clips.length) return;
      setSelected(target);
      setPanel(null);
      withClips(`moved clip ${index + 1} ${delta < 0 ? 'up' : 'down'}`, (clips) => {
        const next = [...clips];
        [next[index], next[target]] = [next[target], next[index]];
        return next;
      });
    },
    [doc, withClips],
  );

  const replaceClip = useCallback(
    async (index: number, candidate: Candidate) => {
      if (!doc) return;
      const old = doc.clips[index];
      if (!old) return;
      setStatus({ kind: 'busy', message: 'fetching full transcript…' });
      let text = candidate.text;
      try {
        // The candidate list carries an excerpt; the EDL must hold the whole
        // transcript, so fetch the segment before writing it in.
        text = (await getSegment(candidate.id)).segment.text;
      } catch {
        /* fall back to the excerpt rather than blocking the edit */
      }
      const replacement: Clip = {
        ...old,
        segment_id: candidate.id,
        segment_ids: [candidate.id],
        source_id: candidate.source_id,
        source_title: candidate.source_title,
        source_path: candidate.source_path,
        start: candidate.start,
        end: candidate.end,
        render_start: candidate.start,
        render_end: candidate.end,
        text,
        summary: candidate.summary,
        role: candidate.role,
        energy: candidate.energy,
        topics: [...candidate.topics],
        visuals: [],
        edited: true,
        note: appendNote(old.note, `replaced ${old.segment_id}`),
      };
      setPanel(null);
      withClips(`replaced clip ${index + 1}`, (clips) =>
        clips.map((clip, i) => (i === index ? replacement : clip)),
      );
    },
    [doc, withClips],
  );

  const extendClip = useCallback(
    (index: number, neighbour: Candidate, direction: 'prev' | 'next') => {
      if (!doc) return;
      const clip = doc.clips[index];
      if (!clip) return;
      const extended: Clip =
        direction === 'next'
          ? {
              ...clip,
              segment_ids: [...(clip.segment_ids || [clip.segment_id]), neighbour.id],
              end: neighbour.end,
              render_end: neighbour.end,
              text: `${clip.text}\n\n${neighbour.text}`,
            }
          : {
              ...clip,
              segment_ids: [neighbour.id, ...(clip.segment_ids || [clip.segment_id])],
              start: neighbour.start,
              render_start: neighbour.start,
              text: `${neighbour.text}\n\n${clip.text}`,
            };
      extended.energy = Math.max(clip.energy, neighbour.energy);
      extended.topics = Array.from(new Set([...clip.topics, ...neighbour.topics]));
      extended.edited = true;
      extended.note = appendNote(clip.note, `extended into ${neighbour.id}`);
      setPanel(null);
      withClips(`extended clip ${index + 1}`, (clips) =>
        clips.map((c, i) => (i === index ? extended : c)),
      );
    },
    [doc, withClips],
  );

  const undo = useCallback(() => {
    const previous = undoStack[undoStack.length - 1];
    if (!previous || busy) return;
    setUndoStack((stack) => stack.slice(0, -1));
    setPanel(null);
    void save(previous, 'undo', { undoable: false });
  }, [undoStack, busy, save]);

  const exportJson = useCallback(() => {
    if (!doc) return;
    const blob = new Blob([`${JSON.stringify(doc, null, 2)}\n`], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = `${doc.strategy}.json`;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    URL.revokeObjectURL(url);
    setStatus({ kind: 'ok', message: `exported ${doc.strategy}.json` });
  }, [doc]);

  // ---- keyboard ---------------------------------------------------------

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.metaKey || event.ctrlKey || event.altKey) return;
      const target = event.target as HTMLElement | null;
      if (target && /^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName)) return;
      if (!doc || doc.clips.length === 0) return;
      const last = doc.clips.length - 1;

      switch (event.key) {
        case 'j':
          setSelected((i) => Math.min(last, i + 1));
          break;
        case 'k':
          setSelected((i) => Math.max(0, i - 1));
          break;
        case 'J':
          moveClip(selected, 1);
          break;
        case 'K':
          moveClip(selected, -1);
          break;
        case 'x':
          removeClip(selected);
          break;
        case 'u':
          undo();
          break;
        case 'r':
          setPanel((p) => (p?.index === selected && p.kind === 'replace' ? null : { index: selected, kind: 'replace' }));
          break;
        case 'e':
          setPanel((p) => (p?.index === selected && p.kind === 'extend' ? null : { index: selected, kind: 'extend' }));
          break;
        case 'p':
          setPreview((p) => (p === selected ? null : selected));
          break;
        case 'Escape':
          setPanel(null);
          return;
        default:
          return;
      }
      event.preventDefault();
    }

    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [doc, selected, moveClip, removeClip, undo]);

  useEffect(() => {
    cards.current.get(selected)?.scrollIntoView({ block: 'nearest' });
  }, [selected]);

  // ---- render -----------------------------------------------------------

  const total = useMemo(() => (doc ? totalDuration(doc.clips) : 0), [doc]);

  if (fatal) {
    return <OfflineEditor detail={fatal} />;
  }

  if (!doc) return <p className="loading">loading…</p>;

  const drift = doc.target_duration > 0 ? (total - doc.target_duration) / doc.target_duration : 0;

  return (
    <div className="editor">
      <ScoreHeader
        doc={doc}
        totalDuration={total}
        scoreMode={scoreMode}
        recomputed={recomputed}
      />

      <div className="toolbar">
        <div className="toolbar-left">
          <span className={`pill ${Math.abs(drift) >= DRIFT_WARN ? 'pill-warn' : 'pill-ok'}`}>
            {duration(total)} / {duration(doc.target_duration)}
            <em>{signed(total - doc.target_duration)}</em>
          </span>
          {Math.abs(drift) >= DRIFT_WARN && (
            <span className="drift-note">
              {drift > 0 ? 'over' : 'under'} target by {Math.abs(drift * 100).toFixed(0)}%
            </span>
          )}
        </div>
        <div className="toolbar-right">
          <button type="button" className="btn" disabled={undoStack.length === 0 || busy} onClick={undo}>
            ↶ undo{undoStack.length > 0 ? ` (${undoStack.length})` : ''}
          </button>
          <button type="button" className="btn btn-primary" onClick={exportJson}>
            ⤓ export JSON
          </button>
        </div>
      </div>

      <p className={`status status-${status.kind}`} role="status" aria-live="polite">
        {status.message}
      </p>

      {doc.clips.length === 0 ? (
        <p className="panel-empty">
          Every clip has been removed. Press <kbd>u</kbd> to undo.
        </p>
      ) : (
        <ol className="timeline">
          {doc.clips.map((clip, index) => (
            <ClipCard
              key={`${clip.segment_id}-${index}`}
              ref={(node) => {
                if (node) cards.current.set(index, node);
                else cards.current.delete(index);
              }}
              clip={clip}
              position={index}
              total={doc.clips.length}
              selected={index === selected}
              busy={busy}
              panel={panel?.index === index ? panel.kind : null}
              previewing={preview === index}
              onSelect={() => setSelected(index)}
              onRemove={() => removeClip(index)}
              onMove={(delta) => moveClip(index, delta)}
              onTogglePanel={(kind) =>
                setPanel((p) => (p?.index === index && p.kind === kind ? null : { index, kind }))
              }
              onTogglePreview={() => setPreview((p) => (p === index ? null : index))}
              onReplace={(candidate) => void replaceClip(index, candidate)}
              onExtend={(neighbour, direction) => extendClip(index, neighbour, direction)}
            />
          ))}
        </ol>
      )}

      <footer className="shortcuts">
        <span><kbd>j</kbd>/<kbd>k</kbd> move between clips</span>
        <span><kbd>J</kbd>/<kbd>K</kbd> reorder</span>
        <span><kbd>x</kbd> remove</span>
        <span><kbd>r</kbd> replace</span>
        <span><kbd>e</kbd> extend</span>
        <span><kbd>p</kbd> preview</span>
        <span><kbd>u</kbd> undo</span>
        <span><kbd>Esc</kbd> close panel</span>
      </footer>
    </div>
  );
}

function OfflineEditor({ detail }: { detail: string }) {
  const previewClips = [
    {
      index: '01',
      role: 'setup',
      source: 'SOURCE 01 · 00:04:18–00:04:42',
      text: 'Open on the moment that names the topic and gives the compilation a reason to exist.',
      evidence: ['provenance linked', 'topic 0.91', 'opening 0.84'],
    },
    {
      index: '02',
      role: 'development',
      source: 'SOURCE 03 · 00:17:06–00:17:39',
      text: 'Build the idea with a distinct source beat, preserving enough context for the cut to feel authored rather than retrieved.',
      evidence: ['transcript linked', 'coherence 0.88', 'novelty 0.76'],
    },
    {
      index: '03',
      role: 'closer',
      source: 'SOURCE 02 · 00:31:12–00:31:29',
      text: 'Finish on a clean ending that resolves the topic without synthetic speech or an invented conclusion.',
      evidence: ['rights approved', 'closure 0.93', 'energy 0.81'],
    },
  ];

  return (
    <section className="editor offline-editor" aria-labelledby="offline-editor-title">
      <header className="offline-editor-head">
        <div>
          <p className="mono offline-label">Illustrative editor structure</p>
          <h2 id="offline-editor-title">The local EDL is not connected.</h2>
          <p>
            Mashup does not upload a placeholder archive. Start the loopback backend with an
            approved EDL to review real source clips, scores, and provenance here.
          </p>
        </div>
        <span className="pill pill-warn">backend offline</span>
      </header>

      <div className="offline-command">
        <span>Start a local review</span>
        <code>mashup serve output/chronological.json</code>
      </div>

      <div className="toolbar offline-toolbar" aria-hidden="true">
        <div className="toolbar-left">
          <span className="pill">01:42 / 02:00 <em>−00:18</em></span>
          <span className="drift-note">illustrative values only</span>
        </div>
        <div className="toolbar-right">
          <button type="button" className="btn" disabled>↶ undo</button>
          <button type="button" className="btn btn-primary" disabled>⤓ export JSON</button>
        </div>
      </div>

      <ol className="timeline offline-timeline" aria-label="Illustrative inspectable edit">
        {previewClips.map((clip) => (
          <li key={clip.index} className="card">
            <div className="card-rail"><span className="card-index">{clip.index}</span></div>
            <div className="card-body">
              <div className="card-head">
                <span className={`badge role-${clip.role}`}>{clip.role}</span>
                <span className="source mono">{clip.source}</span>
              </div>
              <p className="card-text">{clip.text}</p>
              <div className="card-foot">
                {clip.evidence.map((item) => <span key={item} className="tag">{item}</span>)}
              </div>
            </div>
          </li>
        ))}
      </ol>

      <details className="offline-detail">
        <summary>Connection detail</summary>
        <p>{detail}</p>
      </details>
    </section>
  );
}
