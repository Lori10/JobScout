"""Seeds jobscout.db's known_boards registry (see board_registry.py) from
public, unauthenticated GitHub "remote-first companies" lists — a second,
independent discovery source alongside HN comments, for companies that
never happen to get mentioned in an HN "who is hiring" thread.

MVP scope is deliberately narrow: only yanirs/established-remote's README
is scanned (a single markdown fetch, ~105 companies, at least one already-
bare board-root link confirmed live). A second, larger candidate list
(remoteintech/remote-jobs, ~200+ per-company files behind a rate-limited
GitHub API directory listing) was scoped out — see PLAN.md.

Each configured source's README is raw Markdown, not rendered HTML, so
candidate URLs are pulled with common.URL_RE (a plain regex over the raw
text) rather than common.extract_href_urls (which expects HTML `href=`
attributes and would find nothing in a .md file).

Re-scanning is gated to once every `scan_interval_days` per source (via
db.get_last_scan/record_scan) — most candidate URLs aren't already a bare
board-root link, so they cost a resolve_board() call (1-2 real HTTP
requests) each; without this gate that cost would be paid on every single
run for no new information.
"""

from __future__ import annotations

import concurrent.futures
import logging
import sqlite3
from datetime import datetime, timedelta, timezone

import requests

from jobscout.config import GitHubListsConfig
from jobscout.db import get_last_scan, record_scan, upsert_known_board
from jobscout.fetchers.ats_boards import detect_board, resolve_board
from jobscout.fetchers.common import URL_RE

logger = logging.getLogger(__name__)

TIMEOUT = 20
DEFAULT_MAX_WORKERS = 15


def seed_from_github_lists(
    conn: sqlite3.Connection, *, config: GitHubListsConfig, max_workers: int = DEFAULT_MAX_WORKERS
) -> list[tuple[str, str, str, str]]:
    """Scans every configured README source (subject to the per-source
    scan_interval_days cadence gate) for candidate company URLs, resolves
    each to an ATS board, and upserts any found into known_boards. Never
    raises. Returns the (platform, board_slug, source_name, url) tuples
    actually upserted, for logging — the newly-seeded boards themselves
    aren't polled this run (board_registry.poll_known_boards picks them up
    starting next run, same one-run lag as HN-discovered boards)."""
    upserted: list[tuple[str, str, str, str]] = []
    for source_name, readme_url in config.sources.items():
        try:
            upserted.extend(
                _seed_one_source(
                    conn,
                    source_name=source_name,
                    readme_url=readme_url,
                    scan_interval_days=config.scan_interval_days,
                    max_workers=max_workers,
                )
            )
        except Exception:
            logger.warning("github_lists: failed to seed from %r, continuing", source_name, exc_info=True)
    return upserted


def _seed_one_source(
    conn: sqlite3.Connection,
    *,
    source_name: str,
    readme_url: str,
    scan_interval_days: int,
    max_workers: int,
) -> list[tuple[str, str, str, str]]:
    last_scan = get_last_scan(conn, source_name)
    now = datetime.now(timezone.utc)
    if last_scan is not None and now - last_scan < timedelta(days=scan_interval_days):
        logger.debug("github_lists: %s scanned recently (%s), skipping", source_name, last_scan)
        return []

    try:
        response = requests.get(readme_url, timeout=TIMEOUT)
        response.raise_for_status()
        text = response.text
    except Exception:
        logger.warning("github_lists: failed to fetch %s (%s)", source_name, readme_url, exc_info=True)
        return []

    candidates = _extract_candidate_urls(text)
    logger.info("github_lists: %s: %d candidate URLs", source_name, len(candidates))

    upserted: list[tuple[str, str, str, str]] = []

    # Direct hits cost nothing (detect_board is pure regex) — resolve those
    # first and only fan out resolve_board (1-2 real HTTP requests each)
    # for whatever's left, same split hn_whoishiring.py makes.
    needs_resolve: list[str] = []
    for url in candidates:
        board = detect_board(url)
        if board is not None:
            upserted.append(_upsert(conn, board, source_name, url))
        else:
            needs_resolve.append(url)

    if needs_resolve:
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as pool:
            future_to_url = {pool.submit(resolve_board, url): url for url in needs_resolve}
            for future in concurrent.futures.as_completed(future_to_url):
                url = future_to_url[future]
                try:
                    board = future.result()
                except Exception:
                    logger.debug("github_lists: resolve_board failed for %r", url, exc_info=True)
                    board = None
                if board is not None:
                    upserted.append(_upsert(conn, board, source_name, url))

    record_scan(conn, source_name, now)
    return upserted


def _upsert(conn: sqlite3.Connection, board: tuple[str, str], source_name: str, url: str) -> tuple[str, str, str, str]:
    platform, slug = board
    upsert_known_board(
        conn,
        platform=platform,
        board_slug=slug,
        company=None,
        discovered_via=f"github:{source_name}",
        discovered_url=url,
    )
    return platform, slug, source_name, url


def _extract_candidate_urls(markdown_text: str) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for match in URL_RE.finditer(markdown_text):
        url = match.group(0).rstrip(".,;:)]")
        if url not in seen:
            seen.add(url)
            ordered.append(url)
    return ordered
