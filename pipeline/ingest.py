"""Stage 1 — Prepare (code only, no model).

Reads every row of the input CSV with the checker's parser, preserves IDs and
source values, computes the exact source row hash, profiles quality, finds
exact duplicate texts and records which reviews belong to this input's scope.
"""

import json
from collections import Counter
from pathlib import Path

from .common import csv_rows, file_sha, row_sha, text_key, write_json
from .state import now


def ingest(st, csv_path):
    csv_path = Path(csv_path)
    input_sha = file_sha(csv_path)
    if st.db.execute("SELECT 1 FROM inputs WHERE input_sha=?", (input_sha,)).fetchone():
        report = json.loads((st.out / f"ingestion_report_{input_sha[:12]}.json").read_text())
        report["reused_existing_ingestion"] = True
        return input_sha, report
    base = st.db.execute("SELECT COALESCE(MAX(ord), -1) + 1 FROM reviews").fetchone()[0]
    counts = Counter()
    months, ratings, ids, text_seen = Counter(), Counter(), set(), Counter()
    first = last = None
    rev_rows, mem_rows, text_rows = [], [], {}
    for i, row in enumerate(csv_rows(csv_path)):
        rid = row["review_id"]
        counts["records"] += 1
        if rid in ids:
            counts["duplicate_review_ids"] += 1
            continue  # keep the first occurrence; duplicates are reported, never silently merged
        ids.add(rid)
        txt = row["review_text"]
        empty = not txt.strip()
        counts["empty_review_text"] += empty
        counts["missing_app_version"] += not row["app_version"].strip()
        if row["review_rating"] not in {"1", "2", "3", "4", "5"}:
            counts["invalid_rating"] += 1
        months[row["review_timestamp"][:7]] += 1
        ratings[row["review_rating"]] += 1
        ts = row["review_timestamp"]
        first = min(first, ts) if first else ts
        last = max(last, ts) if last else ts
        tk = text_key(txt)
        if not empty:
            text_seen[tk] += 1
        text_rows.setdefault(tk, (tk, txt, rid))
        rev_rows.append((rid, base + i, row_sha(row), tk, int(empty), row["review_rating"], row["review_likes"], row["app_version"], ts))
        mem_rows.append((input_sha, rid))
    with st.tx() as db:
        db.executemany("INSERT OR IGNORE INTO reviews VALUES(?,?,?,?,?,?,?,?,?)", rev_rows)
        db.executemany("INSERT OR IGNORE INTO texts(tkey, text, first_review_id) VALUES(?,?,?)", text_rows.values())
        db.executemany("INSERT OR IGNORE INTO input_members VALUES(?,?)", mem_rows)
        db.execute("INSERT INTO inputs VALUES(?,?,?,?)", (input_sha, str(csv_path), counts["records"], now()))
    dup_groups = [c for c in text_seen.values() if c > 1]
    report = {
        "input_path": str(csv_path), "input_sha256": input_sha, "input_bytes": csv_path.stat().st_size,
        "counts": {**{k: counts[k] for k in ("records", "duplicate_review_ids", "empty_review_text", "missing_app_version")},
                   "invalid_rating": counts["invalid_rating"], "unique_review_ids": len(ids),
                   "nonempty_to_classify": len(ids) - counts["empty_review_text"],
                   "distinct_nonempty_texts": len(text_seen),
                   "reviews_with_repeated_text": sum(dup_groups), "repeated_text_groups": len(dup_groups),
                   "reuse_savings_rows": (len(ids) - counts["empty_review_text"]) - len(text_seen)},
        "first_review": first, "last_review": last,
        "reviews_by_month": dict(sorted(months.items())), "reviews_by_rating": dict(sorted(ratings.items())),
        "top_repeated_texts": [{"text": t, "n": n} for t, n in _top_texts(st, text_seen, 15)],
        "quarantine_rule": "Empty/whitespace-only review_text -> status quarantined, reason empty_review_text. Missing app_version is kept and classified.",
        "ingested_at": now(),
    }
    write_json(st.out / f"ingestion_report_{input_sha[:12]}.json", report)
    return input_sha, report


def _top_texts(st, text_seen, k):
    out = []
    for tk, n in text_seen.most_common(k):
        out.append((st.db.execute("SELECT text FROM texts WHERE tkey=?", (tk,)).fetchone()[0], n))
    return out
