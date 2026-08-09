"""Orchestrates fetch -> dedupe -> eligibility filter -> keyword filter ->
rank -> store -> report. This is the only module that touches every other
module — fetchers, filters, ranker, dedupe, db, report are all designed to
be called from here without needing to know about each other.
"""

from __future__ import annotations

import logging
from dataclasses import replace

from jobscout.config import load_config, load_profile
from jobscout.db import get_jobs, init_db, upsert_job
from jobscout.dedupe import dedupe
from jobscout.fetchers import FETCHER_REGISTRY
from jobscout.filters import apply_eligibility_filter, apply_keyword_relevance_filter
from jobscout.models import EligibilityBucket, Job
from jobscout.ranker import score_job, trivial_rank_result
from jobscout.report import print_summary, render_html_report

logger = logging.getLogger(__name__)


def run(
    config_path: str = "config.yaml",
    profile_path: str = "profile.yaml",
    db_path: str = "data/jobscout.db",
    report_path: str = "report.html",
) -> None:
    config = load_config(config_path)
    profile = load_profile(profile_path)

    all_jobs: list[Job] = []
    for source_name, fetcher_cls in FETCHER_REGISTRY.items():
        if not config.sources.get(source_name, True):
            logger.info("skipping disabled source: %s", source_name)
            continue
        try:
            fetched = fetcher_cls().fetch()
            logger.info("%s: fetched %d jobs", source_name, len(fetched))
        except Exception:
            # Fetcher contract says fetch() never raises, but this is
            # defense in depth: one dead source must never abort the run.
            logger.exception("%s: fetch failed unexpectedly, continuing", source_name)
            fetched = []
        all_jobs.extend(fetched)

    total_fetched = len(all_jobs)
    deduped = dedupe(all_jobs)
    logger.info("deduped %d fetched -> %d unique", total_fetched, len(deduped))

    ranked: list[Job] = []
    for job in deduped:
        job = apply_eligibility_filter(job, config.filters)
        job = apply_keyword_relevance_filter(job, config.filters)

        if job.eligibility_bucket == EligibilityBucket.EXCLUDED or not job.is_relevant:
            result = trivial_rank_result(job)
        else:
            if config.ranking_mode != "heuristic":
                logger.warning(
                    "ranking_mode=%r not supported in Phase 1, falling back to heuristic",
                    config.ranking_mode,
                )
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
            )
        )

    conn = init_db(db_path)
    for job in ranked:
        upsert_job(conn, job)

    stored = get_jobs(conn, order_by="score DESC")
    print_summary(total_fetched, len(deduped), stored, report_path)
    render_html_report(stored, report_path)
