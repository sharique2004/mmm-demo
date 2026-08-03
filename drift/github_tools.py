"""GitHub helpers for Drift — thin wrappers over the authenticated `gh` CLI.

All bodies go over stdin (`-F body=@-`) so markdown is never shell-interpolated.
`gh api` is used (not `gh issue create`) because it returns JSON with .number
and .html_url. See RESEARCH.md for the verified patterns.
"""

import json
import os
import subprocess
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

REPO = os.environ.get("DRIFT_REPO", "sharique2004/mmm-demo")

LABELS = (
    ("decision-changed", "D93F0B", "A meeting changed a prior decision on this issue"),
    ("from-meeting", "0E8A16", "Posted by Drift from a meeting transcript"),
)


def gh(*args: str, stdin: str | None = None) -> str:
    """Run `gh <args>` and return stdout. Raises RuntimeError with stderr on failure."""
    r = subprocess.run(["gh", *args], capture_output=True, text=True, input=stdin)
    if r.returncode != 0:
        raise RuntimeError(f"gh failed: {r.stderr.strip()}")
    return r.stdout


def ensure_labels() -> None:
    """Create (or update) the Drift labels. `--force` makes this idempotent."""
    for name, color, description in LABELS:
        gh(
            "label", "create", name,
            "--repo", REPO,
            "--color", color,
            "--description", description,
            "--force",
        )


def create_issue(title: str, body_md: str, labels: tuple = ()) -> int:
    """Create an issue in REPO; returns the real issue number from the API response."""
    args = ["api", f"repos/{REPO}/issues", "-f", f"title={title}", "-F", "body=@-"]
    for label in labels:
        args += ["-f", f"labels[]={label}"]
    out = gh(*args, stdin=body_md)
    return json.loads(out)["number"]


def post_comment(issue_no: int, body_md: str) -> str:
    """Post a markdown comment on an issue; returns the comment's html_url."""
    out = gh(
        "api", f"repos/{REPO}/issues/{issue_no}/comments",
        "-F", "body=@-",
        stdin=body_md,
    )
    return json.loads(out)["html_url"]


def add_label(issue_no: int, label: str) -> None:
    """Add a single existing label to an issue."""
    gh("api", f"repos/{REPO}/issues/{issue_no}/labels", "-f", f"labels[]={label}")


def list_issue_titles() -> dict[str, int]:
    """{title: number} of open issues in REPO (PRs excluded). Used for idempotent seeding."""
    out = gh("api", "--paginate", f"repos/{REPO}/issues?state=open&per_page=100")
    titles: dict[str, int] = {}
    # --paginate concatenates one JSON array per page; decode them all.
    decoder = json.JSONDecoder()
    idx = 0
    stripped = out.strip()
    while idx < len(stripped):
        page, end = decoder.raw_decode(stripped, idx)
        for item in page:
            if "pull_request" in item:
                continue  # the issues endpoint also returns PRs
            titles[item["title"]] = item["number"]
        idx = end
        while idx < len(stripped) and stripped[idx] in " \t\r\n":
            idx += 1
    return titles
