import xml.etree.ElementTree as ET

import jobscout.fetchers.weworkremotely as wwr_module
from jobscout.fetchers.weworkremotely import WeWorkRemotelyFetcher, _split_company_and_title
from jobscout.models import ApplicationChannel, ContractTypeGuess

# Shape verified live against https://weworkremotely.com/remote-jobs.rss.
# Note <country>/<state>/<skills> present but EMPTY — the common real case.
ITEM_TEMPLATE = """<item>
  <title>{title}</title>
  <region>{region}</region>
  <country>{country}</country>
  <state></state>
  <skills>{skills}</skills>
  <category>Back-End Programming</category>
  <type>{type}</type>
  <description>&lt;p&gt;&lt;strong&gt;Headquarters:&lt;/strong&gt; Remote US&lt;/p&gt;&lt;p&gt;Build LLM pipelines.&lt;/p&gt;</description>
  <pubDate>Thu, 13 Aug 2026 07:31:17 +0000</pubDate>
  <expires_at>Sat, 12 Sep 2026 07:31:17 +0000</expires_at>
  <guid>{guid}</guid>
  <link>{guid}</link>
</item>"""


def make_item(
    title="Grailed: Senior Backend Software Engineer",
    region="Anywhere in the World",
    country="",
    skills="",
    type="Full-Time",
    guid="https://weworkremotely.com/remote-jobs/grailed-senior-backend-software-engineer",
) -> ET.Element:
    return ET.fromstring(
        ITEM_TEMPLATE.format(title=title, region=region, country=country, skills=skills, type=type, guid=guid)
    )


def make_feed(*items_xml: str) -> bytes:
    return ("<rss><channel>" + "".join(items_xml) + "</channel></rss>").encode()


class FakeResponse:
    def __init__(self, content, status_ok=True):
        self.content = content
        self._status_ok = status_ok

    def raise_for_status(self):
        if not self._status_ok:
            raise RuntimeError("boom")


def test_parse_item_maps_core_fields():
    job = WeWorkRemotelyFetcher()._parse_item(make_item())
    assert job.company == "Grailed"
    assert job.title == "Senior Backend Software Engineer"
    assert job.source == "weworkremotely"
    assert job.url.endswith("grailed-senior-backend-software-engineer")
    assert job.application_channel == ApplicationChannel.URL


def test_description_html_stripped():
    job = WeWorkRemotelyFetcher()._parse_item(make_item())
    assert "<strong>" not in job.description
    assert "Build LLM pipelines." in job.description


def test_pubdate_parsed_from_rfc822():
    job = WeWorkRemotelyFetcher()._parse_item(make_item())
    assert job.posted_date is not None
    assert (job.posted_date.year, job.posted_date.month, job.posted_date.day) == (2026, 8, 13)
    assert job.posted_date.tzinfo is not None


def test_type_becomes_contract_type_guess():
    assert WeWorkRemotelyFetcher()._parse_item(make_item()).contract_type_guess == ContractTypeGuess.EMPLOYMENT
    contract = WeWorkRemotelyFetcher()._parse_item(make_item(type="Contract"))
    assert contract.contract_type_guess == ContractTypeGuess.FREELANCE


def test_empty_elements_do_not_leak_into_location_or_tags():
    # <country>/<state>/<skills> are usually present-but-empty, not absent.
    job = WeWorkRemotelyFetcher()._parse_item(make_item())
    assert job.location_text == "Anywhere in the World"
    assert job.tags == ["Back-End Programming"]


def test_populated_country_and_skills_included():
    job = WeWorkRemotelyFetcher()._parse_item(make_item(region="Europe", country="Germany", skills="Python"))
    assert job.location_text == "Europe, Germany"
    assert job.tags == ["Back-End Programming", "Python"]


def test_split_company_and_title_uses_first_separator_only():
    assert _split_company_and_title("Acme: Engineer: Backend") == ("Acme", "Engineer: Backend")


def test_split_company_and_title_falls_back_without_separator():
    company, title = _split_company_and_title("Senior Backend Engineer")
    assert title == "Senior Backend Engineer"
    assert company == "(company not stated)"


def test_split_company_and_title_falls_back_on_empty_side():
    assert _split_company_and_title(": Engineer")[0] == "(company not stated)"
    assert _split_company_and_title("Acme: ")[0] == "(company not stated)"


def test_parse_item_returns_none_without_url_or_title():
    assert WeWorkRemotelyFetcher()._parse_item(make_item(guid="")) is None
    assert WeWorkRemotelyFetcher()._parse_item(make_item(title="")) is None


def test_fetch_dedupes_the_same_job_across_feeds(monkeypatch):
    # The site-wide feed and the category feeds legitimately overlap.
    feed = make_feed(ITEM_TEMPLATE.format(
        title="Acme: Engineer", region="Europe", country="", skills="", type="Contract",
        guid="https://weworkremotely.com/remote-jobs/acme-engineer",
    ))
    monkeypatch.setattr(wwr_module.requests, "get", lambda *a, **k: FakeResponse(feed))
    jobs = WeWorkRemotelyFetcher().fetch()
    assert len(jobs) == 1


def test_fetch_reads_every_configured_feed(monkeypatch):
    seen = []

    def fake_get(url, headers=None, timeout=None):
        seen.append(url)
        slug = str(len(seen))
        return FakeResponse(make_feed(ITEM_TEMPLATE.format(
            title=f"Acme{slug}: Engineer", region="Europe", country="", skills="", type="Full-Time",
            guid=f"https://weworkremotely.com/remote-jobs/{slug}",
        )))

    monkeypatch.setattr(wwr_module.requests, "get", fake_get)
    jobs = WeWorkRemotelyFetcher().fetch()
    assert seen == list(wwr_module.FEED_URLS)
    assert len(jobs) == len(wwr_module.FEED_URLS)


def test_one_dead_feed_does_not_kill_the_others(monkeypatch):
    def fake_get(url, headers=None, timeout=None):
        if url == wwr_module.FEED_URLS[0]:
            raise RuntimeError("network down")
        return FakeResponse(make_feed(ITEM_TEMPLATE.format(
            title="Acme: Engineer", region="Europe", country="", skills="", type="Full-Time",
            guid=f"https://weworkremotely.com/remote-jobs/{url[-12:]}",
        )))

    monkeypatch.setattr(wwr_module.requests, "get", fake_get)
    assert len(WeWorkRemotelyFetcher().fetch()) == len(wwr_module.FEED_URLS) - 1


def test_fetch_returns_empty_when_every_feed_fails(monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("network down")

    monkeypatch.setattr(wwr_module.requests, "get", boom)
    assert WeWorkRemotelyFetcher().fetch() == []


def test_fetch_returns_empty_on_malformed_xml(monkeypatch):
    monkeypatch.setattr(wwr_module.requests, "get", lambda *a, **k: FakeResponse(b"<rss><channel>truncated"))
    assert WeWorkRemotelyFetcher().fetch() == []
