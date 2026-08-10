# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Environment — read this first

This project's actual Python environment (venv, interpreter, all code) lives inside the **WSL Ubuntu filesystem** at `/home/lori28/JobScout`. If your Bash tool is Windows Git Bash (MINGW64) rather than a native WSL shell, `python3.11`, `pip`, and `pytest` are **not** reachable directly — every command must be run through:

```bash
wsl.exe -d Ubuntu -- bash -lc "cd /home/lori28/JobScout && <command>"
```

Check which shell you're in first (`uname -a` — `MINGW64_NT` means you need the wrapper; anything else, you likely don't).

## Commands

Install (bootstraps pip via stdlib `ensurepip` — `pip`/`uv` are not assumed pre-installed):

```bash
python3.11 -m venv .venv
.venv/bin/python -m ensurepip --upgrade
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -e '.[dev]'
```

Run the full pipeline (fetch → dedupe → filter → rank → store → report):

```bash
.venv/bin/python -m jobscout run
```

Run tests:

```bash
.venv/bin/python -m pytest -q                          # full suite
.venv/bin/python -m pytest tests/test_filters.py -q     # one file
.venv/bin/python -m pytest tests/test_filters.py::test_us_citizens_only_excluded -q   # one test
```

Debug why a specific job landed in a given bucket:

```bash
.venv/bin/python -m jobscout --log-level DEBUG run
```

No linter/formatter is configured yet.

## Architecture

See [README.md](README.md) for install/run/tuning details and per-source known limitations, and [PLAN.md](PLAN.md) for the 5-phase roadmap — **only Phase 1 is built**. The points below are the parts that only become clear by reading across multiple files.

**Config-driven, not code-driven.** `config.yaml` (phrase lists, weights, source toggles) and `profile.yaml` (the user's role/skill profile) are loaded fresh every run by `config.py` into typed dataclasses (`AppConfig`, `Profile`) and threaded through as plain arguments. `filters.py` and `ranker.py` never read a file themselves — this is what keeps them pure functions, testable with inline fixtures instead of fixture files, and editable by the user without touching code.

**Two independent classification axes, not one.** A `Job` has `eligibility_bucket` (eligible / excluded / needs_review, set by Stage 1 in `filters.apply_eligibility_filter`) and `is_relevant` (bool, set independently by Stage 2 in `filters.apply_keyword_relevance_filter`). A job can be both excluded *and* irrelevant at once. `report.effective_bucket()` derives the single display bucket from both (irrelevant takes display precedence), but the underlying fields stay separate so each stage's audit trail (`eligibility_reason`, `reasons`/`red_flags`) is preserved regardless of the other's outcome.

**Phrase matching is negation-aware, not plain substring.** `filters.find_phrase_matches()` checks a 5-word window before each match for a negation cue (not/no/never/without/contracted forms) and skips that occurrence — this applies uniformly to every phrase category (exclude, hybrid/onsite, positive signals, relevance keywords), so fixing it once in `find_phrase_matches` fixes it everywhere. A phrase mentioned twice, negated once and not the other time, still counts.

**Ranking and eligibility never fully trust the parsed title.** `ranker.score_job()` is only called for jobs that are relevant and not hard-excluded; `ranker.trivial_rank_result()` handles the rest with a fixed score of 0. A `needs_review` job that *is* ranked always floors at score 1 (never 0), specifically so it can't look visually identical to an excluded/irrelevant job in a score-sorted view.

**Fetchers never raise.** Each fetcher in `fetchers/` catches its own errors internally, logs a warning, and returns `[]`; `pipeline.run()` wraps each fetcher call in try/except anyway as defense in depth. `fetchers/__init__.py`'s `FETCHER_REGISTRY` dict is the extension point for new sources — adding one means a new file plus one registry entry, no changes to `pipeline.py`.

**Dedup is two-stage and company-similarity-gated.** `dedupe.py` matches on normalized URL first, then falls back to a fuzzy company+title comparison (`difflib.SequenceMatcher`) — but only if the two companies are *independently* similar above a floor (`_MIN_COMPANY_SIMILARITY`). This exists because a long shared boilerplate/placeholder title (e.g. two different companies both hitting the HN fetcher's "role not stated" fallback) can otherwise dominate the combined-string similarity score and cause two unrelated postings to merge.

**HN "Who is hiring" parsing is inherently best-effort.** There is no structured format — `fetchers/hn_whoishiring.py` splits the header line on `|` and skips segments that look like salary/employment-type/location/URL to guess which segment is the role, but posters don't use a consistent field order. The full plain-text body is always preserved in `description` regardless of title-parsing quality, so eligibility/relevance/ranking are unaffected — only the *displayed* title/company can be off. See README's "Known limitations" for the specific patterns this does and doesn't catch.

**The `Job` dataclass (`models.py`) is the contract across all 5 phases**, not just Phase 1. Fields like `status`, `application_channel`, `research_brief_path`, and `outreach_draft_path` exist now even though Phase 1 only ever sets them to defaults — later phases read/write them without any schema change. `db.py`'s upsert deliberately excludes `status` (and only `status`) from its `ON CONFLICT DO UPDATE` clause, so a re-fetch never clobbers a user's workflow state once Phase 2 starts setting it.