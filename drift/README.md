# Drift — your meetings, remembered

Meeting transcripts become a **FalkorDB** knowledge graph (who said what, about
which GitHub issue, in which meeting). When a later meeting quietly reverses an
earlier decision, Drift catches the drift and posts a "what changed" comment on
the GitHub issue — through a governed **Guild.ai** cloud agent with a human
approval gate, falling back to a direct `gh` post when Guild isn't configured.

## Quickstart

Prereqs: local FalkorDB on `localhost:6379` (container `falkordb-test` — do not
start a second one; its browser UI is at `http://localhost:3000`) and an
authenticated `gh` CLI.

```bash
cd "/Users/shariquekhatri/Memory Meets Motion/drift"
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # optionally add GEMINI_API_KEY and Guild trigger keys

# PRIMARY DEMO — two live meetings, starting from an EMPTY world:
python demo_reset.py                                              # clean slate
python pipeline.py ingest transcripts/monday.jsonl  --fixtures --live
python pipeline.py ingest transcripts/tuesday.jsonl --fixtures --live
```

**Monday Standup** (Priya + Sam, 8 lines of dialogue, streamed like live
transcription): they decide *GIN index + hot-query cache, Sam owns it, Friday* —
and Drift **files the GitHub issue itself**, plan block and all.
**Tuesday Sync**: the prototype disproves the plan; Priya flips it to a
search-cache service, takes it over, moves the date. Drift detects the conflict
against Monday's memory, **edits the issue's plan block to the new reality**, and
a governed Guild session posts the `decision-changed` comment (after your
approval) explaining what changed, who said it, and why.

There is also a richer three-meeting scenario (m1-kickoff / m2-standup /
m3-sprint-planning, pre-seeded issues via `python seed.py`, two conflicts + a
correctly-silent control case) — same pipeline, kept as an alternate demo.

Drop `--fixtures` to run real Gemini extraction (needs `GEMINI_API_KEY`).
Inspect memory anytime:

```bash
python pipeline.py history 2     # one issue's timeline (number from issues.json)
python pipeline.py stats         # graph node/edge counts
```

## 90-second demo runbook

**Before going on stage:** run `seed.py`, ingest m1 and m2 (fixtures are fine),
and have three browser tabs open:

1. **GitHub** — the *cache* issue (`issues.json` maps `cache` to its number),
   scrolled to the original plan: Redis, owner Sam, target Aug 10.
2. **`http://localhost:3000`** — FalkorDB browser on graph `drift`; pre-type
   `MATCH (p:Person)-[:SAID]->(s:Statement)-[:ABOUT]->(i:Issue) RETURN p,s,i`
   to show the memory graph on demand.
3. **Guild.ai** — your workspace's Sessions view on app.guild.ai (the session's
   Events tab is the live audit trail). Demoing without Guild? Set
   `DIRECT_POST=1` and skip this tab.

**The 90 seconds:**

- **0:00** — GitHub tab: "On July 28 the team decided: Redis cache, Sam owns it,
  ship Aug 20th— sorry, Aug 10. It's all in the issue."
- **0:15** — Terminal: `python pipeline.py ingest transcripts/m3-sprint-planning.jsonl --fixtures --live`.
  Segments replay at two per second; narrate the sprint-planning meeting as it scrolls.
- **0:40** — `[historian] CONFLICT` lines land: Redis→LRU, Sam→Priya, then
  Auth0→Clerk. Point out the offline-mode decision that was judged **consistent**
  — Drift is selective, not trigger-happy.
- **0:55** — Guild tab: a session appeared; the Herald drafts the comment and
  waits at the human approval gate. Approve it. ("Governed agent, human in the
  loop, full audit trail.")
- **1:10** — GitHub tab: refresh the cache issue — the `> [!IMPORTANT]` comment
  with the Before/After table and `decision-changed` label. Expand the meeting
  excerpt to show the verbatim quote with timestamp.
- **1:20** — FalkorDB tab: run the graph query, show the `SUPERSEDES` edge from
  the new decision to the old one. `python pipeline.py history <n>` for the
  full timeline. Done.

## Fixtures mode = demo insurance

`transcripts/fixtures/*.extraction.json` is the ground-truth output a perfect
Scribe would produce, checked into the repo. `--fixtures` swaps the Gemini
extraction step for those files (resolving each statement's `issue_slug` through
`issues.json`), and if no `GEMINI_API_KEY` is set the Historian falls back to a
deterministic offline judge. Combined with `DIRECT_POST=1` (skip Guild, post
via `gh`), the entire m1→m2→m3 arc runs with **no Gemini key and no Guild
account** — conference wifi, API outages, and rate limits cannot kill the demo.
Real mode is one removed flag away, and the two paths produce the same story.

## Reset between runs

```bash
python reset.py
```

Wipes the FalkorDB graph and, if `issues.json` exists, re-seeds the Issue nodes
and full-text index so you can immediately re-ingest. GitHub is never touched —
the script prints the exact `gh api` commands to delete the demo comments and
remove the `decision-changed` labels by hand.

## Architecture

Drift is a four-stage pipeline conducted by `pipeline.py`: the **Scribe**
(`extractor.py`, Gemini schema-constrained JSON) turns raw transcript segments
into typed statements; the **Memory** (`graph.py`) files them into FalkorDB as a
property graph — `(Person)-[:SAID]->(Statement)-[:ABOUT]->(Issue)` plus
`IN_MEETING` and `SUPERSEDES` edges — where FalkorDB's full-text index links
spoken topics to GitHub issues and millisecond local Cypher queries make
"everything ever said about this issue, in date order" a single hop; the
**Historian** (`detector.py`) judges each new decision against that history, so
conflict detection is literally a graph diff over institutional memory; and the
**Herald** (`guild_trigger.py` + `guild-agent/agent.ts`) reports confirmed
drift through a Guild.ai cloud agent — governed session, BYOK LLM, human
approval gate, and an audited GitHub tool call — with a direct `gh` fallback.
FalkorDB is the memory; Guild.ai is the hands: the two pieces of sponsor tech
are not garnish, they are the load-bearing walls.
