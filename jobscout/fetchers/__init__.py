"""Fetcher registry — the extension point for new sources, requiring no
changes to pipeline.py beyond a new entry here.

A source registered here but absent from config.yaml's `sources:` block is
ENABLED by default (pipeline.py reads `config.sources.get(name, True)`), so
every new entry should get an explicit toggle there too.
"""

from jobscout.fetchers.arbeitnow import ArbeitnowFetcher
from jobscout.fetchers.himalayas import HimalayasFetcher
from jobscout.fetchers.hn_freelancer import HNFreelancerFetcher
from jobscout.fetchers.hn_whoishiring import HNWhoIsHiringFetcher
from jobscout.fetchers.jobicy import JobicyFetcher
from jobscout.fetchers.remoteok import RemoteOKFetcher
from jobscout.fetchers.remotive import RemotiveFetcher
from jobscout.fetchers.weworkremotely import WeWorkRemotelyFetcher

FETCHER_REGISTRY = {
    "remoteok": RemoteOKFetcher,
    "remotive": RemotiveFetcher,
    "hn_whoishiring": HNWhoIsHiringFetcher,
    "hn_freelancer": HNFreelancerFetcher,
    "himalayas": HimalayasFetcher,
    "jobicy": JobicyFetcher,
    "weworkremotely": WeWorkRemotelyFetcher,
    "arbeitnow": ArbeitnowFetcher,
}
