"""SQLite state store. One transaction per saved batch, so results are atomic and resumable.

Tables
  reviews(review_id PK, ord, source_sha, tkey, empty, rating, likes, app_version, ts)
  texts(tkey PK, text, first_review_id, n_reviews)
  inputs(input_sha PK, path, rows, ingested_at) + input_members(input_sha, review_id)
  results(tkey, config_family PK) -> status, labels, label_config, sent_review_id, request_id, attempts, reason
  verify(tkey, verify_config PK) -> labels, request_id
  artifacts(key PK) -> cached downstream outputs keyed by input+config hash
"""

import json
import sqlite3
import time
from pathlib import Path

SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS reviews(review_id TEXT PRIMARY KEY, ord INTEGER, source_sha TEXT, tkey TEXT, empty INTEGER,
  rating TEXT, likes TEXT, app_version TEXT, ts TEXT);
CREATE INDEX IF NOT EXISTS reviews_tkey ON reviews(tkey);
CREATE TABLE IF NOT EXISTS texts(tkey TEXT PRIMARY KEY, text TEXT, first_review_id TEXT, n_reviews INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS inputs(input_sha TEXT PRIMARY KEY, path TEXT, rows INTEGER, ingested_at TEXT);
CREATE TABLE IF NOT EXISTS input_members(input_sha TEXT, review_id TEXT, PRIMARY KEY(input_sha, review_id));
CREATE TABLE IF NOT EXISTS results(tkey TEXT, config_family TEXT, status TEXT, labels TEXT, label_config TEXT,
  sent_review_id TEXT, request_id TEXT, attempts INTEGER, reason TEXT, phase TEXT, run_id TEXT, saved_at TEXT,
  PRIMARY KEY(tkey, config_family));
CREATE TABLE IF NOT EXISTS verify(tkey TEXT, verify_config TEXT, labels TEXT, request_id TEXT, run_id TEXT, saved_at TEXT,
  PRIMARY KEY(tkey, verify_config));
CREATE TABLE IF NOT EXISTS artifacts(key TEXT PRIMARY KEY, value TEXT, saved_at TEXT);
CREATE TABLE IF NOT EXISTS batch_items(tkey TEXT PRIMARY KEY, batch_id TEXT, custom_id TEXT, sent_review_id TEXT, submitted_at TEXT);
CREATE TABLE IF NOT EXISTS batch_requests(custom_id TEXT PRIMARY KEY, batch_id TEXT, items TEXT, phase TEXT, run_id TEXT, max_tokens INTEGER, collected INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS runs(run_id TEXT PRIMARY KEY, started_at TEXT, args TEXT, summary TEXT);
"""


def now():
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


class State:
    def __init__(self, directory):
        self.dir = Path(directory)
        self.dir.mkdir(parents=True, exist_ok=True)
        (self.dir / "out").mkdir(exist_ok=True)
        self.db = sqlite3.connect(self.dir / "state.sqlite", isolation_level=None, check_same_thread=False)
        self.db.executescript(SCHEMA)

    @property
    def out(self):
        return self.dir / "out"

    def tx(self):
        return _Tx(self.db)

    # ---- scope helpers
    def scope_ids(self, input_sha):
        return [r[0] for r in self.db.execute(
            "SELECT m.review_id FROM input_members m JOIN reviews r USING(review_id) WHERE m.input_sha=? ORDER BY r.ord", (input_sha,))]

    def latest_input(self):
        row = self.db.execute("SELECT input_sha FROM inputs ORDER BY ingested_at DESC, rowid DESC LIMIT 1").fetchone()
        return row[0] if row else None

    def get_artifact(self, key):
        row = self.db.execute("SELECT value FROM artifacts WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def put_artifact(self, key, value):
        self.db.execute("INSERT OR REPLACE INTO artifacts VALUES(?,?,?)", (key, json.dumps(value, ensure_ascii=False), now()))

    def scope_records(self, input_sha, config_family):
        """Yield (review row dict, result row dict or None) for every review in scope, in source order."""
        q = """SELECT r.review_id, r.ord, r.source_sha, r.tkey, r.empty, r.rating, r.likes, r.app_version, r.ts,
                      t.text, x.status, x.labels, x.label_config, x.sent_review_id, x.request_id, x.attempts, x.reason, x.phase
               FROM input_members m JOIN reviews r USING(review_id) JOIN texts t ON t.tkey=r.tkey
               LEFT JOIN results x ON x.tkey=r.tkey AND x.config_family=?
               WHERE m.input_sha=? ORDER BY r.ord"""
        for row in self.db.execute(q, (config_family, input_sha)):
            rev = dict(zip(("review_id", "ord", "source_sha", "tkey", "empty", "rating", "likes", "app_version", "ts", "text"), row[:10]))
            res = None
            if row[10]:
                res = dict(zip(("status", "labels", "label_config", "sent_review_id", "request_id", "attempts", "reason", "phase"), row[10:]))
                res["labels"] = json.loads(res["labels"]) if res["labels"] else None
            yield rev, res


class _Tx:
    def __init__(self, db):
        self.db = db

    def __enter__(self):
        self.db.execute("BEGIN IMMEDIATE")
        return self.db

    def __exit__(self, et, ev, tb):
        self.db.execute("COMMIT" if et is None else "ROLLBACK")
