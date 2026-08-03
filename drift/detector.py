"""Drift Historian — judges whether a new statement conflicts with an issue's history.

Contract: judge(history, new_statement) -> Verdict. history = Memory.issue_history()
rows EXCLUDING the new statement; new_statement = {"who","kind","text","meeting","date"}.
One schema-constrained gemini-3.5-flash call, retry-on-429 exponential backoff
(8/16/32s, max 3 retries then raise). Client is created lazily so this module imports
with no GEMINI_API_KEY present.
"""

from __future__ import annotations

import time

from google import genai
from google.genai import errors, types
from pydantic import BaseModel

MODEL = "gemini-3.5-flash"
RETRY_DELAYS = (8, 16, 32)  # seconds between retries on 429; then raise


class Verdict(BaseModel):
    conflict: bool
    before: str = ""  # prior decision, one sentence
    after: str = ""  # new state, one sentence
    what_changed: str = ""  # short, e.g. "approach Redis→LRU; owner Sam→Priya"


_client: genai.Client | None = None


def _get_client() -> genai.Client:
    """Create the Gemini client on first use (reads GEMINI_API_KEY from env)."""
    global _client
    if _client is None:
        _client = genai.Client()
    return _client


def _generate_json(prompt: str, schema: dict) -> str:
    """One schema-constrained generate_content call with 429 retry (8/16/32s)."""
    attempt = 0
    while True:
        try:
            resp = _get_client().models.generate_content(
                model=MODEL,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_json_schema=schema,
                ),
            )
            return resp.text
        except errors.ClientError as e:
            code = getattr(e, "code", None) or getattr(e, "status_code", None)
            if code != 429 or attempt >= len(RETRY_DELAYS):
                raise
            delay = RETRY_DELAYS[attempt]
            attempt += 1
            print(f"[historian] rate limited (429) — retry {attempt}/{len(RETRY_DELAYS)} in {delay}s")
            time.sleep(delay)


_PROMPT = """You are the Historian for an engineering team. Your job: decide whether a NEW meeting statement CHANGES a previously settled decision about a GitHub issue.

PRIOR STATEMENTS about this issue, in chronological order (oldest first):
{history}

NEW STATEMENT:
[{date} · {meeting}] {who} ({kind}): {text}

Respond with JSON matching the schema (conflict, before, after, what_changed).

conflict=true ONLY if the new statement REVERSES or REPLACES something that was previously decided — one or more of:
- a different approach or tool than the one previously chosen,
- a different owner than the person previously assigned,
- a moved deadline or target date,
- scope that is cut or materially changed.

These are NOT conflicts — return conflict=false and leave before/after/what_changed as empty strings:
- Consistent progress on the existing plan ("benchmarks look good", "on track", work proceeding as decided).
- The plan getting more specific or gaining detail while staying consistent with what was decided (e.g. a spec that was promised is now presented, sub-tasks are added, the team agrees to start the planned work).
- Restating, confirming, or agreeing with the same plan in different words.
- A question, or an answer that does not change the plan.
- The first decision on a topic where nothing was decided before.

If conflict=true:
- before: the prior decision, one sentence.
- after: the new state, one sentence.
- what_changed: a compact delta listing ONLY the dimensions that actually changed, e.g. "approach Redis→LRU; owner Sam→Priya; deadline Aug 10→Aug 14".
"""


def judge(history: list[dict], new_statement: dict) -> Verdict:
    """Judge new_statement against prior history rows; one Gemini call."""
    if not history:
        return Verdict(conflict=False)
    lines = "\n".join(
        f"{i}. [{row['date']} · {row['meeting']}] {row['who']} ({row['kind']}): {row['text']}"
        for i, row in enumerate(history, 1)
    )
    prompt = _PROMPT.format(
        history=lines,
        date=new_statement["date"],
        meeting=new_statement["meeting"],
        who=new_statement["who"],
        kind=new_statement["kind"],
        text=new_statement["text"],
    )
    print(f"[historian] judging new {new_statement['kind']} against {len(history)} prior statement(s)")
    verdict = Verdict.model_validate_json(_generate_json(prompt, Verdict.model_json_schema()))
    if verdict.conflict:
        print(f"[historian] CONFLICT: {verdict.what_changed}")
    else:
        print("[historian] no conflict")
    return verdict
