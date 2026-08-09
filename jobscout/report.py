"""CLI table + static HTML report — the two Phase 1 output surfaces.

Every stored job appears in report.html (including excluded/irrelevant,
sorted to the bottom by score) so the "audit the excluded bucket for false
positives" requirement is directly satisfiable from one artifact.
"""

from __future__ import annotations

import html as html_lib
from datetime import datetime, timezone
from pathlib import Path

from jobscout.models import Job

_BUCKET_COLORS = {
    "eligible": "#1a7f37",
    "needs_review": "#9a6700",
    "excluded": "#cf222e",
    "irrelevant": "#6e7781",
}
_BUCKET_ORDER = ("eligible", "needs_review", "excluded", "irrelevant")


def effective_bucket(job: Job) -> str:
    """Display grouping: irrelevant takes precedence over eligibility_bucket
    since Stage 2 (relevance) runs independently of Stage 1 (eligibility)."""
    if not job.is_relevant:
        return "irrelevant"
    return job.eligibility_bucket.value


def print_summary(total_fetched: int, deduped_count: int, jobs: list[Job], report_path: str, top_n: int = 20) -> None:
    bucket_counts: dict[str, int] = {}
    for job in jobs:
        bucket = effective_bucket(job)
        bucket_counts[bucket] = bucket_counts.get(bucket, 0) + 1

    print()
    print(f"Fetched: {total_fetched} total | Stored after dedupe: {deduped_count}")
    print("  ".join(f"{bucket}: {bucket_counts.get(bucket, 0)}" for bucket in _BUCKET_ORDER))
    print()

    rankable = [j for j in jobs if effective_bucket(j) in ("eligible", "needs_review")]
    top_jobs = sorted(rankable, key=lambda j: j.score or 0, reverse=True)[:top_n]

    if not top_jobs:
        print("No eligible/needs_review jobs to show.")
    else:
        print(f"Top {len(top_jobs)} by score:")
        _print_table(top_jobs)

    print()
    print(f"Full report: {Path(report_path).resolve().as_uri()}")


def _print_table(jobs: list[Job]) -> None:
    headers = ("SCORE", "BUCKET", "SOURCE", "TITLE", "COMPANY", "CONTRACT")
    rows = [
        (
            str(j.score if j.score is not None else 0),
            effective_bucket(j),
            j.source,
            _truncate(j.title, 45),
            _truncate(j.company, 25),
            j.contract_type_guess.value,
        )
        for j in jobs
    ]
    widths = [max(len(h), *(len(r[i]) for r in rows)) for i, h in enumerate(headers)]
    line_fmt = "  ".join(f"{{:<{w}}}" for w in widths)
    print(line_fmt.format(*headers))
    print(line_fmt.format(*("-" * w for w in widths)))
    for row in rows:
        print(line_fmt.format(*row))


def _truncate(text: str, length: int) -> str:
    text = text or ""
    return text if len(text) <= length else text[: length - 1] + "…"


def render_html_report(jobs: list[Job], path: str = "report.html") -> None:
    sorted_jobs = sorted(jobs, key=lambda j: j.score or 0, reverse=True)
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    bucket_counts: dict[str, int] = {}
    for job in jobs:
        bucket = effective_bucket(job)
        bucket_counts[bucket] = bucket_counts.get(bucket, 0) + 1

    rows_html = "\n".join(_job_row_html(job) for job in sorted_jobs)
    summary_html = " &nbsp;|&nbsp; ".join(
        f"<strong>{bucket}</strong>: {bucket_counts.get(bucket, 0)}" for bucket in _BUCKET_ORDER
    )

    document = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>JobScout Report — {generated_at}</title>
<style>
  body {{ font-family: -apple-system, "Segoe UI", Roboto, sans-serif; margin: 2rem; background: #f6f8fa; color: #1f2328; }}
  h1 {{ font-size: 1.4rem; }}
  .summary {{ margin-bottom: 1.5rem; color: #444; }}
  table {{ border-collapse: collapse; width: 100%; background: white; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }}
  th, td {{ padding: 0.5rem 0.7rem; border-bottom: 1px solid #e1e4e8; text-align: left; vertical-align: top; font-size: 0.85rem; }}
  th {{ background: #eaeef2; position: sticky; top: 0; }}
  a {{ color: #0969da; text-decoration: none; }}
  a:hover {{ text-decoration: underline; }}
  .badge {{ display: inline-block; padding: 0.1rem 0.5rem; border-radius: 10px; color: white; font-size: 0.75rem; font-weight: 600; white-space: nowrap; }}
  .reasons, .flags {{ font-size: 0.75rem; color: #57606a; max-width: 280px; }}
  .flags {{ color: #cf222e; }}
  .score {{ font-weight: 700; }}
</style>
</head>
<body>
<h1>JobScout Report</h1>
<div class="summary">Generated {generated_at} &nbsp;|&nbsp; {summary_html}</div>
<table>
<thead>
<tr>
<th>Score</th><th>Bucket</th><th>Source</th><th>Title</th><th>Company</th>
<th>Contract</th><th>Posted</th><th>Reasons</th><th>Red flags</th>
</tr>
</thead>
<tbody>
{rows_html}
</tbody>
</table>
</body>
</html>
"""
    Path(path).write_text(document, encoding="utf-8")


def _job_row_html(job: Job) -> str:
    bucket = effective_bucket(job)
    color = _BUCKET_COLORS.get(bucket, "#6e7781")
    title = html_lib.escape(job.title or "")
    company = html_lib.escape(job.company or "")
    url = html_lib.escape(job.url or "#")
    posted = job.posted_date.strftime("%Y-%m-%d") if job.posted_date else "?"
    reasons = html_lib.escape("; ".join(job.reasons or []) or (job.eligibility_reason or ""))
    red_flags = html_lib.escape("; ".join(job.red_flags or []))
    score = job.score if job.score is not None else 0

    return (
        "<tr>"
        f'<td class="score">{score}</td>'
        f'<td><span class="badge" style="background:{color}">{bucket}</span></td>'
        f"<td>{html_lib.escape(job.source)}</td>"
        f'<td><a href="{url}" target="_blank" rel="noopener">{title}</a></td>'
        f"<td>{company}</td>"
        f"<td>{job.contract_type_guess.value}</td>"
        f"<td>{posted}</td>"
        f'<td class="reasons">{reasons}</td>'
        f'<td class="flags">{red_flags}</td>'
        "</tr>"
    )
