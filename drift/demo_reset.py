"""Reset the Monday/Tuesday demo to a clean slate.

Deletes the issue that Monday's meeting filed (GraphQL — REST cannot delete
issues), drops its slug from issues.json, and wipes the memory graph, so the
demo starts from literally nothing: no graph, no issue.

Usage: python demo_reset.py [slug]   (default slug: search)
"""

import json
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).parent
load_dotenv(ROOT / ".env")

import github_tools       # noqa: E402
from pipeline import make_memory  # noqa: E402


def delete_issue(number: int) -> None:
    owner, repo = github_tools.REPO.split("/")
    out = github_tools.gh(
        "api", "graphql",
        "-f", "query=query($owner:String!,$repo:String!,$n:Int!){"
              "repository(owner:$owner,name:$repo){issue(number:$n){id}}}",
        "-f", f"owner={owner}", "-f", f"repo={repo}", "-F", f"n={number}",
    )
    node_id = json.loads(out)["data"]["repository"]["issue"]["id"]
    github_tools.gh(
        "api", "graphql",
        "-f", "query=mutation($id:ID!){deleteIssue(input:{issueId:$id}){clientMutationId}}",
        "-f", f"id={node_id}",
    )
    print(f"[reset] deleted GitHub issue #{number}")


def main() -> None:
    slug = sys.argv[1] if len(sys.argv) > 1 else "search"
    path = ROOT / "issues.json"
    issues = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    if slug in issues:
        try:
            delete_issue(int(issues[slug]))
        except Exception as exc:
            print(f"[reset] WARNING: could not delete issue for '{slug}': {exc}")
        del issues[slug]
        path.write_text(json.dumps(issues, indent=2), encoding="utf-8")
        print(f"[reset] removed '{slug}' from issues.json")
    else:
        print(f"[reset] no '{slug}' entry in issues.json — nothing to delete")

    memory = make_memory()
    memory.wipe()
    print(f"[reset] graph wiped: {memory.stats()}")
    print("[reset] demo-ready — Monday starts from an empty world:")
    print("[reset]   python pipeline.py ingest transcripts/monday.jsonl --fixtures --live")
    print("[reset]   python pipeline.py ingest transcripts/tuesday.jsonl --fixtures --live")


if __name__ == "__main__":
    main()
