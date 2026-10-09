import type { EDL } from '../lib/types';
import { TERM_KEYS } from '../lib/types';
import { driftRatio, duration, signed, termLabel } from '../lib/format';

const DRIFT_WARN = 0.1;
const DRIFT_BAD = 0.25;

interface Props {
  doc: EDL;
  totalDuration: number;
  scoreMode: string;
  recomputed: string[];
}

export function ScoreHeader({ doc, totalDuration, scoreMode, recomputed }: Props) {
  const drift = driftRatio(totalDuration, doc.target_duration);
  const level =
    Math.abs(drift) >= DRIFT_BAD ? 'bad' : Math.abs(drift) >= DRIFT_WARN ? 'warn' : 'ok';
  const stale = scoreMode !== 'full';

  return (
    <header className="header">
      <div className="header-main">
        <div className="header-brief">
          <span className="badge strategy">{doc.strategy}</span>
          <h1 title={doc.prompt}>{doc.prompt}</h1>
          <p className="meta">
            {doc.clips.length} clips · generated {doc.generated_at.replace('T', ' ')}
          </p>
        </div>

        <dl className="stats">
          <div className={`stat drift-${level}`}>
            <dt>duration</dt>
            <dd>
              {duration(totalDuration)}
              <span className="stat-sub">
                target {duration(doc.target_duration)} ({signed(totalDuration - doc.target_duration)})
              </span>
            </dd>
          </div>
          <div className="stat">
            <dt>score</dt>
            <dd>
              {doc.score.toFixed(3)}
              <span className="stat-sub">
                {stale ? 'partial rescore' : 'full rescore'}
              </span>
            </dd>
          </div>
        </dl>
      </div>

      <ul className="terms" aria-label="Score breakdown">
        {TERM_KEYS.map((key) => {
          const value = doc.terms[key] ?? 0;
          const weight = doc.weights[key] ?? 0;
          const live = recomputed.length === 0 || recomputed.includes(key);
          return (
            <li key={key} className={live ? 'term' : 'term term-stale'}>
              <span className="term-label">
                {termLabel(key)}
                {weight > 0 && <em className="term-weight">×{weight.toFixed(2)}</em>}
              </span>
              <span
                className="term-bar"
                role="meter"
                aria-valuenow={Number(value.toFixed(2))}
                aria-valuemin={0}
                aria-valuemax={1}
                aria-label={termLabel(key)}
                title={
                  live
                    ? `${termLabel(key)} = ${value.toFixed(3)}`
                    : `${termLabel(key)} = ${value.toFixed(3)} (carried over — needs embeddings to recompute)`
                }
              >
                <span className="term-fill" style={{ width: `${Math.max(0, Math.min(1, value)) * 100}%` }} />
              </span>
              <span className="term-value">{value.toFixed(2)}</span>
            </li>
          );
        })}
      </ul>

      {doc.rationale.length > 0 && (
        <details className="rationale">
          <summary>Planner rationale ({doc.rationale.length})</summary>
          <ul>
            {doc.rationale.map((line, i) => (
              <li key={i}>{line}</li>
            ))}
          </ul>
        </details>
      )}
    </header>
  );
}
