"""Deterministic baseline ranking from the committed grading/ files only (no model, no API key, no state DB).

  python scripts/rerank_from_grading.py [--out /tmp/ranking.csv]

Recomputes complaint_count, severity_sum, six-decimal half-up mean and priority_score per issue from
grading/records.jsonl.gz + grading/membership.csv, sorts by -priority then issue_id, and compares to grading/ranking.csv.
"""
import argparse, csv, gzip, io, json, sys
from collections import defaultdict
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

G = Path(__file__).resolve().parent.parent / "grading"
p = argparse.ArgumentParser(); p.add_argument("--out"); a = p.parse_args()
sev = {}
with gzip.open(G / "records.jsonl.gz", "rt", encoding="utf-8") as f:
    for line in f:
        r = json.loads(line)
        if r["status"] == "completed" and r["intent"] in ("complaint", "cancellation"):
            sev[r["review_id"]] = r["severity"]
by = defaultdict(list)
for row in csv.DictReader(open(G / "membership.csv", encoding="utf-8", newline="")):
    by[row["issue_id"]].append(sev[row["review_id"]])
rows = sorted(({"issue_id": i, "n": len(v), "s": sum(v)} for i, v in by.items()), key=lambda x: (-x["s"], x["issue_id"]))
buf = io.StringIO(); w = csv.writer(buf, lineterminator="\n")
w.writerow(["rank", "issue_id", "complaint_count", "severity_sum", "mean_severity", "priority_score"])
for k, r in enumerate(rows, 1):
    m = (Decimal(r["s"]) / Decimal(r["n"])).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)
    w.writerow([k, r["issue_id"], r["n"], r["s"], m, r["s"]])
out = buf.getvalue()
if a.out: Path(a.out).write_text(out)
same = out == (G / "ranking.csv").read_text()
print(out.splitlines()[1]); print(f"{len(rows)} issues; identical to grading/ranking.csv: {same}")
sys.exit(0 if same else 1)
