"""Fetcher registry — the extension point Phase 4's new sources (We Work
Remotely, Jobicy, Arbeitnow, Himalayas, freelancermap.de/freelance.de) will
drop into, without any changes to pipeline.py beyond a new entry here."""

from jobscout.fetchers.hn_whoishiring import HNWhoIsHiringFetcher
from jobscout.fetchers.remoteok import RemoteOKFetcher
from jobscout.fetchers.remotive import RemotiveFetcher

FETCHER_REGISTRY = {
    "remoteok": RemoteOKFetcher,
    "remotive": RemotiveFetcher,
    "hn_whoishiring": HNWhoIsHiringFetcher,
}
