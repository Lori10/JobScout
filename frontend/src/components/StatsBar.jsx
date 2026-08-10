import { BUCKET_COLORS, BUCKET_ORDER, effectiveBucket } from "../effectiveBucket.js";

export default function StatsBar({ jobs }) {
  const counts = Object.fromEntries(BUCKET_ORDER.map((b) => [b, 0]));
  for (const job of jobs) {
    const bucket = effectiveBucket(job);
    counts[bucket] = (counts[bucket] || 0) + 1;
  }

  return (
    <div className="stats-bar">
      <span className="stats-total">{jobs.length} total</span>
      {BUCKET_ORDER.map((bucket) => (
        <span key={bucket} className="badge" style={{ background: BUCKET_COLORS[bucket] }}>
          {bucket}: {counts[bucket]}
        </span>
      ))}
    </div>
  );
}
