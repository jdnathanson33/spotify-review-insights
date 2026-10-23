"""Model-call layer: bounded retries, per-attempt call log, shared spend ledger.

Every attempt (success or failure) is appended to `<state>/calls.jsonl` with
role, phase, model, settings, review IDs, usage, cost and latency. Costs are
computed from provider-reported usage and the editable rates in cost/rates.csv.
"""

import csv
import json
import os
import random
import threading
import time
import uuid
from pathlib import Path

from .common import ROOT

RATES_PATH = ROOT / "cost" / "rates.csv"


class BudgetExceeded(Exception):
    pass


class CallFailed(Exception):
    def __init__(self, msg, record):
        super().__init__(msg)
        self.record = record


def load_rates(path=RATES_PATH):
    """{model: {item: usd_per_unit}} where unit is one token (rates.csv stores per-million)."""
    rates = {}
    with open(path, encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            per = float(row["price_usd"]) / float(row["unit_size"])
            rates.setdefault(row["model"], {})[row["item"]] = per
    return rates


def cost_of(usage, model, rates):
    r = rates[model]  # model may be a tier key such as "claude-haiku-5-5@batch"
    return (usage.get("input_tokens", 0) * r["input"]
            + usage.get("cache_creation_input_tokens", 0) * r["cache_write_5m"]
            + usage.get("cache_read_input_tokens", 0) * r["cache_read"]
            + usage.get("output_tokens", 0) * r["output"])


class Ledger:
    """Shared spend ledger. Reserve worst-case cost before dispatch; settle with actual."""

    def __init__(self, cap_usd, spent_usd=0.0):
        self.cap = cap_usd
        self.spent = spent_usd
        self.reserved = 0.0
        self.lock = threading.Lock()

    def reserve(self, amount):
        with self.lock:
            if self.spent + self.reserved + amount > self.cap:
                raise BudgetExceeded(f"spent {self.spent:.4f} + reserved {self.reserved:.4f} + next {amount:.4f} > cap {self.cap:.2f}")
            self.reserved += amount
            return amount

    def settle(self, reserved, actual):
        with self.lock:
            self.reserved -= reserved
            self.spent += actual

    def snapshot(self):
        with self.lock:
            return {"cap_usd": self.cap, "spent_usd": round(self.spent, 6), "reserved_usd": round(self.reserved, 6)}


class CallLog:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()

    def append(self, rec):
        line = json.dumps(rec, ensure_ascii=False) + "\n"
        with self.lock, self.path.open("a", encoding="utf-8") as f:
            f.write(line)
            f.flush()
            os.fsync(f.fileno())

    def records(self):
        if not self.path.exists():
            return []
        return [json.loads(l) for l in self.path.read_text(encoding="utf-8").splitlines() if l.strip()]

    def total_cost(self):
        return sum(r.get("cost_usd") or 0 for r in self.records())


def make_client(mock=False):
    if mock or os.environ.get("PIPELINE_MOCK") == "1":
        from .mock import MockClient
        return MockClient()
    import anthropic
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise SystemExit("ANTHROPIC_API_KEY is not set (put it in .env). Offline commands need no key.")
    return anthropic.Anthropic(api_key=key, max_retries=0, timeout=120.0)


RETRYABLE_STATUS = {408, 409, 429, 500, 502, 503, 504, 529}


class RateLimiter:
    """Shared across workers: reserve estimated output tokens in a 60 s sliding window before dispatch."""

    def __init__(self, otpm=70000, rpm=400):
        self.otpm, self.rpm = otpm, rpm
        self.events = []  # (t, tokens)
        self.cv = threading.Condition()

    def acquire(self, tokens):
        tokens = min(tokens, self.otpm)
        with self.cv:
            while True:
                now = time.monotonic()
                self.events = [(t, k) for t, k in self.events if now - t < 60]
                used = sum(k for _, k in self.events)
                if used + tokens <= self.otpm and len(self.events) < self.rpm:
                    self.events.append((now, tokens))
                    return
                wait = 60 - (now - self.events[0][0]) + 0.05 if self.events else 0.5
                self.cv.wait(timeout=max(0.05, min(wait, 5)))


class Caller:
    def __init__(self, client, log, ledger, rates, run_id, max_attempts=6, base_delay=4.0, limiter=None):
        self.client, self.log, self.ledger, self.rates = client, log, ledger, rates
        self.limiter = limiter
        self.run_id, self.max_attempts, self.base_delay = run_id, max_attempts, base_delay
        self.is_mock = type(client).__name__ == "MockClient"

    def call(self, *, role, model, system, user, max_tokens, settings, review_ids, phase=None,
             label_config=None, prompt_version=None, est_input_tokens=None, meta=None, est_output_tokens=None):
        """One logical request with bounded retries. Returns (text, final_record)."""
        est_in = est_input_tokens or (len(system) + len(user)) // 3
        worst = est_in * self.rates[model]["input"] + max_tokens * self.rates[model]["output"]
        held = self.ledger.reserve(worst)  # raises BudgetExceeded before any dispatch
        logical_id = "L-" + uuid.uuid4().hex[:12]
        try:
            for attempt in range(1, self.max_attempts + 1):
                rec = {"run_id": self.run_id, "logical_id": logical_id, "attempt": attempt, "role": role, "phase": phase,
                       "model": model, "settings": settings, "label_config": label_config,
                       "prompt_version": prompt_version, "review_ids": list(review_ids), "max_tokens": max_tokens,
                       "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "mock": self.is_mock, **(meta or {})}
                if self.limiter and not self.is_mock:
                    self.limiter.acquire(est_output_tokens or max_tokens)
                t0 = time.monotonic()
                try:
                    kwargs = dict(model=model, max_tokens=max_tokens,
                                  system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                                  messages=[{"role": "user", "content": user}],
                                  extra_body={"output_config": {"effort": settings["effort"]},
                                              **({"thinking": {"type": "disabled"}} if settings.get("thinking") == "disabled" else {})})
                    resp = self.client.messages.create(**kwargs)
                    u = resp.usage
                    usage = {"input_tokens": u.input_tokens or 0, "output_tokens": u.output_tokens or 0,
                             "cache_creation_input_tokens": getattr(u, "cache_creation_input_tokens", 0) or 0,
                             "cache_read_input_tokens": getattr(u, "cache_read_input_tokens", 0) or 0}
                    text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
                    rec.update(outcome="succeeded", request_id=resp.id, stop_reason=getattr(resp, "stop_reason", None),
                               **usage, cost_usd=round(cost_of(usage, model, self.rates), 8),
                               latency_s=round(time.monotonic() - t0, 3))
                    self.log.append(rec)
                    self.ledger.settle(0, rec["cost_usd"])
                    return text, rec
                except BudgetExceeded:
                    raise
                except Exception as e:  # provider/network errors
                    status = getattr(e, "status_code", None)
                    name = type(e).__name__
                    retryable = status in RETRYABLE_STATUS or name in ("APIConnectionError", "APITimeoutError", "TransientMockError")
                    rec.update(outcome="failed", request_id=f"failed-{logical_id}-a{attempt}",
                               error=f"{name}: {str(e)[:200]}", http_status=status, retryable=retryable,
                               input_tokens=0, output_tokens=0, cost_usd=0.0,
                               charge_unknown=name == "APITimeoutError", latency_s=round(time.monotonic() - t0, 3))
                    self.log.append(rec)
                    if not retryable or attempt == self.max_attempts:
                        raise CallFailed(rec["error"], rec)
                    delay = self.base_delay * (2 ** (attempt - 1)) * (0.5 + random.random())
                    retry_after = getattr(getattr(e, "response", None), "headers", {}) or {}
                    try:
                        delay = max(delay, float(retry_after.get("retry-after", 0)))
                    except (TypeError, ValueError):
                        pass
                    if status == 429:
                        delay = max(delay, 15.0)
                    time.sleep(min(delay, 90) if not self.is_mock else 0.01)
        finally:
            self.ledger.settle(held, 0)
