import { useState } from "react";
import { STATUS_VALUES } from "../effectiveBucket.js";
import { postStatus } from "../api.js";

export default function StatusControl({ job, onUpdated }) {
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);

  async function handleChange(e) {
    const status = e.target.value;
    setSaving(true);
    setError(null);
    try {
      const updated = await postStatus(job.dedup_key, status);
      onUpdated(updated);
    } catch (err) {
      setError(err.message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <span className="status-control">
      <select value={job.status} disabled={saving} onChange={handleChange}>
        {STATUS_VALUES.map((s) => (
          <option key={s} value={s}>
            {s}
          </option>
        ))}
      </select>
      {error && <span className="error-text"> {error}</span>}
    </span>
  );
}
