"""Plain stdlib sqlite3 storage. Single-user local tool, one table, no
joins — an ORM/migration framework would add weight for zero relational
benefit at this scale. Wrapped behind init_db/upsert_job/get_jobs/set_status
so a future phase could swap the backend without touching callers.
"""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from jobscout.models import (
    ApplicationChannel,
    ContractTypeGuess,
    EligibilityBucket,
    Job,
    JobStatus,
    RankingSource,
    SeniorityFit,
)

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


def init_db(path: str | Path = "data/jobscout.db") -> sqlite3.Connection:
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


def upsert_job(conn: sqlite3.Connection, job: Job) -> None:
    now = datetime.now(timezone.utc)
    row = _job_to_row(job, now)
    columns = list(row.keys())
    placeholders = ", ".join(f":{c}" for c in columns)
    update_clause = ", ".join(f"{c} = excluded.{c}" for c in _UPDATE_COLUMNS)
    sql = (
        f"INSERT INTO jobs ({', '.join(columns)}) VALUES ({placeholders}) "
        f"ON CONFLICT(dedup_key) DO UPDATE SET {update_clause}"
    )
    conn.execute(sql, row)
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


_SAFE_ORDER_BY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\s+(ASC|DESC))?(\s*,\s*[A-Za-z_][A-Za-z0-9_]*(\s+(ASC|DESC))?)*$")


def get_jobs(conn: sqlite3.Connection, order_by: str = "score DESC") -> list[Job]:
    # order_by is only ever passed literal strings from our own code, but
    # validate anyway rather than interpolating arbitrary input into SQL.
    if not _SAFE_ORDER_BY_RE.match(order_by.strip()):
        raise ValueError(f"unsafe order_by clause: {order_by!r}")
    cursor = conn.execute(f"SELECT * FROM jobs ORDER BY {order_by}")
    return [_row_to_job(row) for row in cursor.fetchall()]


def get_job_by_dedup_key(conn: sqlite3.Connection, dedup_key: str) -> Job | None:
    row = conn.execute("SELECT * FROM jobs WHERE dedup_key = ?", (dedup_key,)).fetchone()
    return _row_to_job(row) if row else None


def set_status(conn: sqlite3.Connection, dedup_key: str, status: JobStatus) -> None:
    conn.execute("UPDATE jobs SET status = ? WHERE dedup_key = ?", (status.value, dedup_key))
    conn.commit()
