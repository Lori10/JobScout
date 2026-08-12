async function handle(response) {
  if (!response.ok) {
    const detail = await response.json().catch(() => ({}));
    throw new Error(detail.detail || `${response.status} ${response.statusText}`);
  }
  return response.json();
}

export function getJobs() {
  return fetch("/api/jobs").then(handle);
}

export function postStatus(dedupKey, status) {
  return fetch("/api/jobs/status", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ dedup_key: dedupKey, status }),
  }).then(handle);
}

export function postFetch() {
  return fetch("/api/fetch", { method: "POST" }).then(handle);
}

export function postRerank(dedupKey) {
  return fetch("/api/jobs/rerank", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ dedup_key: dedupKey }),
  }).then(handle);
}
