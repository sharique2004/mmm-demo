# Drift — Build Contract (single source of truth)

Every module is authored against THIS file. Follow signatures and data shapes EXACTLY — other agents are building the counterpart files concurrently and integration happens against this contract, not against your improvisation. Read RESEARCH.md (same folder) for verified SDK usage, versions, and gotchas before writing code.

## Product in two lines

Meeting transcripts are ingested into a FalkorDB knowledge graph (who said what, about which GitHub issue, in which meeting). When a new meeting changes/contradicts a prior decision about an issue, we post a "what changed" comment on that GitHub issue — via a Guild.ai cloud agent (governed session + human approval), with a direct `gh` fallback.

## Repo layout (absolute paths)

All code lives in `/Users/shariquekhatri/Memory Meets Motion/drift/`:

```
drift/
├── CONTRACT.md  RESEARCH.md          # spec (already written — do not edit)
├── requirements.txt  .env.example  README.md  reset.py     # agent F
├── transcripts/
│   ├── m1-kickoff.jsonl  m2-standup.jsonl  m3-sprint-planning.jsonl   # agent A
│   └── fixtures/m1.extraction.json  m2.extraction.json  m3.extraction.json  # agent A
├── graph.py                          # agent B
├── extractor.py  detector.py         # agent C
├── github_tools.py  seed.py          # agent D
├── guild_trigger.py                  # agent E
├── guild-agent/agent.ts              # agent E
└── pipeline.py                       # agent F
```

## Environment (.env, loaded with python-dotenv by every entrypoint)

```
GEMINI_API_KEY=            # may be ABSENT during build — code must import fine without it
DRIFT_REPO=sharique2004/mmm-demo
DIRECT_POST=0              # 1 = skip Guild, post comment directly via gh
GUILD_KEY_ID=  GUILD_KEY_SECRET=  GUILD_OWNER=  GUILD_WORKSPACE=
FALKOR_HOST=localhost  FALKOR_PORT=6379  GRAPH_NAME=drift
```

Entrypoints call `load_dotenv()` from the script's own directory (`Path(__file__).parent / ".env"`), so scripts work from any cwd.

## Transcript format (JSONL, one object per line)

Line 1 (meta): `{"type":"meta","meeting_id":"m1","title":"Project Kickoff","date":"2026-07-28"}`
Then segments: `{"type":"segment","segment_id":"m1-s01","ts":"00:01:12","speaker":"Priya","text":"..."}`

- `segment_id` = `<meeting_id>-s<NN>` zero-padded, unique, ordered.
- `date` is ISO `YYYY-MM-DD` (graph ordering depends on it).
- Speakers (fixed cast): **Priya** (eng lead), **Sam** (backend), **Marco** (auth/infra), **Chen** (mobile).

## The four demo issues (agent D seeds these; agents A/D must match this table exactly)

| slug | title | body must state this original plan |
|---|---|---|
| `oauth` | Migrate auth service to OAuth2 | Provider: **Auth0** · Owner: **Marco** · Target: **Aug 20** |
| `cache` | Add cache layer for transcript ingestion | Approach: **Redis cache** · Owner: **Sam** · Target: **Aug 10** |
| `offline` | Mobile offline mode for field agents | Status: scoping · **Chen** drafting spec |
| `ratelimit` | Rate-limit the public API | Status: proposed, no owner yet |

Bodies: realistic engineering issues — a Goal section, the plan fields above, 2-4 task-list checkboxes. seed.py writes the REAL issue numbers to `drift/issues.json` as `{"oauth": 1, "cache": 2, ...}` (numbers come from the gh api response — never hardcode).

## Story arc (agent A: transcripts + fixtures MUST encode exactly this)

**m1 Project Kickoff (2026-07-28), 12-16 segments.** Decisions matching the issue bodies: oauth → Auth0/Marco/Aug 20 (kind `decision`); cache → Redis/Sam/Aug 10 (`decision`); offline → Chen drafts spec (`update`). Plus natural filler segments (greetings, tangents) that produce NO statements.

**m2 Eng Standup (2026-07-31), 8-12 segments.** NO conflicts: cache → Sam benchmarking Redis, on track (`update`); oauth → Marco progress (`update`); ratelimit → Chen asks about per-key limits (`question`).

**m3 Sprint Planning (2026-08-03) — the LIVE demo meeting, 10-14 segments. Exactly TWO conflicts:**
1. **cache CONFLICT:** Priya: Redis is overkill for v1 → switch to **in-memory LRU**, reassign **Sam → Priya**, new target **Aug 14** (kind `decision`, plus the reassignment may be a second `assignment` statement).
2. **oauth CONFLICT:** Marco: Auth0 pricing too high → switch to **Clerk**, target slips to **Sept 1** (`decision`).
3. **offline control case (must NOT conflict):** Chen presents the spec, team agrees to start next sprint (`decision` — consistent with m1's "Chen drafting spec", so the Historian judge should return conflict=false; this shows selectivity).

Dialogue should read like a real meeting (interruptions, "wait, which issue is that? — #2 I think"), and explicit `#N`-style references may appear ONLY as spoken words; linking works by topic full-text too. Every fixture statement must trace to a real segment_id whose text supports the claim.

## Fixtures format (`transcripts/fixtures/mX.extraction.json`)

Ground-truth extraction = what a perfect Scribe would output. Also our no-network demo insurance.

```json
{"statements": [
  {"segment_id": "m1-s03", "speaker": "Priya", "topic": "cache layer",
   "issue_slug": "cache", "issue_number": null,
   "claim": "Use a Redis cache for ingestion; Sam owns it; target Aug 10",
   "kind": "decision"}
]}
```

`issue_slug` present in fixtures (resolved via issues.json); `issue_number` non-null only when a segment says an explicit issue number out loud.

## Module APIs (exact signatures)

### graph.py — agent B
```python
class Memory:
    def __init__(self, host: str = "localhost", port: int = 6379, graph_name: str = "drift"): ...
    def ensure_indexes(self) -> None            # full-text index on Issue.title (idempotent; swallow "already exists")
    def upsert_issue(self, number: int, repo: str, title: str, slug: str) -> None
    def find_issue(self, topic: str) -> int | None   # full-text prefix search on Issue.title; sanitize RediSearch chars; append '*' to terms; best-score hit or None
    def ingest_statement(self, *, segment_id: str, person: str, meeting_id: str,
                         meeting_title: str, meeting_date: str, issue_number: int,
                         text: str, kind: str, ts: str) -> None    # idempotent MERGE keyed on segment_id (pattern in RESEARCH.md)
    def issue_history(self, number: int) -> list[dict]
        # ordered by meeting_date ASC; each dict: {"segment_id","date","meeting","who","kind","text"}
    def mark_superseded(self, new_segment_id: str, old_segment_id: str) -> None   # (new:Statement)-[:SUPERSEDES]->(old:Statement)
    def stats(self) -> dict                     # node/edge counts for logging
    def wipe(self) -> None                      # delete the whole graph (used by reset.py)
```

### extractor.py — agent C
```python
class Statement(pydantic.BaseModel):
    segment_id: str; speaker: str; topic: str
    issue_number: int | None = None
    claim: str
    kind: Literal["decision", "update", "assignment", "question"]

def extract(segments: list[dict], meeting_title: str) -> list[Statement]
    # segments = transcript segment dicts. Batches ≤8 segments per Gemini call (model gemini-3.5-flash,
    # schema-constrained JSON per RESEARCH.md), SERIAL calls with retry-on-429 exponential backoff.
    # Prompt: extract decisions/updates/assignments/questions; copy segment_id verbatim; skip filler.
```
`issue_slug` is NOT in the Gemini schema (fixtures-only field). Module must import fine with no GEMINI_API_KEY (create the client lazily inside extract()).

### detector.py — agent C
```python
class Verdict(pydantic.BaseModel):
    conflict: bool
    before: str = ""     # prior decision, one sentence
    after: str = ""      # new state, one sentence
    what_changed: str = ""  # short, e.g. "approach Redis→LRU; owner Sam→Priya; deadline Aug 10→Aug 14"

def judge(history: list[dict], new_statement: dict) -> Verdict
    # history = Memory.issue_history() rows EXCLUDING the new statement; new_statement =
    # {"who","kind","text","meeting","date"}. One schema-constrained gemini-3.5-flash call.
    # conflict=True ONLY if the new statement CHANGES a prior decision (approach/owner/deadline/scope);
    # consistent progress or a plan getting more specific is NOT a conflict.
```

### github_tools.py — agent D
```python
REPO = os.environ.get("DRIFT_REPO", "sharique2004/mmm-demo")
def gh(*args: str, stdin: str | None = None) -> str          # subprocess wrapper, raises RuntimeError with stderr on failure
def ensure_labels() -> None                                   # 'decision-changed' (color D93F0B), 'from-meeting' (0E8A16); gh label create --force
def create_issue(title: str, body_md: str, labels: tuple = ()) -> int   # via gh api (returns .number); body via stdin -F body=@-
def post_comment(issue_no: int, body_md: str) -> str          # returns html_url; body via stdin
def add_label(issue_no: int, label: str) -> None
def list_issue_titles() -> dict[str, int]                     # {title: number} of open issues (for idempotent seeding)
```

### seed.py — agent D (`python seed.py`)
1. `ensure_labels()`; 2. for each contract issue, create only if title not already present (idempotent), ~1s sleep between creates; 3. write `drift/issues.json` `{slug: number}`; 4. `Memory().ensure_indexes()` + `upsert_issue(...)` for all four; 5. print a summary table. **Never run at import time — everything under `if __name__ == "__main__":`.**

### guild_trigger.py — agent E
```python
def build_comment(payload: dict) -> str
    # GFM markdown: "> [!IMPORTANT]" banner "Decision changed in a later meeting…";
    # Before/After table (rows: Decision = payload before/after); <details><summary>Meeting excerpt</summary>
    # (BLANK LINE after </summary>!) with "[ts] **Speaker:** quote"; footer:
    # "_Meeting: {meeting}, {date} · Detected by Drift Historian (FalkorDB graph diff) · via Guild.ai_"

def report_conflict(payload: dict) -> str | None
    # payload = {"repo","issue_number","prior_decision","new_decision","what_changed",
    #            "speaker","meeting","date","quote","ts"}
    # If DIRECT_POST=1 or GUILD_KEY_ID unset → return None (caller falls back to direct post).
    # Else POST https://app.guild.ai/api/workspaces/{GUILD_OWNER}/{GUILD_WORKSPACE}/sessions,
    # HTTP Basic (GUILD_KEY_ID, GUILD_KEY_SECRET), json {"session_type":"api_trigger","agent_input":payload},
    # timeout 30, return session_id. On any request failure: print warning, return None (fallback, never crash).
```

### guild-agent/agent.ts — agent E
Herald, per RESEARCH.md Guild snippet: zod inputSchema mirroring the payload above (z.object at root, all fields z.string() except issue_number z.number()); `task.llm.generateText` drafts the comment following the same template as build_comment; `task.ui.prompt` yes/no approval gate; on yes, `github_issues_create_comment` from Guild's GitHub tools. Include a short `guild-agent/DEPLOY.md`: the exact `guild agent init/test/save --publish` steps and web-UI steps (BYOK Gemini key, GitHub credential, API trigger key) from RESEARCH.md.

### pipeline.py — agent F
```
python pipeline.py ingest transcripts/m1-kickoff.jsonl [--fixtures] [--live]
python pipeline.py history <issue_number>
python pipeline.py stats
```
Ingest flow per meeting: read meta+segments → statements from fixtures (`transcripts/fixtures/<meeting_id>.extraction.json`, resolving issue_slug via issues.json) or `extractor.extract()` → for each statement: resolve issue number (explicit issue_number, else `Memory.find_issue(topic)`; unresolvable → log & skip) → **capture `prior = memory.issue_history(n)` BEFORE ingesting** → `ingest_statement(...)` (text = the statement claim; ts/quote from the source segment) → if kind in {decision, assignment} and prior has ≥1 decision/assignment row: `verdict = detector.judge(prior, new)` → if conflict: build payload (quote = source segment's verbatim text, ts included), `session = report_conflict(payload)`; if None → `post_comment(n, build_comment(payload))` + `add_label(n, "decision-changed")`; either way `mark_superseded(new_segment_id, latest prior decision's segment_id)`. `--live`: print each segment with 0.5s delay (stage effect). End with a summary (statements ingested, conflicts found, comments posted/sessions started) and `memory.stats()`.
**With --fixtures the whole ingest path must run with NO Gemini key and (DIRECT_POST=1) no Guild account.**

### reset.py — agent F
`python reset.py` → `Memory().wipe()`, then re-run seed's graph side only if `issues.json` exists (re-upsert Issue nodes + indexes — import seed's helpers or duplicate the 5 lines). Prints what to clean manually on GitHub (delete demo comments / remove labels), with the exact `gh api` commands.

## Hard rules for every agent

- Write ONLY your assigned files. Others' files may not exist yet — code against this contract, import by module name (same folder, plain scripts — no package/`src/` nesting).
- Plain runnable Python 3.12+ (host has 3.14). Type hints yes; no TODOs, no placeholders, no `pass` stubs.
- Deps allowed: falkordb, google-genai, pydantic (v2), requests, python-dotenv, stdlib only.
- **Side effects:** NOTHING may write to GitHub, Guild, or the `drift` graph during authoring. Agent B may smoke-test graph.py against local FalkorDB using graph name `drift_smoke` and MUST delete it after. Agent D may run read-only `gh` commands only. Agents C/E: no live API calls (no keys exist yet).
- Console output: short, plain `print()` lines prefixed like `[scribe]`, `[historian]`, `[herald]`, `[graph]`, `[seed]` — the terminal is part of the stage demo.
