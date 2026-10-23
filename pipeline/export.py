"""Standardized grading/ export (GRADING_CONTRACT.md v1).

records.jsonl.gz: exactly one final record per source ID. Completed records
carry the common labels; repeated exact texts carry `cache_source_id` pointing
directly at the record that was actually sent to the model. Empty texts are
quarantined `empty_review_text`; anything unresolved is quarantined with its
reason (never silently counted as completed).
"""

import gzip
import json
import shutil
from pathlib import Path

from .common import LABEL_CONFIG, file_sha, write_json
from .state import State

KEEP = ("topic", "intent", "sentiment", "severity", "entities", "evidence_quote", "needs_review")


def export(st: State, full_csv, out: Path):
    out.mkdir(parents=True, exist_ok=True)
    full_sha = file_sha(full_csv)
    rows = 0
    stats = {"completed": 0, "cache_reuse": 0, "quarantined": 0}
    with gzip.open(out / "records.jsonl.gz", "wt", encoding="utf-8") as f:
        for rev, res in st.scope_records(full_sha, LABEL_CONFIG):
            rows += 1
            base = {"review_id": rev["review_id"], "source_sha256": rev["source_sha"]}
            if res and res["status"] == "completed":
                rec = {**base, "status": "completed", **{k: res["labels"][k] for k in KEEP},
                       "label_config": res["label_config"], "subtopic": res["labels"]["subtopic"]}
                if res["sent_review_id"] != rev["review_id"]:
                    rec["cache_source_id"] = res["sent_review_id"]
                    stats["cache_reuse"] += 1
                stats["completed"] += 1
            else:
                reason = (res or {}).get("reason") or "unresolved_pending_not_classified"
                rec = {**base, "status": "quarantined", "reason": reason, "attempts": (res or {}).get("attempts", 0)}
                stats["quarantined"] += 1
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    if rows == 0:
        raise SystemExit("Full CSV has not been ingested into this state dir.")
    write_json(out / "run.json", {"version": "a5-audit-v1", "analysis_count": rows, "analysis_sha256": full_sha,
                                  "classification_input_fields": ["review_text"], "allow_multi_issue": False})
    for name in ("membership.csv", "ranking.csv", "claims.csv"):
        shutil.copyfile(st.out / name, out / name)
    n_calls, seen, dupes = 0, set(), 0
    (out / "calls.jsonl").unlink(missing_ok=True)
    with gzip.open(out / "calls.jsonl.gz", "wt", encoding="utf-8") as f:
        for r in _calls(st):
            if r.get("mock"):
                continue
            if r["request_id"] in seen:  # same provider result logged twice (collector restarted mid-request)
                dupes += 1
                continue
            seen.add(r["request_id"])
            n_calls += 1
            f.write(json.dumps({
                "request_id": r["request_id"], "role": r["role"], "review_ids": r["review_ids"], "model": r["model"],
                "phase": r.get("phase"), "outcome": r["outcome"], "label_config": r.get("label_config"),
                "input_tokens": r.get("input_tokens", 0) + r.get("cache_creation_input_tokens", 0) + r.get("cache_read_input_tokens", 0),
                "output_tokens": r.get("output_tokens", 0),
                "uncached_input_tokens": r.get("input_tokens", 0),
                "cache_creation_input_tokens": r.get("cache_creation_input_tokens", 0),
                "cache_read_input_tokens": r.get("cache_read_input_tokens", 0),
                "cost_usd": r.get("cost_usd"), "run_id": r["run_id"], "attempt": r["attempt"],
                "input_artifact": r.get("input_artifact"), "error": r.get("error")}, ensure_ascii=False) + "\n")
    print(f"export: {rows} records ({stats}), {n_calls} calls ({dupes} duplicate log lines dropped) -> {out}")
    return stats


def _calls(st):
    p = st.dir / "calls.jsonl"
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            if line.strip():
                yield json.loads(line)
