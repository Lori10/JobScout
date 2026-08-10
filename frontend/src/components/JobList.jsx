import { BUCKET_COLORS, effectiveBucket } from "../effectiveBucket.js";
import StatusControl from "./StatusControl.jsx";

export default function JobList({ jobs, onSelect, onJobUpdated }) {
  if (jobs.length === 0) {
    return <p className="empty-state">No jobs match the current filters.</p>;
  }

  return (
    <table className="job-list">
      <thead>
        <tr>
          <th>Score</th>
          <th>Bucket</th>
          <th>Source</th>
          <th>Title</th>
          <th>Company</th>
          <th>Contract</th>
          <th>Status</th>
        </tr>
      </thead>
      <tbody>
        {jobs.map((job) => {
          const bucket = effectiveBucket(job);
          return (
            <tr key={job.dedup_key}>
              <td className="score">{job.score ?? 0}</td>
              <td>
                <span className="badge" style={{ background: BUCKET_COLORS[bucket] }}>
                  {bucket}
                </span>
              </td>
              <td>{job.source}</td>
              <td>
                <a href="#" onClick={(e) => { e.preventDefault(); onSelect(job); }}>
                  {job.title}
                </a>
              </td>
              <td>{job.company}</td>
              <td>{job.contract_type_guess}</td>
              <td onClick={(e) => e.stopPropagation()}>
                <StatusControl job={job} onUpdated={onJobUpdated} />
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}
