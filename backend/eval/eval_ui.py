"""Local-only labeling + grading UI for the RAG evals (ask_admin.py pattern — never deployed).

Labeling: shows one pooled evidence candidate at a time per unit; keys 0/1/2 label-and-advance,
u undoes. Labels append to qrels.json (atomic write per keypress).
Grading (Task 9): grade generation-run answers into runs/<run>/grades.json.

Run: python backend/eval/eval_ui.py -> http://127.0.0.1:5052
"""
import os, sys, argparse

from eval_common import (load_json, save_json_atomic, unit_id,
                         QRELS_PATH, POOL_PATH, RUNS_DIR, EVAL_DIR)

RUBRIC = {0: "doesn't help answer this question",
          1: "tangentially useful (right entity, adjacent topic)",
          2: "directly supports an answer"}


def labeled_ids(qrels, uid):
    return {l["evidence_id"] for l in qrels.get(uid, {}).get("labels", [])}


def next_candidate(pool_unit, qrels, uid):
    done = labeled_ids(qrels, uid)
    for c in pool_unit["candidates"]:
        if c["evidence_id"] not in done:
            return c
    return None


def record_label(qrels, uid, entity, cand, rel):
    if rel not in (0, 1, 2):
        raise ValueError("rel must be 0, 1, or 2")
    unit = qrels.setdefault(uid, {"entity": entity, "labels": []})
    if cand["evidence_id"] in {l["evidence_id"] for l in unit["labels"]}:
        return  # idempotent: double-post of the same candidate is a no-op
    unit["labels"].append({
        "evidence_id": cand["evidence_id"], "source": cand["source"],
        "source_ref": cand["source_ref"], "professor_slug": cand.get("professor_slug"),
        "course_code": cand.get("course_code") or "", "body_sha": cand.get("body_sha"),
        "rel": rel})


def undo_label(qrels, uid):
    labels = qrels.get(uid, {}).get("labels", [])
    return labels.pop() if labels else None


def unit_progress(pool, qrels):
    out = []
    for uid, unit in pool.get("units", {}).items():
        out.append({"unit_id": uid, "question": unit["question"],
                    "total": len(unit["candidates"]),
                    "labeled": len(labeled_ids(qrels, uid))})
    return out


def selftest():
    fails = []
    def check(label, cond):
        if not cond: fails.append(label)
        print(("PASS" if cond else "FAIL") + ": " + label)

    cand = lambda i: {"evidence_id": i, "source": "trace", "source_ref": f"ref-{i}",
                      "professor_slug": "guha", "course_code": "", "body_sha": f"sha-{i}",
                      "body": f"body {i}"}
    pool_unit = {"question": "q?", "candidates": [cand("a"), cand("b")]}
    qrels = {}
    ent = {"kind": "professor", "slug": "guha"}

    check("first unlabeled is a", next_candidate(pool_unit, qrels, "u1")["evidence_id"] == "a")
    record_label(qrels, "u1", ent, cand("a"), 2)
    check("label recorded with natural key + rel",
          qrels["u1"]["labels"][0]["rel"] == 2 and qrels["u1"]["labels"][0]["source_ref"] == "ref-a")
    check("label never stores the body", "body" not in qrels["u1"]["labels"][0])
    check("advances to b", next_candidate(pool_unit, qrels, "u1")["evidence_id"] == "b")
    record_label(qrels, "u1", ent, cand("a"), 0)
    check("double-label same candidate is a no-op", len(qrels["u1"]["labels"]) == 1)
    try:
        record_label(qrels, "u1", ent, cand("b"), 5); bad = False
    except ValueError:
        bad = True
    check("rel outside 0-2 rejected", bad)
    popped = undo_label(qrels, "u1")
    check("undo pops the last label", popped["evidence_id"] == "a" and qrels["u1"]["labels"] == [])
    check("undo on empty returns None", undo_label(qrels, "u1") is None)
    record_label(qrels, "u1", ent, cand("a"), 1)
    prog = unit_progress({"units": {"u1": pool_unit}}, qrels)
    check("progress counts", prog[0]["labeled"] == 1 and prog[0]["total"] == 2)

    print("ALL PASS" if not fails else f"{len(fails)} FAIL(s): " + ", ".join(fails))
    return 1 if fails else 0


def create_app():
    from flask import Flask, jsonify, request
    app = Flask(__name__)

    @app.route("/")
    def index():
        with open(os.path.join(EVAL_DIR, "eval_ui.html"), encoding="utf-8") as f:
            return f.read(), 200

    @app.route("/api/state")
    def api_state():
        pool = load_json(POOL_PATH, {"units": {}})
        qrels = load_json(QRELS_PATH, {})
        return jsonify({"units": unit_progress(pool, qrels), "rubric": RUBRIC})

    @app.route("/api/unit/<path:uid>")
    def api_unit(uid):
        pool = load_json(POOL_PATH, {"units": {}})
        qrels = load_json(QRELS_PATH, {})
        unit = pool["units"].get(uid)
        if not unit:
            return jsonify({"error": "unknown unit"}), 404
        return jsonify({"unit_id": uid, "question": unit["question"], "entity": unit["entity"],
                        "candidate": next_candidate(unit, qrels, uid),
                        "labeled": len(labeled_ids(qrels, uid)),
                        "total": len(unit["candidates"])})

    @app.route("/api/label", methods=["POST"])
    def api_label():
        body = request.get_json(force=True)
        uid, eid, rel = body["unit_id"], body["evidence_id"], int(body["rel"])
        pool = load_json(POOL_PATH, {"units": {}})
        qrels = load_json(QRELS_PATH, {})
        unit = pool["units"].get(uid)
        cand = next((c for c in unit["candidates"] if c["evidence_id"] == eid), None) if unit else None
        if not cand:
            return jsonify({"error": "unknown candidate"}), 404
        record_label(qrels, uid, unit["entity"], cand, rel)
        save_json_atomic(QRELS_PATH, qrels)
        return api_unit(uid)

    @app.route("/api/undo", methods=["POST"])
    def api_undo():
        uid = request.get_json(force=True)["unit_id"]
        pool = load_json(POOL_PATH, {"units": {}})
        qrels = load_json(QRELS_PATH, {})
        popped = undo_label(qrels, uid)
        if popped:
            save_json_atomic(QRELS_PATH, qrels)
        unit = pool["units"].get(uid, {"candidates": [], "question": "", "entity": {}})
        # after an undo, the popped candidate is the next unlabeled one again
        return jsonify({"unit_id": uid, "question": unit.get("question"), "entity": unit.get("entity"),
                        "candidate": next_candidate(unit, qrels, uid) if unit.get("candidates") else None,
                        "labeled": len(labeled_ids(qrels, uid)),
                        "total": len(unit.get("candidates", []))})

    register_grading_routes(app)  # Task 9 (no-op stub until then)
    return app


def register_grading_routes(app):
    pass  # Task 9 fills this in


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--selftest", action="store_true")
    p.add_argument("--port", type=int, default=5052)
    args = p.parse_args()
    if args.selftest:
        sys.exit(selftest())
    create_app().run(host="127.0.0.1", port=args.port, debug=False)
