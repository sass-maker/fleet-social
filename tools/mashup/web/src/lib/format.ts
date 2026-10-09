/** mm:ss, matching `edl_to_transcript` in the Python renderer. */
export function mmss(seconds: number): string {
  const total = Math.max(0, Math.round(seconds));
  const mins = Math.floor(total / 60);
  const secs = total % 60;
  return `${String(mins).padStart(2, '0')}:${String(secs).padStart(2, '0')}`;
}

export function timecode(start: number, end: number): string {
  return `${mmss(start)} – ${mmss(end)}`;
}

export function duration(seconds: number): string {
  const value = Math.max(0, seconds);
  if (value < 60) return `${value.toFixed(0)}s`;
  return `${Math.floor(value / 60)}m ${String(Math.round(value % 60)).padStart(2, '0')}s`;
}

export function signed(seconds: number): string {
  const sign = seconds >= 0 ? '+' : '−';
  return `${sign}${duration(Math.abs(seconds))}`;
}

/** Percentage of target, used for the drift readout. */
export function driftRatio(total: number, target: number): number {
  if (target <= 0) return 0;
  return (total - target) / target;
}

export function termLabel(key: string): string {
  return key.replace(/_/g, ' ');
}
