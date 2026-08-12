import { useState } from "react";
import { postRerank } from "../api.js";

export default function RerankButton({ job, onUpdated }) {
  const [reranking, setReranking] = useState(false);
  const [error, setError] = useState(null);

  async function handleClick() {
    setReranking(true);
    setError(null);
    try {
      const updated = await postRerank(job.dedup_key);
      onUpdated(updated);
    } catch (err) {
      setError(err.message);
    } finally {
      setReranking(false);
    }
  }

  return (
    <span className="rerank-button-wrap">
      <button onClick={handleClick} disabled={reranking}>
        {reranking ? "Re-ranking…" : "Re-rank with AI"}
      </button>
      {error && <span className="error-text"> {error}</span>}
    </span>
  );
}
