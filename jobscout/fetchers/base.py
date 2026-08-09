"""Shared fetcher contract. Every concrete fetcher must never raise —
internal errors are caught, logged as a warning, and result in an empty
list, so one dead source never takes down the rest of the pipeline."""

from __future__ import annotations

from typing import Protocol

from jobscout.models import Job


class Fetcher(Protocol):
    name: str

    def fetch(self) -> list[Job]: ...
