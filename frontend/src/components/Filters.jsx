import { BUCKET_ORDER, STATUS_VALUES } from "../effectiveBucket.js";

export default function Filters({ sources, filters, onChange }) {
  function update(field, value) {
    onChange({ ...filters, [field]: value });
  }

  return (
    <div className="filters">
      <label>
        Bucket
        <select value={filters.bucket} onChange={(e) => update("bucket", e.target.value)}>
          <option value="">All</option>
          {BUCKET_ORDER.map((b) => (
            <option key={b} value={b}>
              {b}
            </option>
          ))}
        </select>
      </label>

      <label>
        Source
        <select value={filters.source} onChange={(e) => update("source", e.target.value)}>
          <option value="">All</option>
          {sources.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
      </label>

      <label>
        Status
        <select value={filters.status} onChange={(e) => update("status", e.target.value)}>
          <option value="">All</option>
          {STATUS_VALUES.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
      </label>

      <label>
        Min score
        <input
          type="number"
          min="0"
          max="100"
          value={filters.minScore}
          onChange={(e) => update("minScore", e.target.value)}
        />
      </label>
    </div>
  );
}
