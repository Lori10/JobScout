# JobScout

A job aggregation and matching system for a remote AI/LLM engineer based in
Albania. Fetches postings from multiple sources, filters out ones that
aren't legally/practically doable from Albania, ranks the rest against a
hardcoded skills/experience profile, and produces a CLI summary, a static
HTML report, and a web dashboard. See [PLAN.md](PLAN.md) for the full
5-phase roadmap — **this repo currently implements Phases 1, 2, and 3, plus
a slice of Phase 4** (Ashby/Greenhouse bundled-comment expansion).

## Status

Phase 1: done. Fetchers (RemoteOK, Remotive, HN "Who is hiring?"), dedupe,
eligibility filter, keyword relevance filter, heuristic ranking, CLI +
`report.html` output, full pytest coverage of the filter/ranker/dedupe
logic.

Phase 2: done. A FastAPI + React dashboard (see below) for browsing,
filtering, and moving jobs through their status lifecycle, plus
triggering a fetch from the browser. No new fetchers.

Phase 3: done. An optional `ranking_mode: ai` (see below) that re-scores
jobs via a pluggable LLM provider (Google Gemini by default, Anthropic
Claude as a drop-in alternative), DB-cached so a job is never re-scored
twice in the same mode, with retry/backoff, description truncation,
per-run cost logging, automatic fallback to the heuristic scorer, and a
per-job "Re-rank with AI" dashboard action.

Phase 4: partially done. HN "who is hiring" comments that link to a bare
Ashby or Greenhouse board (rather than one specific role) are expanded
into one `Job` per listed role via that ATS's public API — see known
limitations below. No new fetchers (RSS/other job boards) yet.

## Install

Requires Python 3.11+. `pip`/`uv` are not assumed to be pre-installed —
bootstrap via the stdlib `venv` + `ensurepip`:

```bash
cd /home/lori28/JobScout
python3.11 -m venv .venv
.venv/bin/python -m ensurepip --upgrade
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -e '.[dev]'
```

## Run

```bash
.venv/bin/python -m jobscout run
```

This fetches from every enabled source in `config.yaml`, dedupes, filters,
ranks, stores everything in `data/jobscout.db` (created on first run), and
prints a summary:

```
Fetched: 330 total | Stored after dedupe: 299
eligible: 25  needs_review: 1  excluded: 31  irrelevant: 242

Top 13 by score (min_score_threshold=30):
SCORE  BUCKET    SOURCE          TITLE                          COMPANY   CONTRACT
...

Full report: file:///home/lori28/JobScout/report.html
```

The CLI table only shows jobs scoring at or above `min_score_threshold`
(so the "Top N" list stays a `min(top_n, jobs above threshold)` count, not
always a full 20) — `report.html` always shows every stored job regardless
of score. A `needs_review` job that's genuinely relevant always scores at
least 1, even if its raw signal is weak, so it never looks identical in a
sorted view to an excluded/irrelevant job that was never ranked at all
(those always score exactly 0).

Open `report.html` in a browser for the full sorted table — **every**
stored job appears there, including `excluded` and `irrelevant` ones (with
the exact matched phrase in the "Reasons" column), so false positives in
the exclusion logic can be audited directly. Re-running the pipeline never
deletes existing jobs or clobbers a job's `status` — it only adds new jobs
and refreshes score/eligibility/last_seen on ones seen again.

Increase log verbosity for debugging why a specific job landed in a given
bucket:

```bash
.venv/bin/python -m jobscout --log-level DEBUG run
```

### Cron (daily run)

```cron
0 8 * * * cd /home/lori28/JobScout && .venv/bin/python -m jobscout run >> logs/cron.log 2>&1
```

(Create a `logs/` directory first, or redirect elsewhere — it isn't
created automatically.)

## Dashboard (Phase 2)

A FastAPI backend + React frontend reading from the same `data/jobscout.db`
— job list, a detail view, filters by bucket/source/status/score, a
"Fetch now" button that runs the same pipeline as `jobscout run`, a stats
bar, and the ability to move a job through its `status` lifecycle (new →
interested → applied → interview → rejected/ignored). No new fetchers or
ranking logic — it's a read/write UI over what the pipeline already
produces and stores.

Build the frontend once (or after pulling frontend changes):

```bash
cd frontend
npm install
npm run build
```

Then start the server, which serves both the API and the built frontend
on one origin:

```bash
.venv/bin/python -m jobscout serve
```

Open `http://127.0.0.1:8000`. Useful flags: `--host`, `--port`,
`--config`, `--profile`, `--db-path`, `--report-path` (all default to the
same values `jobscout run` uses).

For frontend development with hot reload, run both the backend (with
`--reload`) and the Vite dev server together with one script:

```bash
./scripts/dev.sh
```

This starts `uvicorn jobscout.web.app:app --reload` (restarts on changes
under `jobscout/`) and `npm run dev` (Vite, hot-reloads `.jsx`/`.css`)
side by side, and stops both together on Ctrl-C. Open
`http://localhost:5173` — Vite proxies `/api/*` requests to the backend
on port 8000. (Equivalent to running `.venv/bin/python -m uvicorn
jobscout.web.app:app --reload` and `cd frontend && npm run dev` in two
separate terminals, if you'd rather see each process's output on its
own.)

## AI-assisted ranking (Phase 3)

Set `ranking_mode: ai` in `config.yaml` to re-score eligible/relevant jobs
via an LLM instead of the heuristic scorer, producing the same score/
skill_match/contract_type_guess/reasons/red_flags shape plus an additive
`seniority_fit` field, and folding in a spam/"is this actually a job
posting?" sanity check (see RemoteOK's known limitations below) into the
same call.

```yaml
ranking_mode: ai
ai:
  provider: gemini        # gemini | anthropic
  model: gemini-3.5-flash-lite
  max_description_chars: 4000
  max_retries: 3
```

Copy `.env.example` to `.env` and fill in the key for whichever provider
you use (`jobscout/__main__.py` loads `.env` automatically):

- **`gemini`** (default) — free tier via Google AI Studio. Get a key at
  <https://aistudio.google.com/apikey> and set `GEMINI_API_KEY`.
- **`anthropic`** — Claude API. Get a key at <https://console.anthropic.com/>
  and set `ANTHROPIC_API_KEY`. Switching from Gemini is just these two
  config/env changes — `jobscout/llm/` abstracts the provider behind a
  shared interface (`jobscout/llm/__init__.py`'s `PROVIDER_REGISTRY`, same
  extension pattern as `fetchers/`), so `ai_ranker.py`/`pipeline.py` never
  need to change.

A job already AI-ranked is never re-scored on a later `jobscout run` (DB-
cached by `ranking_source`) — a run logs a one-line cost/call summary
(`AI ranker: N call(s), ... estimated cost $...`). If the configured
provider's API key isn't set, or every retry for a job fails, that job
(or the whole run, if the key is simply missing) falls back to the
heuristic scorer automatically, logging a warning rather than crashing.
The dashboard's per-job "Re-rank with AI" button bypasses this cache and
force-calls the AI scorer for just that job — unlike the bulk run, a
failure there is surfaced as an error rather than silently substituting
the heuristic score, since re-rank is an explicit request for an AI
result.

## Tuning

- **`profile.yaml`** — role targets, core skills, experience, languages,
  work-setup preference, location. Edit freely; reloaded every run.
- **`config.yaml`** — source on/off toggles, `ranking_mode` (`heuristic` or
  `ai` — see "AI-assisted ranking" above), `min_score_threshold`, every
  phrase list used by the eligibility filter (`exclude_phrases`,
  `hybrid_onsite_phrases`, `remote_indicator_phrases`,
  `needs_review_phrases`, `positive_phrases`), the keyword relevance
  pre-filter (`relevance_keywords` — specific terms, a hit anywhere
  counts; `relevance_keywords_weak` — bare `"ai"`/`"ml"`, too generic to
  trust in the body alone, only counts when it's in the job title), a
  title-based veto list (`non_role_title_phrases` — sales/marketing/
  design/recruiting/etc. job titles are marked irrelevant regardless of
  keyword hits elsewhere, since company/product boilerplate often
  mentions "AI" even when hiring for an unrelated function), the
  heuristic ranker's weighted `keyword_groups` + bonus caps, and the `ai`
  block (`provider`/`model`/`max_description_chars`/`max_retries`) used
  only when `ranking_mode: ai`.

All phrase matching is case-insensitive, punctuation-tolerant, and
word-boundary-anchored (so `"US CITIZENS ONLY"`, `"U.S. Citizens Only"`,
and `"us-citizens-only"` all match a configured `"us citizens only"`
phrase, but short keywords like `"ai"` or `"ml"` never fire inside
unrelated words like `"certain"` or `"html"`). It's also negation-aware
within a 5-word window: `"This role does NOT have an in-office
requirement"` does not trigger the `"in-office"` exclude phrase (found via
live audit — a real remote-friendly posting was wrongly excluded this
way). A phrase mentioned twice, negated once and not the other time,
still counts as a match.

## Known limitations per source

- **RemoteOK** — the public API returns only ~100 most-recent postings per
  request (no pagination in Phase 1), and `location`/`tags` fields are
  occasionally free-text/malformed on the source side. RemoteOK's own feed
  occasionally contains non-job entries (e.g. a solo founder using the
  "post a job" flow to announce a product launch instead of hiring
  anyone) — these are detected and skipped when an entry has both an
  empty `slug` and a `url`/`apply_url` that RemoteOK itself fell back to
  the generic `/remote-jobs` listings page rather than a specific job
  permalink. Garbled characters occasionally appearing in a title
  (mis-encoded emoji) are pre-existing corruption in RemoteOK's own
  stored data, not something introduced by this fetcher. Also detected
  and skipped: entries tagged with both `"full time"` and `"part time"`
  simultaneously — no single real job is both, so this reliably flags
  tag-stuffing spam (verified against a live snapshot: caught known junk
  entries with zero false positives against legitimate heavily-tagged
  posts). **This is not exhaustive** — RemoteOK's free/public API has a
  broader, ongoing spam problem (e.g. product-launch announcements,
  duplicate listings reusing identical marketing copy under different
  fake "job titles," and at least one entry whose "description" was a
  scraped HTTP error page). Each fix here targets a specific, well-
  evidenced signal found by manually auditing live data — this is a
  whack-a-mole problem that pure phrase/structure heuristics can't fully
  solve. With `ranking_mode: ai` (Phase 3), the AI scorer's structured
  output includes an `is_job_posting` sanity check folded into the same
  call as scoring, which generalizes far better than hand-rolled rules; a
  listing it flags scores 0 with a red flag noting the AI's judgment, but
  still shows up in `report.html`/the dashboard rather than being
  silently dropped. Spam that slips through either mode is still visible
  and auditable in `report.html` like any other stored job.
- **Remotive** — Phase 1 only polls the `software-dev` category (the URL
  the spec fixed); other categories (e.g. data/AI-specific ones, if
  Remotive ever splits them out) aren't polled yet. `salary` is free text
  and frequently empty.
- **HN "Who is hiring?"** — there is no structured job-posting format on
  HN; comments are freeform prose. Title/company extraction splits the
  first line on `|` and skips segments that look like salary, employment
  type, location/remote status, or a bare URL when picking which segment
  is the actual role — but posters don't use a consistent field order, so
  this is still best-effort and sometimes produces a messy (though at
  least not actively misleading) title for posts with an unusual format.
  The full plain-text body is always preserved in `description`
  regardless, so eligibility/relevance filtering and ranking are
  unaffected by parsing quality — only the displayed title/company can be
  off. When every segment in the header line looks like salary/location/
  employment-type/URL (i.e. no role name at all on the first line — the
  real roles are usually listed as bullets further down, as with
  Foxglove's "Onsite (SF) + Remote | Full Time" header), the title falls
  back to a fixed placeholder ("Role not stated in header — see
  description") rather than silently reusing one of the rejected
  segments. Because that placeholder is identical across different
  postings, `dedupe.py`'s fuzzy company+title matching requires company
  names to be independently similar (not just the combined string) before
  merging two records — otherwise two unrelated companies that both hit
  this fallback would get incorrectly deduped into one. Email/URL
  extraction for `application_channel` is a plain regex
  over the comment body; when a poster advertises multiple roles under one
  link to their general careers page, that's the only URL that exists in
  the text — there's no per-role link to extract. Comments that look like
  a candidate's own "who wants to be hired"-style self-profile (fields
  like `"Willing to relocate:"` or `"Résumé/CV:"`) are detected and
  skipped entirely rather than surfaced as fake jobs, since people
  occasionally cross-post those into the hiring thread.
- **Bundled multi-role HN comments** — a single comment sometimes
  advertises many distinct roles at once instead of one. When the comment
  links to a bare **Ashby or Greenhouse** board-root URL (e.g.
  `jobs.ashbyhq.com/starbridge`, no specific job id in the path —
  `jobscout/fetchers/ats_boards.py`'s `detect_board()` is what
  distinguishes this from an already-specific job link), this is now
  fixed (Phase 4): that one HN comment is expanded into one `Job` per role
  fetched from the ATS's own public JSON API, each with its own
  title/description/location/apply-URL, so eligibility/relevance/ranking
  are judged per-role instead of on the comment's aggregate text. Falls
  back to the old single-Job behavior on any API failure or empty result.
  **Still a limitation for every other case**: a comment that lists
  several role names with separate application links written directly in
  its own prose (e.g. `"Multiple positions in United States - WORK FROM
  HOME"` followed by 9 role names each with its own link, none going
  through Ashby/Greenhouse) still becomes one `Job` record judged on the
  whole comment's text — if none of the listed role names happen to
  contain a configured AI/LLM keyword (even if one individually would be
  relevant), the entire bundle is marked `irrelevant`. Extending this to
  other ATS platforms (Lever, etc.) is straightforward following the same
  pattern in `ats_boards.py`; parsing arbitrary per-role links out of free
  prose (rather than one ATS board API call) is a fundamentally harder,
  not-yet-attempted problem. See [PLAN.md](PLAN.md) Phase 4 for scope.
- **All sources** — the eligibility filter works by matching curated
  phrases (see `config.yaml`), not by NLP/entity extraction. It reliably
  catches explicit statements like `"US citizens only"` or `"visa
  sponsorship required"`, but a posting that enumerates a specific
  whitelist of countries without a trigger phrase (e.g. `"REMOTE
  (US/Canada/Brazil/Poland/UK/India)"`, which doesn't include Albania but
  also doesn't say "only") will not be caught and needs manual review.
  When in doubt, check the "Reasons"/"Red flags" columns in `report.html`
  before applying.
- **"No C2C" is intentionally not an exclude phrase** — it's a
  US-contracting-structure term (corp-to-corp) rather than an
  Albania-eligibility signal on its own, so it's not treated as
  exclusionary by itself in Phase 1.

## Architecture

```
jobscout/
  models.py     Job dataclass + enums (shared across all 5 phases)
  config.py     typed loader for config.yaml / profile.yaml
  htmlutils.py  shared HTML-to-plain-text stripping
  fetchers/     one module per source + a registry (Phase 4 drops in here);
                ats_boards.py expands a bundled Ashby/Greenhouse board link
                into one Job per role (used by hn_whoishiring.py)
  dedupe.py     normalized-URL + fuzzy company/title matching
  filters.py    pure eligibility + keyword-relevance functions
  ranker.py     heuristic scorer, same output shape ai_ranker.py produces
  ai_ranker.py  Phase 3: AI scorer (provider-agnostic; see jobscout/llm/)
  llm/          Phase 3: LLM provider abstraction + registry (gemini.py,
                anthropic_provider.py) - same extension pattern as fetchers/
  db.py         stdlib sqlite3 storage
  pipeline.py   orchestrates fetch -> dedupe -> filter -> rank -> store -> report
  report.py     CLI table + report.html
  web/          Phase 2 dashboard: FastAPI app (app.py), routes (routes.py),
                Pydantic schemas (schemas.py)
frontend/       Phase 2 dashboard: React + Vite SPA served by jobscout/web
scripts/dev.sh  Runs the dashboard backend + frontend together for local dev
```

## Tests

```bash
.venv/bin/python -m pytest -q
```

Covers `filters.py` (every eligibility edge case called out in the spec:
US-only, EU-only vs. needs_review, worldwide, EST overlap vs. PST-required,
hybrid-with/without-remote-mention, German posts not excluded for language,
punctuation/case tolerance), `ranker.py` (score bounds, LLM/RAG keywords
outscoring generic Python, title/positive/recency bonuses, all four
`contract_type_guess` branches), `dedupe.py`, `db.py` (including the
`seniority_fit` column migration for pre-Phase-3 databases), `htmlutils.py`,
`config.py` (`ai` block parsing, `ranking_mode` validation), the `gemini`/
`anthropic` providers' error normalization in `jobscout/llm/` (mocked SDK
clients, no real network calls), `ai_ranker.py` (structured-output mapping,
retry/backoff, fatal-vs-transient error handling, cost/token summary), and
`pipeline.py`'s AI dispatch/caching/fallback logic and `rerank_job()`.
