"""Load processed pipeline outputs into the dashboard's Postgres database (no model calls, no API key).

  DATABASE_URL=postgresql://... python dashboard/load_db.py

Reads only committed repo artifacts: grading/records.jsonl.gz (one final record per
source ID), grading/membership.csv, grading/claims.csv and dashboard/data/*. Creates
the tables in dashboard/schema.sql and loads every record (completed + quarantined),
issue aggregates, monthly complaint counts, headline metrics, the AI-generated
recommendation/memo and its code-validated claims. The deployed backend only reads.
"""

import csv
import gzip
import json
import os
from collections import Counter, defaultdict
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "dashboard" / "data"
GR = ROOT / "grading"


def main():
    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")
    except ImportError:
        pass
    url = os.environ["DATABASE_URL"]
    meta = {}
    with gzip.open(DATA / "reviews_meta.tsv.gz", "rt", encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f, delimiter="\t"):
            meta[r["review_id"]] = (r["review_timestamp"], r["review_rating"], int(r["review_likes"] or 0), r["app_version"])
    issue_of = {r["review_id"]: r["issue_id"] for r in csv.DictReader(open(GR / "membership.csv", encoding="utf-8", newline=""))}
    aggs = json.loads((DATA / "aggregates.json").read_text())
    memo = json.loads((DATA / "memo.json").read_text())
    pack = json.loads((DATA / "evidence_pack.json").read_text())
    claims = list(csv.DictReader(open(GR / "claims.csv", encoding="utf-8", newline="")))
    with psycopg.connect(url) as conn, conn.cursor() as cur:
        cur.execute((ROOT / "dashboard" / "schema.sql").read_text())
        status, intents, topics, months = Counter(), Counter(), Counter(), defaultdict(Counter)
        n = 0
        with cur.copy("COPY reviews FROM STDIN") as cp:
            with gzip.open(GR / "records.jsonl.gz", "rt", encoding="utf-8") as f:
                for line in f:
                    r = json.loads(line)
                    ts, rating, likes, appv = meta[r["review_id"]]
                    status[r["status"]] += 1
                    if r["status"] == "completed":
                        intents[r["intent"]] += 1
                        topics[r["topic"]] += 1
                        months[ts[:7]]["completed"] += 1
                        months[ts[:7]]["complaints"] += r["review_id"] in issue_of
                    cp.write_row((r["review_id"], ts, rating, likes, appv or None, r["status"], r.get("reason"), r.get("topic"),
                                  r.get("subtopic"), r.get("intent"), r.get("severity"), r.get("sentiment"), r.get("needs_review"),
                                  r.get("evidence_quote"), r.get("entities"), r.get("label_config"), r.get("cache_source_id"),
                                  issue_of.get(r["review_id"])))
                    n += 1
        print("reviews loaded:", n, dict(status))
        for g in aggs:
            cur.execute("INSERT INTO issues VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                        (g["issue_id"], int(g["rank"]), g["name"], g["summary"], g["area"], g["topic"], int(g["complaint_count"]),
                         int(g["severity_sum"]), g["mean_severity"], int(g["priority_score"]), int(g["cancellation_count"]),
                         int(g["severity_4plus"]), float(g["mean_sentiment"])))
        by_issue, allm = defaultdict(Counter), Counter()
        for rid, iid in issue_of.items():
            m = meta[rid][0][:7]
            by_issue[iid][m] += 1
            allm[m] += 1
        cur.executemany("INSERT INTO monthly VALUES (%s,%s,%s,%s)",
                        [(iid, m, c.get(m, 0), allm[m]) for iid, c in by_issue.items() for m in sorted(allm)])

        def put(k, v):
            cur.execute("INSERT INTO metrics VALUES (%s, %s)", (k, json.dumps(v)))
        put("status_counts", dict(status))
        put("intent_counts", dict(intents))
        put("topic_counts", dict(topics))
        put("monthly_totals", {m: dict(c) for m, c in sorted(months.items())})
        put("area_totals", pack["area_totals"])
        put("scope", pack["scope"])
        for key, fname in (("run_summary", "run_summary.json"), ("verification", "verify_report.json"),
                           ("golden", "golden_results.json"), ("run_cost", "run_cost.json"), ("pilot", "pilot_runs.json")):
            if (DATA / fname).exists():
                v = json.loads((DATA / fname).read_text())
                for drop in ("disagreement_rows", "per_class_f1", "confusion"):
                    v.pop(drop, None)
                put(key, v)
        m = memo.get("memo") or {}
        cur.execute("INSERT INTO recommendations VALUES (1,%s,%s,%s,%s,%s,%s)",
                    (m.get("priority_area"), m.get("headline"),
                     json.dumps({k: m.get(k) for k in ("recommendations", "alternatives", "limitations")}),
                     m.get("memo_markdown"), json.dumps(memo.get("validation_errors")), memo.get("config")))
        cur.executemany("INSERT INTO claims VALUES (%s,%s,%s,%s)", [(c["claim_id"], c["issue_id"], c["metric"], c["value"]) for c in claims])
    with psycopg.connect(url) as conn:
        conn.autocommit = True
        conn.execute("ANALYZE")
    print("issues", len(aggs), "claims", len(claims), "done")


if __name__ == "__main__":
    main()
