"""Build dashboard/data/ (committed) from the pipeline state, so the DB can be loaded from the repo alone.

  python dashboard/export_for_db.py --state state/main

Outputs: reviews_meta.tsv.gz (review_id, timestamp, rating, likes, app_version — source values),
aggregates.json, issues.json, memo.json, evidence_pack.json, verify_report.json and quality summaries.
Labels themselves come from grading/records.jsonl.gz and membership from grading/membership.csv.
"""

import argparse
import csv
import gzip
import json
import shutil
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--state", default=str(ROOT / "state" / "main"))
    a = p.parse_args()
    st = Path(a.state)
    out = ROOT / "dashboard" / "data"
    out.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(st / "state.sqlite")
    full = db.execute("SELECT input_sha FROM inputs WHERE rows=660622").fetchone()[0]
    with gzip.open(out / "reviews_meta.tsv.gz", "wt", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter="\t", lineterminator="\n")
        w.writerow(["review_id", "review_timestamp", "review_rating", "review_likes", "app_version"])
        for row in db.execute("""SELECT r.review_id, r.ts, r.rating, r.likes, r.app_version FROM input_members m JOIN reviews r USING(review_id)
                                 WHERE m.input_sha=? ORDER BY r.ord""", (full,)):
            w.writerow(row)
    aggs = list(csv.DictReader(open(st / "out" / "aggregates.csv", encoding="utf-8", newline="")))
    (out / "aggregates.json").write_text(json.dumps(aggs, ensure_ascii=False, indent=0))
    for name in ("issues.json", "memo.json", "evidence_pack.json", "verify_report.json", "run_summary.json"):
        if (st / "out" / name).exists():
            shutil.copyfile(st / "out" / name, out / name)
    for src in (ROOT / "evals" / "golden_results.json", ROOT / "cost" / "run_cost.json", ROOT / "cost" / "pilot_runs.json"):
        if src.exists():
            shutil.copyfile(src, out / src.name)
    print("wrote", sorted(x.name for x in out.iterdir()))


if __name__ == "__main__":
    main()
