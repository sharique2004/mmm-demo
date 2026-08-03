"""Drift pipeline — the conductor.

Reads a meeting transcript (JSONL), turns it into Statements (Gemini Scribe or
ground-truth fixtures), files each statement into the FalkorDB memory graph,
asks the Historian whether a new decision contradicts prior memory, and — on
conflict — has the Herald report it (Guild.ai governed session, direct `gh`
post as fallback).

Usage:
    python pipeline.py ingest transcripts/m1-kickoff.jsonl [--fixtures] [--live]
    python pipeline.py history <issue_number>
    python pipeline.py stats
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).parent
load_dotenv(ROOT / ".env")

import detector           # noqa: E402  (env must load before sibling imports)
import extractor          # noqa: E402
import github_tools       # noqa: E402
import guild_trigger      # noqa: E402
from graph import Memory  # noqa: E402


# ---------------------------------------------------------------- helpers ---

def make_memory() -> Memory:
    return Memory(
        host=os.environ.get("FALKOR_HOST", "localhost"),
        port=int(os.environ.get("FALKOR_PORT", "6379")),
        graph_name=os.environ.get("GRAPH_NAME", "drift"),
    )


def read_transcript(path: Path) -> tuple[dict, list[dict]]:
    """Parse a JSONL transcript into (meta, segments)."""
    meta: dict | None = None
    segments: list[dict] = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            if obj.get("type") == "meta":
                meta = obj
            elif obj.get("type") == "segment":
                segments.append(obj)
    if meta is None:
        raise SystemExit(f"[scribe] ERROR: no meta line in {path}")
    return meta, segments


def load_issues_map() -> dict[str, int]:
    """slug -> real GitHub issue number, written by seed.py."""
    path = ROOT / "issues.json"
    if not path.exists():
        return {}
    return {slug: int(num) for slug, num in json.loads(path.read_text(encoding="utf-8")).items()}


def load_fixture_statements(meeting_id: str) -> list[dict]:
    path = ROOT / "transcripts" / "fixtures" / f"{meeting_id}.extraction.json"
    if not path.exists():
        raise SystemExit(f"[scribe] ERROR: fixture not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))["statements"]


def resolve_issue_number(stmt: dict, issues_map: dict[str, int], memory: Memory) -> int | None:
    """Explicit issue number > fixture slug (via issues.json) > full-text topic search."""
    if stmt.get("issue_number"):
        return int(stmt["issue_number"])
    slug = stmt.get("issue_slug")
    if slug and slug in issues_map:
        return issues_map[slug]
    return memory.find_issue(stmt.get("topic", ""))


def create_new_issue(spec: dict, memory: Memory, issues_map: dict[str, int]) -> int:
    """File a GitHub issue for work that was decided in a meeting but isn't tracked yet."""
    github_tools.ensure_labels()
    number = github_tools.create_issue(spec["title"], spec["body"], labels=("from-meeting",))
    issues_map[spec["slug"]] = number
    (ROOT / "issues.json").write_text(json.dumps(issues_map, indent=2), encoding="utf-8")
    memory.upsert_issue(number, github_tools.REPO, spec["title"], spec["slug"])
    print(f'[herald] no tracked issue for this work — filed GitHub issue #{number}: "{spec["title"]}"')
    print(f"[herald]   https://github.com/{github_tools.REPO}/issues/{number}")
    return number


# ------------------------------------------------------------- historian ----

# Deterministic fallback judge, used ONLY in fixtures mode when Gemini is
# unavailable — the demo-insurance path. Real runs use detector.judge().
_CHANGE_MARKERS = (
    "switch", "instead", "rather than", "reassign", "re-assign", "take over",
    "takes over", "taking over", "hand off", "hands off", "no longer",
    "dropping", "drop redis", "drop auth0", "scrap", "replace", "replacing",
    "overkill", "slip", "slips", "pushed", "pushing back", "move the target",
    "new target", "new deadline", "new owner", "pivot",
)


def heuristic_judge(prior_da: list[dict], new_statement: dict) -> "detector.Verdict":
    latest = prior_da[-1]
    if latest["date"] == new_statement["date"]:
        # Refines a decision made earlier in this same meeting — not drift.
        return detector.Verdict(conflict=False)
    text = new_statement["text"].lower()
    if not any(marker in text for marker in _CHANGE_MARKERS):
        return detector.Verdict(conflict=False)
    return detector.Verdict(
        conflict=True,
        before=latest["text"],
        after=new_statement["text"],
        what_changed=f'{latest["meeting"]} ({latest["date"]}) plan revised in {new_statement["meeting"]}',
    )


def run_judge(history: list[dict], prior_da: list[dict], new_statement: dict,
              *, fixtures: bool) -> "detector.Verdict":
    if fixtures and not os.environ.get("GEMINI_API_KEY"):
        print("[historian] no GEMINI_API_KEY — using offline heuristic judge (fixtures insurance mode)")
        return heuristic_judge(prior_da, new_statement)
    try:
        return detector.judge(history, new_statement)
    except Exception as exc:
        if not fixtures:
            raise
        print(f"[historian] Gemini judge failed ({exc.__class__.__name__}) — offline heuristic fallback")
        return heuristic_judge(prior_da, new_statement)


# -------------------------------------------------------------- commands ----

def cmd_ingest(args: argparse.Namespace) -> None:
    tpath = Path(args.transcript)
    if not tpath.exists():
        tpath = ROOT / args.transcript
    if not tpath.exists():
        raise SystemExit(f"[scribe] ERROR: transcript not found: {args.transcript}")

    meta, segments = read_transcript(tpath)
    meeting_id = meta["meeting_id"]
    meeting_title = meta["title"]
    meeting_date = meta["date"]
    seg_by_id = {s["segment_id"]: s for s in segments}

    print(f'[scribe] meeting {meeting_id} — "{meeting_title}" ({meeting_date}) · {len(segments)} segments')
    if args.live:
        # Live-transcription effect: words arrive as they are "spoken".
        print(f"[scribe] ● LIVE — transcribing {meeting_title}…\n")
        for seg in segments:
            sys.stdout.write(f'  {seg["ts"]}  {seg["speaker"]:>5}  ')
            sys.stdout.flush()
            for word in seg["text"].split():
                sys.stdout.write(word + " ")
                sys.stdout.flush()
                time.sleep(0.045)
            sys.stdout.write("\n")
            time.sleep(0.3)
        print(f"\n[scribe] ■ meeting ended — processing what was said\n")

    issues_map = load_issues_map()
    if args.fixtures and not issues_map:
        print("[scribe] WARNING: issues.json not found — run `python seed.py`; "
              "falling back to full-text issue lookup")

    if args.fixtures:
        statements = load_fixture_statements(meeting_id)
        print(f"[scribe] {len(statements)} statements loaded from fixtures (ground-truth extraction)")
    else:
        print(f"[scribe] extracting statements with Gemini ({len(segments)} segments, batched)…")
        statements = [s.model_dump() for s in extractor.extract(segments, meeting_title)]
        print(f"[scribe] {len(statements)} statements extracted")

    memory = make_memory()
    memory.ensure_indexes()

    ingested = skipped = judged = conflicts = comments = sessions = 0
    seen_segment_ids: dict[str, int] = {}

    for stmt in statements:
        seg = seg_by_id.get(stmt["segment_id"])
        if seg is None:
            print(f'[scribe] SKIP {stmt["segment_id"]}: segment not found in transcript')
            skipped += 1
            continue

        # Statements MERGE on segment_id in the graph; when live extraction yields
        # several statements from one segment, suffix so none are silently dropped.
        count = seen_segment_ids.get(stmt["segment_id"], 0)
        seen_segment_ids[stmt["segment_id"]] = count + 1
        if count:
            stmt = {**stmt, "segment_id": f'{stmt["segment_id"]}.{count + 1}'}

        number = resolve_issue_number(stmt, issues_map, memory)
        if number is None and stmt.get("new_issue"):
            # Fixture-declared new work item: the meeting itself files the issue.
            number = create_new_issue(stmt["new_issue"], memory, issues_map)
        elif number is None and not args.fixtures and stmt["kind"] == "decision":
            # Live path: a decision about untracked work also files an issue.
            topic = stmt.get("topic", "").strip() or "untracked work item"
            spec = {
                "slug": "-".join(topic.lower().split())[:40],
                "title": topic[:1].upper() + topic[1:][:90],
                "body": (
                    f"## Goal\n{stmt['claim']}\n\n"
                    f"{github_tools.PLAN_OPEN}\n**Plan:** {stmt['claim']}\n{github_tools.PLAN_CLOSE}\n\n"
                    f"_Filed by Drift from a meeting transcript._"
                ),
            }
            number = create_new_issue(spec, memory, issues_map)
        if number is None:
            print(f'[scribe] SKIP "{stmt["topic"]}" ({stmt["speaker"]}): could not resolve to an issue')
            skipped += 1
            continue

        # Capture the issue's memory BEFORE the new statement lands in the graph.
        prior = memory.issue_history(number)

        memory.ingest_statement(
            segment_id=stmt["segment_id"],
            person=stmt["speaker"],
            meeting_id=meeting_id,
            meeting_title=meeting_title,
            meeting_date=meeting_date,
            issue_number=number,
            text=stmt["claim"],
            kind=stmt["kind"],
            ts=seg["ts"],
        )
        ingested += 1
        print(f'[graph] {stmt["speaker"]} → issue #{number} ({stmt["kind"]}): "{stmt["claim"]}"')

        if stmt["kind"] not in ("decision", "assignment"):
            continue
        prior_da = [r for r in prior if r["kind"] in ("decision", "assignment")]
        if not prior_da:
            print(f"[historian] issue #{number}: first decision on record — nothing to compare")
            continue

        new_statement = {
            "who": stmt["speaker"],
            "kind": stmt["kind"],
            "text": stmt["claim"],
            "meeting": meeting_title,
            "date": meeting_date,
        }
        print(f'[historian] issue #{number}: {len(prior)} prior statements '
              f'({len(prior_da)} decisions/assignments) — judging the new {stmt["kind"]}…')
        verdict = run_judge(prior, prior_da, new_statement, fixtures=args.fixtures)
        judged += 1

        if not verdict.conflict:
            print(f"[historian] consistent — no drift on issue #{number}")
            continue

        conflicts += 1
        print(f"[historian] CONFLICT on issue #{number}: {verdict.what_changed}")
        print(f"[historian]   before: {verdict.before}")
        print(f"[historian]   after:  {verdict.after}")

        payload = {
            "repo": github_tools.REPO,
            "issue_number": number,
            "prior_decision": verdict.before,
            "new_decision": verdict.after,
            "what_changed": verdict.what_changed,
            "speaker": stmt["speaker"],
            "meeting": meeting_title,
            "date": meeting_date,
            "quote": seg["text"],
            "ts": seg["ts"],
        }
        session = guild_trigger.report_conflict(payload)
        if session is not None:
            sessions += 1
            print(f"[herald] Guild session started: {session} — Herald is drafting the comment, "
                  "human approval pending in Guild")
        else:
            try:
                url = github_tools.post_comment(number, guild_trigger.build_comment(payload))
                comments += 1
                print(f"[herald] posted directly to GitHub: {url}")
            except Exception as exc:
                print(f"[herald] WARNING: direct post failed ({exc}) — continuing")
        try:
            github_tools.add_label(number, "decision-changed")
            print("[herald] label added: decision-changed")
        except Exception as exc:
            print(f"[herald] WARNING: label add failed ({exc}) — continuing")

        latest = prior_da[-1]
        memory.mark_superseded(stmt["segment_id"], latest["segment_id"])
        print(f'[graph] SUPERSEDES: {stmt["segment_id"]} → {latest["segment_id"]} '
              f'({latest["date"]}: "{latest["text"]}")')

        # Keep the issue body itself in sync: replace the drift:plan block so the
        # next developer reads the CURRENT plan, not Monday's.
        plan_md = stmt.get("updated_plan") or (
            f"**Current plan (updated from {meeting_title}, {meeting_date}):** {verdict.after}"
        )
        try:
            github_tools.update_plan_block(number, plan_md)
            print(f"[herald] issue #{number} body updated — the stated plan now matches {meeting_title}")
        except Exception as exc:
            print(f"[herald] WARNING: body update failed ({exc}) — continuing")

    print()
    print(f'[scribe]    meeting "{meeting_title}" ingested — {ingested} statements, {skipped} skipped')
    print(f"[historian] {judged} decision(s) judged against prior memory — {conflicts} conflict(s) found")
    print(f"[herald]    {sessions} Guild session(s) started · {comments} comment(s) posted directly")
    stats = memory.stats()
    print("[graph]     " + " · ".join(f"{k}={v}" for k, v in stats.items()))


def cmd_history(args: argparse.Namespace) -> None:
    memory = make_memory()
    rows = memory.issue_history(args.issue_number)
    if not rows:
        print(f"[historian] issue #{args.issue_number}: no statements in memory")
        return
    print(f"[historian] issue #{args.issue_number} — {len(rows)} statement(s), oldest first")
    for row in rows:
        print(f'[historian] {row["date"]}  {row["meeting"]:<18} {row["who"]:<7} '
              f'{row["kind"]:<10} {row["text"]}')


def cmd_stats(_args: argparse.Namespace) -> None:
    memory = make_memory()
    print("[graph] drift memory stats")
    for key, value in memory.stats().items():
        print(f"[graph]   {key}: {value}")


# ------------------------------------------------------------------ main ----

def main() -> None:
    parser = argparse.ArgumentParser(
        prog="pipeline.py",
        description="Drift — turn meeting transcripts into graph memory and catch decision drift",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_ingest = sub.add_parser("ingest", help="ingest one meeting transcript")
    p_ingest.add_argument("transcript", help="path to a JSONL transcript, e.g. transcripts/m1-kickoff.jsonl")
    p_ingest.add_argument("--fixtures", action="store_true",
                          help="use ground-truth extraction fixtures (no Gemini needed)")
    p_ingest.add_argument("--live", action="store_true",
                          help="replay segments with 0.5s pacing (stage effect)")
    p_ingest.set_defaults(func=cmd_ingest)

    p_history = sub.add_parser("history", help="print one issue's memory timeline")
    p_history.add_argument("issue_number", type=int)
    p_history.set_defaults(func=cmd_history)

    p_stats = sub.add_parser("stats", help="print graph node/edge counts")
    p_stats.set_defaults(func=cmd_stats)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
