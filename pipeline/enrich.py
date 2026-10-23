"""Stage 2 — Classify (model role `enrich`, code-validated).

Code decides WHAT to send (pending distinct texts in scope, <=50 per request),
validates EVERY returned item (ref, labels, exact quote), retries invalid items
once, routes still-invalid items to a capped single-review fallback, and
quarantines what remains. Each batch's results are saved in one transaction.
Exact-text reuse: results are keyed by text hash + config, so duplicate texts
(and reruns) cost no new calls.
"""

import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, FIRST_COMPLETED, wait

from . import entities
from .common import (ENRICH_MODEL, FALLBACK_LABEL_CONFIG, FALLBACK_MODEL, LABEL_CONFIG, MAX_BATCH,
                     SHORT_QUOTE_CHARS, SMALL_SETTINGS, MEMO_SETTINGS, ENRICH_PROMPT_VERSION, prompt)
from .labels import SUBTOPICS, subtopic_table, validate_labels
from .llm import BudgetExceeded, CallFailed
from .state import now

SYSTEM = prompt(ENRICH_PROMPT_VERSION).replace("{SUBTOPICS}", subtopic_table())
EMPTY_REASON = "empty_review_text"


def parse_json(text):
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t)
    start = t.find("{")
    if start < 0:
        raise ValueError("no JSON object")
    return json.loads(t[start:t.rfind("}") + 1])


def resolve_quote(q, text, intent=None):
    """Return an exact source substring or None. '*' (or empty) means full text for short reviews."""
    if not isinstance(q, str):
        return None
    if q.strip() in ("", "*"):
        return text if (len(text) <= SHORT_QUOTE_CHARS or intent == "unclear") else None
    if q in text:
        return q
    q2 = q.strip().strip('"“”\'').strip().rstrip(".…").strip()
    if q2 and q2 in text:
        return q2
    if q2:
        i = text.lower().find(q2.lower())
        if i >= 0:
            return text[i:i + len(q2)]
    return None


def to_labels(item, text):
    """Map one compact model item to internal labels; returns (labels, error)."""
    def as_bool(v):
        if isinstance(v, bool):
            return v
        if isinstance(v, str) and v.lower() in ("true", "false"):
            return v.lower() == "true"
        return v
    try:
        sev = item.get("v")
        sev = int(sev) if isinstance(sev, (int, float, str)) and str(sev).strip().lstrip("-").isdigit() else sev
        sent = item.get("m")
        sent = float(sent) if isinstance(sent, (int, float, str)) and re.fullmatch(r"-?\d+(\.\d+)?", str(sent).strip()) else sent
    except (TypeError, ValueError):
        return None, "bad_number"
    lab = {"topic": item.get("t"), "subtopic": item.get("s"), "intent": item.get("n"), "severity": sev,
           "sentiment": round(sent, 2) if isinstance(sent, float) else sent, "needs_review": as_bool(item.get("x")),
           "evidence_quote": resolve_quote(item.get("q"), text, item.get("n")), "entities": entities.extract(text)}
    if isinstance(lab["subtopic"], str) and "." in lab["subtopic"]:
        lab["subtopic"] = lab["subtopic"].split(".", 1)[1]  # model may echo the "topic.subtopic" code from the table
    if lab["topic"] in SUBTOPICS and lab["subtopic"] not in SUBTOPICS[lab["topic"]]:
        lab["subtopic"] = "other"  # keep the shared topic; unknown custom subtopic collapses to 'other'
    if lab["evidence_quote"] is None:
        return None, "quote_not_exact_substring"
    err = validate_labels(lab)
    return (None, err) if err else (lab, None)


def build_user(items):
    lines = [json.dumps({"i": ref, "text": text}, ensure_ascii=False) for ref, text in items]
    return f"Label these {len(items)} reviews.\n" + "\n".join(lines)


class Enricher:
    def __init__(self, st, caller, input_sha, *, workers=1, phase="initial", max_batches=None,
                 fallback_fraction=0.0, stop_event=None, max_consecutive_failures=5, out_tokens_per_item=110,
                 log=print):
        self.st, self.caller, self.input_sha = st, caller, input_sha
        self.workers, self.phase, self.max_batches = workers, phase, max_batches
        self.fallback_fraction = fallback_fraction
        self.stop_event = stop_event or threading.Event()
        self.max_consecutive_failures = max_consecutive_failures
        self.out_tokens_per_item = out_tokens_per_item
        self.log = log
        self.stats = {"batches": 0, "items_sent": 0, "completed": 0, "retried_items": 0, "fallback_items": 0,
                      "quarantined": 0, "failed_batches": 0, "stopped_reason": None}
        self.lock = threading.Lock()

    # ---------- planning (code)
    def quarantine_empties(self):
        rows = self.st.db.execute("""SELECT DISTINCT r.tkey, r.review_id FROM input_members m JOIN reviews r USING(review_id)
            WHERE m.input_sha=? AND r.empty=1""", (self.input_sha,)).fetchall()
        with self.st.tx() as db:
            for tk, rid in rows:
                db.execute("""INSERT OR IGNORE INTO results VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                           (tk, LABEL_CONFIG, "quarantined", None, None, rid, None, 0, EMPTY_REASON, self.phase,
                            self.caller.run_id, now()))
        return len(rows)

    def pending(self):
        """Distinct pending texts in scope, in source order; the first in-scope review is the one sent."""
        q = """SELECT r.tkey, r.review_id, t.text
               FROM input_members m JOIN reviews r USING(review_id) JOIN texts t ON t.tkey=r.tkey
               LEFT JOIN results x ON x.tkey=r.tkey AND x.config_family=?
               WHERE m.input_sha=? AND r.empty=0 AND x.tkey IS NULL
                 AND r.tkey NOT IN (SELECT tkey FROM batch_items) ORDER BY r.ord"""
        out, seen = [], set()
        for tk, rid, text in self.st.db.execute(q, (LABEL_CONFIG, self.input_sha)):
            if tk not in seen:
                seen.add(tk)
                out.append((tk, text, rid))
        return out

    # ---------- one batch (runs in a worker thread; returns results for the main thread to save)
    def _call(self, items, label, retry=False, errors=None):
        refs = [(i + 1, it[1]) for i, it in enumerate(items)]
        user = build_user(refs)
        if errors:  # the single retry tells the model exactly what failed validation
            notes = []
            for i, it in enumerate(items, 1):
                e = errors.get(it[0], "")
                hint = (f" (this review is {len(it[1])} characters, so '*' is not allowed unless intent is unclear; copy an exact substring)"
                        if e.startswith("quote") else "")
                notes.append(f"ref {i}: {e}{hint}")
            user += "\n\nYour previous answer for these reviews failed validation:\n" + "\n".join(notes) + "\nReturn corrected JSON for all refs."
        text, rec = self.caller.call(
            role="enrich", model=ENRICH_MODEL, system=SYSTEM, user=user,
            max_tokens=min(8000, 400 + self.out_tokens_per_item * len(items)), settings=SMALL_SETTINGS,
            review_ids=[it[2] for it in items], phase=self.phase, label_config=LABEL_CONFIG,
            prompt_version=ENRICH_PROMPT_VERSION, est_output_tokens=75 * len(items) + 100,
            meta={"batch": label, "retry_of_invalid": retry, "n_items": len(items)})
        return text, rec

    def _validate(self, items, text):
        good, bad = {}, {}
        try:
            data = parse_json(text)
            arr = data.get("r") if isinstance(data, dict) else None
            if not isinstance(arr, list):
                raise ValueError("missing r[]")
        except ValueError as e:
            return {}, {it[0]: f"unparseable_response:{str(e)[:60]}" for it in items}
        by_ref = {}
        for obj in arr:
            if isinstance(obj, dict) and isinstance(obj.get("i"), (int, str)):
                try:
                    ref = int(obj["i"])
                except ValueError:
                    continue
                by_ref.setdefault(ref, []).append(obj)
        for ref, it in enumerate(items, 1):
            got = by_ref.get(ref, [])
            if len(got) != 1:
                bad[it[0]] = "missing_ref" if not got else "duplicate_ref"
                continue
            lab, err = to_labels(got[0], it[1])
            if err:
                bad[it[0]] = err
            else:
                good[it[0]] = lab
        extra = set(by_ref) - set(range(1, len(items) + 1))
        if extra:
            self.log(f"  ignored unknown refs {sorted(extra)[:5]}")
        return good, bad

    def run_batch(self, items, label):
        """items: list of (tkey, text, sent_review_id). Returns dict tkey -> result tuple."""
        text, rec = self._call(items, label)
        return self.complete_with_retry(items, text, rec, label)

    def complete_with_retry(self, items, text, rec, label):
        """Validate a response (sync or Batch API); retry invalid items once with feedback; quarantine the rest."""
        out = {}
        good, bad = self._validate(items, text)
        for it in items:
            if it[0] in good:
                out[it[0]] = ("completed", good[it[0]], LABEL_CONFIG, it[2], rec["request_id"], 1, None)
        if bad:  # retry invalid items once, together, same model/settings
            retry_items = [it for it in items if it[0] in bad]
            with self.lock:
                self.stats["retried_items"] += len(retry_items)
            try:
                text2, rec2 = self._call(retry_items, label + "-retry", retry=True, errors=bad)
                good2, bad2 = self._validate(retry_items, text2)
            except CallFailed:
                # transient provider failure on the retry: leave these items pending (not quarantined)
                return out
            for it in retry_items:
                if it[0] in good2:
                    out[it[0]] = ("completed", good2[it[0]], LABEL_CONFIG, it[2], rec2["request_id"], 2, None)
                else:
                    out[it[0]] = self._fallback_or_quarantine(it, bad2.get(it[0], bad[it[0]]))
        return out

    def _fallback_or_quarantine(self, it, reason):
        with self.lock:
            allowed = self.fallback_fraction > 0 and self.stats["fallback_items"] < int(self.fallback_fraction * max(self.stats["items_sent"], 200))
            if allowed:
                self.stats["fallback_items"] += 1
        if allowed:
            try:
                text, rec = self.caller.call(
                    role="enrich", model=FALLBACK_MODEL, system=SYSTEM, user=build_user([(1, it[1])]),
                    max_tokens=2000, settings=MEMO_SETTINGS, review_ids=[it[2]], phase=self.phase,
                    label_config=FALLBACK_LABEL_CONFIG, prompt_version=ENRICH_PROMPT_VERSION + "-single",
                    meta={"fallback_for": reason, "n_items": 1})
                good, bad = self._validate([it], text)
                if it[0] in good:
                    return ("completed", good[it[0]], FALLBACK_LABEL_CONFIG, it[2], rec["request_id"], 3, None)
                reason = "fallback_invalid:" + bad.get(it[0], "?")
            except CallFailed:
                reason = "fallback_call_failed"
        return ("quarantined", None, LABEL_CONFIG, it[2], None, 3, "invalid_model_output_after_retry:" + reason)

    # ---------- orchestration loop (main thread owns all DB writes)
    def save(self, results):
        with self.st.tx() as db:
            for tk, (status, lab, cfg, rid, req, attempts, reason) in results.items():
                db.execute("INSERT OR REPLACE INTO results VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                           (tk, LABEL_CONFIG, status, json.dumps(lab, ensure_ascii=False) if lab else None, cfg, rid, req,
                            attempts, reason, self.phase, self.caller.run_id, now()))
        with self.lock:
            for r in results.values():
                self.stats["completed" if r[0] == "completed" else "quarantined"] += 1

    def requeue_quarantined(self):
        """Explicit operator action: give invalid-output quarantines one more full cycle (logged in run summary)."""
        with self.st.tx() as db:
            n = db.execute("DELETE FROM results WHERE config_family=? AND status='quarantined' AND reason LIKE 'invalid_model_output%'",
                           (LABEL_CONFIG,)).rowcount
        self.stats["requeued_quarantined"] = n
        return n

    def run(self):
        n_empty = self.quarantine_empties()
        todo = self.pending()
        self.log(f"enrich: {len(todo)} pending distinct texts in scope; {n_empty} empty-text quarantines; workers={self.workers}")
        batches = [todo[i:i + MAX_BATCH] for i in range(0, len(todo), MAX_BATCH)]
        if self.max_batches is not None:
            batches = batches[: self.max_batches]
        t0, consecutive_fail = time.monotonic(), 0
        with ThreadPoolExecutor(max_workers=self.workers) as pool:
            inflight, it = {}, iter(enumerate(batches))
            def admit():
                while len(inflight) < self.workers and not self.stop_event.is_set():
                    nxt = next(it, None)
                    if nxt is None:
                        return
                    k, b = nxt
                    with self.lock:
                        self.stats["items_sent"] += len(b)
                    inflight[pool.submit(self.run_batch, b, f"b{k}")] = (k, b)
            admit()
            while inflight:
                done, _ = wait(inflight, return_when=FIRST_COMPLETED)
                for fut in done:
                    k, b = inflight.pop(fut)
                    try:
                        res = fut.result()
                        self.save(res)
                        consecutive_fail = 0
                        self.stats["batches"] += 1
                    except BudgetExceeded as e:
                        self.stats["stopped_reason"] = f"budget: {e}"
                        self.stop_event.set()
                    except CallFailed as e:
                        self.stats["failed_batches"] += 1
                        consecutive_fail += 1
                        self.log(f"  batch b{k} failed after retries ({e}); left pending")
                        if consecutive_fail >= self.max_consecutive_failures:
                            self.stats["stopped_reason"] = f"{consecutive_fail} consecutive failed batches"
                            self.stop_event.set()
                    if self.stats["batches"] % 20 == 0 and self.stats["batches"]:
                        el = time.monotonic() - t0
                        self.log(f"  {self.stats['batches']}/{len(batches)} batches, {self.stats['completed']} completed, "
                                 f"{self.stats['completed'] / el:.1f} texts/s, spend {self.caller.ledger.snapshot()['spent_usd']:.4f}")
                admit()
        if self.stop_event.is_set() and not self.stats["stopped_reason"]:
            self.stats["stopped_reason"] = "interrupted"
        self.stats["elapsed_s"] = round(time.monotonic() - t0, 2)
        self.stats["remaining_pending"] = len(self.pending())
        return self.stats
