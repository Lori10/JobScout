"""Core data model shared by every phase of JobScout.

The Job dataclass and its enums are designed to be stable across phases:
Phase 1 populates title/company/description/... and the heuristic ranking
fields; Phase 2 only ever *reads* status; Phase 3's AI ranker fills the same
score/reasons/red_flags/ranking_source fields via a different code path;
Phase 5 writes research_brief_path/outreach_draft_path. No caller needs to
change when a later phase starts populating a field Phase 1 left at its
default.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class ApplicationChannel(str, Enum):
    """Best-effort classification of how to apply to a job."""

    PLATFORM = "platform"
    EMAIL = "email"
    URL = "url"
    UNKNOWN = "unknown"


class EligibilityBucket(str, Enum):
    """Stage 1 hard-eligibility classification (filters.py)."""

    ELIGIBLE = "eligible"
    EXCLUDED = "excluded"
    NEEDS_REVIEW = "needs_review"


class ContractTypeGuess(str, Enum):
    """Rule-based guess at the contracting structure (ranker.py)."""

    B2B = "b2b"
    EMPLOYMENT = "employment"
    FREELANCE = "freelance"
    UNCLEAR = "unclear"


class JobStatus(str, Enum):
    """User workflow state. Nothing in Phase 1 sets anything but NEW;
    Phase 2's dashboard is what lets the user move jobs through this."""

    NEW = "new"
    INTERESTED = "interested"
    APPLIED = "applied"
    INTERVIEW = "interview"
    REJECTED = "rejected"
    IGNORED = "ignored"


class RankingSource(str, Enum):
    """Which scorer produced score/skill_match/.../red_flags. Phase 3 adds AI."""

    HEURISTIC = "heuristic"
    AI = "ai"


@dataclass
class Job:
    # --- core content, populated by every fetcher ---
    title: str
    company: str
    description: str  # HTML-stripped plain text; raw HTML is never persisted
    url: str
    source: str  # "remoteok" | "remotive" | "hn_whoishiring" (Phase 4 adds more)
    posted_date: datetime | None = None
    salary_text: str | None = None
    tags: list[str] = field(default_factory=list)
    location_text: str | None = None
    application_channel: ApplicationChannel = ApplicationChannel.UNKNOWN

    # --- dedup ---
    dedup_key: str = ""
    source_id: str | None = None  # native id from the source API, for idempotent upsert

    # --- stage 1: eligibility (filters.py) ---
    eligibility_bucket: EligibilityBucket = EligibilityBucket.ELIGIBLE
    eligibility_reason: str | None = None  # exact phrase(s) that fired, for audit

    # --- stage 2: keyword relevance (filters.py), independent of eligibility ---
    is_relevant: bool = True

    # --- stage 3: ranking (ranker.py now; Phase 3 AI scorer produces the same shape) ---
    score: int | None = None  # 0-100
    skill_match: int | None = None  # 0-100
    eligibility_confidence: int | None = None  # 0-100
    contract_type_guess: ContractTypeGuess = ContractTypeGuess.UNCLEAR
    reasons: list[str] = field(default_factory=list)
    red_flags: list[str] = field(default_factory=list)
    ranking_source: RankingSource = RankingSource.HEURISTIC

    # --- Phase 2+ user workflow state ---
    status: JobStatus = JobStatus.NEW
    first_seen_at: datetime | None = None
    last_seen_at: datetime | None = None

    # --- Phase 5 placeholders ---
    research_brief_path: str | None = None
    outreach_draft_path: str | None = None
