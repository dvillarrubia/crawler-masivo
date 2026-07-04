"""
robots.txt versioning (T16).

Persists a snapshot of robots.txt per seed host at job start, so T7's
diff can flag ``robots_txt_changed`` between crawls — silent indexation
disasters usually start there.

Always on (one tiny fetch per host); failures never abort the crawl.
Fetching is injectable for tests, sharing the convention of
``sitemap_ingest``.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Callable, Iterable
from urllib.parse import urljoin, urlparse

from shared.models import RobotsSnapshot

logger = logging.getLogger(__name__)

Fetch = Callable[[str], bytes]


def _default_fetch(url: str) -> bytes:
    import urllib.request

    req = urllib.request.Request(
        url, headers={"User-Agent": "SEOCrawler/1.0 (+robots-snapshot)"}
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return resp.read()


def persist_robots_snapshots(
    session,
    job_id,
    seeds: Iterable[str],
    fetch: Fetch | None = None,
) -> int:
    """Snapshot robots.txt for every distinct seed host. Returns count."""
    fetch = fetch or _default_fetch

    seen_hosts: set[str] = set()
    count = 0
    for seed in seeds:
        parsed = urlparse(seed)
        if not parsed.hostname or parsed.hostname in seen_hosts:
            continue
        seen_hosts.add(parsed.hostname)

        content: str | None
        try:
            body = fetch(urljoin(f"{parsed.scheme}://{parsed.netloc}", "/robots.txt"))
            content = body.decode("utf-8", "replace")
        except Exception as exc:
            logger.warning(
                "robots.txt snapshot failed for %s: %s", parsed.hostname, exc
            )
            content = None

        session.add(RobotsSnapshot(
            job_id=job_id,
            host=parsed.hostname,
            content=content,
            content_hash=(
                hashlib.sha256(content.encode("utf-8")).hexdigest()
                if content is not None else None
            ),
        ))
        count += 1
    session.flush()
    return count
