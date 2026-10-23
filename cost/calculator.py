"""100-review cost & runtime calculator (COST_CALCULATOR.md).

DEFAULT = OFFLINE REPLAY (no API key, no network, no model calls):
    python cost/calculator.py                       # same as `replay`
    python cost/calculator.py replay --rates cost/rates.csv --budget-usd 40 --workers 8
PAID PILOT (explicit, separate command; needs ANTHROPIC_API_KEY in .env):
    python cost/calculator.py pilot --execute

Replay recomputes every charge as billed_units x price_per_unit from the saved
per-attempt usage in pilot_calls.jsonl and the editable rates.csv, then projects
the full run. Importing this module never makes a call.
"""

import argparse
import csv
import json
import shutil
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
FULL_ROWS, FULL_NONEMPTY, FULL_EMPTY, FULL_DISTINCT = 660622, 660609, 13, 484189
ITEMS = ("input", "cache_write_5m", "cache_read", "output")
USAGE_FIELD = {"input": "input_tokens", "cache_write_5m": "cache_creation_input_tokens",
               "cache_read": "cache_read_input_tokens", "output": "output_tokens"}


def load_rates(path):
    rates, meta = defaultdict(dict), {}
    with open(path, encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            rates[r["model"]][r["item"]] = float(r["price_usd"]) / float(r["unit_size"])
            meta[(r["model"], r["item"])] = r
    return rates, meta


def jsonl(path):
    return [json.loads(l) for l in Path(path).read_text(encoding="utf-8").splitlines() if l.strip()] if Path(path).exists() else []


def charge(call, rates):
    """Mutually exclusive billing items; each = billed units x unit price."""
    lines = {}
    for item in ITEMS:
        units = call.get(USAGE_FIELD[item], 0) or 0
        lines[item] = (units, units * rates[call.get("rate_key") or call["model"]][item])
    return lines


def stage_table(calls, rates):
    by = defaultdict(lambda: {"attempts": 0, "succeeded": 0, "failed": 0, "requests": set(), "review_ids": set(),
                              "retry_attempts": 0, "invalid_item_retries": 0, "fallback_calls": 0, "latency_s": 0.0,
                              "usage": defaultdict(int), "cost": defaultdict(float), "models": set(), "settings": set(),
                              "prompts": set(), "batch_sizes": []})
    for c in calls:
        s = by[c["role"]]
        s["attempts"] += 1
        s["succeeded" if c["outcome"] == "succeeded" else "failed"] += 1
        s["requests"].add(c["logical_id"])
        s["retry_attempts"] += c.get("attempt", 1) > 1
        s["invalid_item_retries"] += bool(c.get("retry_of_invalid"))
        s["fallback_calls"] += bool(c.get("fallback_for"))
        s["latency_s"] += c.get("latency_s", 0)
        s["review_ids"].update(c.get("review_ids", []))
        s["models"].add(c["model"])
        s["settings"].add(json.dumps(c.get("settings"), sort_keys=True))
        s["prompts"].add(c.get("prompt_version") or "")
        if c["role"] == "enrich" and c["outcome"] == "succeeded" and not c.get("retry_of_invalid"):
            s["batch_sizes"].append(len(c.get("review_ids", [])))
        for item, (units, usd) in charge(c, rates).items():
            s["usage"][item] += units
            s["cost"][item] += usd
    return by


def fmt(x, d=4):
    return f"{x:,.{d}f}"


def project(cold, rates, args, pilot_distinct, enrich_wall_s):
    """Full-run projection from measured per-unit usage (base) and a stressed conservative case."""
    e = cold.get("enrich")
    if not e:
        return None
    n = max(pilot_distinct, 1)
    in_per = {i: e["usage"][i] / n for i in ITEMS}
    cost_per_text = sum(e["cost"].values()) / n
    sec_per_batch = (e["latency_s"] / max(e["succeeded"], 1))
    out = {}
    for name, distinct in (("base_with_exact_text_reuse", FULL_DISTINCT), ("no_reuse_comparison", FULL_NONEMPTY)):
        for case, mult_out, retry_mult in (("base", 1.0, 1.0), ("conservative", 1.5, 3.0)):
            retry_rate = max(e["invalid_item_retries"] / max(e["succeeded"], 1), 0.0)
            enrich_cost = distinct * cost_per_text * (1 + (mult_out - 1) * 0.8) * (1 + retry_rate * (retry_mult - 1))
            # verification sample (declared): fraction x distinct, capped
            v = cold.get("verify")
            ver_n = min(args.verify_max, round(args.verify_fraction * distinct))
            ver_cost = (sum(v["cost"].values()) / max(len(v["review_ids"]), 1)) * ver_n * (1.2 if case == "conservative" else 1) if v else 0
            # fallback: Sonnet single-review calls on the declared max fraction (conservative only, base = measured)
            fb_n = distinct * (args.fallback_fraction if case == "conservative" else 0)
            fb_unit = (in_per["input"] + in_per["cache_write_5m"] + in_per["cache_read"]) * 50 * rates["claude-sonnet-5-5"]["input"] \
                + 150 * rates["claude-sonnet-5-5"]["output"]
            fb_cost = fb_n * fb_unit
            fixed = sum(sum(cold[r]["cost"].values()) for r in ("group", "memo") if r in cold) * (2.0 if case == "conservative" else 1.3)
            total = enrich_cost + ver_cost + fb_cost + fixed
            batches = distinct / 50
            hours = batches * sec_per_batch * (1.3 if case == "conservative" else 1) / max(args.workers, 1) / 3600
            out[f"{name}/{case}"] = {"distinct_texts_sent": distinct, "enrich_usd": enrich_cost, "verify_usd": ver_cost,
                                     "verify_texts": ver_n, "fallback_usd": fb_cost, "fixed_group_memo_usd": fixed,
                                     "total_api_usd": total, "enrich_hours_at_workers": hours,
                                     "exceeds_budget": total > args.budget_usd}
    return out, cost_per_text, sec_per_batch


def replay(args):
    rates, meta = load_rates(args.rates)
    calls = jsonl(HERE / "pilot_calls.jsonl")
    records = jsonl(HERE / "pilot_records.jsonl")
    runs = json.loads((HERE / "pilot_runs.json").read_text()) if (HERE / "pilot_runs.json").exists() else None
    if not calls or not runs:
        print("No saved pilot measurements yet (cost/pilot_calls.jsonl, cost/pilot_runs.json). Run: python cost/calculator.py pilot --execute")
        return 1
    cold_id, warm_id = runs["cold"]["run_id"], runs["warm"]["run_id"]
    cold_calls = [c for c in calls if c["run_id"] == cold_id]
    warm_calls = [c for c in calls if c["run_id"] == warm_id]
    cold, warm = stage_table(cold_calls, rates), stage_table(warm_calls, rates)
    api_cold = sum(sum(s["cost"].values()) for s in cold.values())
    api_warm = sum(sum(s["cost"].values()) for s in warm.values())
    completed = sum(r["status"] == "completed" for r in records)
    failed = len(records) - completed
    distinct = runs["cold"]["distinct_texts"]
    proj, cost_per_text, sec_per_batch = project(cold, rates, args, distinct, runs["cold"]["stages"].get("enrich", {}).get("seconds", 0))
    # usage.csv: every charged item mapped to its call
    with (HERE / "usage.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["run_id", "phase", "request_id", "role", "model", "attempt", "outcome", "item", "billed_units", "unit", "price_per_unit_usd", "cost_usd", "rate_source", "rate_checked"])
        for c in calls:
            ph = "cold" if c["run_id"] == cold_id else "warm"
            for item, (units, usd) in charge(c, rates).items():
                rk = c.get("rate_key") or c["model"]
                m = meta[(rk, item)]
                w.writerow([c["run_id"], ph, c["request_id"], c["role"], c["model"], c["attempt"], c["outcome"], item, units,
                            "token", f"{rates[rk][item]:.10f}", f"{usd:.10f}", m["source_url"], m["checked_date"]])
    L = []
    L.append("# 100-review cost & runtime calculator — measured pilot + full-run projection\n")
    L.append(f"_Generated by `python cost/calculator.py replay` (offline; recomputed from saved usage × `{Path(args.rates).name}`)._\n")
    L.append("## Measured on the 100-review pilot\n")
    L.append(f"- Input: `data/cost_100.csv`, SHA-256 `{runs['input_sha256']}` (manifest: c884ac3b…dd0e) — {runs['cold']['rows']} IDs, {distinct} distinct texts (result-cache hits from duplicate texts: {runs['cold']['rows'] - distinct}).")
    L.append(f"- Records: **{completed} completed**, {failed} failed/quarantined (`cost/pilot_records.jsonl`).")
    L.append(f"- Cold run `{cold_id}`: empty result cache, 1 worker. **End-to-end wall clock {runs['cold']['wall_clock_s']:.1f} s**; API spend **${fmt(api_cold, 6)}**.")
    L.append(f"- Warm run `{warm_id}`: same settings, saved cache. **{sum(s['attempts'] for s in warm.values())} new model calls**, wall clock {runs['warm']['wall_clock_s']:.2f} s, incremental API spend ${fmt(api_warm, 6)}.\n")
    L.append("| stage | model / settings / prompt | requests | attempts (ok / failed) | retries (transient / invalid-item) | fallback | input | cache write | cache read | output | API cost USD | call time s | stage wall s |")
    L.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for role in ("enrich", "verify", "group", "memo"):
        s = cold.get(role)
        if not s:
            continue
        L.append(f"| {role} | {', '.join(s['models'])} {', '.join(json.loads(x) and ' '.join(f'{k}={v}' for k, v in json.loads(x).items()) for x in s['settings'])} {', '.join(s['prompts'])} | {len(s['requests'])} | {s['attempts']} ({s['succeeded']} / {s['failed']}) | {s['retry_attempts']} / {s['invalid_item_retries']} | {s['fallback_calls']} | "
                 f"{s['usage']['input']:,} | {s['usage']['cache_write_5m']:,} | {s['usage']['cache_read']:,} | {s['usage']['output']:,} | {fmt(sum(s['cost'].values()), 6)} | {s['latency_s']:.1f} | {runs['cold']['stages'].get(role, {}).get('seconds', 0):.1f} |")
    L.append(f"| ingest + rank (code) | local | 0 | — | — | — | — | — | — | — | 0 (local compute not metered) | — | {runs['cold']['stages'].get('ingest', {}).get('seconds', 0) + runs['cold']['stages'].get('rank', {}).get('seconds', 0):.2f} |")
    e = cold.get("enrich")
    if e:
        bs = e["batch_sizes"]
        L.append(f"\n- Enrichment batch size ≤ 50 (observed {min(bs)}–{max(bs)} reviews/request), workers = 1, output cap = 400 + 110 × reviews tokens/request.")
        L.append(f"- **Cost per 1,000 input rows: ${fmt(api_cold / runs['cold']['rows'] * 1000, 4)}**; per completed record: ${fmt(api_cold / max(completed, 1), 6)}; enrichment-only cost per distinct text: ${fmt(cost_per_text, 7)}.")
        L.append(f"- Throughput (cold, 1 worker): {runs['cold']['rows'] / runs['cold']['wall_clock_s']:.2f} rows/s end-to-end; enrichment {distinct / max(runs['cold']['stages']['enrich']['seconds'], 1e-9):.2f} distinct texts/s; mean enrichment request latency {sec_per_batch:.1f} s.")
        L.append("- Provider prompt caching (separate from our result cache): " + ("cache reads observed" if e["usage"]["cache_read"] else "no cache reads observed (system prompt below the model's minimum cacheable length or cache not hit); none assumed in projections."))
    L.append("\nRates used (editable `cost/rates.csv`, USD per token = price ÷ unit_size):\n")
    L.append("| model | item | price per 1M | source | checked |\n|---|---|---|---|---|")
    for (m, item), r in meta.items():
        L.append(f"| {m} | {item} | ${r['price_usd']} | {r['source_url']} | {r['checked_date']} |")
    L.append("\nLocal compute (ingest/profile/rank on a laptop or cloud container) is **not metered — cost unknown, not assumed zero**; measured time is shown separately above.\n")
    L.append("## Estimated before the full run\n")
    L.append(f"Scope: {FULL_ROWS:,} rows → {FULL_NONEMPTY:,} nonempty classifications + {FULL_EMPTY} empty-text quarantines; with valid exact-text reuse, first-pass work is {FULL_DISTINCT:,} distinct texts.\n")
    L.append(f"Controls: budget cap **${args.budget_usd:.2f}** (enforced by the pipeline's spend ledger with worst-case reservation before dispatch), max workers **{args.workers}**, "
             f"output-token cap 400 + 110 × reviews per enrichment request, declared max fallback fraction **{args.fallback_fraction:.3%}** (Sonnet 5.5 single-review), verification sample {args.verify_fraction:.1%} capped at {args.verify_max:,} texts.\n")
    L.append("| scenario | distinct texts sent | enrich $ | verify $ (texts) | fallback $ | group+memo fixed $ | **total API $** | enrich hours @ workers | over budget? |\n|---|---|---|---|---|---|---|---|---|")
    for k, p in proj.items():
        L.append(f"| {k} | {p['distinct_texts_sent']:,} | {fmt(p['enrich_usd'], 2)} | {fmt(p['verify_usd'], 2)} ({p['verify_texts']:,}) | {fmt(p['fallback_usd'], 2)} | {fmt(p['fixed_group_memo_usd'], 2)} | **{fmt(p['total_api_usd'], 2)}** | {p['enrich_hours_at_workers']:.1f} | {'⚠️ EXCEEDS BUDGET' if p['exceeds_budget'] else 'within'} |")
    L.append("\nAssumptions: base = measured per-text usage × volume, measured invalid-item retry rate, measured group/memo calls once (×1.3 for a larger pack). "
             "Conservative = output tokens +50%, invalid-item retry rate ×3, fallback at the declared cap, group/memo ×2, latency +30%. "
             "Fixed overhead is counted once, never per review. Time assumes linear scaling with workers up to provider rate limits (modeled, not measured). "
             "Batch API discounts are not applied (not used).\n")
    L.append("## Formulas\n\n`item_cost = billed_units × price_per_unit` for mutually exclusive items (uncached input, 5-minute cache write, cache read, output; output already includes any thinking tokens, none here because thinking is disabled). `total = Σ item_cost`. "
             "Every charged line is in `cost/usage.csv`, mapped to its `request_id` in `cost/pilot_calls.jsonl`.\n")
    (HERE / "report.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    print("\n".join(L[:12]))
    print(f"\n... full report: {HERE / 'report.md'}")
    print(json.dumps({"api_cold_usd": round(api_cold, 6), "api_warm_usd": round(api_warm, 6),
                      "projection": {k: round(v["total_api_usd"], 2) for k, v in proj.items()}}, indent=1))
    return 0


def pilot(args):
    if not args.execute:
        print("Refusing to spend money without --execute. Offline replay: python cost/calculator.py replay")
        return 2
    state = HERE / "pilot_state"
    if state.exists():
        shutil.rmtree(state)  # empty result cache for this experiment
    inp = ROOT / "data" / "cost_100.csv"
    out = {}
    for phase in ("cold", "warm"):
        cmd = [sys.executable, "-m", "pipeline", "run", "--input", str(inp), "--state", str(state), "--workers", "1",
               "--budget-usd", str(args.pilot_budget_usd), "--verify-fraction", "0.2", "--verify-min", "20",
               "--run-id", f"pilot-{phase}-{time.strftime('%Y%m%dT%H%M%S')}"]
        print("$", " ".join(cmd))
        t0 = time.monotonic()
        subprocess.run(cmd, cwd=ROOT, check=True)
        summ = json.loads((state / "out" / "run_summary.json").read_text())
        out[phase] = {"run_id": summ["run_id"], "wall_clock_s": round(time.monotonic() - t0, 3), "stages": summ["stages"],
                      "rows": 100, "distinct_texts": None, "calls": summ.get("calls")}
    import sqlite3
    db = sqlite3.connect(state / "state.sqlite")
    distinct = db.execute("SELECT COUNT(DISTINCT tkey) FROM reviews WHERE empty=0").fetchone()[0]
    for ph in out:
        out[ph]["distinct_texts"] = distinct
    sys.path.insert(0, str(ROOT))
    from pipeline.common import LABEL_CONFIG, file_sha
    from pipeline.state import State
    st = State(state)
    sha = file_sha(inp)
    with (HERE / "pilot_records.jsonl").open("w", encoding="utf-8") as f:
        for rev, res in st.scope_records(sha, LABEL_CONFIG):
            base = {"review_id": rev["review_id"], "source_sha256": rev["source_sha"]}
            if res and res["status"] == "completed":
                lab = res["labels"]
                rec = {**base, "status": "completed", **{k: lab[k] for k in ("topic", "subtopic", "intent", "sentiment", "severity", "entities", "evidence_quote", "needs_review")},
                       "label_config": res["label_config"], "request_id": res["request_id"]}
                if res["sent_review_id"] != rev["review_id"]:
                    rec["cache_source_id"] = res["sent_review_id"]
            else:
                rec = {**base, "status": "quarantined", "reason": (res or {}).get("reason") or "pending"}
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    shutil.copyfile(state / "calls.jsonl", HERE / "pilot_calls.jsonl")
    for name in ("memo.md", "ranking.csv", "verify_report.json", "issues.json"):
        if (state / "out" / name).exists():
            shutil.copyfile(state / "out" / name, HERE / f"pilot_{name}")
    (HERE / "pilot_runs.json").write_text(json.dumps({"input_sha256": sha, **out}, indent=2) + "\n")
    return replay(args)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("cmd", nargs="?", default="replay", choices=["replay", "pilot"])
    p.add_argument("--execute", action="store_true", help="required for the paid pilot")
    p.add_argument("--rates", default=str(HERE / "rates.csv"))
    p.add_argument("--budget-usd", type=float, default=40.0)
    p.add_argument("--pilot-budget-usd", type=float, default=2.0)
    p.add_argument("--workers", type=int, default=8)
    p.add_argument("--fallback-fraction", type=float, default=0.005)
    p.add_argument("--verify-fraction", type=float, default=0.01)
    p.add_argument("--verify-max", type=int, default=5000)
    args = p.parse_args(argv)
    return replay(args) if args.cmd == "replay" else pilot(args)


if __name__ == "__main__":
    sys.exit(main())
