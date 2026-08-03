"""Seed the four Drift demo issues on GitHub and mirror them into the FalkorDB graph.

Idempotent: re-running never duplicates issues (matched by open-issue title) and
graph upserts are MERGE-based. Run with `python seed.py`. Nothing effectful
happens at import time — reset.py imports upsert_graph() from here.
"""

import json
import os
import time
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

import graph
from github_tools import REPO, create_issue, ensure_labels, list_issue_titles

ISSUES_JSON = Path(__file__).parent / "issues.json"

# The four contract issues, in creation order. Titles/plan fields are load-bearing:
# agent A's transcripts and the CONTRACT.md table reference them exactly.
ISSUES: list[dict] = [
    {
        "slug": "oauth",
        "title": "Migrate auth service to OAuth2",
        "body": """\
## Goal

Replace the legacy session-cookie auth service with a standards-compliant OAuth2 flow so partner integrations get scoped, expiring tokens instead of long-lived shared secrets.

## Plan

- Provider: **Auth0**
- Owner: **Marco**
- Target: **Aug 20**

## Tasks

- [ ] Spike: Auth0 tenant setup + dev application config
- [ ] Implement authorization-code flow in the auth service
- [ ] Swap session middleware for JWT validation
- [ ] Cut partner API keys over to scoped tokens
""",
    },
    {
        "slug": "cache",
        "title": "Add cache layer for transcript ingestion",
        "body": """\
## Goal

Ingestion currently re-parses every transcript on each run; hot meeting data should be served from a cache so a full meeting ingests in under 2 seconds.

## Plan

- Approach: **Redis cache**
- Owner: **Sam**
- Target: **Aug 10**

## Tasks

- [ ] Define cache key scheme (meeting_id + segment hash)
- [ ] Wire a Redis client with connection pooling into the ingest worker
- [ ] Benchmark cold vs warm ingest on the m-series fixtures
""",
    },
    {
        "slug": "offline",
        "title": "Mobile offline mode for field agents",
        "body": """\
## Goal

Field agents routinely lose connectivity on site; the mobile app must queue reads and writes locally and reconcile when the device comes back online.

## Plan

- Status: **scoping**
- **Chen** drafting spec

## Tasks

- [ ] Chen: draft offline-mode spec (local storage, sync protocol, conflict policy)
- [ ] Review spec with mobile + backend
- [ ] Estimate implementation for an upcoming sprint
""",
    },
    {
        "slug": "ratelimit",
        "title": "Rate-limit the public API",
        "body": """\
## Goal

The public API has no rate limiting, so a single misbehaving client can saturate the ingestion queue for everyone. Add per-key limits before the beta opens up.

## Plan

- Status: **proposed, no owner yet**

## Tasks

- [ ] Choose limiter strategy (token bucket vs sliding window)
- [ ] Pick the enforcement point (gateway vs app middleware)
- [ ] Define per-key quotas and the 429 response shape
""",
    },
]


def upsert_graph(numbers: dict[str, int]) -> None:
    """Ensure indexes and upsert the four Issue nodes. Reused by reset.py."""
    mem = graph.Memory(
        host=os.environ.get("FALKOR_HOST", "localhost"),
        port=int(os.environ.get("FALKOR_PORT", "6379")),
        graph_name=os.environ.get("GRAPH_NAME", "drift"),
    )
    mem.ensure_indexes()
    for spec in ISSUES:
        mem.upsert_issue(
            number=numbers[spec["slug"]],
            repo=REPO,
            title=spec["title"],
            slug=spec["slug"],
        )
    print(f"[seed] graph: indexes ensured, {len(ISSUES)} Issue nodes upserted")


def main() -> None:
    print(f"[seed] repo: {REPO}")

    ensure_labels()
    print("[seed] labels ok: decision-changed, from-meeting")

    existing = list_issue_titles()
    numbers: dict[str, int] = {}
    status: dict[str, str] = {}
    for spec in ISSUES:
        if spec["title"] in existing:
            numbers[spec["slug"]] = existing[spec["title"]]
            status[spec["slug"]] = "exists"
        else:
            numbers[spec["slug"]] = create_issue(spec["title"], spec["body"])
            status[spec["slug"]] = "created"
            time.sleep(1)  # secondary-rate-limit courtesy between creates

    ISSUES_JSON.write_text(json.dumps(numbers, indent=2) + "\n")
    print(f"[seed] wrote {ISSUES_JSON.name}: {json.dumps(numbers)}")

    upsert_graph(numbers)

    print("[seed] slug       #    status   title")
    for spec in ISSUES:
        slug = spec["slug"]
        print(f"[seed] {slug:<10} #{numbers[slug]:<3} {status[slug]:<8} {spec['title']}")


if __name__ == "__main__":
    main()
