"""graph.py — Drift's FalkorDB memory layer (agent B).

Knowledge graph of who said what, about which GitHub issue, in which meeting.
Schema:  (Person)-[:SAID]->(Statement)-[:ABOUT]->(Issue)
         (Statement)-[:IN_MEETING]->(Meeting)
         (Statement)-[:SUPERSEDES]->(Statement)
All writes are parameterized MERGEs (idempotent re-ingest); all reads use
ro_query. See CONTRACT.md for the exact API and RESEARCH.md for verified
FalkorDB patterns.
"""

import re

from falkordb import FalkorDB

# Words carrying no signal for issue-title matching. Anything shorter than
# _MIN_TERM_LEN is dropped too (RediSearch tokens like "a"/"of" never help).
_STOPWORDS = frozenset({
    "the", "a", "an", "and", "or", "but", "nor", "for", "of", "to", "in",
    "on", "at", "by", "with", "from", "about", "into", "over", "under",
    "is", "are", "was", "were", "be", "been", "being", "it", "its", "this",
    "that", "these", "those", "there", "here", "when", "where", "how", "why",
    "we", "our", "you", "your", "they", "their", "he", "she", "him", "her",
    "i", "me", "my", "us", "do", "does", "did", "done", "should", "would",
    "could", "will", "shall", "can", "may", "might", "must", "have", "has",
    "had", "not", "no", "yes", "if", "then", "than", "so", "too", "very",
    "just", "also", "all", "any", "some", "such", "other", "more", "most",
    "new", "get", "got", "use", "using", "need", "needs", "want", "wants",
    "issue", "ticket", "thing", "stuff", "team", "work", "working",
})
_MIN_TERM_LEN = 3

_INGEST = """
MERGE (p:Person {name: $person})
MERGE (m:Meeting {id: $meeting_id})
  ON CREATE SET m.title = $meeting_title, m.date = $meeting_date
MERGE (i:Issue {number: $issue_number})
MERGE (s:Statement {segment_id: $segment_id})
  ON CREATE SET s.text = $text, s.kind = $kind, s.ts = $ts
MERGE (p)-[:SAID]->(s)
MERGE (s)-[:ABOUT]->(i)
MERGE (s)-[:IN_MEETING]->(m)
"""

_HISTORY = """
MATCH (p:Person)-[:SAID]->(s:Statement)-[:ABOUT]->(i:Issue {number: $issue_number}),
      (s)-[:IN_MEETING]->(m:Meeting)
RETURN s.segment_id, m.date, m.title, p.name, s.kind, s.text
ORDER BY m.date ASC
"""

_UPSERT_ISSUE = """
MERGE (i:Issue {number: $number})
SET i.repo = $repo, i.title = $title, i.slug = $slug
"""

_SUPERSEDES = """
MATCH (new:Statement {segment_id: $new_id})
MATCH (old:Statement {segment_id: $old_id})
MERGE (new)-[:SUPERSEDES]->(old)
"""

_FULLTEXT = ("CALL db.idx.fulltext.queryNodes('Issue', $t) YIELD node, score "
             "RETURN node.number, score")


class Memory:
    def __init__(self, host: str = "localhost", port: int = 6379,
                 graph_name: str = "drift"):
        self._db = FalkorDB(host=host, port=port)
        self._graph_name = graph_name
        self._g = self._db.select_graph(graph_name)

    # -- reads --------------------------------------------------------------

    def _ro(self, query: str, params: dict | None = None) -> list:
        """ro_query returning []. when the graph key doesn't exist yet."""
        try:
            return self._g.ro_query(query, params or {}).result_set
        except Exception as e:  # fresh DB: no graph key until first write
            if "empty key" in str(e).lower():
                return []
            raise

    # -- contract API -------------------------------------------------------

    def ensure_indexes(self) -> None:
        """Full-text index on Issue.title (idempotent; backfills existing nodes)."""
        try:
            self._g.query("CREATE FULLTEXT INDEX FOR (i:Issue) ON (i.title)")
            print(f"[graph] full-text index on Issue.title created ({self._graph_name})")
        except Exception as e:
            if "already" in str(e).lower():
                print("[graph] full-text index on Issue.title already exists")
            else:
                raise

    def upsert_issue(self, number: int, repo: str, title: str, slug: str) -> None:
        self._g.query(_UPSERT_ISSUE,
                      {"number": number, "repo": repo, "title": title, "slug": slug})
        print(f"[graph] issue #{number} ({slug}) upserted")

    def find_issue(self, topic: str) -> int | None:
        """Full-text prefix search of `topic` against Issue.title.

        Splits the topic into words, drops stopwords/short words, appends '*'
        to each surviving term (RediSearch matches whole tokens: 'oauth' would
        miss 'OAuth2'), queries term-by-term, returns the best-scoring issue
        number or None. Tokenizing on [a-z0-9] runs also strips every
        RediSearch syntax char (- | @ ( ) { } [ ] : ; ~ ^ ...) from
        LLM-derived topics.
        """
        terms = [w for w in re.findall(r"[a-z0-9]+", topic.lower())
                 if len(w) >= _MIN_TERM_LEN and w not in _STOPWORDS]
        best_number: int | None = None
        best_score = float("-inf")
        for term in terms:
            try:
                rows = self._ro(_FULLTEXT, {"t": term + "*"})
            except Exception:  # no index yet / malformed term — treat as miss
                continue
            for number, score in rows:
                if number is not None and score > best_score:
                    best_score = score
                    best_number = int(number)
        return best_number

    def ingest_statement(self, *, segment_id: str, person: str, meeting_id: str,
                         meeting_title: str, meeting_date: str, issue_number: int,
                         text: str, kind: str, ts: str) -> None:
        """Idempotent: MERGE keyed on segment_id — re-ingesting a meeting is a no-op."""
        self._g.query(_INGEST, {
            "segment_id": segment_id, "person": person, "meeting_id": meeting_id,
            "meeting_title": meeting_title, "meeting_date": meeting_date,
            "issue_number": issue_number, "text": text, "kind": kind, "ts": ts,
        })

    def issue_history(self, number: int) -> list[dict]:
        rows = self._ro(_HISTORY, {"issue_number": number})
        return [
            {"segment_id": seg, "date": date, "meeting": meeting,
             "who": who, "kind": kind, "text": text}
            for seg, date, meeting, who, kind, text in rows
        ]

    def mark_superseded(self, new_segment_id: str, old_segment_id: str) -> None:
        self._g.query(_SUPERSEDES,
                      {"new_id": new_segment_id, "old_id": old_segment_id})
        print(f"[graph] {new_segment_id} SUPERSEDES {old_segment_id}")

    def stats(self) -> dict:
        counts: dict[str, int] = {}
        for label in ("Person", "Meeting", "Issue", "Statement"):
            rows = self._ro(f"MATCH (n:{label}) RETURN count(n)")
            counts[label.lower() + "s"] = int(rows[0][0]) if rows else 0
        nodes = self._ro("MATCH (n) RETURN count(n)")
        edges = self._ro("MATCH ()-[r]->() RETURN count(r)")
        counts["nodes"] = int(nodes[0][0]) if nodes else 0
        counts["edges"] = int(edges[0][0]) if edges else 0
        return counts

    def wipe(self) -> None:
        """Delete the whole graph. Safe to call when the graph doesn't exist."""
        try:
            self._g.delete()
            print(f"[graph] graph '{self._graph_name}' wiped")
        except Exception as e:
            if "empty key" in str(e).lower():
                print(f"[graph] graph '{self._graph_name}' already empty")
            else:
                raise
        # a deleted graph object is stale — reselect so the instance stays usable
        self._g = self._db.select_graph(self._graph_name)
