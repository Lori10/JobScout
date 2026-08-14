"""Seeds jobscout.db's known_boards registry (see board_registry.py) from
public, unauthenticated GitHub "remote-first companies" lists — a second,
independent discovery source alongside HN comments, for companies that
never happen to get mentioned in an HN "who is hiring" thread.

Two source kinds are configured, both funneling into the same
detect_board/resolve_board/upsert_known_board path and the same per-source
scan_interval_days cadence gate:

- `sources` (dict[name, README URL]): a single raw-Markdown fetch, regexed
  for candidate URLs with common.URL_RE (a plain regex over the raw text,
  not common.extract_href_urls, since a .md file has no HTML `href=`
  attributes to find). e.g. yanirs/established-remote.
- `repo_sources` (list[GitHubRepoSource]): a GitHub repo of one markdown
  file per company with YAML frontmatter, e.g. remoteintech/remote-jobs
  (882 companies under src/companies/*.md, each with a careers_url field).
  The full file list comes from one git trees API call
  (/repos/{owner}/{repo}/git/trees/{branch}?recursive=1), then each file's
  frontmatter is fetched from raw.githubusercontent.com (a CDN, NOT subject
  to api.github.com's 60/req/hour unauthenticated limit) and read directly
  for `frontmatter_field` via yaml.safe_load, rather than regexed as prose
  like `sources` above — a company's markdown body can itself contain
  unrelated URLs (e.g. a benefits blurb linking to a blog post) that
  URL_RE would wrongly pick up. This is a meaningfully heavier cost tier
  (hundreds of raw-file fetches vs. one README fetch), which is why it's a
  separate config list with its own worker pool rather than folded into
  `sources`.

Re-scanning is gated to once every `scan_interval_days` per source (via
db.get_last_scan/record_scan) — most candidate URLs aren't already a bare
board-root link, so they cost a resolve_board() call (1-2 real HTTP
requests) each; without this gate that cost would be paid on every single
run for no new information.
"""

from __future__ import annotations

import concurrent.futures
import logging
import re
import sqlite3
from datetime import datetime, timedelta, timezone

import requests
import yaml

from jobscout.config import GitHubListsConfig, GitHubRepoSource
from jobscout.db import get_last_scan, record_scan, upsert_known_board
from jobscout.fetchers.ats_boards import detect_board, resolve_board
from jobscout.fetchers.common import URL_RE

logger = logging.getLogger(__name__)

TIMEOUT = 20
DEFAULT_MAX_WORKERS = 15

# raw.githubusercontent.com is a single shared CDN host hit up to ~900
# times for one repo_source (vs. resolve_board's fan-out across many
# distinct company domains) — kept deliberately smaller than
# DEFAULT_MAX_WORKERS as a politeness margin, not because the CDN is known
# to rate-limit at this value; there's no published concurrency limit to
# target.
DEFAULT_REPO_FILE_MAX_WORKERS = 10

USER_AGENT = "JobScout/0.1 (personal job-search tool; contact via GitHub)"

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?\n)---\s*\n", re.DOTALL)


def seed_from_github_lists(
    conn: sqlite3.Connection,
    *,
    config: GitHubListsConfig,
    max_workers: int = DEFAULT_MAX_WORKERS,
    repo_file_max_workers: int = DEFAULT_REPO_FILE_MAX_WORKERS,
) -> list[tuple[str, str, str, str]]:
    """Scans every configured README and repo source (subject to the
    per-source scan_interval_days cadence gate) for candidate company
    URLs, resolves each to an ATS board, and upserts any found into
    known_boards. Never raises. Returns the (platform, board_slug,
    source_name, url) tuples actually upserted, for logging — the
    newly-seeded boards themselves aren't polled this run
    (board_registry.poll_known_boards picks them up starting next run,
    same one-run lag as HN-discovered boards)."""
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
    for repo_source in config.repo_sources:
        try:
            upserted.extend(
                _seed_one_repo_source(
                    conn,
                    repo_source=repo_source,
                    scan_interval_days=config.scan_interval_days,
                    resolve_max_workers=max_workers,
                    file_max_workers=repo_file_max_workers,
                )
            )
        except Exception:
            logger.warning(
                "github_lists: failed to seed from repo source %r, continuing", repo_source.name, exc_info=True
            )
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


def _seed_one_repo_source(
    conn: sqlite3.Connection,
    *,
    repo_source: GitHubRepoSource,
    scan_interval_days: int,
    resolve_max_workers: int,
    file_max_workers: int,
) -> list[tuple[str, str, str, str]]:
    source_name = repo_source.name
    last_scan = get_last_scan(conn, source_name)
    now = datetime.now(timezone.utc)
    if last_scan is not None and now - last_scan < timedelta(days=scan_interval_days):
        logger.debug("github_lists: %s scanned recently (%s), skipping", source_name, last_scan)
        return []

    tree_url = (
        f"https://api.github.com/repos/{repo_source.owner}/{repo_source.repo}"
        f"/git/trees/{repo_source.branch}?recursive=1"
    )
    try:
        response = requests.get(
            tree_url,
            headers={"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"},
            timeout=TIMEOUT,
        )
        response.raise_for_status()
        tree = response.json().get("tree", [])
    except Exception:
        logger.warning("github_lists: failed to fetch repo tree for %s (%s)", source_name, tree_url, exc_info=True)
        return []

    paths = [
        entry["path"]
        for entry in tree
        if entry.get("type") == "blob"
        and entry.get("path", "").startswith(repo_source.path_prefix)
        and entry["path"].endswith(".md")
    ]
    logger.info("github_lists: %s: %d company files to scan", source_name, len(paths))

    raw_base = f"https://raw.githubusercontent.com/{repo_source.owner}/{repo_source.repo}/{repo_source.branch}/"
    candidates: list[str] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=file_max_workers) as pool:
        future_to_path = {
            pool.submit(_fetch_frontmatter_url, raw_base + path, repo_source.frontmatter_field): path
            for path in paths
        }
        for future in concurrent.futures.as_completed(future_to_path):
            path = future_to_path[future]
            try:
                url = future.result()
            except Exception:
                logger.debug("github_lists: failed to fetch/parse %r", path, exc_info=True)
                url = None
            if url:
                candidates.append(url)

    logger.info("github_lists: %s: %d candidate careers URLs", source_name, len(candidates))

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
        with concurrent.futures.ThreadPoolExecutor(max_workers=resolve_max_workers) as pool:
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


def _fetch_frontmatter_url(raw_url: str, frontmatter_field: str) -> str | None:
    """Fetches one raw company markdown file and returns its
    frontmatter_field value (e.g. careers_url) from the YAML frontmatter
    block between the two leading `---` lines, or None if the file has no
    frontmatter block, the block doesn't parse as a YAML mapping, or the
    field is missing/blank. Allowed to raise (network error, bad YAML) —
    the per-future try/except at the call site is what enforces "one bad
    file can't drop the whole pass," same division of responsibility
    resolve_board's fan-out already uses."""
    response = requests.get(raw_url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
    response.raise_for_status()
    match = _FRONTMATTER_RE.match(response.text)
    if not match:
        return None
    frontmatter = yaml.safe_load(match.group(1))
    if not isinstance(frontmatter, dict):
        return None
    value = frontmatter.get(frontmatter_field)
    return value.strip() if isinstance(value, str) and value.strip() else None


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
