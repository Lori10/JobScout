"""Pydantic request/response models for the Phase 2 dashboard API.

JobOut mirrors the Job dataclass field-for-field (from_attributes=True
lets it build directly off a Job instance) and relies on Pydantic's
default enum/datetime serialization instead of a hand-written mapping
function like db.py's _job_to_row.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from jobscout.models import (
    ApplicationChannel,
    ContractTypeGuess,
    EligibilityBucket,
    JobStatus,
    RankingSource,
)


class JobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    title: str
    company: str
    description: str
    url: str
    source: str
    posted_date: datetime | None = None
    salary_text: str | None = None
    tags: list[str] = []
    location_text: str | None = None
    application_channel: ApplicationChannel

    dedup_key: str
    source_id: str | None = None

    eligibility_bucket: EligibilityBucket
    eligibility_reason: str | None = None

    is_relevant: bool

    score: int | None = None
    skill_match: int | None = None
    eligibility_confidence: int | None = None
    contract_type_guess: ContractTypeGuess
    reasons: list[str] = []
    red_flags: list[str] = []
    ranking_source: RankingSource

    status: JobStatus
    first_seen_at: datetime | None = None
    last_seen_at: datetime | None = None

    research_brief_path: str | None = None
    outreach_draft_path: str | None = None


class StatusUpdateIn(BaseModel):
    dedup_key: str
    status: JobStatus
