import { useEffect, useMemo, useRef, useState } from 'react';
import { getCandidates } from '../lib/api';
import { duration, timecode } from '../lib/format';
import type { Candidate, CandidateResponse, Clip } from '../lib/types';
import { EnergyMeter } from './EnergyMeter';

const MODE_NOTE: Record<string, string> = {
  embedding: 'ranked by embedding similarity',
  substring: 'ranked by word match — archive has no embeddings (run `mashup embed`)',
  browse: 'browsing the archive — type to search',
};

function seedQuery(clip: Clip): string {
  if (clip.summary.trim()) return clip.summary.trim();
  return clip.text.split(/\s+/).slice(0, 8).join(' ');
}

interface Props {
  clip: Clip;
  busy: boolean;
  onPick: (candidate: Candidate) => void;
  onClose: () => void;
}

export function ReplacePanel({ clip, busy, onPick, onClose }: Props) {
  const [query, setQuery] = useState(() => seedQuery(clip));
  const [data, setData] = useState<CandidateResponse | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    inputRef.current?.focus();
    inputRef.current?.select();
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    const timer = window.setTimeout(async () => {
      setLoading(true);
      try {
        setData(await getCandidates(query, 20, controller.signal));
        setError('');
      } catch (exc) {
        if (!controller.signal.aborted) setError(String((exc as Error).message));
      } finally {
        if (!controller.signal.aborted) setLoading(false);
      }
    }, 220);
    return () => {
      controller.abort();
      window.clearTimeout(timer);
    };
  }, [query]);

  const results = useMemo(
    () => (data?.results ?? []).filter((c) => c.id !== clip.segment_id),
    [data, clip.segment_id],
  );

  return (
    <section
      className="panel"
      aria-label={`Replace clip ${clip.index + 1}`}
      onKeyDown={(event) => {
        if (event.key === 'Escape') {
          event.stopPropagation();
          onClose();
        }
      }}
    >
      <div className="panel-head">
        <label className="panel-search">
          <span className="sr-only">Search the archive for a replacement</span>
          <input
            ref={inputRef}
            type="search"
            value={query}
            placeholder="search the archive…"
            onChange={(event) => setQuery(event.target.value)}
          />
        </label>
        <span className="panel-note">
          {loading ? 'searching…' : MODE_NOTE[data?.mode ?? ''] ?? ''}
        </span>
        <button type="button" className="btn" onClick={onClose}>
          close
        </button>
      </div>

      {error && <p className="error-inline">{error}</p>}
      {!error && !loading && results.length === 0 && (
        <p className="panel-empty">No candidates match “{query}”.</p>
      )}

      <ul className="candidates">
        {results.map((candidate) => (
          <li key={candidate.id} className="candidate">
            <div className="candidate-meta">
              <button
                type="button"
                className="btn btn-primary"
                disabled={busy}
                onClick={() => onPick(candidate)}
              >
                use
              </button>
              <span className="mono">{candidate.source_id}</span>
              <span className="mono">{timecode(candidate.start, candidate.end)}</span>
              <span className="mono dim">{duration(candidate.duration)}</span>
              <span className={`badge role role-${candidate.role}`}>{candidate.role}</span>
              <EnergyMeter value={candidate.energy} />
              {typeof candidate.relevance === 'number' && (
                <span className="mono dim">sim {candidate.relevance.toFixed(2)}</span>
              )}
            </div>
            {candidate.summary && <p className="candidate-summary">{candidate.summary}</p>}
            <p className="candidate-text">{candidate.text}</p>
          </li>
        ))}
      </ul>
    </section>
  );
}
