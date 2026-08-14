import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from jobscout.config import GitHubListsConfig
from jobscout.db import SCHEMA, get_known_boards, get_last_scan, record_scan
from jobscout.fetchers.github_lists import _extract_candidate_urls, seed_from_github_lists


@pytest.fixture
def conn():
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.executescript(SCHEMA)
    connection.commit()
    return connection


_FIXTURE_MARKDOWN = """
[37signals](https://37signals.com) | Collaboration tools | Ruby | :keyboard: [Jobs](https://boards.greenhouse.io/sourcegraph91)
[Basecamp](https://basecamp.com) | Project management | Ruby | :keyboard: [Jobs](https://basecamp.com/about/jobs)
"""


def test_extract_candidate_urls_from_fixture_markdown():
    urls = _extract_candidate_urls(_FIXTURE_MARKDOWN)
    assert "https://boards.greenhouse.io/sourcegraph91" in urls
    assert "https://basecamp.com/about/jobs" in urls
    # Trailing markdown punctuation (the closing paren of the link syntax)
    # must not leak into the extracted URL.
    assert not any(u.endswith(")") for u in urls)


def test_extract_candidate_urls_dedupes_within_run():
    text = "https://example.com/a https://example.com/a https://example.com/b"
    assert _extract_candidate_urls(text) == ["https://example.com/a", "https://example.com/b"]


class _FakeResponse:
    def __init__(self, text, status=200):
        self.text = text
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def _config(**overrides) -> GitHubListsConfig:
    defaults = dict(
        enabled=True,
        scan_interval_days=7,
        sources={"established-remote": "https://raw.githubusercontent.com/yanirs/established-remote/master/README.md"},
    )
    defaults.update(overrides)
    return GitHubListsConfig(**defaults)


def test_seed_from_github_lists_detect_board_direct_hit(conn, monkeypatch):
    monkeypatch.setattr(
        "jobscout.fetchers.github_lists.requests.get",
        lambda *a, **k: _FakeResponse("[Jobs](https://boards.greenhouse.io/sourcegraph91)"),
    )
    upserted = seed_from_github_lists(conn, config=_config())
    assert upserted == [("greenhouse", "sourcegraph91", "established-remote", "https://boards.greenhouse.io/sourcegraph91")]
    boards = get_known_boards(conn)
    assert len(boards) == 1
    assert boards[0].discovered_via == "github:established-remote"


def test_seed_from_github_lists_falls_back_to_resolve_board(conn, monkeypatch):
    monkeypatch.setattr(
        "jobscout.fetchers.github_lists.requests.get",
        lambda *a, **k: _FakeResponse("[Jobs](https://acme.com/careers)"),
    )
    monkeypatch.setattr(
        "jobscout.fetchers.github_lists.resolve_board",
        lambda url: ("ashby", "acme") if url == "https://acme.com/careers" else None,
    )
    upserted = seed_from_github_lists(conn, config=_config())
    assert upserted == [("ashby", "acme", "established-remote", "https://acme.com/careers")]


def test_seed_from_github_lists_respects_cadence_gate(conn, monkeypatch):
    record_scan(conn, "established-remote", datetime.now(timezone.utc) - timedelta(days=1))

    calls = []
    monkeypatch.setattr(
        "jobscout.fetchers.github_lists.requests.get",
        lambda *a, **k: calls.append(1) or _FakeResponse(""),
    )
    upserted = seed_from_github_lists(conn, config=_config(scan_interval_days=7))
    assert upserted == []
    assert calls == []


def test_seed_from_github_lists_scans_again_after_interval_elapses(conn, monkeypatch):
    record_scan(conn, "established-remote", datetime.now(timezone.utc) - timedelta(days=10))
    monkeypatch.setattr(
        "jobscout.fetchers.github_lists.requests.get",
        lambda *a, **k: _FakeResponse("[Jobs](https://boards.greenhouse.io/sourcegraph91)"),
    )
    upserted = seed_from_github_lists(conn, config=_config(scan_interval_days=7))
    assert len(upserted) == 1
    assert get_last_scan(conn, "established-remote") is not None


def test_seed_from_github_lists_never_raises_on_fetch_failure(conn, monkeypatch):
    def raise_error(*a, **k):
        raise RuntimeError("network down")

    monkeypatch.setattr("jobscout.fetchers.github_lists.requests.get", raise_error)
    upserted = seed_from_github_lists(conn, config=_config())
    assert upserted == []


def test_seed_from_github_lists_records_scan_even_with_no_candidates(conn, monkeypatch):
    monkeypatch.setattr("jobscout.fetchers.github_lists.requests.get", lambda *a, **k: _FakeResponse("no links here"))
    seed_from_github_lists(conn, config=_config())
    assert get_last_scan(conn, "established-remote") is not None
