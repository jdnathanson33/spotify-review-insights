"""Offline: per-run measured usage/cost from the main state's call log (100 -> 500 -> 10K -> full).

  python cost/scaling.py [--state state/main] [--rates cost/rates.csv]

Recomputes every charge from saved usage x editable rates (no API key). Writes cost/scaling.md.
"""

import argparse
import json
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
import sys
sys.path.insert(0, str(HERE))
from calculator import charge, load_rates  # noqa: E402


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--state", default=str(HERE.parent / "state" / "main"))
    p.add_argument("--rates", default=str(HERE / "rates.csv"))
    a = p.parse_args()
    rates, _ = load_rates(a.rates)
    calls = [json.loads(l) for l in (Path(a.state) / "calls.jsonl").read_text().splitlines() if l.strip()]
    runs = {}
    for l in (Path(a.state) / "run_log.jsonl").read_text().splitlines():
        d = json.loads(l)
        runs.setdefault(d["run_id"], d)
    by = defaultdict(lambda: defaultdict(float))
    order, seen = [], set()
    for c in calls:
        if c["request_id"] in seen:
            continue  # duplicate log line of one provider result (collector restart); counted once
        seen.add(c["request_id"])
        r = c["run_id"]
        if r not in by:
            order.append(r)
        b = by[r]
        b["attempts"] += 1
        b["failed"] += c["outcome"] == "failed"
        b[c["role"] + "_calls"] += 1
        if c["role"] == "enrich" and c["outcome"] == "succeeded" and not c.get("retry_of_invalid"):
            b["texts_sent"] += len(c["review_ids"])
        for item, (units, usd) in charge(c, rates).items():
            b[item] += units
            b["usd"] += usd
        b["batch_tier_calls"] += c.get("tier") == "batch"
    total = sum(b["usd"] for b in by.values())
    L = ["# Scaling ladder — measured usage and cost per run (recomputed offline)\n",
         "| run_id | enrich texts sent | enrich / verify / group / memo calls | failed attempts | input | cache write | cache read | output | API $ | $ per 1k texts | wall s |",
         "|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in order:
        b = by[r]
        wall = (runs.get(r) or {}).get("wall_clock_s", "")
        per = b["usd"] / b["texts_sent"] * 1000 if b["texts_sent"] else 0
        L.append(f"| `{r}` | {int(b['texts_sent']):,} | {int(b['enrich_calls'])} / {int(b['verify_calls'])} / {int(b['group_calls'])} / {int(b['memo_calls'])} | {int(b['failed'])} | "
                 f"{int(b['input']):,} | {int(b['cache_write_5m']):,} | {int(b['cache_read']):,} | {int(b['output']):,} | {b['usd']:.4f} | {per:.4f} | {wall} |")
    L.append(f"\n**Total API spend in the main state: ${total:.4f}** (pilot state reported separately in `cost/report.md`).")
    L.append("\nNotes: `full-batch-*` runs used the Message Batches API (rates key `claude-haiku-5-5@batch`, 50% off). 429 failures in `scale-10k-w8` "
             "came from the account's 80,000 output-tokens/minute limit; they were retried with backoff or left pending and re-run, never counted as completed. "
             "After that run the pipeline gained a shared token-bucket limiter (`--otpm`).")
    (HERE / "scaling.md").write_text("\n".join(L) + "\n")
    json.dump({"total_usd": round(total, 4), "calls": len(calls), "input_tokens": int(sum(b["input"] + b["cache_write_5m"] + b["cache_read"] for b in by.values())),
               "output_tokens": int(sum(b["output"] for b in by.values())), "note": "all runs incl. dev checkpoints; recomputed from saved usage"},
              open(HERE / "run_cost.json", "w"), indent=1)
    print("\n".join(L))


if __name__ == "__main__":
    main()
