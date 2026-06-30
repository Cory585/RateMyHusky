"""
Thread reconstruction from Reddit posts and comments.

Loads resolved mentions, reconstructs complete Reddit threads (OP post +
comments in chronological order) as readable strings, and indexes posts/comments
for efficient lookup.

Usage
-----
    python build_threads.py --selftest             # offline checks, then exit
"""

import argparse
import csv
import os
import re
import sys

csv.field_size_limit(10 * 1024 * 1024)

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "reddit_data")


def load_resolved_mentions(path):
    out = []
    with open(path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row["status"] == "resolved":
                out.append(row)
    return out


def resolved_thread_ids(mentions):
    return {m["thread_id"] for m in mentions}


def load_posts_index(path):
    idx = {}
    with open(path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            idx[row["id"]] = row
    return idx


def _strip_link(link_id):
    return link_id[3:] if link_id.startswith("t3_") else link_id


def load_comments_by_link(path, needed_threads):
    by = {}
    with open(path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            tid = _strip_link(row["link_id"])
            if tid in needed_threads:
                by.setdefault(tid, []).append(row)
    for tid in by:
        by[tid].sort(key=lambda r: int(r["created_utc"] or 0))
    return by


def reconstruct_thread(thread_id, posts_idx, comments_by_link):
    parts = []
    post = posts_idx.get(thread_id)
    if post:
        parts.append("OP TITLE: " + (post.get("title") or ""))
        if post.get("selftext"):
            parts.append("OP BODY: " + post["selftext"])
    for c in comments_by_link.get(thread_id, []):
        parts.append("- " + (c.get("body") or ""))
    return "\n".join(parts).strip()


_COURSE_RE = re.compile(r"\b[A-Z]{2,4}\s?\d{3,4}\b")
_TEACH_WORDS = ("professor", "prof ", "class", "course", "lecture", "exam",
                "quiz", "homework", "take ", "taking", "taught", "teaches",
                "ta ", "office hours", "syllabus", "grade")


def load_catalog_surnames(backup_path=None):
    import match_professors as mp
    path = backup_path or os.path.join(
        HERE, "..", "backend", "backups", "ratemyhusky_new_20260602T001500Z.sql.gz")
    out = set()
    for p in mp.load_catalog(path):
        toks = (p.name_key or "").split()
        if toks:
            out.add(toks[-1].strip(",").lower())
    return out


def _is_topical(thread_text):
    if _COURSE_RE.search(thread_text):
        return True
    low = thread_text.lower()
    return any(w in low for w in _TEACH_WORDS)


_WORD_RE = re.compile(r"[a-z]+")


def compute_hints(mention, mention_text, thread_text, surnames):
    hints = []
    own = (mention.get("matched_token") or "").strip().lower()
    own_last = own.split()[-1] if own else ""
    text_low = (mention_text or "").lower()
    # Intersect the text's words with the surname set (O(words)) rather than
    # regex-searching every catalog surname against the text (O(surnames)).
    words = set(_WORD_RE.findall(text_low))
    others = sorted(w for w in words
                    if w in surnames and w != own_last and len(w) >= 4)
    if others:
        hints.append("another_prof_in_text: different surname(s) present: " + ", ".join(others[:3]))
    if not (mention.get("matched_token") or "").strip():
        hints.append("conv_context_no_token: prof never named in this text (inferred from thread)")
    if not _is_topical(thread_text):
        hints.append("non_topical_thread: no course code or teaching keywords in thread")
    return hints


def pack_batches(thread_entries, budget=50000):
    ordered = sorted(thread_entries, key=lambda e: e["char_len"], reverse=True)
    batches = []
    for e in ordered:
        if e["char_len"] >= budget:
            batches.append([e]); continue
        placed = False
        for b in batches:
            if len(b) == 1 and b[0]["char_len"] >= budget:
                continue
            if sum(x["char_len"] for x in b) + e["char_len"] <= budget:
                b.append(e); placed = True; break
        if not placed:
            batches.append([e])
    return batches


def _mention_text(mention, posts_idx, comments_by_link):
    if mention["source_type"] == "post":
        p = posts_idx.get(mention["source_id"], {})
        return (p.get("title") or "") + "\n" + (p.get("selftext") or "")
    for c in comments_by_link.get(mention["thread_id"], []):
        if c["id"] == mention["source_id"]:
            return c.get("body") or ""
    return ""


def build():
    mentions = load_resolved_mentions(os.path.join(DATA, "reddit_mentions.csv"))
    tids = resolved_thread_ids(mentions)
    posts_idx = load_posts_index(os.path.join(DATA, "reddit_neu_posts.csv"))
    comments_by_link = load_comments_by_link(os.path.join(DATA, "reddit_neu_comments.csv"), tids)
    surnames = load_catalog_surnames()

    by_thread = {}
    for m in mentions:
        by_thread.setdefault(m["thread_id"], []).append(m)

    entries = []
    for tid in tids:
        text = reconstruct_thread(tid, posts_idx, comments_by_link)
        entries.append({"thread_id": tid, "char_len": len(text), "thread_text": text})

    batches = pack_batches(entries, budget=50000)
    os.makedirs(os.path.join(DATA, "thread_packets"), exist_ok=True)

    manifest = []
    for i, batch in enumerate(batches, 1):
        lines = []
        idx = 0  # per-batch target index; agents cite this, not the opaque mention_id
        for e in batch:
            tid = e["thread_id"]
            lines.append(f"########## THREAD {tid} ##########")
            lines.append(e["thread_text"])
            lines.append("\n--- VERIFY TARGETS for this thread ---")
            for m in by_thread[tid]:
                idx += 1
                mtext = _mention_text(m, posts_idx, comments_by_link)
                hints = compute_hints(m, mtext, e["thread_text"], surnames)
                lines.append(f"[#{idx}] PROF: {m['professor_name']} "
                             f"({m['professor_slug']})  method={m['method']} "
                             f"conf={m['confidence']} token='{m['matched_token']}'")
                lines.append("  SOURCE TEXT: " + (mtext.strip()[:600] or "<empty>"))
                if hints:
                    lines.append("  notes: " + " | ".join(hints))
                lines.append("  -> your call (cite #" + str(idx) + "): keep / drop / reassign:<slug>  + quote + confidence")
                manifest.append({"idx": idx, "mention_id": m["mention_id"], "batch": i,
                                 "professor_slug": m["professor_slug"], "method": m["method"]})
            lines.append("")
        with open(os.path.join(DATA, "thread_packets", f"batch_{i:03d}.txt"), "w", encoding="utf-8") as f:
            f.write("\n".join(lines))

    with open(os.path.join(DATA, "verify_targets.csv"), "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["idx", "mention_id", "batch", "professor_slug", "method"])
        w.writeheader(); w.writerows(manifest)
    print(f"wrote {len(batches)} batches, {len(manifest)} verify targets")


def selftest() -> int:
    fails = []
    def check(name, cond):
        print(("PASS" if cond else "FAIL"), name)
        if not cond: fails.append(name)

    posts_idx = {"abc": {"id": "abc", "title": "Calc 3 prof?", "selftext": "anyone taken garcia"}}
    comments_by_link = {"abc": [
        {"id": "c1", "body": "first", "created_utc": "100"},
        {"id": "c2", "body": "second", "created_utc": "200"},
        {"id": "c3", "body": "third", "created_utc": "300"},
    ]}
    thread = reconstruct_thread("abc", posts_idx, comments_by_link)
    check("OP title present", "Calc 3 prof?" in thread)
    check("OP selftext present", "garcia" in thread)
    check("comments preserve given order",
          thread.index("first") < thread.index("second") < thread.index("third"))
    check("thread with no comments still returns OP", "Calc 3 prof?" in reconstruct_thread("abc", posts_idx, {}))

    surnames = {"liu", "garcia", "rachlin"}
    m_token = {"matched_token": "liu", "professor_name": "Rongbing Liu", "method": "lastname"}
    h1 = compute_hints(m_token, "weiling liu garcia was great", "no course here", surnames)
    check("another_prof_in_text when other surname present",
          any("another_prof" in x or "different surname" in x for x in h1))
    m_cc = {"matched_token": "", "professor_name": "Patricia Mabrouk", "method": "conv_context"}
    h2 = compute_hints(m_cc, "", "housing help please call the office", surnames)
    check("conv_context_no_token flagged on empty token",
          any("no_token" in x or "never named" in x for x in h2))
    check("non_topical_thread flagged when no course/teaching words",
          any("non_topical" in x or "no course" in x for x in h2))
    h3 = compute_hints(m_cc, "", "taking BIOL 1153 exam, the lecture was hard", surnames)
    check("topical thread NOT flagged non_topical",
          not any("non_topical" in x or "no course" in x for x in h3))

    entries = [
        {"thread_id": "a", "char_len": 30000},
        {"thread_id": "b", "char_len": 30000},
        {"thread_id": "big", "char_len": 90000},
        {"thread_id": "c", "char_len": 10000},
    ]
    batches = pack_batches(entries, budget=50000)
    flat = [e["thread_id"] for b in batches for e in b]
    check("every thread placed exactly once", sorted(flat) == ["a", "b", "big", "c"])
    check("oversized thread isolated in its own batch",
          any(len(b) == 1 and b[0]["thread_id"] == "big" for b in batches))
    check("no batch exceeds budget unless single oversized",
          all(sum(e["char_len"] for e in b) <= 50000 or len(b) == 1 for b in batches))

    print("ALL PASS" if not fails else f"{len(fails)} FAIL")
    return 1 if fails else 0


def main():
    parser = argparse.ArgumentParser(description="Thread reconstruction from Reddit data")
    parser.add_argument("--selftest", action="store_true", help="Run offline checks")
    parser.add_argument("--build", action="store_true", help="Build thread packets")
    args = parser.parse_args()

    if args.selftest:
        sys.exit(selftest())
    build()


if __name__ == "__main__":
    main()
