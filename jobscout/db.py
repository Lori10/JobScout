"""Plain stdlib sqlite3 storage. Single-user local tool, no joins — an
ORM/migration framework would add weight for zero relational benefit at
this scale. Wrapped behind init_db/upsert_job/get_jobs/set_status (plus
the known_boards equivalents) so a future phase could swap the backend
without touching callers.
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import psycopg2
import psycopg2.extras

from jobscout.models import (
    ApplicationChannel,
    ContractTypeGuess,
    EligibilityBucket,
    Job,
    JobStatus,
    RankingSource,
    SeniorityFit,
)

DBConnection = sqlite3.Connection | psycopg2.extensions.connection

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dedup_key TEXT NOT NULL UNIQUE,
    source TEXT NOT NULL,
    source_id TEXT,
    title TEXT NOT NULL,
    company TEXT NOT NULL,
    description TEXT NOT NULL,
    url TEXT NOT NULL,
    posted_date TEXT,
    salary_text TEXT,
    tags TEXT,
    location_text TEXT,
    application_channel TEXT NOT NULL DEFAULT 'unknown',
    eligibility_bucket TEXT NOT NULL DEFAULT 'eligible',
    eligibility_reason TEXT,
    is_relevant INTEGER NOT NULL DEFAULT 1,
    score INTEGER,
    skill_match INTEGER,
    eligibility_confidence INTEGER,
    contract_type_guess TEXT NOT NULL DEFAULT 'unclear',
    reasons TEXT,
    red_flags TEXT,
    ranking_source TEXT NOT NULL DEFAULT 'heuristic',
    seniority_fit TEXT,
    status TEXT NOT NULL DEFAULT 'new',
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    research_brief_path TEXT,
    outreach_draft_path TEXT
);
CREATE INDEX IF NOT EXISTS idx_jobs_score ON jobs(score);
CREATE INDEX IF NOT EXISTS idx_jobs_bucket ON jobs(eligibility_bucket);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);

CREATE TABLE IF NOT EXISTS known_boards (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    platform TEXT NOT NULL,
    board_slug TEXT NOT NULL,
    company TEXT,
    discovered_via TEXT NOT NULL,
    discovered_url TEXT,
    enabled INTEGER NOT NULL DEFAULT 1,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    last_polled_at TEXT,
    last_poll_result_count INTEGER,
    UNIQUE(platform, board_slug)
);
CREATE INDEX IF NOT EXISTS idx_known_boards_enabled ON known_boards(enabled);

CREATE TABLE IF NOT EXISTS github_list_scans (
    source_name TEXT PRIMARY KEY,
    last_scanned_at TEXT NOT NULL
);
"""

SCHEMA_POSTGRES = """
CREATE TABLE IF NOT EXISTS jobs (
    id SERIAL PRIMARY KEY,
    dedup_key TEXT NOT NULL UNIQUE,
    source TEXT NOT NULL,
    source_id TEXT,
    title TEXT NOT NULL,
    company TEXT NOT NULL,
    description TEXT NOT NULL,
    url TEXT NOT NULL,
    posted_date TEXT,
    salary_text TEXT,
    tags TEXT,
    location_text TEXT,
    application_channel TEXT NOT NULL DEFAULT 'unknown',
    eligibility_bucket TEXT NOT NULL DEFAULT 'eligible',
    eligibility_reason TEXT,
    is_relevant INTEGER NOT NULL DEFAULT 1,
    score INTEGER,
    skill_match INTEGER,
    eligibility_confidence INTEGER,
    contract_type_guess TEXT NOT NULL DEFAULT 'unclear',
    reasons TEXT,
    red_flags TEXT,
    ranking_source TEXT NOT NULL DEFAULT 'heuristic',
    seniority_fit TEXT,
    status TEXT NOT NULL DEFAULT 'new',
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    research_brief_path TEXT,
    outreach_draft_path TEXT
);
CREATE INDEX IF NOT EXISTS idx_jobs_score ON jobs(score);
CREATE INDEX IF NOT EXISTS idx_jobs_bucket ON jobs(eligibility_bucket);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);

CREATE TABLE IF NOT EXISTS known_boards (
    id SERIAL PRIMARY KEY,
    platform TEXT NOT NULL,
    board_slug TEXT NOT NULL,
    company TEXT,
    discovered_via TEXT NOT NULL,
    discovered_url TEXT,
    enabled INTEGER NOT NULL DEFAULT 1,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    last_polled_at TEXT,
    last_poll_result_count INTEGER,
    UNIQUE(platform, board_slug)
);
CREATE INDEX IF NOT EXISTS idx_known_boards_enabled ON known_boards(enabled);

CREATE TABLE IF NOT EXISTS github_list_scans (
    source_name TEXT PRIMARY KEY,
    last_scanned_at TEXT NOT NULL
);
"""

# Columns updated on a re-fetch. status and first_seen_at are deliberately
# excluded: a re-fetch must never clobber the user's workflow state or
# overwrite when a job was first seen.
_UPDATE_COLUMNS = [
    "source",
    "source_id",
    "title",
    "company",
    "description",
    "url",
    "posted_date",
    "salary_text",
    "tags",
    "location_text",
    "application_channel",
    "eligibility_bucket",
    "eligibility_reason",
    "is_relevant",
    "score",
    "skill_match",
    "eligibility_confidence",
    "contract_type_guess",
    "reasons",
    "red_flags",
    "ranking_source",
    "seniority_fit",
    "last_seen_at",
]

# Additive columns for DBs created before they existed. CREATE TABLE IF NOT
# EXISTS is a no-op on an existing table, so a new column needs its own
# ALTER TABLE here, guarded by a PRAGMA table_info check so it's idempotent.
_MIGRATIONS: list[tuple[str, str]] = [
    ("seniority_fit", "ALTER TABLE jobs ADD COLUMN seniority_fit TEXT"),
]

# Columns updated when a known board is rediscovered. enabled and
# first_seen_at are user-owned/insert-only (same reasoning as jobs.status
# and jobs.first_seen_at); last_polled_at and last_poll_result_count belong
# to the poller's write path (record_board_poll), not discovery.
_KNOWN_BOARD_UPDATE_COLUMNS = [
    "company",
    "discovered_via",
    "discovered_url",
    "last_seen_at",
]


@dataclass
class KnownBoard:
    platform: str
    board_slug: str
    company: str | None
    discovered_via: str
    discovered_url: str | None
    enabled: bool
    first_seen_at: datetime | None
    last_seen_at: datetime | None
    last_polled_at: datetime | None
    last_poll_result_count: int | None


def _is_pg(conn: DBConnection) -> bool:
    return not isinstance(conn, sqlite3.Connection)


def _ph(conn: DBConnection) -> str:
    return "%s" if _is_pg(conn) else "?"


def _named(conn: DBConnection, name: str) -> str:
    return f"%({name})s" if _is_pg(conn) else f":{name}"


def _exec(conn: DBConnection, sql: str, params=None):
    if _is_pg(conn):
        cur = conn.cursor()
        cur.execute(sql, params or {})
        return cur
    return conn.execute(sql, params or {})


def init_db(path: str | Path = "data/jobscout.db") -> DBConnection:
    if isinstance(path, str) and (path.startswith("postgres://") or path.startswith("postgresql://")):
        conn = psycopg2.connect(path, cursor_factory=psycopg2.extras.RealDictCursor)
        with conn.cursor() as cur:
            cur.execute(SCHEMA_POSTGRES)
        conn.commit()
        return conn

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    existing_columns = {row["name"] for row in conn.execute("PRAGMA table_info(jobs)")}
    for column, alter_sql in _MIGRATIONS:
        if column not in existing_columns:
            conn.execute(alter_sql)
    conn.commit()
    return conn


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _job_to_row(job: Job, now: datetime) -> dict:
    return {
        "dedup_key": job.dedup_key,
        "source": job.source,
        "source_id": job.source_id,
        "title": job.title,
        "company": job.company,
        "description": job.description,
        "url": job.url,
        "posted_date": _iso(job.posted_date),
        "salary_text": job.salary_text,
        "tags": json.dumps(job.tags or []),
        "location_text": job.location_text,
        "application_channel": job.application_channel.value,
        "eligibility_bucket": job.eligibility_bucket.value,
        "eligibility_reason": job.eligibility_reason,
        "is_relevant": 1 if job.is_relevant else 0,
        "score": job.score,
        "skill_match": job.skill_match,
        "eligibility_confidence": job.eligibility_confidence,
        "contract_type_guess": job.contract_type_guess.value,
        "reasons": json.dumps(job.reasons or []),
        "red_flags": json.dumps(job.red_flags or []),
        "ranking_source": job.ranking_source.value,
        "seniority_fit": job.seniority_fit.value if job.seniority_fit else None,
        "status": job.status.value,
        "first_seen_at": _iso(job.first_seen_at) or now.isoformat(),
        "last_seen_at": now.isoformat(),
    }


def upsert_job(conn: DBConnection, job: Job) -> None:
    now = datetime.now(timezone.utc)
    row = _job_to_row(job, now)
    columns = list(row.keys())
    placeholders = ", ".join(_named(conn, c) for c in columns)
    update_clause = ", ".join(f"{c} = excluded.{c}" for c in _UPDATE_COLUMNS)
    sql = (
        f"INSERT INTO jobs ({', '.join(columns)}) VALUES ({placeholders}) "
        f"ON CONFLICT(dedup_key) DO UPDATE SET {update_clause}"
    )
    _exec(conn, sql, row)
    conn.commit()


def _row_to_job(row: sqlite3.Row) -> Job:
    return Job(
        title=row["title"],
        company=row["company"],
        description=row["description"],
        url=row["url"],
        source=row["source"],
        posted_date=_parse_iso(row["posted_date"]),
        salary_text=row["salary_text"],
        tags=json.loads(row["tags"]) if row["tags"] else [],
        location_text=row["location_text"],
        application_channel=ApplicationChannel(row["application_channel"]),
        dedup_key=row["dedup_key"],
        source_id=row["source_id"],
        eligibility_bucket=EligibilityBucket(row["eligibility_bucket"]),
        eligibility_reason=row["eligibility_reason"],
        is_relevant=bool(row["is_relevant"]),
        score=row["score"],
        skill_match=row["skill_match"],
        eligibility_confidence=row["eligibility_confidence"],
        contract_type_guess=ContractTypeGuess(row["contract_type_guess"]),
        reasons=json.loads(row["reasons"]) if row["reasons"] else [],
        red_flags=json.loads(row["red_flags"]) if row["red_flags"] else [],
        ranking_source=RankingSource(row["ranking_source"]),
        seniority_fit=SeniorityFit(row["seniority_fit"]) if row["seniority_fit"] else None,
        status=JobStatus(row["status"]),
        first_seen_at=_parse_iso(row["first_seen_at"]),
        last_seen_at=_parse_iso(row["last_seen_at"]),
        research_brief_path=row["research_brief_path"],
        outreach_draft_path=row["outreach_draft_path"],
    )


_SAFE_ORDER_BY_RE = re.compile(
    r"^[A-Za-z_][A-Za-z0-9_]*(\s+(ASC|DESC))?(\s*,\s*[A-Za-z_][A-Za-z0-9_]*(\s+(ASC|DESC))?)*$"
)


def get_jobs(conn: DBConnection, order_by: str = "score DESC") -> list[Job]:
    # order_by is only ever passed literal strings from our own code, but
    # validate anyway rather than interpolating arbitrary input into SQL.
    if not _SAFE_ORDER_BY_RE.match(order_by.strip()):
        raise ValueError(f"unsafe order_by clause: {order_by!r}")
    cursor = _exec(conn, f"SELECT * FROM jobs ORDER BY {order_by}")
    return [_row_to_job(row) for row in cursor.fetchall()]


def get_job_by_dedup_key(conn: DBConnection, dedup_key: str) -> Job | None:
    ph = _ph(conn)
    row = _exec(conn, f"SELECT * FROM jobs WHERE dedup_key = {ph}", (dedup_key,)).fetchone()
    return _row_to_job(row) if row else None


def set_status(conn: DBConnection, dedup_key: str, status: JobStatus) -> None:
    ph = _ph(conn)
    _exec(conn, f"UPDATE jobs SET status = {ph} WHERE dedup_key = {ph}", (status.value, dedup_key))
    conn.commit()


def _row_to_known_board(row: sqlite3.Row) -> KnownBoard:
    return KnownBoard(
        platform=row["platform"],
        board_slug=row["board_slug"],
        company=row["company"],
        discovered_via=row["discovered_via"],
        discovered_url=row["discovered_url"],
        enabled=bool(row["enabled"]),
        first_seen_at=_parse_iso(row["first_seen_at"]),
        last_seen_at=_parse_iso(row["last_seen_at"]),
        last_polled_at=_parse_iso(row["last_polled_at"]),
        last_poll_result_count=row["last_poll_result_count"],
    )


def upsert_known_board(
    conn: DBConnection,
    *,
    platform: str,
    board_slug: str,
    company: str | None,
    discovered_via: str,
    discovered_url: str | None,
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    row = {
        "platform": platform,
        "board_slug": board_slug,
        "company": company,
        "discovered_via": discovered_via,
        "discovered_url": discovered_url,
        "enabled": 1,
        "first_seen_at": now,
        "last_seen_at": now,
    }
    columns = list(row.keys())
    placeholders = ", ".join(_named(conn, c) for c in columns)
    update_clause = ", ".join(f"{c} = excluded.{c}" for c in _KNOWN_BOARD_UPDATE_COLUMNS)
    sql = (
        f"INSERT INTO known_boards ({', '.join(columns)}) VALUES ({placeholders}) "
        f"ON CONFLICT(platform, board_slug) DO UPDATE SET {update_clause}"
    )
    _exec(conn, sql, row)
    conn.commit()


def get_known_boards(
    conn: DBConnection,
    *,
    enabled_only: bool = True,
    order_by: str = "platform, board_slug",
) -> list[KnownBoard]:
    if not _SAFE_ORDER_BY_RE.match(order_by.strip()):
        raise ValueError(f"unsafe order_by clause: {order_by!r}")
    sql = "SELECT * FROM known_boards"
    if enabled_only:
        sql += " WHERE enabled = 1"
    sql += f" ORDER BY {order_by}"
    cursor = _exec(conn, sql)
    return [_row_to_known_board(row) for row in cursor.fetchall()]


def record_board_poll(conn: DBConnection, *, platform: str, board_slug: str, result_count: int) -> None:
    ph = _ph(conn)
    _exec(
        conn,
        f"UPDATE known_boards SET last_polled_at = {ph}, last_poll_result_count = {ph} "
        f"WHERE platform = {ph} AND board_slug = {ph}",
        (datetime.now(timezone.utc).isoformat(), result_count, platform, board_slug),
    )
    conn.commit()


def set_board_enabled(conn: DBConnection, *, platform: str, board_slug: str, enabled: bool) -> None:
    ph = _ph(conn)
    _exec(
        conn,
        f"UPDATE known_boards SET enabled = {ph} WHERE platform = {ph} AND board_slug = {ph}",
        (1 if enabled else 0, platform, board_slug),
    )
    conn.commit()


def get_last_scan(conn: DBConnection, source_name: str) -> datetime | None:
    ph = _ph(conn)
    row = _exec(
        conn, f"SELECT last_scanned_at FROM github_list_scans WHERE source_name = {ph}", (source_name,)
    ).fetchone()
    return _parse_iso(row["last_scanned_at"]) if row else None


def record_scan(conn: DBConnection, source_name: str, when: datetime) -> None:
    ph = _ph(conn)
    _exec(
        conn,
        f"INSERT INTO github_list_scans (source_name, last_scanned_at) VALUES ({ph}, {ph}) "
        "ON CONFLICT(source_name) DO UPDATE SET last_scanned_at = excluded.last_scanned_at",
        (source_name, when.isoformat()),
    )
    conn.commit()
