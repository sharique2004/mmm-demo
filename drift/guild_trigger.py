"""Drift Herald trigger.

Renders the "decision changed" GitHub comment and, when Guild.ai is configured,
starts a governed agent session (human-approved posting) instead of letting the
caller post directly.

Exports (per CONTRACT.md):
    build_comment(payload)  -> str        GFM comment shown on the GitHub issue
    report_conflict(payload) -> str|None  Guild session_id, or None -> caller
                                          falls back to a direct `gh` post
"""

from __future__ import annotations

import os
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

GUILD_SESSIONS_URL = "https://app.guild.ai/api/workspaces/{owner}/{workspace}/sessions"


def _line(value: object) -> str:
    """Collapse whitespace/newlines into one tidy line."""
    return " ".join(str(value).split())


def _cell(value: object) -> str:
    """One-line, pipe-safe text for a GFM table cell."""
    return _line(value).replace("|", "\\|")


def build_comment(payload: dict) -> str:
    """Render the conflict payload as a GitHub-flavored-markdown comment.

    Layout: [!IMPORTANT] banner, Before/After decision table, what-changed diff,
    collapsible meeting excerpt (blank line after </summary> so GitHub renders
    the inner markdown), provenance footer.
    """
    before = _cell(payload.get("prior_decision", ""))
    after = _cell(payload.get("new_decision", ""))
    what = _line(payload.get("what_changed", ""))
    speaker = _line(payload.get("speaker", ""))
    meeting = _line(payload.get("meeting", ""))
    date = _line(payload.get("date", ""))
    quote = _line(payload.get("quote", ""))
    ts = _line(payload.get("ts", ""))

    lines: list[str] = [
        "> [!IMPORTANT]",
        "> **Decision changed in a later meeting.**",
        f"> The plan recorded on this issue was revised during **{meeting}** ({date}).",
        "",
        "|            | Decision |",
        "| ---------- | -------- |",
        f"| **Before** | {before} |",
        f"| **After**  | {after} |",
        "",
    ]
    if what:
        lines += [f"**What changed:** `{what}`", ""]
    lines += [
        "<details>",
        "<summary>Meeting excerpt</summary>",
        "",
        f"> [{ts}] **{speaker}:** {quote}",
        "",
        "</details>",
        "",
        "---",
        "",
        f"_Meeting: {meeting}, {date} · Detected by Drift Historian (FalkorDB graph diff) · via Guild.ai_",
    ]
    return "\n".join(lines) + "\n"


def report_conflict(payload: dict) -> str | None:
    """Start a governed Guild.ai session for this conflict.

    payload = {"repo","issue_number","prior_decision","new_decision","what_changed",
               "speaker","meeting","date","quote","ts"}

    Returns the Guild session_id, or None whenever the caller should fall back to
    a direct post: DIRECT_POST=1, any missing Guild config, or any request
    failure. Never raises.
    """
    try:
        if os.environ.get("DIRECT_POST", "0").strip() == "1":
            print("[herald] DIRECT_POST=1 — skipping Guild, falling back to direct post")
            return None
        env = {
            name: os.environ.get(name, "").strip()
            for name in ("GUILD_KEY_ID", "GUILD_KEY_SECRET", "GUILD_OWNER", "GUILD_WORKSPACE")
        }
        missing = [name for name, value in env.items() if not value]
        if missing:
            print(f"[herald] {'/'.join(missing)} unset — falling back to direct post")
            return None
        url = GUILD_SESSIONS_URL.format(owner=env["GUILD_OWNER"], workspace=env["GUILD_WORKSPACE"])
        response = requests.post(
            url,
            auth=(env["GUILD_KEY_ID"], env["GUILD_KEY_SECRET"]),
            json={"session_type": "api_trigger", "agent_input": payload},
            timeout=30,
        )
        response.raise_for_status()
        body = response.json()
        session_id = str(body.get("id") or body["session_id"])
        session_url = body.get("session_url", "")
    except Exception as exc:  # contract: warn and fall back, never crash
        reason = " ".join(str(exc).split())[:160] or exc.__class__.__name__
        print(f"[herald] Guild trigger failed ({reason}) — falling back to direct post")
        return None
    print(f"[herald] Guild session started: {session_id} (approve in the Guild session UI)")
    if session_url:
        print(f"[herald] session: {session_url}")
    return session_id
