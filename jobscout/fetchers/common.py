"""Small helpers shared across fetchers: email/URL extraction for
application_channel inference, and ISO8601 date parsing."""

from __future__ import annotations

import re
from datetime import datetime, timezone

from jobscout.models import ApplicationChannel

EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
URL_RE = re.compile(r"https?://[^\s)>\]]+")


def extract_email(text: str | None) -> str | None:
    match = EMAIL_RE.search(text or "")
    return match.group(0) if match else None


def extract_url(text: str | None) -> str | None:
    match = URL_RE.search(text or "")
    return match.group(0).rstrip(".,;:") if match else None


def infer_application_channel(description: str, has_url: bool) -> ApplicationChannel:
    if has_url:
        return ApplicationChannel.URL
    if extract_email(description):
        return ApplicationChannel.EMAIL
    return ApplicationChannel.UNKNOWN


def parse_iso_datetime(value: str | None, assume_utc: bool = False) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None and assume_utc:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt
