# JobScout — Phase Plan

JobScout fetches remote job postings from multiple sources, filters out the
ones that aren't legally/practically doable from Albania, ranks the rest
against a hardcoded skills/experience profile, and surfaces the results.
It is being built in five phases. Each phase ends with a real end-to-end
run and a stop for review before the next phase begins.

## Phase 1 (built) — Core pipeline

Project skeleton, the `Job` dataclass, SQLite storage, three fetchers
(RemoteOK, Remotive, HN "Who is hiring"), cross-source dedupe, the full
three-bucket hard-eligibility filter (eligible / excluded / needs_review,
with an audit-trail reason stored for every non-eligible job), a keyword
relevance pre-filter, a pure-Python heuristic ranker, and CLI + static
`report.html` output. No dashboard, no AI ranking, no other fetchers. The
schema, config, and module boundaries are designed so Phases 2–5 slot in
without rework — notably, the `Job.status` and `Job.application_channel`
fields already exist even though Phase 1 only ever sets them to their
defaults (`new` / best-effort inference).

## Phase 2 (later) — Dashboard

A FastAPI backend + frontend reading from the same SQLite DB: job list,
detail view, filters by bucket/source/score, a "Fetch now" button that
triggers `pipeline.run()`, a stats bar, and the ability to move a job
through its `status` lifecycle (new → interested → applied → interview →
rejected/ignored). No new fetchers or ranking logic — purely a read/write
UI over what Phase 1 already produces and stores.

## Phase 3 (later) — AI-assisted ranking

An optional `ranking_mode: ai` that calls the Claude API to re-score a job,
producing the exact same `RankResult` shape `ranker.py` already produces
(plus an additive `seniority_fit` field), with DB-cached results (a job is
never re-ranked twice in the same mode), retry/backoff, description
truncation (~4000 chars), per-run cost logging, and automatic fallback to
the heuristic scorer on any failure or missing `ANTHROPIC_API_KEY`. A
per-job "re-rank" action overwrites the AI fields while leaving
`ranking_source` accurate so the dashboard can show which scorer produced
a given result.

Also worth scoping in here: a lightweight "is this actually a job
posting?" sanity check. Phase 1's phrase/structure heuristics (e.g.
RemoteOK's `full time`+`part time` tag contradiction) catch specific,
well-evidenced spam patterns one at a time, but RemoteOK's free API has
an ongoing, broader spam problem (product-launch announcements, duplicate
listings reusing identical marketing copy under fake "job titles," scraped
error pages) that a growing whack-a-mole list of rules won't fully solve.
An LLM classification pass generalizes far better here than more rules.

## Phase 4 (later) — More sources

Additional fetchers dropped into the existing `fetchers/` registry with no
changes to `pipeline.py`: We Work Remotely (RSS), Jobicy (API), Arbeitnow
(API), Himalayas (API/RSS), and experimental German freelance marketplaces
(freelancermap.de, freelance.de). These are exactly where the German-
language positive-signal phrases already in `config.yaml` start mattering
most, since Phase 1's three sources are almost entirely English-language.

Also in scope for this phase: expanding bundled multi-role HN "Who is
hiring" comments (one comment advertising several distinct roles, each
with its own application link — see README known limitations) into one
`Job` per linked role where the link is a known ATS with a public API
(e.g. Greenhouse's per-company job board API), instead of treating the
whole bundle as a single Job whose relevance is judged on the comment's
aggregate text. This is source-fetching work, so it fits naturally
alongside the new fetchers above, even though it improves an existing
source (HN) rather than adding a new one.

## Phase 5 (later) — Research briefs and outreach drafting

For jobs marked `interested` in the dashboard, fetch public company info
(site, GitHub, news) and generate a one-page research brief, then draft a
first application message in the user's voice, branched by
`application_channel` (platform proposal vs. direct email vs. unknown).
Drafts are saved to disk (`research_brief_path` / `outreach_draft_path`,
already reserved on the `Job` model) for manual review — this system never
sends anything automatically.
