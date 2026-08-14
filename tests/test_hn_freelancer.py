import jobscout.fetchers.hn_whoishiring as hn_module
from jobscout.fetchers.hn_freelancer import HNFreelancerFetcher

# Real SEEKING FREELANCER comment from the June 2026 thread.
REAL_SEEKING_FREELANCER = (
    "SEEKING FREELANCER | Remote (US+EU only) Stack: Vue3, Ruby+Sinatra, Postgres, Railway, Cloudflare\n"
    "We've built a new platform in the electronic music space, currently in limited beta."
)
# The overwhelmingly common comment in that thread — an individual
# advertising themselves, which is not a job.
REAL_SEEKING_WORK = "SEEKING WORK | US | Remote | chad@chadworks.co\nOne-person web studio building custom sites."


def make_comment(text: str, comment_id: int = 42) -> dict:
    return {"id": comment_id, "text": text, "created_at": "2026-08-03T12:00:00.000Z"}


def test_seeking_freelancer_comment_is_accepted():
    job = HNFreelancerFetcher()._parse_comment(make_comment(REAL_SEEKING_FREELANCER))
    assert job is not None
    assert job.source == "hn_freelancer"


def test_seeking_work_comment_is_rejected():
    # 95 of 102 real comments across five threads were SEEKING WORK.
    assert HNFreelancerFetcher()._parse_comment(make_comment(REAL_SEEKING_WORK)) is None


def test_unmarked_self_profile_is_rejected():
    text = "I'm a seasoned generalist with deep focus in game development and performance engineering."
    assert HNFreelancerFetcher()._parse_comment(make_comment(text)) is None


def test_mid_body_mention_of_seeking_freelancer_is_rejected():
    # The marker is a lead-with convention; a SEEKING WORK post referring to
    # the thread must not sneak through.
    text = "SEEKING WORK | Germany | Remote\nI reply to every SEEKING FREELANCER post in this thread."
    assert HNFreelancerFetcher()._parse_comment(make_comment(text)) is None


def test_plural_and_alternate_separators_accepted():
    for header in (
        "SEEKING FREELANCERS | Berlin | Remote OK",
        "Seeking freelancer - Berlin - Remote OK",
        "SEEKING FREELANCER: Berlin | Remote OK",
    ):
        assert HNFreelancerFetcher()._parse_comment(make_comment(header + "\nWe need help.")) is not None, header


def test_marker_is_stripped_before_company_parsing():
    # Without the strip, every posting's company would be "SEEKING FREELANCER".
    job = HNFreelancerFetcher()._parse_comment(make_comment("SEEKING FREELANCER | Acme Corp | Remote | Python work"))
    assert job.company == "Acme Corp"
    assert "SEEKING FREELANCER" not in job.company


def test_header_line_strips_only_the_marker():
    assert HNFreelancerFetcher()._header_line("SEEKING FREELANCER | Acme | Remote\nbody") == "Acme | Remote"


def test_full_body_preserved_in_description():
    job = HNFreelancerFetcher()._parse_comment(make_comment(REAL_SEEKING_FREELANCER))
    assert "electronic music space" in job.description
    assert job.description.startswith("SEEKING FREELANCER")


def test_thread_title_matching_rejects_sibling_threads():
    fetcher = HNFreelancerFetcher()
    assert fetcher._matches_thread_title("ask hn: freelancer? seeking freelancer? (august 2026)")
    assert not fetcher._matches_thread_title("ask hn: who is hiring? (august 2026)")
    assert not fetcher._matches_thread_title("ask hn: who wants to be hired? (august 2026)")


def test_search_query_targets_the_freelancer_thread(monkeypatch):
    seen = {}

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"hits": [{"title": "Ask HN: Freelancer? Seeking freelancer? (August 2026)", "objectID": "49157021"}]}

    def fake_get(url, params=None, timeout=None):
        seen.update(params)
        return FakeResponse()

    monkeypatch.setattr(hn_module.requests, "get", fake_get)
    assert HNFreelancerFetcher()._find_latest_thread_id() == "49157021"
    assert "Freelancer? Seeking freelancer?" in seen["query"]


def test_who_is_hiring_filter_is_unchanged_by_the_refactor():
    # The base class's own comment filter must keep rejecting résumé-style
    # self-profiles while accepting ordinary employer postings.
    base = hn_module.HNWhoIsHiringFetcher()
    assert base._parse_comment(make_comment("Acme | Senior LLM Engineer | Remote\nWe are hiring.")) is not None
    assert base._parse_comment(make_comment("Location: Berlin\nWilling to relocate: yes\nRésumé/CV: link")) is None


def test_freelancer_fetcher_inherits_board_expansion():
    # The ATS bundle expansion is the main reason this is a subclass rather
    # than a copy — a client linking a whole board still expands.
    assert HNFreelancerFetcher()._expand_bundled_board.__func__ is hn_module.HNWhoIsHiringFetcher._expand_bundled_board
