const STEPS = 5;

/** Five-dot energy readout. Text alternative lives in aria-label. */
export function EnergyMeter({ value }: { value: number }) {
  const filled = Math.round(Math.max(0, Math.min(1, value)) * STEPS);
  return (
    <span
      className="energy"
      role="meter"
      aria-valuenow={Number(value.toFixed(2))}
      aria-valuemin={0}
      aria-valuemax={1}
      aria-label={`energy ${value.toFixed(2)}`}
      title={`energy ${value.toFixed(2)}`}
    >
      {Array.from({ length: STEPS }, (_, i) => (
        <span key={i} className={i < filled ? 'energy-dot on' : 'energy-dot'} />
      ))}
    </span>
  );
}
