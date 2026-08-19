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

## Git workflow

This repo uses a solo-dev branch-per-change workflow. Two PreToolUse hooks enforce the hard rules below as a safety net — follow them proactively rather than relying on the hooks to catch a mistake.

- **Never edit or write code directly on `main`.** Before starting any change, create a branch: `feature/<name>` for new functionality, `fix/<name>` for bug fixes, `hotfix/<name>` for urgent fixes. Do the work and commit there. `.claude/hooks/main-branch-edit-guard.sh` blocks Edit/Write on `main`/`master` for anything other than docs and config — `*.md`, `*.yaml`/`*.yml`, and `.claude/**` are exempt so things like this file or `config.yaml` can still be maintained directly on main.
- **Never commit directly on `main`.** `.claude/hooks/git-guard.sh` blocks it regardless of what's being committed.
- **Never add a "Co-Authored-By: Claude" trailer to a commit message.** This project doesn't want it — omit it entirely rather than relying on `attribution` settings.
- **To land a branch, merge it into `main` with a fast-forward only merge** (`git checkout main && git merge --ff-only <branch>`), then delete the branch (`git branch -d <branch>`). If `--ff-only` fails because `main` has diverged, stop and ask — don't force it and don't fall back to a merge commit without checking with the user first.
- **Push `main` to `origin` right after a successful merge, without asking** — this is pre-authorized for this workflow specifically, since the hook blocks the dangerous cases (force-push, wrong branch). Feature/fix/hotfix branches themselves stay local-only and are never pushed to `origin`.
- **Never force-push (`--force`/`--force-with-lease`) to `main`/`master`.** If history has diverged, resolve it with a normal merge, not by overwriting shared history.

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

**A fetcher's structured employment type outranks the ranker's prose scan.** Phase 4's sources state the engagement type as a real field (Himalayas `employmentType`, We Work Remotely `<type>`, Lever `categories.commitment`, Jobicy `jobType`, Arbeitnow `job_types`), so their fetchers map it onto `Job.contract_type_guess` via `fetchers.common.contract_type_from_label` *before* ranking. `ranker.score_job` used to overwrite that unconditionally; it now calls `ranker.resolve_contract_type(job, text_norm)`, which only falls back to `guess_contract_type`'s phrase scan when the fetcher left the value `UNCLEAR`. `ai_ranker.py` applies the same precedence with the LLM's answer as the fallback, so the dashboard's contract column means the same thing in both ranking modes. Sources with no such field (RemoteOK, Remotive, HN) are on the unchanged Phase 1 path.

**`dedupe._has_source_assigned_role_id` replaced the ATS-substring check, and is what any new multi-role-per-company source must opt into.** The old `_is_ats_expansion_role` grepped `source_id` for `":ashby:"`/`":greenhouse:"`. Every Phase 4 source hits the same problem that check was added for — one company legitimately posting several near-identically-titled roles, fuzzy-merged at `FUZZY_THRESHOLD = 0.88` — so the check now also returns True for any `job.source` in `_UNIQUE_ROLE_ID_SOURCES` (the sources whose API assigns a stable unique id/URL per role), and the ATS marker regex covers `:lever:` too. `_fuzzy_similarity` additionally requires `a.source == b.source` before skipping: cross-source fuzzy matching is the entire reason fuzzy dedup exists (one posting mirrored on two boards) and must keep working. This is behavior-preserving for the ATS case, where both expansion roles are always `source="hn_whoishiring"`. `remoteok`/`remotive` are deliberately excluded from the set — adding them would change Phase 1 behavior with no evidence it's needed.

**Two Phase 4 fetchers write structured non-text fields into `location_text` on purpose, because Stage 1 reads text, not fields.** `filters.job_eligibility_text` concatenates title + description + location_text, so a structured answer that never reaches that string is invisible to eligibility. Arbeitnow's `remote` boolean is rendered as `"Berlin, vor Ort"` / `"Berlin, Remote"`, and Lever's `workplaceType` is appended to its location — both use vocabulary `config.yaml` already has (`vor ort` in `hybrid_onsite_phrases`, `remote` in `remote_indicator_phrases`). Without this a commute-to-Berlin role passes as `eligible`; with it, a real run correctly excluded 304 of 559 Arbeitnow postings. `fetchers.common.format_location_restrictions` does the same job for Himalayas' `locationRestrictions` and Jobicy's `jobGeo` by appending `" only"` — see README for why that only partly solves the allow-list problem. This is translating a source's own structured answer into the channel the filter reads; it is *not* licence to invent facts the source didn't state.

**`ats_boards.resolve_board` has two resolution strategies, cheapest first, because a redirect and an embedded link are different problems.** The original implementation only followed HTTP redirects (a streamed, header-only request, closed without reading the body) — built for vanity URLs that 30x to their ATS board (`starbridge.ai/careers` → `jobs.ashbyhq.com/starbridge`). Real case that strategy can't see: `langfuse.com/careers` returns a plain `200` with **no redirect at all** — it's Langfuse's own rendered careers page, which merely *links* `jobs.ashbyhq.com/langfuse` (7 real open roles) from an anchor in the body. Since nothing redirects, the header-only check finds nothing, and the whole HN comment fell back to one bundled Job titled from the comment's own aggregate header instead of 7 per-role Jobs. `resolve_board` now falls through to a second, heavier request only when the first finds nothing: downloads the actual page body and regex-scans its HTML (via `fetchers.common.extract_href_urls`) for an `href` matching a known ATS board-root pattern. The two-request split matters for cost — the cheap streamed check still runs first for every one of a thread's ~100+ candidate links, and only the subset that doesn't redirect anywhere pays for a full body download.

**Workable is a 4th `ats_boards.py` platform with a real content gap the other three don't have, so it gets different treatment.** Ashby/Greenhouse/Lever's JSON APIs each include a full posting description; Workable's public widget list API (`apply.workable.com/api/v1/widget/accounts/{slug}`) has none at all — the only place description-shaped text exists is each role's individual page, which is a Cloudflare-protected client-rendered SPA whose sole server-rendered text is a truncated ~250-char SEO `<meta description>` tag. `_fetch_workable_snippet` fetches that best-effort (never raises), and `_workable_posting_to_job` prepends the *original* HN comment's full text ahead of it — passed through as the new `original_description` kwarg on `posting_to_job`, which only Workable consumes; Ashby/Greenhouse/Lever ignore it since their own API descriptions are already complete. This means relevance/skill-match scoring for an expanded Workable role isn't solely dependent on a thin, truncated blurb. Workable is also the only platform whose response carries a board-level company name (`data["name"]`) — more reliable than the HN comment's own parsed company, which can be badly wrong for a header with no `|` delimiters (real case: "Sumble is the newco..." became both the fallback title AND company). That name is stashed as a synthetic `_board_company_name` key on each posting dict (JobScout's own addition, not part of Workable's response) for `_workable_posting_to_job` to prefer over the comment-parsed fallback.

**`_bulleted_roles_title` recovers a better display title for the placeholder case, without attempting to split the comment into multiple Jobs.** When `_parse_company_and_title` falls back to `"Role not stated in header — see description"` (no role-like segment on the header line), `_parse_comment` tries this as a second pass: some posters list several role names as body bullets under an "Open roles:"/"Open now:" heading instead (real cases: Flywheel Motion, Foxglove). Anchoring to that heading and only taking the *contiguous* run of bullets right after it is deliberate, not incidental — a bare "line starts with -" scan produces a false positive on a real comment (Reef Technologies) whose perks/benefits bullets read exactly like role names but have no such heading before them, so the heading anchor correctly excludes it. This is display-quality only: the comment still becomes one bundled Job, same as today, just with `"Multiple roles: A, B, C"` instead of the generic placeholder when at least 2 plausible names are found. Splitting genuinely into N separate Jobs from prose alone (as opposed to a known ATS board API) remains the harder, unsolved problem described above and in README's known limitations — this does not attempt it, including for cases like Pomelo Care that have per-bullet Greenhouse shortlinks but no heading for this heuristic to anchor on.

**`_bulleted_role_link_jobs` splits a comment into real per-role Jobs without ever calling an external API, because some posters already put each role's own URL right in the text.** This is a different, more tractable shape than the Ashby/Greenhouse/Lever/Workable board expansion in `ats_boards.py` (one shared board-root link) — here each bullet carries its own distinct application URL, so nothing needs to be fetched or resolved. Only tried when `_expand_bundled_board` found no shared board link, since a real ATS API's per-role data is richer than what can be recovered from prose. Measured live across a full thread fetch: 9 comments split this way (28 real Jobs) — spanning self-hosted careers pages (Mitte.ai, G-Research, Kadoa), generic shortlink/form services (`bit.ly`, `tally.so`), and ATS platforms not otherwise integrated here (Talentio, Pinpoint, Deel, and Greenhouse via its `grnh.se` shortlink) — with zero code needed for any of those specific platforms, since detection works on the comment's own bullet structure, not the target domain. Two exclusions matter: a URL on `linkedin.com`/`twitter.com`/`x.com`/`github.com` is treated as a person's profile, not an application link (real case: a "leadership team" bio bullet list matches the identical bullet+URL shape as a genuine role list) — and a role-name candidate wrapped in matching `*asterisks*` is rejected outright (real case: a job-board aggregator reposting *Company* — role — ... for several *different* companies under one comment, which is a fundamentally different shape this only handles for one company's own roles). A minimum of 2 valid pairs is required before splitting; below that, `[job]` is returned unchanged — same "must never lose a posting outright" contract as `_expand_bundled_board`. Requires `href_urls` (from `extract_href_urls` on the raw comment HTML) to resolve HN's own truncated-URL-with-`...`-in-plain-text display quirk, the same one `extract_apply_url` already works around — real case: a G-Research bullet's plain text read `.../performance-engineering-...` while the actual href was the complete `.../performance-engineering-manager/`. `source_id` is stamped `f"{comment_id}:bullet:{index}"`, recognized by `dedupe._has_source_assigned_role_id` alongside the ATS platform markers.

**`hn_freelancer.py` is a subclass of `HNWhoIsHiringFetcher`, not a copy.** The base class exposes exactly three hooks — `search_query`, `_matches_thread_title`, `_accept_comment` — and everything else (the two-step Algolia fetch, `_resolve_boards_concurrently`, `_expand_bundled_board`, header parsing) is inherited, which is why bundled ATS board links expand there for free. The one semantically important difference is that `_accept_comment` is *inverted*: in the "Who is hiring?" thread the filter rejects candidate self-profiles as the exception, while in the freelancer thread `SEEKING WORK` self-advertisements are the overwhelming majority (95 of 102 real comments) and only `SEEKING FREELANCER` posts are jobs. Expect this source to return zero on most runs — measured yield is ~1 posting per five months.

**`eligibility_confidence` is never something the LLM is asked to guess.** It's a fixed, deterministic mapping from `eligibility_bucket` (`ranker.ELIGIBILITY_CONFIDENCE_BY_BUCKET`/`eligibility_confidence_for()`) that both the heuristic and AI scorer call into — Stage 1 (`filters.apply_eligibility_filter`) already decided the bucket before either scorer runs, so there's nothing for a scorer to judge there. The AI structured-output schema (`ai_ranker._AIRankingSchema`) has no `eligibility_confidence` field at all.

**The `known_boards` registry exists because board discovery used to be thrown away every run, and `conn = init_db(db_path)` had to move to make that fixable.** Before this, the only way `pipeline.py` ever learned about an Ashby/Greenhouse/Lever/Workable board was `hn_whoishiring.py` resolving one mid-fetch, used once to expand that run's comment, then discarded — a board mentioned in one month's HN thread was invisible to every later run unless HN happened to mention it again. `jobscout/db.py`'s new `known_boards` table persists every board ever discovered (by HN or `github_lists.py`, see below) so `jobscout/fetchers/board_registry.py`'s `poll_known_boards()` can re-poll all of them on every future run. This required hoisting `conn = init_db(db_path)` in `pipeline.run()` from after the fetch loop (where Phase 3 had left it, opened just before ranking) to before it — needed both to read `known_boards` ahead of fetching and to record newly-discovered boards once fetching finishes. This is safe for Phase 3's AI-ranking cache read (`existing_by_key = {j.dedup_key: j for j in get_jobs(conn)}`) purely because nothing before that read writes to the `jobs` table this run — `upsert_job` still only happens after ranking, unchanged — so opening `conn` earlier doesn't change what `get_jobs(conn)` returns there.

**Board discovery and board polling are deliberately one run apart, never in the same run.** A board found via an HN comment this run is *not* also polled through `poll_known_boards` this same run — `hn_whoishiring._expand_bundled_board` already fully expands it into per-role Jobs as part of that fetch, and `pipeline.run()` only records the board into `known_boards` afterward. It only becomes independently pollable starting *next* run. The same lag applies to `github_lists.seed_from_github_lists`: a board it seeds this run isn't polled until the run after. This is intentional, not an oversight — `known_boards` is a next-run compounding mechanism, not a same-run double-fetch of a board already fully expanded through the normal path.

**`posting_to_job`'s `source` kwarg is what tells a registry-polled Job apart from an HN-discovered one, and dedupe doesn't care.** `ats_boards.py`'s four `_*_posting_to_job` functions used to hardcode `Job.source = "hn_whoishiring"` unconditionally, which would have mislabeled every Job `board_registry.poll_known_boards()` produces as having come from an HN comment that was never actually read this run. `source` now defaults to `"hn_whoishiring"` (so the original HN call site needs no changes) and `board_registry.py` passes `source="ats_board_registry"` explicitly — this is purely a `report.html`/dashboard display and audit-trail distinction. `dedupe._has_source_assigned_role_id`'s regex still keys off the `:ashby:`/`:greenhouse:`/`:lever:` marker embedded in `source_id`, not `Job.source`, so which value `source` holds has no effect on fuzzy-dedup behavior.