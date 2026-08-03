# Memory Meets Motion — Battle Plan

**Deadline:** 3:30 PM submission (started planning ~11:30 AM → ~4 hours of build time)

## What the hackathon actually demands

Not a theme suggestion — a **mandated 4-part stack**, and judges will explicitly check each one is *load-bearing* (a dead import doesn't count):

| Layer | Sponsor | Role in our build |
|---|---|---|
| Real-time input | **LaserData** (Apache Iggy) | Live event stream feeding the system |
| Memory | **FalkorDB** (graph DB, Cypher) | Persistent knowledge graph that compounds |
| Motion / orchestration | **RocketRide.ai** | Reads memory, decides, executes actions |
| Multi-agent | **Guild.ai** | Specialist agents + human-in-the-loop gates |

Canonical data flow: **LaserData → FalkorDB → RocketRide → Guild.ai → user**.

Extra prize: **Best Use of RocketRide = $1000** (AirPods Pro 3) + $250 social track. RocketRide is worth making the star, not an afterthought.

## The pick: "Recall" — a meeting-memory agent (built on MeetingScribe)

Reuse **MeetingScribe** (working native system-audio capture + Apple SpeechAnalyzer ASR, already on this Mac). It gives us the one thing nobody else in the room will have: a **live, real-world data source on stage** — you literally talk, and the demo reacts.

**One-liner:** An AI chief-of-staff that listens to your meetings, builds a permanent knowledge graph of people / decisions / action items across every meeting, and then *acts* — drafting follow-ups, scheduling, flagging contradictions with what was said last week.

This is "Memory Meets Motion" in its most literal form: speech is motion-in, the graph is memory, agent actions are motion-out.

### How each sponsor is load-bearing

1. **LaserData** — transcript segments are published to an Iggy stream (`meeting.transcript`) as they're spoken. Consumers read from the stream, not from the app. Also a second stream `agent.actions` for executed actions. (Local Laser Stack via Docker, or free cloud tier.)
2. **FalkorDB** — consumer agent extracts entities/relations from each segment and MERGEs into a graph: `(Person)-[:SAID]->(Statement)`, `(Person)-[:OWNS]->(ActionItem)`, `(Meeting)-[:DECIDED]->(Decision)`, `(Statement)-[:CONTRADICTS]->(Statement)`. Multi-hop Cypher queries power "what did we decide about X, and who pushed back?" — a genuinely graph-shaped query vector search can't do.
3. **RocketRide.ai** — the pipeline runtime: `ingest → extract → remember → decide → act`. When the graph crosses a trigger (action item assigned, contradiction detected, meeting ended), RocketRide executes the action chain (draft email, create calendar entry, write summary). Target the $1000 prize: deploy the pipeline on RocketRide Cloud.
4. **Guild.ai** — three specialist agents coordinated with handoffs: **Scribe** (extracts facts) → **Historian** (checks new facts against graph memory, flags contradictions) → **Operator** (proposes actions). Human-in-the-loop gate: Operator's actions require one-click approval in the dashboard before firing — exactly the HITL pattern the doc calls out.

### Demo script (90 seconds)

1. Open dashboard: graph already seeded with 2 "past meetings" (pre-loaded).
2. Start talking: "OK, decision — we're going with FalkorDB for memory. Sharique owns the demo, due 3 PM."
3. Watch: transcript streams in live → nodes light up in the graph → Historian flags "conflicts with Friday's decision to use Postgres" → Operator proposes a follow-up email → click Approve → action executes.
4. Kill the app, restart it, ask "what does Sharique owe and when?" — memory survived. Memory + motion, proven.

## What we reuse vs. build

**Reuse (already exists):**
- MeetingScribe: audio capture + live ASR (the hard native-Mac part — done)
- ATS Scanner / Screener patterns: multi-agent-via-claude-CLI prompt patterns for the extract/judge agents
- OrbitronAI polish habits for the dashboard

**Build (the 4 hours):**
- `bridge.py` — tails MeetingScribe live transcript → publishes to LaserData stream
- `graph_agent.py` — stream consumer → LLM extraction → FalkorDB MERGE
- RocketRide pipeline definition + deploy
- Guild.ai agent trio + approval gate
- `dashboard` — one-page web UI: live transcript, live graph viz (force-directed), pending actions with Approve buttons

## Timeboxed build order (with fallbacks)

| Time | Task | Fallback if blocked |
|---|---|---|
| 11:45–12:15 | Accounts/keys: RocketRide Cloud (Discord promo), Guild.ai, LaserData free tier; `docker run falkordb` locally | All four checklist items are in the doc; sponsor tables on-site for help |
| 12:15–1:00 | FalkorDB schema + extraction agent working end-to-end on a canned transcript | — (this must work; it's the memory core) |
| 1:00–1:45 | LaserData stream in the middle; live bridge from MeetingScribe | Fallback: replay a recorded transcript file through the stream — deterministic demo, still "live streaming" |
| 1:45–2:30 | RocketRide pipeline + Guild agent trio wired | Fallback: run agents locally via claude CLI, keep RocketRide as the orchestration entry point with at least one real deployed pipeline |
| 2:30–3:10 | Dashboard (graph viz + approve gate) + seed data for demo | Fallback: terminal + FalkorDB browser UI |
| 3:10–3:30 | Submit via Discord `/submit`, record demo GIF, LinkedIn post (RocketRide social track, $250) | — |

## Risks

- **RocketRide & Guild are unknown SDKs** — mitigate by hitting their docs/Discord first, and by keeping their integration surface thin (one pipeline, one crew of 3 agents). The doc says sponsor mentors are the source of truth — use their tables.
- **Live mic demo risk** — always have the replay-file fallback ready as a flag.
- **Time** — the demo script is the spec. Anything not visible in the 90-second demo gets cut.
