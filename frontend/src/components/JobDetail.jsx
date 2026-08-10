import StatusControl from "./StatusControl.jsx";

export default function JobDetail({ job, onClose, onJobUpdated }) {
  if (!job) return null;

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <button className="modal-close" onClick={onClose} aria-label="Close">
          ×
        </button>
        <h2>{job.title}</h2>
        <p className="modal-subtitle">
          {job.company} &middot; {job.source} &middot;{" "}
          <a href={job.url} target="_blank" rel="noopener noreferrer">
            view posting
          </a>
        </p>

        <div className="modal-row">
          <strong>Status:</strong> <StatusControl job={job} onUpdated={onJobUpdated} />
        </div>
        <div className="modal-row">
          <strong>Score:</strong> {job.score ?? 0} &nbsp;
          <strong>Skill match:</strong> {job.skill_match ?? "?"} &nbsp;
          <strong>Contract:</strong> {job.contract_type_guess}
        </div>

        {job.reasons.length > 0 && (
          <div className="modal-row">
            <strong>Reasons:</strong> {job.reasons.join("; ")}
          </div>
        )}
        {job.red_flags.length > 0 && (
          <div className="modal-row flags">
            <strong>Red flags:</strong> {job.red_flags.join("; ")}
          </div>
        )}
        {job.eligibility_reason && (
          <div className="modal-row">
            <strong>Eligibility reason:</strong> {job.eligibility_reason}
          </div>
        )}

        <div className="modal-row description">
          <strong>Description:</strong>
          <p>{job.description}</p>
        </div>
      </div>
    </div>
  );
}
