# Drift — your issues go stale the moment the meeting ends

**Memory Meets Motion hackathon · Frontier Tower SF · Aug 3, 2026**

🌐 **Live site:** [drift-mmm.vercel.app](https://drift-mmm.vercel.app) · 🎯 **The demo lives in this repo's [Issues tab](https://github.com/sharique2004/mmm-demo/issues)** — look for the `decision-changed` comments.

Decisions change in meetings; GitHub issues keep saying what they said three weeks ago. Drift listens to meeting transcripts, remembers every decision as a **FalkorDB knowledge graph**, detects when a new meeting **contradicts a prior decision** (approach, owner, deadline), and posts a *what-changed* comment on the affected issue — through a **governed Guild.ai agent session with a human approval gate**. The developer who picks up the issue sees how the plan actually evolved.

## Memory meets Motion

| Half | What it means here |
|---|---|
| **Memory** | `(Person)-[:SAID]->(Statement)-[:ABOUT]->(Issue)` compounding across meetings in FalkorDB; multi-hop Cypher pulls an issue's full decision history; `SUPERSEDES` edges record every overridden decision |
| **Motion** | A typed TypeScript agent ("Herald") deployed on Guild.ai's control plane: API-triggered sessions, Gemini-drafted comments, human-in-the-loop approval, comment posted by Guild's own GitHub credential — every step in the session audit log |

## Architecture

```
transcripts (JSONL) ──► Scribe (Gemini, schema-locked JSON) ──► FalkorDB graph
                                                                    │
                                              Historian (Cypher history + Gemini judge)
                                                                    │ conflict
                                              Guild.ai trigger API (governed session)
                                                                    │
                                     Herald: draft ► HUMAN APPROVAL ► GitHub comment
```

- `drift/` — the pipeline: `graph.py` (memory), `extractor.py` (Scribe), `detector.py` (Historian), `guild_trigger.py` + `guild-agent/` (Herald), `pipeline.py` (conductor), `seed.py`, real transcripts + ground-truth fixtures. See [`drift/README.md`](drift/README.md) for setup & the demo runbook, [`drift/CONTRACT.md`](drift/CONTRACT.md) for the build spec.
- `drift-site/` — the public site (real graph export, real pipeline output, the real comment).
- [`PRD.md`](PRD.md) / [`PLAN.md`](PLAN.md) — the plan this was built from, unedited.

## Honest engineering notes

- The three meetings are **scripted transcripts** (MeetingScribe-style output) with hand-written ground-truth fixtures — deterministic on stage, and the **live Gemini extraction path is real and verified** (run without `--fixtures`).
- Sprint planning contains exactly two changed decisions and one consistent one; Drift flags **2/2 conflicts and stays silent on the control case**.
- Every sponsor integration is load-bearing: kill FalkorDB and there is no memory; kill Guild and nothing reaches GitHub.

Built in one afternoon by **Sharique Khatri** with FalkorDB · Guild.ai · Gemini · GitHub.
