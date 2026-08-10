import { useState } from "react";
import { postFetch } from "../api.js";

export default function FetchButton({ onFetched }) {
  const [fetching, setFetching] = useState(false);
  const [error, setError] = useState(null);

  async function handleClick() {
    setFetching(true);
    setError(null);
    try {
      const jobs = await postFetch();
      onFetched(jobs);
    } catch (err) {
      setError(err.message);
    } finally {
      setFetching(false);
    }
  }

  return (
    <div className="fetch-button-wrap">
      <button onClick={handleClick} disabled={fetching}>
        {fetching ? "Fetching…" : "Fetch now"}
      </button>
      {error && <span className="error-text"> {error}</span>}
    </div>
  );
}
