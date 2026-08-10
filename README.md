# JobScout

A job aggregation and matching system for a remote AI/LLM engineer based in
Albania. Fetches postings from multiple sources, filters out ones that
aren't legally/practically doable from Albania, ranks the rest against a
hardcoded skills/experience profile, and produces a CLI summary plus a
static HTML report. See [PLAN.md](PLAN.md) for the full 5-phase roadmap —
**this repo currently implements Phase 1 only.**

## Status

Phase 1: done. Fetchers (RemoteOK, Remotive, HN "Who is hiring?"), dedupe,
eligibility filter, keyword relevance filter, heuristic ranking, CLI +
`report.html` output, full pytest coverage of the filter/ranker/dedupe
logic. No dashboard, no AI ranking, no other fetchers yet.

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
Fetched: 334 total | Stored after dedupe: 303
eligible: 74  needs_review: 3  excluded: 60  irrelevant: 166

Top 20 by score:
SCORE  BUCKET    SOURCE          TITLE                          COMPANY   CONTRACT
...

Full report: file:///home/lori28/JobScout/report.html
```

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

## Tuning

- **`profile.yaml`** — role targets, core skills, experience, languages,
  work-setup preference, location. Edit freely; reloaded every run.
- **`config.yaml`** — source on/off toggles, `ranking_mode` (only
  `heuristic` is implemented in Phase 1), `min_score_threshold`, every
  phrase list used by the eligibility filter (`exclude_phrases`,
  `hybrid_onsite_phrases`, `remote_indicator_phrases`,
  `needs_review_phrases`, `positive_phrases`), the keyword relevance
  pre-filter list, and the ranker's weighted `keyword_groups` + bonus caps.

All phrase matching is case-insensitive, punctuation-tolerant, and
word-boundary-anchored (so `"US CITIZENS ONLY"`, `"U.S. Citizens Only"`,
and `"us-citizens-only"` all match a configured `"us citizens only"`
phrase, but short keywords like `"ai"` or `"ml"` never fire inside
unrelated words like `"certain"` or `"html"`).

## Known limitations per source

- **RemoteOK** — the public API returns only ~100 most-recent postings per
  request (no pagination in Phase 1), and `location`/`tags` fields are
  occasionally free-text/malformed on the source side.
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
  off. Email/URL extraction for `application_channel` is a plain regex
  over the comment body; when a poster advertises multiple roles under one
  link to their general careers page, that's the only URL that exists in
  the text — there's no per-role link to extract. Comments that look like
  a candidate's own "who wants to be hired"-style self-profile (fields
  like `"Willing to relocate:"` or `"Résumé/CV:"`) are detected and
  skipped entirely rather than surfaced as fake jobs, since people
  occasionally cross-post those into the hiring thread.
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
  fetchers/     one module per source + a registry (Phase 4 drops in here)
  dedupe.py     normalized-URL + fuzzy company/title matching
  filters.py    pure eligibility + keyword-relevance functions
  ranker.py     heuristic scorer (Phase 3 adds an AI scorer, same output shape)
  db.py         stdlib sqlite3 storage
  pipeline.py   orchestrates fetch -> dedupe -> filter -> rank -> store -> report
  report.py     CLI table + report.html
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
`contract_type_guess` branches), `dedupe.py`, `db.py`, and `htmlutils.py`.
