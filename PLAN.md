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

## Phase 2 (built) — Dashboard

A FastAPI backend + frontend reading from the same SQLite DB: job list,
detail view, filters by bucket/source/score, a "Fetch now" button that
triggers `pipeline.run()`, a stats bar, and the ability to move a job
through its `status` lifecycle (new → interested → applied → interview →
rejected/ignored). No new fetchers or ranking logic — purely a read/write
UI over what Phase 1 already produces and stores.

## Phase 3 (built) — AI-assisted ranking

An optional `ranking_mode: ai` that calls an LLM to re-score a job,
producing the exact same `RankResult` shape `ranker.py` already produces
(plus an additive `seniority_fit` field), with DB-cached results (a job is
never re-ranked twice in the same mode), retry/backoff, description
truncation (~4000 chars), per-run cost logging, and automatic fallback to
the heuristic scorer on any failure or missing provider API key. A per-job
"re-rank" action overwrites the AI fields while leaving `ranking_source`
accurate so the dashboard can show which scorer produced a given result,
and — unlike the bulk pipeline run — surfaces a failure as an error rather
than silently substituting the heuristic score, since it's an explicit
request for an AI result.

The LLM call itself goes through a small provider abstraction
(`jobscout/llm/`, registry-extensible like `fetchers/`) rather than being
hard-wired to one vendor: **Google Gemini is the default** (free tier via
Google AI Studio, no ongoing cost), with **Anthropic Claude implemented
behind the same interface** as a drop-in alternative — switching is a
`config.yaml` change (`ai.provider`) plus the corresponding API key env
var, no code changes.

The "is this actually a job posting?" sanity check is also built, folded
into the same structured-output call as scoring (not a second API call).
Phase 1's phrase/structure heuristics (e.g. RemoteOK's `full time`+
`part time` tag contradiction) catch specific, well-evidenced spam
patterns one at a time, but RemoteOK's free API has an ongoing, broader
spam problem (product-launch announcements, duplicate listings reusing
identical marketing copy under fake "job titles," scraped error pages)
that a growing whack-a-mole list of rules won't fully solve — the LLM
classification generalizes far better. A listing flagged this way scores
0 with a red flag noting the AI's judgment, but stays visible (not
silently dropped) so it can be audited like any other stored job.

## Phase 4 (built) — More sources

**Built:** expanding bundled multi-role HN "Who is hiring" comments (one
comment advertising several distinct roles, each with its own application
link — see README known limitations) into one `Job` per linked role, for
the two most common ATS platforms found in practice — **Ashby and
Greenhouse** — via their public, unauthenticated JSON job-board APIs
(`jobscout/fetchers/ats_boards.py`). A bare board-root link (e.g.
`jobs.ashbyhq.com/starbridge`, no specific job id in the path) is detected
and expanded into N `Job`s, one per listed role, each with its own
title/description/location/apply-URL from the ATS's own data — instead of
the whole bundle being judged as a single Job on the HN comment's
aggregate text. A URL that already names one specific role (has an extra
path segment) is left untouched. Falls back to the original single-Job
behavior on any API failure, empty result, or unsupported platform — this
must never lose a posting outright. Capped at 40 roles per board so one
large company's board doesn't flood the pipeline.

**Also built — Lever**, completing the ATS expansion above via the same
registry-style pattern (a `_fetch_lever`/`_lever_posting_to_job` pair plus
a `detect_board` regex, mirroring Ashby/Greenhouse). Its API differs in two
ways worth knowing: it returns a bare JSON list rather than a `{"jobs":
[...]}` wrapper, and `createdAt` is unix milliseconds rather than an ISO
string.

**Also built — five new fetchers**, each dropped into the existing
`fetchers/` registry with no changes to `pipeline.py`: **Himalayas**,
**Jobicy**, **We Work Remotely** (RSS), **Arbeitnow**, and the HN
**"Freelancer? Seeking freelancer?"** thread. Arbeitnow is where the
German-language phrases already in `config.yaml` start mattering, since
Phase 1's three sources are almost entirely English-language.

The selection was driven by `profile.yaml`'s
`work_setup.preferred: "B2B contract via own registered Albanian company"`:
every one of these states the engagement type as a **structured field**
rather than prose to be inferred from, so `Job.contract_type_guess` becomes
real source data and freelance/B2B work is filterable rather than buried.
Supporting that required three groundwork changes shared by all of them —
RFC-822/unix date parsing and a structured-employment-type mapper in
`fetchers/common.py`, `ranker.resolve_contract_type` so a fetcher-set value
is no longer overwritten by the prose scan, and generalizing `dedupe.py`'s
ATS-only fuzzy-merge escape hatch into `_has_source_assigned_role_id` (any
source whose API assigns a unique id per role hits the same problem the
Ashby/Greenhouse skip was added for).

**Deliberately not built:** the German freelance marketplaces
(freelancermap.de, freelance.de) named in earlier drafts of this plan.
Neither has a public API — page 2+ of any search is gated behind account
registration, and the `freelance-o-mat.de` RSS aggregator that mirrored
freelancermap now returns HTTP 410. Only HTML scraping behind a login would
work, which no other source here requires. Upwork, Malt, Toptal, Contra and
Freelancer.com were rejected for the same reason (OAuth-gated or
scraping-prohibited); Adzuna has a usable free API but needs a registered
`app_id`/`app_key`. Arbeitnow covers the German market through a real API
instead. See README's "Known limitations per source" for what each source
does and doesn't catch.

## Phase 4.5 (built) — Persistent ATS board registry + GitHub-list seeding

**Built:** Phase 4's Ashby/Greenhouse/Lever/Workable bundle expansion
(`ats_boards.py`) only ever ran mid-fetch, on a board URL an HN comment
happened to mention that run — discovered fresh in memory and thrown away
afterward, so a board never re-mentioned in a later HN thread became
invisible again. `db.py`'s new `known_boards` table persists every board
ever discovered (platform + slug, with `enabled`/`first_seen_at`/
`last_polled_at` bookkeeping), and `fetchers/board_registry.py`'s
`poll_known_boards()` re-polls every enabled one, concurrently, on every
future run — turning one-off discovery into standing, compounding
coverage. `hn_whoishiring.py` now tracks which boards it resolved each run
(`self.discovered_boards`) and `pipeline.py` records them into the
registry after the fetch loop. A board discovered this run is deliberately
*not* also polled this same run — its roles are already in this run's Jobs
via the normal HN expansion; it only becomes independently pollable
starting next run.

**Also built — `fetchers/github_lists.py`**, a second, independent
discovery source: scans `yanirs/established-remote`'s public README (a
single unauthenticated fetch, ~105 remote-first companies, at least one
already-bare `boards.greenhouse.io/...` link confirmed live) for candidate
company URLs, runs them through the same `detect_board`/`resolve_board`
logic HN links use, and feeds any newly-found boards into the same
registry. Re-scanned at most once every `github_lists.scan_interval_days`
(default 7) per source, since resolving a non-board-root candidate costs
1-2 real HTTP requests. Both this and `board_registry` have explicit
`config.yaml` `enabled` toggles — unlike a code-level ATS-platform
addition (e.g. Lever), both make outbound calls to third-party
infrastructure on every run, so turning either off shouldn't require a
code change.

Required fixing `ats_boards.py`'s four `_*_posting_to_job` functions,
which hardcoded `Job.source = "hn_whoishiring"` unconditionally — a
registry-polled Job would otherwise misleadingly claim to have come from
an HN comment never actually read this run. `posting_to_job` now takes an
optional `source` kwarg (default `"hn_whoishiring"`, so the original HN
call site is unchanged) and the registry poller passes
`"ats_board_registry"`.

**Also built (later pass) — `remoteintech/remote-jobs`**, a second, larger
(882 companies as of 2026-08) candidate GitHub source, one markdown file
per company under `src/companies/` with a `careers_url` field in YAML
frontmatter. Originally scoped out of this phase over a rate-limit
concern — "60 req/hour unauthenticated GitHub API directory listing plus
~200 individual file fetches" — that turned out to be avoidable: the full
file list comes back in one `git/trees/{branch}?recursive=1` API call
(not a paginated `contents` listing), and the per-file bodies are fetched
from `raw.githubusercontent.com`, a CDN not subject to `api.github.com`'s
rate limit. Still meaningfully heavier than `established-remote`'s single
README fetch (hundreds of raw-file fetches vs. one), so it's config-shaped
differently: `github_lists.repo_sources` (a list of `{name, owner, repo,
branch, path_prefix, frontmatter_field}` entries) rather than reusing
`github_lists.sources`' `name → URL` dict, since a repo-of-frontmatter-
files source needs more than a single URL to describe. Frontmatter is
read via `yaml.safe_load` on the field named by `frontmatter_field`,
rather than regexed for any URL-shaped text like `sources` does — more
precise, since a company's markdown body can itself contain unrelated
URLs a generic regex would wrongly pick up.

## Phase 5 (later) — Research briefs and outreach drafting

For jobs marked `interested` in the dashboard, fetch public company info
(site, GitHub, news) and generate a one-page research brief, then draft a
first application message in the user's voice, branched by
`application_channel` (platform proposal vs. direct email vs. unknown).
Drafts are saved to disk (`research_brief_path` / `outreach_draft_path`,
already reserved on the `Job` model) for manual review — this system never
sends anything automatically.
