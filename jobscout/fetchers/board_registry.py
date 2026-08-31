"""Polls the persistent known_boards registry (jobscout/db.py) — every
Ashby/Greenhouse/Lever/Workable board ever discovered, by hn_whoishiring.py
or github_lists.py, gets re-polled here on every future run regardless of
whether its original discovery source mentions it again. This is what
turns board discovery from a one-run-only side effect into standing,
compounding coverage.

Not a FETCHER_REGISTRY entry (like ats_boards.py itself, which this module
wraps) — it needs a live DB connection, which the zero-arg Fetcher
protocol has no way to supply. pipeline.py calls poll_known_boards()
directly, before the FETCHER_REGISTRY loop.

Deliberately same-run-decoupled from discovery: a board found via an HN
comment this run is NOT also polled here this same run — it's already
been fully expanded by hn_whoishiring._expand_bundled_board as part of
that fetch, and only becomes independently pollable starting next run,
once pipeline.py has recorded it into known_boards.
"""

from __future__ import annotations

import concurrent.futures
import logging
from datetime import datetime, timezone

from jobscout.db import DBConnection, KnownBoard, get_known_boards, record_board_poll
from jobscout.fetchers.ats_boards import fetch_board_postings, posting_to_job
from jobscout.models import Job

logger = logging.getLogger(__name__)

# Same rationale as hn_whoishiring._RESOLVE_MAX_WORKERS: each board is a
# separate HTTP request to a different ATS API, and the registry can grow
# to hold many boards over time — poll them concurrently rather than
# sequentially.
DEFAULT_MAX_WORKERS = 15

REGISTRY_SOURCE = "ats_board_registry"


def poll_known_boards(conn: DBConnection, *, max_workers: int = DEFAULT_MAX_WORKERS) -> list[Job]:
    """Fetches every enabled known_boards row concurrently via
    fetch_board_postings + posting_to_job, records last_polled_at/
    last_poll_result_count for each, and returns the flattened Job list.
    Never raises — one board's failure never aborts the others or the
    caller."""
    boards = get_known_boards(conn, enabled_only=True)
    if not boards:
        return []

    jobs: list[Job] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as pool:
        future_to_board = {
            pool.submit(fetch_board_postings, board.platform, board.board_slug): board for board in boards
        }
        for future in concurrent.futures.as_completed(future_to_board):
            board = future_to_board[future]
            try:
                postings = future.result()
            except Exception:
                logger.warning("board_registry: failed to poll %s/%s", board.platform, board.board_slug, exc_info=True)
                postings = []
            jobs.extend(_postings_to_jobs(board, postings))
            try:
                record_board_poll(
                    conn, platform=board.platform, board_slug=board.board_slug, result_count=len(postings)
                )
            except Exception:
                logger.warning(
                    "board_registry: failed to record poll for %s/%s", board.platform, board.board_slug, exc_info=True
                )
    return jobs


def _postings_to_jobs(board: KnownBoard, postings: list[dict]) -> list[Job]:
    now = datetime.now(timezone.utc)
    comment_id = f"registry:{board.platform}:{board.board_slug}"
    company = board.company or board.board_slug
    result = []
    for raw in postings:
        job = posting_to_job(
            board.platform,
            raw,
            company=company,
            comment_id=comment_id,
            fallback_posted_date=now,
            source=REGISTRY_SOURCE,
        )
        if job is not None:
            result.append(job)
    return result
