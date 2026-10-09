import { useEffect, useRef, useState } from 'react';
import { mediaUrl } from '../lib/api';
import { mmss } from '../lib/format';
import type { Clip } from '../lib/types';

/**
 * Plays exactly the clip's render window.
 *
 * The server streams the whole source file and honours Range, so seeking to
 * `render_start` costs one partial request rather than a transcode. Playback
 * is stopped at `render_end` on `timeupdate` — the browser fires it every
 * ~250ms, which is close enough for judging a cut.
 */
export function ClipPlayer({ clip }: { clip: Clip }) {
  const ref = useRef<HTMLVideoElement>(null);
  const [position, setPosition] = useState(clip.render_start);
  const [error, setError] = useState('');

  const src = mediaUrl(clip.source_id, clip.render_start, clip.render_end);
  const span = Math.max(0.001, clip.render_end - clip.render_start);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    setPosition(clip.render_start);
    // A source change resets the element; seek once metadata is known.
    if (el.readyState >= 1) {
      el.currentTime = clip.render_start;
    }
  }, [src, clip.render_start]);

  const start = () => {
    const el = ref.current;
    if (!el) return;
    el.currentTime = clip.render_start;
    void el.play().catch(() => setError('playback blocked by the browser'));
  };

  return (
    <div className="player">
      <video
        ref={ref}
        src={src}
        controls
        preload="metadata"
        playsInline
        onLoadedMetadata={(event) => {
          const el = event.currentTarget;
          el.currentTime = clip.render_start;
          void el.play().catch(() => undefined);
        }}
        onTimeUpdate={(event) => {
          const el = event.currentTarget;
          setPosition(el.currentTime);
          if (el.currentTime >= clip.render_end) {
            el.pause();
            el.currentTime = clip.render_end;
          }
        }}
        onError={() => setError('could not load media for this source')}
      />
      <div className="player-bar">
        <button type="button" className="btn" onClick={start}>
          ⟲ replay clip
        </button>
        <span className="player-pos" aria-live="off">
          {mmss(position)} / {mmss(clip.render_end)}
        </span>
        <span className="player-track" aria-hidden="true">
          <span
            className="player-fill"
            style={{
              width: `${Math.max(0, Math.min(1, (position - clip.render_start) / span)) * 100}%`,
            }}
          />
        </span>
      </div>
      {error && <p className="error-inline">{error}</p>}
    </div>
  );
}
