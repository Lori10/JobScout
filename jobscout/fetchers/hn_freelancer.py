"""Hacker News "Freelancer? Seeking freelancer?" fetcher — the monthly
companion thread to "Who is hiring?", and the only source here that carries
genuine client-side project postings rather than employer headcount.

Mechanically it is the Who-is-hiring fetcher with three hooks overridden
(see HNWhoIsHiringFetcher's hook block): a different search query, a
different thread-title test, and an INVERTED comment filter. Everything
else — the two-step Algolia fetch, concurrent board resolution, and
Ashby/Greenhouse/Lever bundle expansion — is inherited unchanged.

The inverted filter is the whole point. The thread's own guidelines are
"lead with either SEEKING WORK or SEEKING FREELANCER":

  SEEKING WORK       an individual advertising themselves  -> not a job
  SEEKING FREELANCER a client with a project to hire for   -> a job

Only the latter is a posting. Without this filter the source would produce
almost nothing BUT other freelancers' self-advertisements.

EXPECT ALMOST NOTHING FROM THIS SOURCE. Measured live across the five
threads from April-August 2026: 102 top-level comments, of which 95 were
SEEKING WORK, 5 were unmarked self-profiles, and exactly **1** was SEEKING
FREELANCER — roughly one usable posting every five months. It is included
because it is a ~40-line subclass over an existing fetcher and because a
client posting a real project is the single best match for a B2B contract
profile, not because it will move the numbers. A run that returns zero jobs
here is the normal case, not a bug.
"""

from __future__ import annotations

import re

from jobscout.fetchers.hn_whoishiring import HNWhoIsHiringFetcher

# Anchored at the start: the marker is a lead-with convention, and a comment
# merely mentioning "seeking freelancer" mid-body is a SEEKING WORK post
# referring to the thread itself. Tolerates the "SEEKING FREELANCERS" plural
# and both the "|" and "-"/":" separators seen in real posts.
_SEEKING_FREELANCER_RE = re.compile(r"\s*seeking\s+freelancers?\b\s*[|\-:,]?\s*", re.IGNORECASE)


class HNFreelancerFetcher(HNWhoIsHiringFetcher):
    name = "hn_freelancer"
    search_query = '"Ask HN: Freelancer? Seeking freelancer?"'

    def _matches_thread_title(self, title: str) -> bool:
        # The same Algolia query also returns the "Who is hiring?" and
        # "Who wants to be hired?" threads, which must not be picked up.
        return "freelancer" in title and "seeking freelancer" in title

    def _accept_comment(self, plain: str) -> bool:
        return _SEEKING_FREELANCER_RE.match(plain) is not None

    def _header_line(self, plain: str) -> str:
        """Drops the leading SEEKING FREELANCER marker before the inherited
        "|"-splitting runs — otherwise the marker itself would be parsed as
        the company name for every single posting."""
        first_line = plain.splitlines()[0] if plain.splitlines() else ""
        return _SEEKING_FREELANCER_RE.sub("", first_line, count=1).strip()
