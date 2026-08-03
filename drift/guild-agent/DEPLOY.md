# Deploying Herald (guild-agent) to Guild.ai

Herald is Drift's governed reporter: an API-triggered Guild agent that drafts the
"decision changed" comment, blocks on a human yes/no, then posts to GitHub. Every
step (input, LLM draft, approval, post) lands in the session's Events tab — that
audit trail is the demo surface.

Prerequisites: `@guildai/cli` is installed globally (`guild` on PATH).
`@guildai/agents-sdk` resolves only after login (private npm), so step 1 is not
optional. The agent sandbox allows only `@guildai/agents-sdk`, `zod`, and
`@guildai-services/*` — do not add other imports to `agent.ts`.

## CLI steps

1. Authenticate the CLI (opens a browser window):

   ```
   guild auth login
   ```

2. Scaffold the agent from the LLM template, inside this folder:

   ```
   cd "/Users/shariquekhatri/Memory Meets Motion/drift/guild-agent"
   guild agent init --name conflict-reporter --template LLM
   ```

3. Replace the scaffold's generated `agent.ts` with this folder's finished
   `agent.ts` (overwrite whatever the template wrote; if `init` created a
   subfolder, copy it in there):

   ```
   cp agent.ts <path-to-scaffolded-agent.ts>
   ```

4. Select the workspace the agent will live in:

   ```
   guild workspace select
   ```

5. Test locally. When prompted for input, paste:

   ```
   guild agent test
   ```

   ```json
   {
     "repo": "sharique2004/mmm-demo",
     "issue_number": 2,
     "prior_decision": "Use a Redis cache for transcript ingestion; Sam owns it; target Aug 10.",
     "new_decision": "Switch to an in-memory LRU cache for v1; Priya takes it over; new target Aug 14.",
     "what_changed": "approach Redis→LRU; owner Sam→Priya; deadline Aug 10→Aug 14",
     "speaker": "Priya",
     "meeting": "Sprint Planning",
     "date": "2026-08-03",
     "quote": "Honestly, Redis is overkill for v1 — let's do an in-memory LRU, I'll take it, and we land it by the fourteenth.",
     "ts": "00:12:41"
   }
   ```

   Expected: a drafted markdown comment, then the approval prompt. Answer
   anything but "yes" so the test run returns `{"posted": false}` without
   touching GitHub.

6. Save AND publish — forgetting `--publish` leaves the agent unavailable to
   triggers:

   ```
   guild agent save --message "v1" --wait --publish
   ```

## Web UI steps (app.guild.ai)

7. **BYOK Gemini key** — Workspace → LLM settings → add the Gemini API key
   (same `GEMINI_API_KEY` value as `drift/.env`). `task.llm.generateText` runs
   on this key; Herald calls it non-streaming because streaming 501s with
   Gemini.

8. **GitHub credential** — Workspace → Credentials → GitHub integration →
   connect the `sharique2004` account and grant access to
   `sharique2004/mmm-demo`. Herald's `github_issues_create_comment` tool posts
   through this credential.

9. **API trigger key** — Triggers → New → API. Copy the key id and secret (the
   secret is shown once), then edit
   `/Users/shariquekhatri/Memory Meets Motion/drift/.env`:

   ```
   GUILD_KEY_ID=<key id>
   GUILD_KEY_SECRET=<key secret>
   GUILD_OWNER=<workspace owner slug>
   GUILD_WORKSPACE=<workspace slug>
   DIRECT_POST=0
   ```

   With `DIRECT_POST=0` and all four `GUILD_*` values set,
   `guild_trigger.report_conflict()` POSTs
   `https://app.guild.ai/api/workspaces/$GUILD_OWNER/$GUILD_WORKSPACE/sessions`
   with HTTP Basic auth and the conflict payload. Any config gap or request
   failure falls back to a direct `gh` post — the demo never stalls.

## Verify end-to-end

10. Run the live meeting through the pipeline and approve in the browser:

    ```
    cd "/Users/shariquekhatri/Memory Meets Motion/drift"
    python pipeline.py ingest transcripts/m3-sprint-planning.jsonl --fixtures
    ```

    A session appears in the workspace; open it, review the drafted comment,
    reply "yes". Check status from the CLI with:

    ```
    guild session get <session_id>
    ```

    The session's Events tab shows input → LLM draft → human approval → GitHub
    post, and the comment lands on the issue with the `[!IMPORTANT]` banner.
