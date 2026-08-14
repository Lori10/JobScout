"""Typed loaders for config.yaml and profile.yaml.

Kept deliberately dumb: read YAML, validate shape minimally, hand back
small dataclasses. filters.py and ranker.py take these as plain data and
never touch the filesystem themselves, which is what keeps them pure and
easily unit-testable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class FilterConfig:
    exclude_phrases: list[str] = field(default_factory=list)
    hybrid_onsite_phrases: list[str] = field(default_factory=list)
    remote_indicator_phrases: list[str] = field(default_factory=list)
    needs_review_phrases: list[str] = field(default_factory=list)
    positive_phrases: list[str] = field(default_factory=list)
    relevance_keywords: list[str] = field(default_factory=list)
    relevance_keywords_weak: list[str] = field(default_factory=list)
    non_role_title_phrases: list[str] = field(default_factory=list)


@dataclass
class KeywordGroup:
    weight: float
    phrases: list[str]


@dataclass
class RankerConfig:
    keyword_groups: dict[str, KeywordGroup] = field(default_factory=dict)
    title_match_bonus: int = 15
    positive_signal_bonus_cap: int = 15
    recency_bonus_max: int = 10
    near_miss_penalty: int = 10


@dataclass
class AIConfig:
    provider: str = "gemini"
    model: str = "gemini-3.5-flash-lite"
    max_description_chars: int = 4000
    max_retries: int = 3
    min_seconds_between_calls: float = 4.5


_VALID_RANKING_MODES = {"heuristic", "ai"}


@dataclass
class AppConfig:
    sources: dict[str, bool] = field(default_factory=dict)
    ranking_mode: str = "heuristic"
    min_score_threshold: int = 30
    filters: FilterConfig = field(default_factory=FilterConfig)
    ranker: RankerConfig = field(default_factory=RankerConfig)
    ai: AIConfig = field(default_factory=AIConfig)


@dataclass
class Profile:
    role_targets: list[str] = field(default_factory=list)
    core_skills: list[str] = field(default_factory=list)
    experience: dict = field(default_factory=dict)
    languages: list[str] = field(default_factory=list)
    work_setup: dict = field(default_factory=dict)
    location: dict = field(default_factory=dict)


def load_config(path: str | Path = "config.yaml") -> AppConfig:
    raw = _read_yaml(path)
    filters_raw = raw.get("filters", {})
    ranker_raw = raw.get("ranker", {})

    keyword_groups = {
        name: KeywordGroup(weight=float(group.get("weight", 1.0)), phrases=list(group.get("phrases", [])))
        for name, group in ranker_raw.get("keyword_groups", {}).items()
    }

    ranking_mode = raw.get("ranking_mode", "heuristic")
    if ranking_mode not in _VALID_RANKING_MODES:
        raise ValueError(f"ranking_mode must be one of {sorted(_VALID_RANKING_MODES)}, got {ranking_mode!r}")

    ai_raw = raw.get("ai", {})

    return AppConfig(
        sources=dict(raw.get("sources", {})),
        ranking_mode=ranking_mode,
        min_score_threshold=int(raw.get("min_score_threshold", 30)),
        filters=FilterConfig(
            exclude_phrases=list(filters_raw.get("exclude_phrases", [])),
            hybrid_onsite_phrases=list(filters_raw.get("hybrid_onsite_phrases", [])),
            remote_indicator_phrases=list(filters_raw.get("remote_indicator_phrases", [])),
            needs_review_phrases=list(filters_raw.get("needs_review_phrases", [])),
            positive_phrases=list(filters_raw.get("positive_phrases", [])),
            relevance_keywords=list(filters_raw.get("relevance_keywords", [])),
            relevance_keywords_weak=list(filters_raw.get("relevance_keywords_weak", [])),
            non_role_title_phrases=list(filters_raw.get("non_role_title_phrases", [])),
        ),
        ranker=RankerConfig(
            keyword_groups=keyword_groups,
            title_match_bonus=int(ranker_raw.get("title_match_bonus", 15)),
            positive_signal_bonus_cap=int(ranker_raw.get("positive_signal_bonus_cap", 15)),
            recency_bonus_max=int(ranker_raw.get("recency_bonus_max", 10)),
            near_miss_penalty=int(ranker_raw.get("near_miss_penalty", 10)),
        ),
        ai=AIConfig(
            provider=ai_raw.get("provider", "gemini"),
            model=ai_raw.get("model", "gemini-3.5-flash-lite"),
            max_description_chars=int(ai_raw.get("max_description_chars", 4000)),
            max_retries=int(ai_raw.get("max_retries", 3)),
            min_seconds_between_calls=float(ai_raw.get("min_seconds_between_calls", 4.5)),
        ),
    )


def load_profile(path: str | Path = "profile.yaml") -> Profile:
    raw = _read_yaml(path)
    return Profile(
        role_targets=list(raw.get("role_targets", [])),
        core_skills=list(raw.get("core_skills", [])),
        experience=dict(raw.get("experience", {})),
        languages=list(raw.get("languages", [])),
        work_setup=dict(raw.get("work_setup", {})),
        location=dict(raw.get("location", {})),
    )


def _read_yaml(path: str | Path) -> dict:
    with open(path, encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    return data or {}
