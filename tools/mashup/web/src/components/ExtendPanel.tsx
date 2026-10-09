import { useEffect, useState } from 'react';
import { getSegment } from '../lib/api';
import { duration, timecode } from '../lib/format';
import type { Candidate, Clip, SegmentDetail } from '../lib/types';

interface Props {
  clip: Clip;
  busy: boolean;
  onExtend: (neighbour: Candidate, direction: 'prev' | 'next') => void;
  onClose: () => void;
}

/** Pull the adjacent segment from the same source into this clip. */
export function ExtendPanel({ clip, busy, onExtend, onClose }: Props) {
  const [detail, setDetail] = useState<SegmentDetail | null>(null);
  const [error, setError] = useState('');

  useEffect(() => {
    let live = true;
    getSegment(clip.segment_id)
      .then((data) => live && setDetail(data))
      .catch((exc) => live && setError(String((exc as Error).message)));
    return () => {
      live = false;
    };
  }, [clip.segment_id]);

  const options: { neighbour: Candidate; direction: 'prev' | 'next' }[] = [];
  if (detail?.prev) options.push({ neighbour: detail.prev, direction: 'prev' });
  if (detail?.next) options.push({ neighbour: detail.next, direction: 'next' });

  return (
    <section
      className="panel"
      aria-label={`Extend clip ${clip.index + 1}`}
      onKeyDown={(event) => {
        if (event.key === 'Escape') {
          event.stopPropagation();
          onClose();
        }
      }}
    >
      <div className="panel-head">
        <span className="panel-note">
          Adjacent material in{" "}
          <span className="mono">{clip.source_title || clip.source_id}</span>
        </span>
        <button type="button" className="btn" onClick={onClose}>
          close
        </button>
      </div>

      {error && <p className="error-inline">{error}</p>}
      {!error && !detail && <p className="panel-empty">loading neighbours…</p>}
      {detail && options.length === 0 && (
        <p className="panel-empty">
          This segment sits at the edge of its source — nothing to extend into.
        </p>
      )}

      <ul className="candidates">
        {options.map(({ neighbour, direction }) => (
          <li key={neighbour.id} className="candidate">
            <div className="candidate-meta">
              <button
                type="button"
                className="btn btn-primary"
                disabled={busy}
                onClick={() => onExtend(neighbour, direction)}
              >
                {direction === 'prev' ? '↑ extend before' : '↓ extend after'}
              </button>
              <span className="mono">{timecode(neighbour.start, neighbour.end)}</span>
              <span className="mono dim">+{duration(neighbour.duration)}</span>
              <span className={`badge role role-${neighbour.role}`}>{neighbour.role}</span>
            </div>
            <p className="candidate-text">{neighbour.text}</p>
          </li>
        ))}
      </ul>
    </section>
  );
}
