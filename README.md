# JobScout

A job aggregation and matching system for a remote AI/LLM engineer based in
Albania. Fetches postings from multiple sources, filters out ones that
aren't legally/practically doable from Albania, ranks the rest against a
hardcoded skills/experience profile, and produces a CLI summary, a static
HTML report, and a web dashboard. See [PLAN.md](PLAN.md) for the full
5-phase roadmap — **this repo currently implements Phases 1 through 4,
plus Phase 4.5**.

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

Phase 4: done. Five new sources on top of Phase 1's three — **Himalayas**,
**Jobicy**, **We Work Remotely** (RSS), **Arbeitnow** (German/EU market),
and the HN **"Freelancer? Seeking freelancer?"** thread — plus **Lever**
added to the ATS bundle expansion alongside Ashby and Greenhouse. All are
free and unauthenticated; no API keys are needed for any of them.

The selection is driven by `profile.yaml`'s
`work_setup.preferred: "B2B contract via own registered Albanian company"`.
Each new source states the engagement type as a **structured field**
(Himalayas `employmentType`, WWR `<type>`, Lever `categories.commitment`,
Jobicy `jobType`, Arbeitnow `job_types`), which the fetchers map onto
`Job.contract_type_guess` — so the dashboard's contract column is real
source data rather than a guess from description prose, and freelance/B2B
work is filterable instead of buried. Several also state hiring locations
as structured allow-lists, which feeds Stage 1 eligibility directly.

Sources considered and **rejected**: `freelancermap.de` and `freelance.de`
(both named in earlier drafts of PLAN.md) have no public API — page 2+ of
any search is gated behind account registration, and the
`freelance-o-mat.de` RSS aggregator that used to mirror freelancermap now
returns HTTP 410. Only HTML scraping behind a login would work. Upwork,
Malt, Toptal, Contra, Freelancer.com and Wellfound are OAuth-gated or
prohibit scraping (Wellfound has no official public API at all — only paid
third-party scrapers). LinkedIn and Indeed have no accessible public API
either: LinkedIn's Jobs API is partner-only and has granted essentially no
new third-party data access since 2018; Indeed's Publisher API closed to
new publishers in 2022 and was fully deprecated in 2024. Adzuna has a
usable free API but requires registering for an `app_id`/`app_key`, which
every current source avoids. Arbeitnow covers the German market through a
real API instead.

[speedyapply/JobSpy](https://github.com/speedyapply/JobSpy) doesn't change
the LinkedIn/Indeed calculus above — it works around the lack of a public
API by scraping HTML instead, which is a different and riskier integration
shape than every source actually built here. Its own docs say "all of the
job boards are aggressive with blocking," recommend proxy rotation, and
note LinkedIn typically rate-limits by page 10 on a single IP. There's real
ToS-enforcement precedent, too: *hiQ v. LinkedIn* ended in a permanent
injunction and a $500k judgment against a scraping company — the CFAA
doesn't bar scraping public data, but the breach-of-contract/ToS theory
still won. Not worth the risk for a personal pipeline, especially since
most of JobSpy's non-LinkedIn/Indeed coverage (Naukri/India,
Bdjobs/Bangladesh, Bayt/Middle East, ZipRecruiter US/Canada-only) doesn't
match this project's target profile anyway.

[maurobonfietti/remote-jobs](https://github.com/maurobonfietti/remote-jobs)
was also considered: a GitHub README auto-updated daily with ~1,500 job
rows, technically cheap to consume (same "fetch one raw file" pattern as
`github_lists.py`'s `established-remote` source). Rejected anyway — it has
no LICENSE file and no stated data source, and turned out (checked via the
raw file) to be silently mirroring a third-party aggregator,
opentoworkremote.com, through UTM-tagged redirect links with no visible
attribution or permission. It's also a single hobby project ("star to keep
me motivated"), and a live sample of ~50 rows was overwhelmingly
non-technical (sales, marketing, ops, nursing) with only occasional AI/ML
titles — a relevance-yield problem like Arbeitnow's, minus Arbeitnow's
redeeming factor of being an official regional API with clear provenance.

[freehire](https://github.com/strelov1/freehire) (freehire.me) is a
**parked candidate, not a rejection** — it's architecturally a much better
fit than the two above. Verified directly rather than trusting the README:
MIT-licensed, and `GET https://freehire.me/api/v1/jobs` is a real, live,
keyless endpoint returning clean structured JSON (`title`, `company`,
`countries[]`, `regions[]`, `enrichment.employment_type`, `skills[]`,
`posted_at`) sourced from 80+ ATS platforms (Workday, Greenhouse, Lever,
Ashby, etc.) rather than scraping restricted sites directly. Its
`countries`/`regions` arrays would even solve the "structured hiring-
location allow-list" gap called out below for Himalayas/Jobicy. But the
repo is only ~2 months old (created 2026-06), single-maintainer, and every
filter parameter tried against the live public endpoint (`q`, `title`,
`search`, `keyword`, `countries`, `skills`) returned an identical
unfiltered 5.5M-row result set — either real filtering needs a mechanism
not found during this check, or the public endpoint doesn't support it
yet. Not usable as a fetcher until that's resolved and the project has more
of a track record; worth checking again in a few months.

Phase 4.5: done. A persistent **ATS board registry** (see below) that
re-polls every Ashby/Greenhouse/Lever/Workable/Recruitee/Personio board
ever discovered — via HN comments or a public GitHub company list — on
every future run, instead of discovery being thrown away after the run
that found it.

Phase 4.6: done. Two more ATS board-expansion platforms, **Recruitee** and
**Personio**, added to `jobscout/fetchers/ats_boards.py` alongside
Ashby/Greenhouse/Lever/Workable — prompted by asking whether recruitment/
staffing agencies themselves would be a better source to add. They
weren't: generalist staffing agencies (Robert Half, Randstad, CyberCoders)
have no public job API, only scraping; freelance marketplaces (Toptal,
Turing, Arc.dev, Crossover) are candidate-vetting platforms with no
published-requisition feed to poll at all; and `jobdataapi.com`, a paid
aggregator with an explicit agency filter, was the one real option for
genuine agency-posted data but has no free tier ($345-495/mo). Recruitee
and Personio are ATS platforms, not agencies — same free, public,
structured shape as the existing four, wired into the same board-
expansion/registry mechanism with no other call sites changed. Live
verification against real boards (`hygraph`/`onramper` on Recruitee,
`mercanis` on Personio) caught one real bug before it shipped: Recruitee's
`published_at`/`created_at` come back as `"2026-07-31 22:26:40 UTC"`, not
ISO8601 — `parse_iso_datetime` silently returned `None` for every real
value, dropping `posted_date` to the fallback on every single job.
`_parse_recruitee_datetime` normalizes the space-separated shape before
handing off (see Known limitations below for both platforms' remaining
quirks).

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

### Run with Docker

An alternative to the `.venv`-based setup above: build and run the
dashboard in a container with Docker Compose. `config.yaml`,
`profile.yaml`, and (if using AI ranking) `.env` must already exist on
the host — they're bind-mounted read-only into the container, not baked
into the image. `report.html` is also bind-mounted; since Docker creates
an empty directory at a nonexistent bind-mount file path, run `touch
report.html` first if it doesn't already exist (or drop that line from
`docker-compose.yml` if you don't need the report through the
container).

```bash
docker compose up --build
```

Open `http://localhost:8000`. This compose stack runs three services:
`jobscout` (the app), `postgres` (16-alpine, storing jobs in the
`jobscout-postgres-data` named Docker volume — *not* `./data/jobscout.db`),
and `adminer` (a zero-config DB browser UI). Jobs persist in that named
volume across restarts; run `docker compose down -v` to wipe it and start
fresh.

Browse the database at `http://localhost:8080` — System: `PostgreSQL`,
Server: `postgres`, Username: `jobscout`, Password: `jobscout`, Database:
`jobscout`. These are throwaway local-only credentials: the `postgres`
service has no `ports:` mapping to the host, so it's unreachable outside
the compose network regardless of what the password is.

Plain non-Docker dev (`.venv/bin/python -m jobscout serve`, see above)
still defaults to sqlite at `./data/jobscout.db` — nothing outside
`docker-compose.yml` sets `JOBSCOUT_DB_PATH`.

The non-root container user (uid 1000) still needs write access to
`./report.html` on the host — if `docker compose up` fails with a
permission error on first run:

```bash
chown -R 1000:1000 data/ report.html
```

To trigger a fetch without using the dashboard's "Fetch now" button:

```bash
docker compose exec jobscout python -m jobscout run
```

This setup has no scheduling — fetches are manual, via the dashboard
button or the command above.

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

## ATS board registry (Phase 4.5)

Phase 4's Ashby/Greenhouse/Lever/Workable board expansion
(`jobscout/fetchers/ats_boards.py`) only ever ran mid-fetch, on whichever
board URL an HN comment happened to mention that run — discovered fresh in
memory and thrown away afterward. The registry makes that persistent:
every board ever discovered is saved to `data/jobscout.db`'s
`known_boards` table and re-polled, concurrently, on every future run
regardless of whether its original source mentions it again.

Two discovery sources feed the same registry:

- **HN comments** — unchanged from Phase 4's board expansion; every board
  `hn_whoishiring.py`/`hn_freelancer.py` resolves this run is now also
  recorded for future runs, not just used once.
- **GitHub company lists** (`jobscout/fetchers/github_lists.py`) — scans
  public, unauthenticated "remote-first companies" lists (currently just
  `yanirs/established-remote`'s README) for more candidate company URLs,
  running them through the same board-detection logic HN links use.

A board discovered this run is deliberately *not* also polled this same
run — it becomes independently pollable starting *next* run, once it's
been recorded. This is a compounding mechanism, not a same-run
double-fetch.

`config.yaml`'s `board_registry` and `github_lists` blocks each have their
own `enabled` toggle, since both make outbound HTTP calls to third-party
infrastructure on every run:

```yaml
board_registry:
  enabled: true
  max_workers: 15

github_lists:
  enabled: true
  scan_interval_days: 7
  sources:
    established-remote: "https://raw.githubusercontent.com/yanirs/established-remote/master/README.md"
```

`github_lists` re-scans each configured source at most once every
`scan_interval_days` (default 7) — resolving a candidate URL that isn't
already a bare board-root link costs 1-2 real HTTP requests, so scanning
every run for no new information isn't worth the cost.

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
  through a supported ATS) still becomes one `Job` record judged on the
  whole comment's text — if none of the listed role names happen to
  contain a configured AI/LLM keyword (even if one individually would be
  relevant), the entire bundle is marked `irrelevant`. Parsing arbitrary
  per-role links out of free prose (rather than one ATS board API call) is
  a fundamentally harder, not-yet-attempted problem.
- **Lever** (bundled-comment expansion, same mechanism as above) — its API
  returns a **bare JSON list**, with no `{"jobs": [...]}` wrapper, unlike
  both other platforms, and `createdAt` is unix **milliseconds** where
  Ashby/Greenhouse use ISO strings. Lever splits a posting's prose across
  several fields and none is reliably present (on a real 81-role board,
  `descriptionPlain` was missing on 4 and `additionalPlain` on 2), so the
  description is assembled from `descriptionPlain` + the `lists`
  Responsibilities/Requirements bullets + `additionalPlain` — dropping
  `lists` would discard exactly the skill text the ranker needs. An empty
  board is a valid response (`api.lever.co/v0/postings/lever` returns
  `[]`) and falls back to the original single-Job behavior.
- **Recruitee** (bundled-comment expansion) — `published_at`/`created_at`
  come back as `"YYYY-MM-DD HH:MM:SS UTC"` (space-separated, not ISO8601)
  despite the OpenAPI docs typing them as a bare string with no format
  shown — confirmed against a live board. `_parse_recruitee_datetime`
  normalizes this before parsing so `posted_date` isn't silently dropped to
  the fallback for every job. Some companies front their Recruitee board
  behind a custom domain (e.g. `jobs.hygraph.com` rather than
  `hygraph.recruitee.com`, seen live) — an HN comment linking only the
  custom domain isn't detected as a Recruitee board root unless it also
  links to (or redirects through) the `*.recruitee.com` host
  `detect_board()`/`resolve_board()` look for.
- **Personio** — the public XML feed lives at either `*.jobs.personio.de`
  or `*.jobs.personio.com`, and which TLD is live for a given company isn't
  guessable from the company name alone, so `board_slug` encodes both as
  `"{company}.{tld}"` rather than a bare token like every other platform
  here. The individual job-detail URL (`/job/{id}`) isn't documented in
  Personio's own XML feed docs — it's inferred from career-site convention
  and was confirmed live against a real board (`mercanis`) before shipping.
- **ATS board registry, Workable specifically** — a Workable role found via
  the registry (`jobscout/fetchers/board_registry.py`) has a weaker
  description than the same role found via an HN comment, since
  `posting_to_job`'s `original_description` (the full original HN comment
  text, prepended ahead of Workable's thin scraped snippet — see
  `ats_boards.py`'s module docstring) has nothing to be filled with outside
  an HN discovery. This is a pre-existing limitation either way: Workable's
  own public API has no description field at all, so even the HN path
  depends on a truncated ~250-char SEO snippet plus whatever context the
  comment happened to add.
- **Himalayas** — the API **clamps `limit` to 20 server-side** regardless
  of what you request (`limit=100` returns 20 and echoes `"limit": 20`),
  so paging steps by 20 via `offset`. `totalCount` is ~99,000, far past
  anything worth fetching; `_MAX_PAGES = 15` makes this a "most recent
  ~300 postings" window, which is meaningful because results are sorted
  newest-first. `Job.url` uses `guid` rather than `applicationLink` — the
  latter is sometimes a third-party ATS URL, which would make `dedup_key`
  depend on where a company happens to host its board. The API can repeat
  a posting across pages when new jobs are published mid-fetch (the offset
  window shifts), so the fetcher dedupes on `guid` before returning.
- **Jobicy** — `count` is **clamped to 100 server-side** (asking for 200
  or 500 returns 100) and there is no offset/page parameter, so **100
  most-recent postings per run is a hard ceiling for this source**, not a
  self-imposed cap. Their API response carries a `friendlyNotice` asking
  that Jobicy be credited with a direct link to the source; `Job.url` is
  always the jobicy.com posting page. In practice this source is almost
  entirely full-time employment (a live sample of 100 was 100%
  `jobType: ["Full-Time"]`), so it adds volume rather than contract work.
  `jobIndustry` values arrive HTML-escaped and are unescaped here.
- **We Work Remotely** — RSS, not JSON, parsed with stdlib
  `xml.etree.ElementTree` (the project has no RSS/XML dependency and
  doesn't need one). `<title>` packs company and role into one string as
  `"Company: Role"`; splitting on the first `": "` is best-effort — a
  role whose own title contains `": "` before the separator would split
  wrong, and a title with no separator at all falls back to a fixed
  `"(company not stated)"` placeholder. That placeholder is exactly why
  `weworkremotely` is listed in `dedupe._UNIQUE_ROLE_ID_SOURCES`: two jobs
  sharing it would otherwise score `company_similarity` 1.0 and be
  fuzzy-merged. `<country>`, `<state>` and `<skills>` are frequently
  present but **empty** rather than absent. Three feeds are read (site-wide
  plus the programming and devops category feeds) and deduped by `<guid>`,
  since the site-wide feed alone drops older engineering roles.
- **Arbeitnow** — **expect a low relevant yield, by design.** This is a
  general German job board, not a tech/remote one: measured live, only
  ~5% of postings are remote and the large majority are non-engineering,
  so Stage 2's relevance filter correctly discards most of what's fetched
  (a real run: 559 fetched, 34 landing in eligible-and-relevant). That is
  the pipeline working, not something to "fix" by loosening the filter.
  Its structured `remote` boolean is rendered into `location_text` as
  `"Berlin, vor Ort"` / `"Berlin, Remote"` — Stage 1 reads text, so a bare
  `"Berlin"` would otherwise pass as eligible when it's a commute-to-Berlin
  role; both markers are already in `config.yaml`'s vocabulary. `job_types`
  mixes engagement types with seniority labels (`"berufserfahren"`,
  `"Mid"`, `"berufseinstieg"`) and is often empty, so `contract_type_guess`
  is `unclear` for many postings — the correct answer, since those labels
  say nothing about the engagement. The API's `url` field is sometimes the
  company's own homepage rather than the Arbeitnow job page; it is used
  as-is anyway because across 576 real records its normalized form was
  exactly as unique as `slug` (559 distinct each), and the canonical
  Arbeitnow URL cannot be reconstructed for a genuinely external listing
  (`/jobs/companies/{company}/{slug}` returns 410 for those, which would
  hand you a dead link instead of a working one).
- **HN "Freelancer? Seeking freelancer?"** — **this source will usually
  return zero jobs, and that is the normal case.** The thread's convention
  is to lead with either `SEEKING WORK` (an individual advertising
  themselves — not a job) or `SEEKING FREELANCER` (a client with a project
  — a job), and only the latter is kept. Measured live across the five
  threads from April–August 2026: 102 top-level comments, of which 95 were
  `SEEKING WORK`, 5 were unmarked self-profiles, and exactly **1** was
  `SEEKING FREELANCER` — roughly one usable posting every five months. It
  is included because it is a ~40-line subclass of the existing
  `hn_whoishiring` fetcher (inheriting the Algolia fetch, concurrent board
  resolution and ATS bundle expansion unchanged) and because a client
  posting a real project is the single best match for a B2B contract
  profile — not because it will move the numbers. Header parsing inherits
  every caveat of the "Who is hiring?" fetcher above, after stripping the
  leading `SEEKING FREELANCER` marker.
- **All sources** — the eligibility filter works by matching curated
  phrases (see `config.yaml`), not by NLP/entity extraction. It reliably
  catches explicit statements like `"US citizens only"` or `"visa
  sponsorship required"`, but a posting that enumerates a specific
  whitelist of countries without a trigger phrase (e.g. `"REMOTE
  (US/Canada/Brazil/Poland/UK/India)"`, which doesn't include Albania but
  also doesn't say "only") will not be caught and needs manual review.
  When in doubt, check the "Reasons"/"Red flags" columns in `report.html`
  before applying.
- **Structured hiring-location allow-lists are only partly handled.**
  Himalayas (`locationRestrictions: ["United States"]`) and Jobicy
  (`jobGeo: "USA"`) state where a company will hire as structured data,
  but as bare country names — and a bare name is indistinguishable from a
  passing mention to a phrase matcher. Left alone, that put 79 of 227
  Himalayas postings and 35 of 100 Jobicy postings in the `eligible`
  bucket while the source itself said US-only.
  `fetchers.common.format_location_restrictions` renders these as
  `"<country> only"`, which both states what the field actually asserts
  and lands in vocabulary `config.yaml` already has (`"USA only"` is an
  exclude phrase, `"Europe only"` a needs-review phrase). **This does not
  fully solve it**: an allow-list naming any of the ~40 other countries
  seen in real data still needs a matching `"<country> only"` phrase in
  `config.yaml` to be caught, and only the four that dominate live data
  are listed there. The general rule — "the source named an explicit
  country list and Albania isn't in it" — is structural and can't be
  expressed as a phrase; catching it properly would need a new Stage 1
  rule that understands allow-lists, which is not built.
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
                into one Job per role (used by hn_whoishiring.py);
                board_registry.py (Phase 4.5) re-polls every board ever
                discovered; github_lists.py (Phase 4.5) seeds it from
                public GitHub company lists
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

To also exercise the Postgres backend locally (CI always does):

```bash
docker run --rm -d --name jobscout-test-pg -p 5432:5432 \
  -e POSTGRES_USER=jobscout -e POSTGRES_PASSWORD=jobscout -e POSTGRES_DB=jobscout_test \
  postgres:16-alpine
TEST_DATABASE_URL=postgresql://jobscout:jobscout@localhost:5432/jobscout_test \
  .venv/bin/python -m pytest -q
docker stop jobscout-test-pg
```

Covers `filters.py` (every eligibility edge case called out in the spec:
US-only, EU-only vs. needs_review, worldwide, EST overlap vs. PST-required,
hybrid-with/without-remote-mention, German posts not excluded for language,
punctuation/case tolerance), `ranker.py` (score bounds, LLM/RAG keywords
outscoring generic Python, title/positive/recency bonuses, all four
`contract_type_guess` branches), `dedupe.py`, `db.py` (including the
`seniority_fit` column migration for pre-Phase-3 sqlite databases; when
`TEST_DATABASE_URL` is set — e.g. in CI — the same test bodies also re-run
against a real Postgres backend to exercise the psycopg2 code path),
`htmlutils.py`,
`config.py` (`ai` block parsing, `ranking_mode` validation), the `gemini`/
`anthropic` providers' error normalization in `jobscout/llm/` (mocked SDK
clients, no real network calls), `ai_ranker.py` (structured-output mapping,
retry/backoff, fatal-vs-transient error handling, cost/token summary),
`pipeline.py`'s AI dispatch/caching/fallback logic and `rerank_job()`,
`board_registry.py` and `github_lists.py` (mocked network boundaries, no
real HTTP calls), and `pipeline.py`'s registry/discovery wiring and the
`board_registry`/`github_lists` `enabled` toggles.
