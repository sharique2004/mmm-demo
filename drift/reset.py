"""Reset Drift's local memory between demo runs.

Wipes the FalkorDB graph, then — if seed.py has already created the GitHub
issues (issues.json exists) — re-seeds just the graph side: full-text index +
the four Issue nodes. GitHub itself is never touched; the exact `gh api`
cleanup commands are printed instead.

Usage: python reset.py
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).parent
load_dotenv(ROOT / ".env")

from graph import Memory  # noqa: E402  (env must load before sibling imports)

# Titles from CONTRACT.md — must match what seed.py creates.
ISSUE_TITLES: dict[str, str] = {
    "oauth": "Migrate auth service to OAuth2",
    "cache": "Add cache layer for transcript ingestion",
    "offline": "Mobile offline mode for field agents",
    "ratelimit": "Rate-limit the public API",
}


def main() -> None:
    host = os.environ.get("FALKOR_HOST", "localhost")
    port = int(os.environ.get("FALKOR_PORT", "6379"))
    graph_name = os.environ.get("GRAPH_NAME", "drift")
    repo = os.environ.get("DRIFT_REPO", "sharique2004/mmm-demo")

    try:
        Memory(host=host, port=port, graph_name=graph_name).wipe()
        print(f'[graph] wiped graph "{graph_name}" on {host}:{port}')
    except Exception as exc:
        print(f'[graph] wipe skipped ({exc}) — graph "{graph_name}" may not exist yet')

    issues_path = ROOT / "issues.json"
    issues: dict[str, int] = {}
    if issues_path.exists():
        issues = {slug: int(num) for slug, num in
                  json.loads(issues_path.read_text(encoding="utf-8")).items()}
        memory = Memory(host=host, port=port, graph_name=graph_name)
        memory.ensure_indexes()
        for slug, number in issues.items():
            title = ISSUE_TITLES.get(slug, slug)
            memory.upsert_issue(number, repo, title, slug)
            print(f"[graph] re-seeded Issue #{number} ({slug}) — {title}")
    else:
        print("[graph] issues.json not found — run `python seed.py` to create the "
              "GitHub issues and seed the graph")

    print()
    print("[reset] GitHub is NOT touched by this script. To clean the demo repo manually:")
    print("[reset]   1. List Drift's demo comments (id + url):")
    jq = '.[] | select(.body | contains("Drift Historian")) | "\\(.id) \\(.html_url)"'
    print(f"[reset]      gh api repos/{repo}/issues/comments --paginate --jq '{jq}'")
    print("[reset]   2. Delete each one:")
    print(f"[reset]      gh api -X DELETE repos/{repo}/issues/comments/<COMMENT_ID>")
    print("[reset]   3. Remove the decision-changed label where it was added:")
    if issues:
        for number in issues.values():
            print(f'[reset]      gh api -X DELETE "repos/{repo}/issues/{number}/labels/decision-changed"')
    else:
        print(f'[reset]      gh api -X DELETE "repos/{repo}/issues/<N>/labels/decision-changed"')


if __name__ == "__main__":
    main()
