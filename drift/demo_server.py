"""Drift stage-demo server — drives the REAL pipeline from a browser UI.

GET  /                     the dashboard (demo_ui.html)
GET  /api/transcript/<id>  meta + segments of a transcript (for client-side live typing)
GET  /api/run/<id>         SSE: spawns `pipeline.py ingest ... --fixtures` and streams stdout lines
GET  /api/graph            current FalkorDB graph as {nodes, edges}
GET  /api/issue/<n>        live GitHub issue state (comments, labels, latest comment author)
POST /api/approve/<sid>    approves a waiting Guild session (`guild session send <sid> yes`)
POST /api/reset            runs demo_reset.py (delete demo issue, wipe graph)

Run:  ./.venv/bin/python demo_server.py   →  http://localhost:5057
"""

import json
import subprocess
import sys
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, Response, jsonify, request

ROOT = Path(__file__).parent
load_dotenv(ROOT / ".env")

import github_tools  # noqa: E402
from pipeline import make_memory, read_transcript  # noqa: E402

app = Flask(__name__)
PY = sys.executable


@app.get("/")
def index() -> Response:
    return Response((ROOT / "demo_ui.html").read_text(encoding="utf-8"), mimetype="text/html")


@app.get("/api/transcript/<meeting>")
def transcript(meeting: str):
    meta, segments = read_transcript(ROOT / "transcripts" / f"{meeting}.jsonl")
    return jsonify({"meta": meta, "segments": segments})


@app.get("/api/run/<meeting>")
def run(meeting: str) -> Response:
    path = ROOT / "transcripts" / f"{meeting}.jsonl"
    if not path.exists():
        return Response("unknown meeting", status=404)

    def stream():
        proc = subprocess.Popen(
            [PY, "-u", "pipeline.py", "ingest", str(path), "--fixtures"],
            cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1,
        )
        assert proc.stdout is not None
        for line in proc.stdout:
            yield f"data: {json.dumps(line.rstrip())}\n\n"
        proc.wait()
        yield f"data: {json.dumps('__DONE__ ' + str(proc.returncode))}\n\n"

    return Response(stream(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/graph")
def graph_json():
    memory = make_memory()
    g = memory._g
    nodes, edges = [], []
    try:
        for row in g.ro_query("MATCH (n) RETURN id(n), labels(n)[0], properties(n)").result_set:
            nodes.append({"id": row[0], "label": row[1], "props": row[2]})
        for row in g.ro_query("MATCH (a)-[r]->(b) RETURN id(a), type(r), id(b)").result_set:
            edges.append({"s": row[0], "t": row[2], "type": row[1]})
    except Exception:
        pass  # empty graph
    return jsonify({"nodes": nodes, "edges": edges, "stats": memory.stats()})


@app.get("/api/issue/<int:number>")
def issue(number: int):
    try:
        raw = json.loads(github_tools.gh("api", f"repos/{github_tools.REPO}/issues/{number}"))
        comments = json.loads(github_tools.gh("api", f"repos/{github_tools.REPO}/issues/{number}/comments"))
        return jsonify({
            "number": number,
            "html_url": raw["html_url"],
            "comments": raw["comments"],
            "labels": [lb["name"] for lb in raw["labels"]],
            "last_comment_by": comments[-1]["user"]["login"] if comments else None,
            "last_comment_url": comments[-1]["html_url"] if comments else None,
        })
    except Exception as exc:
        return jsonify({"error": str(exc)}), 502


@app.post("/api/approve/<sid>")
def approve(sid: str):
    r = subprocess.run(["guild", "session", "send", sid, "--message", "yes"],
                       capture_output=True, text=True, timeout=60)
    ok = r.returncode == 0
    return jsonify({"ok": ok, "detail": (r.stderr or r.stdout)[-300:]}), (200 if ok else 502)


@app.post("/api/reset")
def reset():
    r = subprocess.run([PY, "demo_reset.py"], cwd=ROOT, capture_output=True, text=True, timeout=120)
    return jsonify({"ok": r.returncode == 0, "log": r.stdout[-1500:]})


if __name__ == "__main__":
    print("Drift stage demo → http://localhost:5057")
    app.run(host="127.0.0.1", port=5057, threaded=True, debug=False)
