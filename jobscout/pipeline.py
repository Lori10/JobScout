"""Orchestrates fetch -> dedupe -> eligibility filter -> keyword filter ->
rank -> store -> report. This is the only module that touches every other
module — fetchers, filters, ranker, dedupe, db, report are all designed to
be called from here without needing to know about each other.
"""

from __future__ import annotations

import logging
import os
from dataclasses import replace

from jobscout.ai_ranker import AIRanker, AIRankingError
from jobscout.config import AIConfig, Profile, load_config, load_profile
from jobscout.db import get_job_by_dedup_key, get_jobs, init_db, upsert_job, upsert_known_board
from jobscout.dedupe import dedupe
from jobscout.fetchers import FETCHER_REGISTRY
from jobscout.fetchers.board_registry import poll_known_boards
from jobscout.fetchers.github_lists import seed_from_github_lists
from jobscout.filters import apply_eligibility_filter, apply_keyword_relevance_filter
from jobscout.llm import PROVIDER_REGISTRY, build_provider
from jobscout.models import EligibilityBucket, Job, RankingSource
from jobscout.ranker import RankResult, score_job, trivial_rank_result
from jobscout.report import print_summary, render_html_report

logger = logging.getLogger(__name__)


def _build_ai_ranker(ai_config: AIConfig, profile: Profile) -> AIRanker | None:
    """Returns None (never raises) if AI ranking can't run right now — the
    caller falls back to heuristic scoring for the whole run, per Phase 3's
    "automatic fallback on missing key" requirement."""
    provider_cls = PROVIDER_REGISTRY.get(ai_config.provider)
    if provider_cls is None:
        logger.warning("ai.provider=%r is not a known provider, falling back to heuristic", ai_config.provider)
        return None

    api_key = os.environ.get(provider_cls.api_key_env)
    if not api_key:
        logger.warning(
            "ranking_mode=ai but %s is not set, falling back to heuristic for this run",
            provider_cls.api_key_env,
        )
        return None

    provider = build_provider(ai_config.provider, api_key, ai_config.model)
    return AIRanker(provider, ai_config, profile)


def _cached_ai_result(existing: Job | None) -> RankResult | None:
    """A job already AI-ranked is never re-scored on a later run (DB-cached
    by ranking_source) — only an explicit re-rank (rerank_job) bypasses this."""
    if existing is None or existing.ranking_source != RankingSource.AI:
        return None
    return RankResult(
        score=existing.score or 0,
        skill_match=existing.skill_match or 0,
        eligibility_confidence=existing.eligibility_confidence or 0,
        contract_type_guess=existing.contract_type_guess,
        reasons=existing.reasons,
        red_flags=existing.red_flags,
        ranking_source=RankingSource.AI,
        seniority_fit=existing.seniority_fit,
    )


def run(
    config_path: str = "config.yaml",
    profile_path: str = "profile.yaml",
    db_path: str = "data/jobscout.db",
    report_path: str = "report.html",
) -> None:
    config = load_config(config_path)
    profile = load_profile(profile_path)

    # Opened before fetching (not just before ranking, as Phase 3 had it) so
    # poll_known_boards below can read the known_boards registry, and so
    # newly-discovered boards can be recorded once fetching finishes. This
    # is safe for the AI-ranking cache read further down: nothing before it
    # writes to `jobs` this run (upsert_job doesn't happen until after
    # ranking), so opening conn earlier doesn't change what get_jobs(conn)
    # returns there.
    conn = init_db(db_path)

    all_jobs: list[Job] = []

    if config.board_registry.enabled:
        try:
            registry_jobs = poll_known_boards(conn, max_workers=config.board_registry.max_workers)
            logger.info("board_registry: polled known boards -> %d jobs", len(registry_jobs))
        except Exception:
            logger.exception("board_registry: poll failed unexpectedly, continuing")
            registry_jobs = []
        all_jobs.extend(registry_jobs)

    discovered_boards: list[tuple[str, str, str | None, str | None]] = []
    for source_name, fetcher_cls in FETCHER_REGISTRY.items():
        if not config.sources.get(source_name, True):
            logger.info("skipping disabled source: %s", source_name)
            continue
        try:
            fetcher = fetcher_cls()
            fetched = fetcher.fetch()
            logger.info("%s: fetched %d jobs", source_name, len(fetched))
        except Exception:
            # Fetcher contract says fetch() never raises, but this is
            # defense in depth: one dead source must never abort the run.
            logger.exception("%s: fetch failed unexpectedly, continuing", source_name)
            fetched = []
            fetcher = None
        all_jobs.extend(fetched)
        # Not every Fetcher tracks this (only HNWhoIsHiringFetcher/
        # HNFreelancerFetcher do) — getattr with a default keeps this from
        # requiring a Fetcher protocol change.
        discovered_boards.extend(getattr(fetcher, "discovered_boards", []))

    # Boards discovered this run (via HN comments above, or a GitHub list
    # below) are recorded now but deliberately NOT polled again this same
    # run — an HN-discovered board's roles are already in all_jobs via the
    # normal HN expansion above, and a newly-seeded board becomes pollable
    # starting next run via poll_known_boards. This is a next-run
    # compounding mechanism, not a same-run double-fetch.
    for platform, slug, company, discovered_url in discovered_boards:
        try:
            upsert_known_board(
                conn,
                platform=platform,
                board_slug=slug,
                company=company,
                discovered_via="hn_whoishiring",
                discovered_url=discovered_url,
            )
        except Exception:
            logger.warning("board_registry: failed to record discovered board %s/%s", platform, slug, exc_info=True)

    if config.github_lists.enabled:
        try:
            seeded = seed_from_github_lists(conn, config=config.github_lists)
            logger.info("github_lists: seeded %d new/updated boards", len(seeded))
        except Exception:
            logger.exception("github_lists: seeding failed unexpectedly, continuing")

    total_fetched = len(all_jobs)
    deduped = dedupe(all_jobs)
    logger.info("deduped %d fetched -> %d unique", total_fetched, len(deduped))

    existing_by_key = {j.dedup_key: j for j in get_jobs(conn)}

    ai_ranker = _build_ai_ranker(config.ai, profile) if config.ranking_mode == "ai" else None

    ranked: list[Job] = []
    for job in deduped:
        job = apply_eligibility_filter(job, config.filters)
        job = apply_keyword_relevance_filter(job, config.filters)

        if job.eligibility_bucket == EligibilityBucket.EXCLUDED or not job.is_relevant:
            result = trivial_rank_result(job)
        elif ai_ranker is not None:
            result = _cached_ai_result(existing_by_key.get(job.dedup_key))
            if result is None:
                try:
                    result = ai_ranker.score_job(job)
                except AIRankingError as exc:
                    logger.warning("AI ranking failed for %s, falling back to heuristic: %s", job.dedup_key, exc)
                    result = score_job(job, config.ranker, config.filters, profile)
        else:
            result = score_job(job, config.ranker, config.filters, profile)

        ranked.append(
            replace(
                job,
                score=result.score,
                skill_match=result.skill_match,
                eligibility_confidence=result.eligibility_confidence,
                contract_type_guess=result.contract_type_guess,
                reasons=result.reasons,
                red_flags=result.red_flags,
                ranking_source=result.ranking_source,
                seniority_fit=result.seniority_fit,
            )
        )

    if ai_ranker is not None:
        logger.info(ai_ranker.summary_line())

    for job in ranked:
        upsert_job(conn, job)

    stored = get_jobs(conn, order_by="score DESC")
    print_summary(total_fetched, len(deduped), stored, report_path, min_score_threshold=config.min_score_threshold)
    render_html_report(stored, report_path)


def rerank_job(
    dedup_key: str,
    config_path: str = "config.yaml",
    profile_path: str = "profile.yaml",
    db_path: str = "data/jobscout.db",
) -> Job:
    """Force an AI re-score of exactly one job, bypassing the "never re-rank
    twice in the same mode" cache. Unlike run(), this raises on AI failure
    instead of falling back — a re-rank is an explicit user request for an
    AI result, so silently substituting the heuristic score would misrepresent
    what happened."""
    config = load_config(config_path)
    profile = load_profile(profile_path)

    conn = init_db(db_path)
    job = get_job_by_dedup_key(conn, dedup_key)
    if job is None:
        raise ValueError(f"no job with dedup_key {dedup_key!r}")
    if job.eligibility_bucket == EligibilityBucket.EXCLUDED or not job.is_relevant:
        raise ValueError(f"job {dedup_key!r} is excluded/irrelevant and is never AI-ranked")

    ai_ranker = _build_ai_ranker(config.ai, profile)
    if ai_ranker is None:
        raise AIRankingError("AI ranking is unavailable: check ai.provider in config.yaml and its API key env var")

    result = ai_ranker.score_job(job)
    job = replace(
        job,
        score=result.score,
        skill_match=result.skill_match,
        eligibility_confidence=result.eligibility_confidence,
        contract_type_guess=result.contract_type_guess,
        reasons=result.reasons,
        red_flags=result.red_flags,
        ranking_source=result.ranking_source,
        seniority_fit=result.seniority_fit,
    )
    upsert_job(conn, job)
    return job
