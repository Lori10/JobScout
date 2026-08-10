// Mirrors jobscout/report.py's effective_bucket(): display grouping where
// "irrelevant" (Stage 2, keyword relevance) takes precedence over
// eligibility_bucket (Stage 1), since the two are independent axes.
export function effectiveBucket(job) {
  if (!job.is_relevant) return "irrelevant";
  return job.eligibility_bucket;
}

export const BUCKET_ORDER = ["eligible", "needs_review", "excluded", "irrelevant"];

export const BUCKET_COLORS = {
  eligible: "#1a7f37",
  needs_review: "#9a6700",
  excluded: "#cf222e",
  irrelevant: "#6e7781",
};

export const STATUS_VALUES = ["new", "interested", "applied", "interview", "rejected", "ignored"];
