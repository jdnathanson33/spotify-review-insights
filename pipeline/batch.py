"""Enrichment via the Message Batches API (same prompt, model, schema and validation as the sync path).

  python -m pipeline batch-submit  --input CSV --state state/main --phase resume [--max-requests N] [--budget-usd 40]
  python -m pipeline batch-collect --state state/main [--batch-id ID] [--wait]

Each batch request holds <=50 distinct texts (the same bounded unit as a sync call).
Submission reserves worst-case cost against the budget before anything is sent;
texts in flight are excluded from the pending queue. Collection logs every
request outcome to calls.jsonl with real usage priced at batch rates, validates
every returned ID/label/quote, saves completed records atomically, sends invalid
items through the single sync retry with validation feedback, and releases
errored/expired requests back to pending. Batches are asynchronous, so an
interrupted collector simply resumes on its next run.
"""

import json
import time
import uuid

from .common import ENRICH_MODEL, ENRICH_PROMPT_VERSION, LABEL_CONFIG, MAX_BATCH, SMALL_SETTINGS
from .enrich import SYSTEM, Enricher, build_user
from .state import now

RATE_KEY = ENRICH_MODEL + "@batch"


def _params(items, max_tokens):
    return {"model": ENRICH_MODEL, "max_tokens": max_tokens,
            "system": [{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}],
            "messages": [{"role": "user", "content": build_user([(i + 1, it[1]) for i, it in enumerate(items)])}],
            "output_config": {"effort": SMALL_SETTINGS["effort"]}, "thinking": {"type": "disabled"}}


def submit(st, caller, input_sha, phase, max_requests=None, chunk=5000, log=print):
    e = Enricher(st, caller, input_sha, phase=phase)
    e.quarantine_empties()
    todo = e.pending()
    groups = [todo[i:i + MAX_BATCH] for i in range(0, len(todo), MAX_BATCH)]
    if max_requests:
        groups = groups[:max_requests]
    r = caller.rates[RATE_KEY]
    worst = sum((len(SYSTEM) // 3 + sum(len(t) for _, t, _ in g) // 2) * r["input"] + (400 + 110 * len(g)) * r["output"] for g in groups)
    caller.ledger.reserve(worst)  # BudgetExceeded stops submission before anything is sent
    log(f"batch-submit: {len(todo)} pending texts -> {len(groups)} requests; worst-case reserve ${worst:.2f} "
        f"(spent so far ${caller.ledger.spent:.4f}, cap ${caller.ledger.cap:.2f})")
    batch_ids = []
    for c0 in range(0, len(groups), chunk):
        reqs, rows = [], []
        for g in groups[c0:c0 + chunk]:
            cid = "e" + uuid.uuid4().hex[:20]
            mt = min(8000, 400 + 110 * len(g))
            reqs.append({"custom_id": cid, "params": _params(g, mt)})
            rows.append((cid, g, mt))
        b = caller.client.messages.batches.create(requests=reqs)
        with st.tx() as db:
            for cid, g, mt in rows:
                db.execute("INSERT INTO batch_requests VALUES(?,?,?,?,?,?,0)",
                           (cid, b.id, json.dumps([[tk, rid] for tk, _, rid in g]), phase, caller.run_id, mt))
                for tk, _, rid in g:
                    db.execute("INSERT INTO batch_items VALUES(?,?,?,?,?)", (tk, b.id, cid, rid, now()))
        batch_ids.append(b.id)
        log(f"  submitted {b.id} with {len(reqs)} requests")
    return {"batch_ids": batch_ids, "requests": len(groups), "texts": sum(len(g) for g in groups), "worst_case_reserved_usd": round(worst, 4)}


def collect(st, caller, batch_id=None, wait=False, poll_s=60, log=print):
    ids = [batch_id] if batch_id else [r[0] for r in st.db.execute("SELECT DISTINCT batch_id FROM batch_requests WHERE collected=0")]
    summary = {}
    for bid in ids:
        while True:
            b = caller.client.messages.batches.retrieve(bid)
            log(f"batch {bid}: {b.processing_status} {b.request_counts}")
            if b.processing_status == "ended" or not wait:
                break
            time.sleep(poll_s)
        if b.processing_status != "ended":
            summary[bid] = {"status": b.processing_status}
            continue
        texts = {}
        stats = {"succeeded": 0, "errored": 0, "expired": 0, "canceled": 0, "completed": 0, "quarantined": 0, "released": 0}
        for res in caller.client.messages.batches.results(bid):
            row = st.db.execute("SELECT items, phase, collected FROM batch_requests WHERE custom_id=?", (res.custom_id,)).fetchone()
            if not row or row[2]:
                continue  # already collected (idempotent re-collection)
            pairs, phase = json.loads(row[0]), row[1]
            items = []
            for tk, rid in pairs:
                if tk not in texts:
                    texts[tk] = st.db.execute("SELECT text FROM texts WHERE tkey=?", (tk,)).fetchone()[0]
                items.append((tk, texts[tk], rid))
            rec = {"run_id": caller.run_id, "logical_id": "B-" + res.custom_id, "attempt": 1, "role": "enrich", "phase": phase,
                   "model": ENRICH_MODEL, "rate_key": RATE_KEY, "tier": "batch", "batch_id": bid, "custom_id": res.custom_id,
                   "settings": SMALL_SETTINGS, "label_config": LABEL_CONFIG, "prompt_version": ENRICH_PROMPT_VERSION,
                   "review_ids": [it[2] for it in items], "n_items": len(items), "started_at": now(), "mock": False}
            t = res.result.type
            stats[t] = stats.get(t, 0) + 1
            e = Enricher(st, caller, None, phase=phase)
            if t == "succeeded":
                msg = res.result.message
                u = msg.usage
                usage = {"input_tokens": u.input_tokens or 0, "output_tokens": u.output_tokens or 0,
                         "cache_creation_input_tokens": getattr(u, "cache_creation_input_tokens", 0) or 0,
                         "cache_read_input_tokens": getattr(u, "cache_read_input_tokens", 0) or 0}
                from .llm import cost_of
                rec.update(outcome="succeeded", request_id=msg.id, stop_reason=msg.stop_reason, **usage,
                           cost_usd=round(cost_of(usage, RATE_KEY, caller.rates), 8))
                caller.log.append(rec)
                caller.ledger.settle(0, rec["cost_usd"])
                text = "".join(blk.text for blk in msg.content if getattr(blk, "type", "") == "text")
                good, bad = e._validate(items, text)
                e.save({it[0]: ("completed", good[it[0]], LABEL_CONFIG, it[2], rec["request_id"], 1, None) for it in items if it[0] in good})
                stats["completed"] += len(good)
                with st.tx() as db:  # invalid items: keep in flight as 'deferred' for the parallel retry pass
                    for it in items:
                        if it[0] in bad:
                            db.execute("UPDATE batch_items SET batch_id='deferred', custom_id=? WHERE tkey=?",
                                       (json.dumps({"error": bad[it[0]], "phase": phase}), it[0]))
                stats["deferred_retry"] = stats.get("deferred_retry", 0) + len(bad)
            else:
                rec.update(outcome="failed", request_id=f"batch-{t}-{res.custom_id}", error=t, input_tokens=0, output_tokens=0, cost_usd=0.0)
                caller.log.append(rec)
            with st.tx() as db:  # release in-flight marks (except deferred retries); unsaved items become pending again
                db.execute("UPDATE batch_requests SET collected=1 WHERE custom_id=?", (res.custom_id,))
                db.executemany("DELETE FROM batch_items WHERE tkey=? AND batch_id<>'deferred'", [(it[0],) for it in items])
            stats["released"] += t != "succeeded"
        summary[bid] = stats
        log(f"collected {bid}: {stats}")
    summary["deferred_retries"] = retry_deferred(st, caller, log=log)
    return summary


def retry_deferred(st, caller, workers=6, log=print):
    """Single retry, with validation feedback, for items whose batch output was invalid; run in parallel sync calls."""
    from concurrent.futures import ThreadPoolExecutor
    rows = st.db.execute("""SELECT b.tkey, b.sent_review_id, b.custom_id, t.text FROM batch_items b JOIN texts t USING(tkey)
                            WHERE b.batch_id='deferred'""").fetchall()
    if not rows:
        return {"items": 0}
    by_phase = {}
    for tk, rid, info, text in rows:
        d = json.loads(info)
        by_phase.setdefault(d["phase"], []).append(((tk, text, rid), d["error"]))
    stats = {"items": len(rows), "completed": 0, "quarantined": 0, "left_pending": 0}
    for phase, lst in by_phase.items():
        e = Enricher(st, caller, None, phase=phase)
        chunks = [lst[i:i + MAX_BATCH] for i in range(0, len(lst), MAX_BATCH)]

        def work(chunk):
            items = [c[0] for c in chunk]
            errors = {c[0][0]: c[1] for c in chunk}
            try:
                text, rec = e._call(items, "deferred-retry", retry=True, errors=errors)
            except Exception as ex:  # transient failure: leave pending
                return items, None, {}, {}, str(ex)
            good, bad = e._validate(items, text)
            return items, rec, good, bad, None
        log(f"retry_deferred: {len(lst)} items in {len(chunks)} calls ({workers} workers)")
        with ThreadPoolExecutor(workers) as pool:
            for items, rec, good, bad, err in pool.map(work, chunks):
                out = {}
                for it in items:
                    if rec and it[0] in good:
                        out[it[0]] = ("completed", good[it[0]], LABEL_CONFIG, it[2], rec["request_id"], 2, None)
                    elif rec:
                        out[it[0]] = ("quarantined", None, LABEL_CONFIG, it[2], None, 2,
                                      "invalid_model_output_after_retry:" + bad.get(it[0], "?"))
                e.save(out)
                stats["completed"] += sum(v[0] == "completed" for v in out.values())
                stats["quarantined"] += sum(v[0] == "quarantined" for v in out.values())
                stats["left_pending"] += 0 if rec else len(items)
                with st.tx() as db:
                    db.executemany("DELETE FROM batch_items WHERE tkey=?", [(it[0],) for it in items])
    log(f"retry_deferred: {stats}")
    return stats
