# Verified SDK research (Aug 3, 2026) — build against THIS, not memory

## FalkorDB (ALREADY RUNNING locally)

- Container `falkordb-test` is UP: DB `localhost:6379` (no auth), browser UI `http://localhost:3000`. Do NOT `docker run` again (port conflict).
- Python: `pip install falkordb` (v1.6.2). NOT redisgraph-py.

```python
from falkordb import FalkorDB
db = FalkorDB(host="localhost", port=6379)
g = db.select_graph("drift")                 # auto-created on first write

INGEST = """
MERGE (p:Person {name: $person})
MERGE (m:Meeting {id: $meeting_id}) ON CREATE SET m.title = $meeting_title, m.date = $meeting_date
MERGE (i:Issue {number: $issue_number})
MERGE (s:Statement {segment_id: $segment_id}) ON CREATE SET s.text = $text, s.kind = $kind, s.ts = $ts
MERGE (p)-[:SAID]->(s)
MERGE (s)-[:ABOUT]->(i)
MERGE (s)-[:IN_MEETING]->(m)
"""
g.query(INGEST, {...params...})              # parameterized with $param (verified)

HISTORY = """
MATCH (p:Person)-[:SAID]->(s:Statement)-[:ABOUT]->(i:Issue {number: $issue_number}),
      (s)-[:IN_MEETING]->(m:Meeting)
RETURN s.segment_id, m.date, m.title, p.name, s.kind, s.text
ORDER BY m.date ASC
"""
rows = g.ro_query(HISTORY, {"issue_number": 42}).result_set   # ro_query for all reads

g.query("CREATE FULLTEXT INDEX FOR (i:Issue) ON (i.title)")   # backfills; raises if exists → try/except
hits = g.ro_query("CALL db.idx.fulltext.queryNodes('Issue', $t) YIELD node, score "
                  "RETURN node.number, node.title, score", {"t": "oauth*"}).result_set
```

Gotchas (all verified live):
- Full-text is whole-token: `'oauth'` → ZERO hits on "Migrate auth to OAuth2" (token is `oauth2`). ALWAYS append `*` to each term; strip RediSearch syntax chars (`-` = negation, `|` = OR, also `@(){}[]:;~^` ) from LLM-derived terms; multi-word topics: query each significant word with `*`, take best score.
- MERGE Statement on unique segment_id (bare CREATE duplicates on re-ingest).
- ORDER BY m.date works only because dates are ISO `YYYY-MM-DD` strings.
- Graph delete: `g.delete()` on the selected graph object.
- Module query timeout is 1000ms — fine at our scale.

## Gemini (`google-genai`, NOT google-generativeai)

- `pip install google-genai` (v2.16.0). Import: `from google import genai`. The old `google-generativeai` is DEAD (EOL 2025-11-30) — do not use it or its patterns.
- Model: `gemini-3.5-flash` (free tier). Free tier ≈ **10 requests/min** → SERIAL calls only, catch 429 (`genai.errors.ClientError`) and retry with exponential backoff (e.g. 8s, 16s, 32s). Batch ≤8 transcript segments per call.
- Auth: `genai.Client()` auto-reads `GEMINI_API_KEY` env var. (GOOGLE_API_KEY takes precedence if set — we don't set it.)

```python
from google import genai
from google.genai import types
resp = client.models.generate_content(
    model="gemini-3.5-flash",
    contents=prompt_text,
    config=types.GenerateContentConfig(
        response_mime_type="application/json",
        response_json_schema=MyPydanticModel.model_json_schema(),   # schema-constrained decoding
    ),
)
obj = MyPydanticModel.model_validate_json(resp.text)   # use model_validate_json, NOT resp.parsed
```

- Keep schemas flat (≤2 nesting levels); use str-Enum/Literal for constrained fields.
- Create the client lazily (inside the function) so modules import without a key.

## GitHub (`gh` CLI — already authenticated)

- gh v2.95.0 at /opt/homebrew/bin/gh, authed as **sharique2004**, scopes include `repo`. No env tokens; keyring auth — subprocess inherits it.
- Use `gh api` (returns JSON with `.number`/`.html_url`), NOT `gh issue create` (prints only URL).
- Bodies ALWAYS via stdin: `-F body=@-` with `subprocess.run(..., input=body_md)`. Never shell-interpolate markdown. `-f` = raw string field (title), `-F` = typed/@- capable (body).

```python
def gh(*args, stdin=None):
    r = subprocess.run(["gh", *args], capture_output=True, text=True, input=stdin)
    if r.returncode != 0: raise RuntimeError(f"gh failed: {r.stderr.strip()}")
    return r.stdout

n   = json.loads(gh("api", "repos/OWNER/REPO/issues", "-f", "title=...", "-F", "body=@-",
                    "-f", "labels[]=enhancement", stdin=body_md))["number"]
url = json.loads(gh("api", "repos/OWNER/REPO/issues/2/comments", "-F", "body=@-", stdin=md))["html_url"]
gh("api", "-X", "PATCH", "repos/OWNER/REPO/issues/2", "-F", "body=@-", stdin=new_body)
gh("label", "create", "decision-changed", "--repo", "OWNER/REPO", "--color", "D93F0B", "--force")  # idempotent
```

- Labels must exist before use in issue create (defaults exist: bug, enhancement, documentation, question).
- ~1s sleep between issue creates (secondary rate limit); sleep 2 after repo create.
- Comment markdown that renders (high demo value): `> [!IMPORTANT]` / `> [!WARNING]` alert banners; tables; `<details><summary>…</summary>` + **BLANK LINE after `</summary>`** or inner markdown won't render; `- [ ]` task lists; ```mermaid fences; `#N` auto-links.

## Guild.ai (the NEW control-plane product — TypeScript SDK only)

- CLI `@guildai/cli` is installed globally. NEVER `pip install guildai` (unrelated dead 2023 package).
- `@guildai/agents-sdk` resolves ONLY after `guild auth login` (private npm). Sandbox allows only @guildai/agents-sdk + zod + @guildai-services/* — no FalkorDB/redis/Node builtins inside agents.
- Python → Guild: REST trigger only. Trigger API keys are created in the web UI (Triggers → New → API), auth is HTTP Basic `api_key_id:api_key_secret`:

```python
r = requests.post(f"https://app.guild.ai/api/workspaces/{OWNER}/{WORKSPACE}/sessions",
                  auth=(KEY_ID, KEY_SECRET),
                  json={"session_type": "api_trigger", "agent_input": payload}, timeout=30)
session_id = r.json()["session_id"]
```

- Agent shape (agent.ts): `"use agent"` directive; `import { type Task, agent, pick, userInterfaceTools } from "@guildai/agents-sdk"`; `import { gitHubTools } from "@guildai-services/guildai~github"`; `import { z } from "zod"`; inputSchema MUST be `z.object({...})` at root (no unions); tools = `{ ...userInterfaceTools, ...pick(gitHubTools, ["github_issues_get", "github_issues_create_comment"]) }`; in `run(input, task)`: `await task.llm.generateText({prompt})` (NON-streaming — streaming 501s with Gemini), `await task.ui?.prompt({type:"text", text})` blocks for human reply, `await task.tools.github_issues_create_comment({owner, repo, issue_number, body})`; `export default agent({description, inputSchema, outputSchema, tools, run})`.
- Deploy: `guild agent init --name conflict-reporter --template LLM` (scaffold) → replace agent.ts → `guild workspace select` → `guild agent test` → `guild agent save --message "v1" --wait --publish` (forgetting --publish = agent unavailable). Web UI: workspace LLM settings → add Gemini BYOK key; Credentials → GitHub integration; session Events tab = live audit trail (our demo surface). `guild session get <id>` from CLI.
