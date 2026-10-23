"""Orchestrator CLI.

  python -m pipeline run --input data/checkpoint_500.csv --state state/main [--stages ingest,enrich,verify,group,rank,memo]
         [--workers 1] [--budget-usd 40] [--phase initial|resume] [--max-batches N] [--verify-fraction 0.01] [--mock]
  python -m pipeline rerank --state state/main [--input CSV]      # deterministic, no model calls, no API key
  python -m pipeline checkpoint --state state/main --input CSV --out grading/checkpoint_before.json
  python -m pipeline export --state state/main --full data/spotify_reviews_18months.csv --out grading

Code owns dispatch, state, validation, budgets and logs; models only read language.
"""

import argparse
import json
import signal
import sys
import threading
import time
import uuid
from pathlib import Path

from .common import LABEL_CONFIG, file_sha, load_env, write_json
from .state import State, now


def _caller(st, args, run_id):
    from .llm import CallLog, Caller, Ledger, load_rates, make_client
    log = CallLog(st.dir / "calls.jsonl")
    spent = log.total_cost()
    ledger = Ledger(args.budget_usd, spent)
    print(f"budget: cap ${args.budget_usd:.2f} for this state dir; already spent ${spent:.4f}")
    from .llm import RateLimiter
    return Caller(make_client(mock=args.mock), log, ledger, load_rates(), run_id,
                  limiter=RateLimiter(otpm=getattr(args, "otpm", 70000)))


def cmd_run(args):
    load_env()
    st = State(args.state)
    stages = args.stages.split(",")
    run_id = args.run_id or time.strftime("run-%Y%m%dT%H%M%S-") + uuid.uuid4().hex[:6]
    argd = {k: v for k, v in vars(args).items() if k != "fn"}
    summary = {"run_id": run_id, "started_at": now(), "args": argd, "label_config": LABEL_CONFIG, "stages": {}}
    stop = threading.Event()

    def on_signal(sig, frame):
        print(f"\n!! signal {sig}: finishing in-flight batches, saving, then stopping (no new dispatch)")
        stop.set()
    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)
    t_all = time.monotonic()
    caller = None
    needs_model = any(s in stages for s in ("enrich", "verify", "group", "memo"))
    if needs_model:
        caller = _caller(st, args, run_id)

    def timed(name, fn):
        t0 = time.monotonic()
        res = fn()
        summary["stages"][name] = {"seconds": round(time.monotonic() - t0, 3), **(res if isinstance(res, dict) else {})}
        return res

    input_sha = None
    if "ingest" in stages:
        from .ingest import ingest
        input_sha, rep = None, None
        def _ing():
            nonlocal input_sha, rep
            input_sha, rep = ingest(st, args.input)
            print(f"ingest: {rep['counts']}")
            return {"input_sha256": input_sha, "counts": rep["counts"]}
        timed("ingest", _ing)
    input_sha = input_sha or file_sha(args.input)
    if "enrich" in stages and not stop.is_set():
        from .enrich import Enricher
        e = Enricher(st, caller, input_sha, workers=args.workers, phase=args.phase, max_batches=args.max_batches,
                     fallback_fraction=args.fallback_fraction, stop_event=stop)
        if args.requeue_quarantined:
            print(f"requeued {e.requeue_quarantined()} invalid-output quarantines for one more cycle")
        timed("enrich", e.run)
        print(f"enrich: {summary['stages']['enrich']}")
    if "verify" in stages and not stop.is_set():
        from .verify import run_verify
        timed("verify", lambda: {k: v for k, v in run_verify(st, caller, input_sha, fraction=args.verify_fraction,
                                                              minimum=args.verify_min, maximum=args.verify_max).items()
                                 if k != "disagreement_rows"})
    records = members = ranking = aggs = issues = None
    if any(s in stages for s in ("group", "rank", "memo")) and not stop.is_set():
        from .group_rank import final_records, membership, run_group, run_rank
        if "group" in stages:
            def _grp():
                nonlocal issues
                recs = final_records(st, input_sha)
                issues, calls = run_group(st, caller, input_sha, recs, membership(recs))
                write_json(st.out / "issues.json", issues)
                return {"issues": len(issues), "calls": calls}
            timed("group", _grp)
        def _rank():
            nonlocal records, members, ranking, aggs
            records, members, ranking, aggs = run_rank(st, input_sha, issues)
            return {"completed_records": len(records), "memberships": len(members), "issues_ranked": len(ranking)}
        timed("rank", _rank)
        if "memo" in stages:
            from .memo import run_memo
            def _memo():
                memo, claims, errs, calls = run_memo(st, caller, input_sha, records, members, ranking, aggs)
                return {"claims": len(claims), "validation_errors": errs, "calls": calls,
                        "priority_area": (memo or {}).get("priority_area")}
            timed("memo", _memo)
    summary["wall_clock_s"] = round(time.monotonic() - t_all, 3)
    summary["finished_at"] = now()
    summary["interrupted"] = stop.is_set()
    if caller:
        summary["ledger"] = caller.ledger.snapshot()
        mine = [r for r in caller.log.records() if r["run_id"] == run_id]
        summary["calls"] = {"attempts": len(mine), "succeeded": sum(r["outcome"] == "succeeded" for r in mine),
                            "failed": sum(r["outcome"] == "failed" for r in mine),
                            "input_tokens": sum(r.get("input_tokens", 0) for r in mine),
                            "output_tokens": sum(r.get("output_tokens", 0) for r in mine),
                            "cost_usd": round(sum(r.get("cost_usd", 0) for r in mine), 6)}
    st.db.execute("INSERT OR REPLACE INTO runs VALUES(?,?,?,?)", (run_id, summary["started_at"], json.dumps(argd), json.dumps(summary)))
    with (st.dir / "run_log.jsonl").open("a") as f:
        f.write(json.dumps(summary) + "\n")
    write_json(st.out / "run_summary.json", summary)
    print(json.dumps({k: summary[k] for k in ("run_id", "wall_clock_s", "interrupted") if k in summary} | {"calls": summary.get("calls")}, indent=1))
    return summary


def cmd_rerank(args):
    st = State(args.state)
    input_sha = file_sha(args.input) if args.input else st.latest_input()
    from .group_rank import run_rank
    run_rank(st, input_sha)
    print(f"wrote {st.out / 'ranking.csv'} (no model calls)")


def cmd_checkpoint(args):
    st = State(args.state)
    input_sha = file_sha(args.input)
    ids = [rev["review_id"] for rev, res in st.scope_records(input_sha, LABEL_CONFIG) if res and res["status"] == "completed"]
    write_json(args.out, {"completed_ids": ids, "count": len(ids), "saved_at": now(), "label_config": LABEL_CONFIG})
    print(f"checkpoint: {len(ids)} completed IDs -> {args.out}")


def _batch_caller(args, st, run_id):
    load_env()
    return _caller(st, args, run_id)


def cmd_batch_submit(args):
    from .batch import submit
    st = State(args.state)
    run_id = args.run_id or time.strftime("batch-%Y%m%dT%H%M%S")
    caller = _batch_caller(args, st, run_id)
    res = submit(st, caller, file_sha(args.input), args.phase, max_requests=args.max_requests)
    with (st.dir / "run_log.jsonl").open("a") as f:
        f.write(json.dumps({"run_id": run_id, "event": "batch_submit", "at": now(), **res}) + "\n")
    print(json.dumps(res, indent=1))


def cmd_batch_collect(args):
    from .batch import collect
    st = State(args.state)
    run_id = args.run_id or time.strftime("collect-%Y%m%dT%H%M%S")
    caller = _batch_caller(args, st, run_id)
    res = collect(st, caller, args.batch_id, wait=args.wait)
    with (st.dir / "run_log.jsonl").open("a") as f:
        f.write(json.dumps({"run_id": run_id, "event": "batch_collect", "at": now(), "result": res}) + "\n")


def cmd_export(args):
    from .export import export
    export(State(args.state), args.full, Path(args.out))


def main(argv=None):
    p = argparse.ArgumentParser(prog="pipeline", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--input", required=True)
    r.add_argument("--state", required=True)
    r.add_argument("--stages", default="ingest,enrich,verify,group,rank,memo")
    r.add_argument("--workers", type=int, default=1)
    r.add_argument("--budget-usd", type=float, default=40.0)
    r.add_argument("--phase", choices=["initial", "resume"], default="initial")
    r.add_argument("--max-batches", type=int)
    r.add_argument("--fallback-fraction", type=float, default=0.0, help="capped single-review Sonnet fallback share (0 disables)")
    r.add_argument("--verify-fraction", type=float, default=0.01)
    r.add_argument("--verify-min", type=int, default=20)
    r.add_argument("--verify-max", type=int, default=5000)
    r.add_argument("--run-id")
    r.add_argument("--requeue-quarantined", action="store_true", help="retry invalid-output quarantines once more")
    r.add_argument("--otpm", type=int, default=70000, help="shared output-tokens-per-minute limiter (account limit 80,000)")
    r.add_argument("--mock", action="store_true", help="offline mock provider (tests only)")
    r.set_defaults(fn=cmd_run)
    k = sub.add_parser("rerank"); k.add_argument("--state", required=True); k.add_argument("--input"); k.set_defaults(fn=cmd_rerank)
    c = sub.add_parser("checkpoint"); c.add_argument("--state", required=True); c.add_argument("--input", required=True)
    c.add_argument("--out", required=True); c.set_defaults(fn=cmd_checkpoint)
    bs = sub.add_parser("batch-submit"); bs.add_argument("--input", required=True); bs.add_argument("--state", required=True)
    bs.add_argument("--phase", choices=["initial", "resume"], default="initial"); bs.add_argument("--max-requests", type=int)
    bs.add_argument("--budget-usd", type=float, default=40.0); bs.add_argument("--run-id"); bs.add_argument("--otpm", type=int, default=70000)
    bs.add_argument("--mock", action="store_true"); bs.set_defaults(fn=cmd_batch_submit)
    bc = sub.add_parser("batch-collect"); bc.add_argument("--state", required=True); bc.add_argument("--batch-id")
    bc.add_argument("--wait", action="store_true"); bc.add_argument("--budget-usd", type=float, default=40.0); bc.add_argument("--run-id")
    bc.add_argument("--otpm", type=int, default=70000); bc.add_argument("--mock", action="store_true"); bc.set_defaults(fn=cmd_batch_collect)
    e = sub.add_parser("export"); e.add_argument("--state", required=True); e.add_argument("--full", required=True)
    e.add_argument("--out", required=True); e.set_defaults(fn=cmd_export)
    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(0 if main() is not None or True else 1)
