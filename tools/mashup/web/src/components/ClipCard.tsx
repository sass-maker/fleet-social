import { forwardRef } from 'react';
import { duration, timecode } from '../lib/format';
import type { Candidate, Clip } from '../lib/types';
import { ClipPlayer } from './ClipPlayer';
import { EnergyMeter } from './EnergyMeter';
import { ExtendPanel } from './ExtendPanel';
import { ReplacePanel } from './ReplacePanel';

export type PanelKind = 'replace' | 'extend' | null;

interface Props {
  clip: Clip;
  position: number;
  total: number;
  selected: boolean;
  busy: boolean;
  panel: PanelKind;
  previewing: boolean;
  onSelect: () => void;
  onRemove: () => void;
  onMove: (delta: -1 | 1) => void;
  onTogglePanel: (kind: Exclude<PanelKind, null>) => void;
  onTogglePreview: () => void;
  onReplace: (candidate: Candidate) => void;
  onExtend: (neighbour: Candidate, direction: 'prev' | 'next') => void;
}

export const ClipCard = forwardRef<HTMLLIElement, Props>(function ClipCard(props, ref) {
  const {
    clip,
    position,
    total,
    selected,
    busy,
    panel,
    previewing,
    onSelect,
    onRemove,
    onMove,
    onTogglePanel,
    onTogglePreview,
    onReplace,
    onExtend,
  } = props;
  const source = clip.source_title || clip.source_id;

  return (
    <li
      ref={ref}
      className={`card${selected ? ' is-selected' : ''}${clip.edited ? ' is-edited' : ''}`}
      tabIndex={0}
      aria-current={selected ? 'true' : undefined}
      aria-label={`Clip ${position + 1} of ${total}, source ${source} at ${timecode(
        clip.start,
        clip.end,
      )}`}
      onFocus={onSelect}
      onClick={onSelect}
    >
      <div className="card-rail">
        <span className="card-index">{String(position + 1).padStart(2, '0')}</span>
        <div className="card-move">
          <button
            type="button"
            className="btn btn-icon"
            title="Move up (Shift+K)"
            aria-label={`Move clip ${position + 1} up`}
            disabled={busy || position === 0}
            onClick={() => onMove(-1)}
          >
            ↑
          </button>
          <button
            type="button"
            className="btn btn-icon"
            title="Move down (Shift+J)"
            aria-label={`Move clip ${position + 1} down`}
            disabled={busy || position === total - 1}
            onClick={() => onMove(1)}
          >
            ↓
          </button>
        </div>
      </div>

      <div className="card-body">
        <div className="card-head">
          <span className="mono source" title={`Source: ${clip.source_id}`}>
            {source}
          </span>
          <span className="mono time" title="Original source timecode">
            {timecode(clip.start, clip.end)}
          </span>
          <span className="mono dim">{duration(clip.render_end - clip.render_start)}</span>
          <span className={`badge role role-${clip.role}`}>{clip.role}</span>
          <EnergyMeter value={clip.energy} />
          {clip.transition === 'crossfade' && <span className="badge dimbadge">crossfade</span>}
          {clip.edited && <span className="badge edited">edited</span>}
          <span className="spacer" />
          <div className="card-actions">
            <button
              type="button"
              className={previewing ? 'btn btn-on' : 'btn'}
              title="Preview this clip (p)"
              aria-pressed={previewing}
              onClick={onTogglePreview}
            >
              ▶ preview
            </button>
            <button
              type="button"
              className={panel === 'replace' ? 'btn btn-on' : 'btn'}
              title="Find a replacement (r)"
              aria-pressed={panel === 'replace'}
              onClick={() => onTogglePanel('replace')}
            >
              ⇄ replace
            </button>
            <button
              type="button"
              className={panel === 'extend' ? 'btn btn-on' : 'btn'}
              title="Extend into an adjacent segment (e)"
              aria-pressed={panel === 'extend'}
              onClick={() => onTogglePanel('extend')}
            >
              ⇥ extend
            </button>
            <button
              type="button"
              className="btn btn-danger"
              title="Remove this clip (x)"
              aria-label={`Remove clip ${position + 1}`}
              disabled={busy}
              onClick={onRemove}
            >
              ✕ remove
            </button>
          </div>
        </div>

        {clip.summary && <p className="card-summary">{clip.summary}</p>}
        <p className="card-text">{clip.text}</p>

        {(clip.topics.length > 0 || clip.note) && (
          <p className="card-foot">
            {clip.topics.map((topic) => (
              <span key={topic} className="tag">
                {topic}
              </span>
            ))}
            {clip.note && <span className="note">{clip.note}</span>}
          </p>
        )}

        {previewing && <ClipPlayer clip={clip} />}
        {panel === 'replace' && (
          <ReplacePanel
            clip={clip}
            busy={busy}
            onPick={onReplace}
            onClose={() => onTogglePanel('replace')}
          />
        )}
        {panel === 'extend' && (
          <ExtendPanel
            clip={clip}
            busy={busy}
            onExtend={onExtend}
            onClose={() => onTogglePanel('extend')}
          />
        )}
      </div>
    </li>
  );
});
