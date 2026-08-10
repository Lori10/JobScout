import { useEffect, useMemo, useState } from "react";
import { getJobs } from "./api.js";
import { effectiveBucket } from "./effectiveBucket.js";
import StatsBar from "./components/StatsBar.jsx";
import Filters from "./components/Filters.jsx";
import JobList from "./components/JobList.jsx";
import JobDetail from "./components/JobDetail.jsx";
import FetchButton from "./components/FetchButton.jsx";

const EMPTY_FILTERS = { bucket: "", source: "", status: "", minScore: "" };

export default function App() {
  const [jobs, setJobs] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [filters, setFilters] = useState(EMPTY_FILTERS);
  const [selectedJob, setSelectedJob] = useState(null);

  useEffect(() => {
    getJobs()
      .then(setJobs)
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false));
  }, []);

  const sources = useMemo(() => [...new Set(jobs.map((j) => j.source))].sort(), [jobs]);

  const filteredJobs = useMemo(() => {
    const minScore = filters.minScore === "" ? 0 : Number(filters.minScore);
    return jobs
      .filter((job) => !filters.bucket || effectiveBucket(job) === filters.bucket)
      .filter((job) => !filters.source || job.source === filters.source)
      .filter((job) => !filters.status || job.status === filters.status)
      .filter((job) => (job.score ?? 0) >= minScore)
      .sort((a, b) => (b.score ?? 0) - (a.score ?? 0));
  }, [jobs, filters]);

  function patchJob(updated) {
    setJobs((prev) => prev.map((j) => (j.dedup_key === updated.dedup_key ? updated : j)));
    setSelectedJob((prev) => (prev && prev.dedup_key === updated.dedup_key ? updated : prev));
  }

  return (
    <div className="app">
      <header>
        <h1>JobScout Dashboard</h1>
        <FetchButton onFetched={setJobs} />
      </header>

      {error && <p className="error-text">Failed to load jobs: {error}</p>}
      {loading ? (
        <p>Loading…</p>
      ) : (
        <>
          <StatsBar jobs={jobs} />
          <Filters sources={sources} filters={filters} onChange={setFilters} />
          <JobList jobs={filteredJobs} onSelect={setSelectedJob} onJobUpdated={patchJob} />
        </>
      )}

      <JobDetail job={selectedJob} onClose={() => setSelectedJob(null)} onJobUpdated={patchJob} />
    </div>
  );
}
