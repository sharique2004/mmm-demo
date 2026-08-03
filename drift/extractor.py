"""Drift Scribe — turns raw transcript segments into structured Statements via Gemini.

Contract: extract(segments, meeting_title) -> list[Statement]. Serial batches of <=8
segments per call, schema-constrained JSON, retry-on-429 exponential backoff (8/16/32s,
max 3 retries then raise). Client is created lazily so this module imports with no
GEMINI_API_KEY present.
"""

from __future__ import annotations

import json
import time
from typing import Literal

from google import genai
from google.genai import errors, types
from pydantic import BaseModel

MODEL = "gemini-3.5-flash"
BATCH_SIZE = 8
RETRY_DELAYS = (8, 16, 32)  # seconds between retries on 429; then raise


class Statement(BaseModel):
    segment_id: str
    speaker: str
    topic: str
    issue_number: int | None = None
    claim: str
    kind: Literal["decision", "update", "assignment", "question"]


class Extraction(BaseModel):
    """Wrapper for the Gemini response: {"statements": [...]}."""

    statements: list[Statement]


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
            print(f"[scribe] rate limited (429) — retry {attempt}/{len(RETRY_DELAYS)} in {delay}s")
            time.sleep(delay)


_PROMPT = """You are the Scribe for an engineering meeting titled "{title}".

INPUT: transcript segments below, one JSON object per line, each with segment_id, ts, speaker, text.

TASK: extract every distinct claim about a work item as a statement object. Respond with JSON of the shape {{"statements": [...]}}.

Rules:
1. segment_id: copy it EXACTLY, character for character, from the input segment the claim comes from. Never invent, merge, or alter ids.
2. speaker: the speaker of that segment.
3. Skip filler entirely: greetings, small talk, jokes, thanks and goodbyes, scheduling chatter, and meta-talk about the meeting itself produce NO statements.
4. One statement per distinct claim. If a single segment both picks an approach and assigns an owner, emit separate statements (both with that same segment_id).
5. topic: a short lowercase key (2-4 words) naming the underlying work item, e.g. "cache layer", "oauth migration", "offline mode", "rate limiting". Normalize aggressively: every mention of the same subject must reuse the IDENTICAL topic key, even when speakers phrase it differently across the meeting.{known_topics}
6. issue_number: set ONLY when an issue number is explicitly spoken in the segment text (e.g. "issue #2", "number 4"). Otherwise it must be null. Never infer a number.
7. claim: one self-contained sentence capturing the claim with its specifics (tool/approach names, owners, dates).
8. kind — pick exactly one:
   - "decision": a choice or plan is made or changed (approach, owner, deadline, scope).
   - "assignment": ownership of work is given to or moved onto a person.
   - "update": progress or status report on existing work.
   - "question": an open question raised about a work item.

SEGMENTS:
{segments}
"""


def extract(segments: list[dict], meeting_title: str) -> list[Statement]:
    """Extract Statements from transcript segment dicts, in serial batches of <=8."""
    schema = Extraction.model_json_schema()
    out: list[Statement] = []
    seen_topics: list[str] = []
    batches = [segments[i : i + BATCH_SIZE] for i in range(0, len(segments), BATCH_SIZE)]
    for bi, batch in enumerate(batches, 1):
        lines = "\n".join(
            json.dumps(
                {
                    "segment_id": s["segment_id"],
                    "ts": s.get("ts", ""),
                    "speaker": s.get("speaker", ""),
                    "text": s.get("text", ""),
                }
            )
            for s in batch
        )
        known = ""
        if seen_topics:
            known = (
                " Topic keys already used earlier in this meeting — reuse them verbatim "
                "when the subject matches: " + ", ".join(seen_topics) + "."
            )
        prompt = _PROMPT.format(title=meeting_title, known_topics=known, segments=lines)
        print(f"[scribe] extracting batch {bi}/{len(batches)} ({len(batch)} segments)")
        extraction = Extraction.model_validate_json(_generate_json(prompt, schema))
        valid_ids = {s["segment_id"] for s in batch}
        for st in extraction.statements:
            if st.segment_id not in valid_ids:
                print(f"[scribe] dropped statement with unknown segment_id {st.segment_id!r}")
                continue
            out.append(st)
            if st.topic not in seen_topics:
                seen_topics.append(st.topic)
    print(f"[scribe] extracted {len(out)} statements from {len(segments)} segments")
    return out
