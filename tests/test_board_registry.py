import sqlite3

import pytest

from jobscout.db import SCHEMA, get_known_boards, upsert_known_board
from jobscout.fetchers.board_registry import poll_known_boards


@pytest.fixture
def conn():
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.executescript(SCHEMA)
    connection.commit()
    return connection


def _seed_board(conn, platform="ashby", slug="starbridge", company="Starbridge"):
    upsert_known_board(
        conn, platform=platform, board_slug=slug, company=company, discovered_via="hn_whoishiring", discovered_url=None
    )


def test_poll_known_boards_empty_registry_returns_empty_list(conn):
    assert poll_known_boards(conn) == []


def test_poll_known_boards_polls_all_enabled_boards(conn, monkeypatch):
    _seed_board(conn, slug="starbridge")
    _seed_board(conn, slug="acme")

    def fake_fetch(platform, board_slug):
        return [{"id": f"{board_slug}-1", "title": "Role", "jobUrl": f"https://jobs.ashbyhq.com/{board_slug}/1"}]

    monkeypatch.setattr("jobscout.fetchers.board_registry.fetch_board_postings", fake_fetch)

    jobs = poll_known_boards(conn)
    assert len(jobs) == 2
    assert {j.source for j in jobs} == {"ats_board_registry"}
    assert {j.source_id for j in jobs} == {"registry:ashby:starbridge:ashby:starbridge-1", "registry:ashby:acme:ashby:acme-1"}

    boards = {b.board_slug: b for b in get_known_boards(conn)}
    assert boards["starbridge"].last_poll_result_count == 1
    assert boards["starbridge"].last_polled_at is not None


def test_poll_known_boards_skips_disabled_boards(conn, monkeypatch):
    from jobscout.db import set_board_enabled

    _seed_board(conn, slug="starbridge")
    set_board_enabled(conn, platform="ashby", board_slug="starbridge", enabled=False)

    calls = []

    def fake_fetch(platform, board_slug):
        calls.append(board_slug)
        return []

    monkeypatch.setattr("jobscout.fetchers.board_registry.fetch_board_postings", fake_fetch)

    jobs = poll_known_boards(conn)
    assert jobs == []
    assert calls == []


def test_poll_known_boards_one_board_failure_does_not_abort_others(conn, monkeypatch):
    _seed_board(conn, slug="broken")
    _seed_board(conn, slug="fine")

    def fake_fetch(platform, board_slug):
        if board_slug == "broken":
            raise RuntimeError("boom")
        return [{"id": "1", "title": "Role", "jobUrl": "https://jobs.ashbyhq.com/fine/1"}]

    monkeypatch.setattr("jobscout.fetchers.board_registry.fetch_board_postings", fake_fetch)

    jobs = poll_known_boards(conn)
    assert len(jobs) == 1
    assert jobs[0].source_id.startswith("registry:ashby:fine:")

    boards = {b.board_slug: b for b in get_known_boards(conn)}
    assert boards["fine"].last_poll_result_count == 1
    # The failing board's poll still gets recorded as a zero-result poll —
    # fetch_board_postings raising is treated the same as it returning [].
    assert boards["broken"].last_poll_result_count == 0
