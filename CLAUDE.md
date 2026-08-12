# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Environment — read this first

This project's actual Python environment (venv, interpreter, all code) lives inside the **WSL Ubuntu filesystem** at `/home/lori28/JobScout`. If your Bash tool is Windows Git Bash (MINGW64) rather than a native WSL shell, `python3.11`, `pip`, and `pytest` are **not** reachable directly — every command must be run through:

```bash
wsl.exe -d Ubuntu -- bash -lc "cd /home/lori28/JobScout && <command>"
```

Check which shell you're in first (`uname -a` — `MINGW64_NT` means you need the wrapper; anything else, you likely don't).

**Node.js lives in WSL too, but via `nvm`, not apt or the system PATH.** There is no system-wide `node`/`npm` in WSL (no passwordless `sudo`, so `apt install nodejs` isn't an option) and Windows' native npm (`C:\Program Files\nodejs`) cannot reliably run against the `\\wsl$` UNC path (hits a "Maximum call stack size exceeded" bug). Node was installed via `nvm` into `~/.nvm` instead — every command from the wrapper above needs it sourced first:

```bash
wsl.exe -d Ubuntu -- bash -lc "source \$HOME/.nvm/nvm.sh && cd /home/lori28/JobScout/frontend && <npm command>"
```

`scripts/dev.sh` already sources it automatically.

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

Dashboard (Phase 2) — build the frontend once, then serve API + built frontend from one process:

```bash
cd frontend && npm install && npm run build
.venv/bin/python -m jobscout serve   # http://127.0.0.1:8000
```

Dashboard dev mode (backend `--reload` + Vite hot reload together, one process group, Ctrl-C stops both):

```bash
./scripts/dev.sh   # http://localhost:5173, proxies /api to :8000
```

AI-assisted ranking (Phase 3) — copy `.env.example` to `.env`, fill in the key for whichever `ai.provider` is set in `config.yaml` (`GEMINI_API_KEY` by default, or `ANTHROPIC_API_KEY` if `ai.provider: anthropic`), then set `ranking_mode: ai` in `config.yaml` and run normally:

```bash
.venv/bin/python -m jobscout run
```

No linter/formatter is configured yet.

## Architecture

See [README.md](README.md) for install/run/tuning details and per-source known limitations, and [PLAN.md](PLAN.md) for the 5-phase roadmap — **Phases 1, 2, and 3 are built**. The points below are the parts that only become clear by reading across multiple files.

**Phase 2's dashboard (`jobscout/web/` + `frontend/`) is purely additive.** It reuses `db.py`/`pipeline.py` exactly as Phase 1 left them — `routes.py`'s `GET /api/jobs` returns every stored job unfiltered (bucket/source/status/score filtering happens client-side in React, mirroring `report.py`'s existing "dump everything, let the display layer handle it" approach) and `POST /api/fetch` just calls `pipeline.run()` synchronously. Status updates address a job by `dedup_key` in the POST body rather than a URL path segment, since `dedup_key` is a normalized URL (contains `/`, `:`) and not path-segment-safe — no `id` field was added to `Job`/`db.py` to avoid a shared-model change for a purely additive feature.

**Config-driven, not code-driven.** `config.yaml` (phrase lists, weights, source toggles) and `profile.yaml` (the user's role/skill profile) are loaded fresh every run by `config.py` into typed dataclasses (`AppConfig`, `Profile`) and threaded through as plain arguments. `filters.py` and `ranker.py` never read a file themselves — this is what keeps them pure functions, testable with inline fixtures instead of fixture files, and editable by the user without touching code.

**Two independent classification axes, not one.** A `Job` has `eligibility_bucket` (eligible / excluded / needs_review, set by Stage 1 in `filters.apply_eligibility_filter`) and `is_relevant` (bool, set independently by Stage 2 in `filters.apply_keyword_relevance_filter`). A job can be both excluded *and* irrelevant at once. `report.effective_bucket()` derives the single display bucket from both (irrelevant takes display precedence), but the underlying fields stay separate so each stage's audit trail (`eligibility_reason`, `reasons`/`red_flags`) is preserved regardless of the other's outcome.

**Phrase matching is negation-aware, not plain substring.** `filters.find_phrase_matches()` checks a 5-word window before each match for a negation cue (not/no/never/without/contracted forms) and skips that occurrence — this applies uniformly to every phrase category (exclude, hybrid/onsite, positive signals, relevance keywords), so fixing it once in `find_phrase_matches` fixes it everywhere. A phrase mentioned twice, negated once and not the other time, still counts.

**Ranking and eligibility never fully trust the parsed title.** `ranker.score_job()` is only called for jobs that are relevant and not hard-excluded; `ranker.trivial_rank_result()` handles the rest with a fixed score of 0. A `needs_review` job that *is* ranked always floors at score 1 (never 0), specifically so it can't look visually identical to an excluded/irrelevant job in a score-sorted view.

**Fetchers never raise.** Each fetcher in `fetchers/` catches its own errors internally, logs a warning, and returns `[]`; `pipeline.run()` wraps each fetcher call in try/except anyway as defense in depth. `fetchers/__init__.py`'s `FETCHER_REGISTRY` dict is the extension point for new sources — adding one means a new file plus one registry entry, no changes to `pipeline.py`.

**Dedup is two-stage and company-similarity-gated.** `dedupe.py` matches on normalized URL first, then falls back to a fuzzy company+title comparison (`difflib.SequenceMatcher`) — but only if the two companies are *independently* similar above a floor (`_MIN_COMPANY_SIMILARITY`). This exists because a long shared boilerplate/placeholder title (e.g. two different companies both hitting the HN fetcher's "role not stated" fallback) can otherwise dominate the combined-string similarity score and cause two unrelated postings to merge.

**HN "Who is hiring" parsing is inherently best-effort.** There is no structured format — `fetchers/hn_whoishiring.py` splits the header line on `|` and skips segments that look like salary/employment-type/location/URL to guess which segment is the role, but posters don't use a consistent field order. The full plain-text body is always preserved in `description` regardless of title-parsing quality, so eligibility/relevance/ranking are unaffected — only the *displayed* title/company can be off. See README's "Known limitations" for the specific patterns this does and doesn't catch.

**The `Job` dataclass (`models.py`) is the contract across all 5 phases**, not just Phase 1. Fields like `status`, `application_channel`, `research_brief_path`, and `outreach_draft_path` exist now even though Phase 1 only ever sets them to defaults — later phases read/write them without any schema change. `db.py`'s upsert deliberately excludes `status` (and only `status`) from its `ON CONFLICT DO UPDATE` clause, so a re-fetch never clobbers a user's workflow state once Phase 2 starts setting it.

**The AI scorer is a provider behind an interface, not a vendor call.** `ai_ranker.AIRanker` never imports `anthropic` or `google.genai` directly — it calls `LLMProvider.complete_structured()` (`jobscout/llm/base.py`), and each concrete provider (`jobscout/llm/gemini.py`, `jobscout/llm/anthropic_provider.py`) normalizes its own SDK's exceptions into just two cross-provider types, `TransientProviderError` (retryable) and `FatalProviderError` (bad/missing key — disables the ranker for the rest of the run after one warning, rather than retrying every remaining job). `AIRanker` owns the single retry/backoff loop shared by every provider, since retry semantics differ per SDK. `jobscout/llm/__init__.py`'s `PROVIDER_REGISTRY` is the extension point for a new provider — same pattern as `fetchers/__init__.py`'s `FETCHER_REGISTRY`.

**AI ranking is cached by `ranking_source`, which means `pipeline.py` now reads the DB before it ranks, not just after.** Phase 1/2 only ever wrote to the DB at the end of a run. Phase 3's "never re-rank the same job twice in the same mode" requirement means `pipeline.run()` now opens the DB and loads existing rows *before* the ranking loop, so a job whose stored `ranking_source` is already `ai` reuses those fields instead of calling the provider again. `pipeline.rerank_job()` is the one place that intentionally bypasses this cache (an explicit per-job dashboard action) — and unlike `run()`, it re-raises on AI failure instead of falling back to the heuristic scorer, since silently downgrading an explicitly-requested AI result would misrepresent what happened.

**Fuzzy dedup skips two ATS-expansion roles from the same company.** `dedupe._is_ats_expansion_role()` checks `Job.source_id` for the `:ashby:`/`:greenhouse:` marker `ats_boards.posting_to_job()` sets — when both sides of a fuzzy comparison are ATS-expansion roles, `_fuzzy_similarity()` returns `0.0` unconditionally. Real case: two distinct Starbridge roles ("Account Executive - Mid Market" vs. "...| NYC") scored 0.95 similarity, above `FUZZY_THRESHOLD` (0.88), and got wrongly merged — the fuzzy matcher exists to catch the *same* posting mirrored under different URLs, not to distinguish between several *different* same-company roles, which only became possible once one HN comment could expand into many Jobs. The skip only applies when *both* sides are ATS-expansion roles — cross-source fuzzy dedup (comparing an expansion role against a normal fetcher result) is untouched.

**HN board-root links get expanded before dedupe/filtering ever sees them, not after.** `hn_whoishiring.py`'s `_expand_bundled_board()` runs inside `fetch()`, immediately after `_parse_comment()` builds the base Job — so by the time `pipeline.py`'s dedupe/eligibility/relevance/ranking stages run, a bundled Ashby/Greenhouse link has already become N independent Jobs (one per role, own title/description/URL), each judged on its own merits. `ats_boards.detect_board()` only matches a *bare* board-root URL (no extra path segment) — a link that already names one specific role is left alone, since it isn't a bundle. Any failure (bad API response, empty board, unsupported platform) falls back to the original single Job for that comment; a bundle expansion must never lose a posting outright, same philosophy as "fetchers never raise."

**`eligibility_confidence` is never something the LLM is asked to guess.** It's a fixed, deterministic mapping from `eligibility_bucket` (`ranker.ELIGIBILITY_CONFIDENCE_BY_BUCKET`/`eligibility_confidence_for()`) that both the heuristic and AI scorer call into — Stage 1 (`filters.apply_eligibility_filter`) already decided the bucket before either scorer runs, so there's nothing for a scorer to judge there. The AI structured-output schema (`ai_ranker._AIRankingSchema`) has no `eligibility_confidence` field at all.