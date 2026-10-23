"""Shared helpers: exact CSV reading, source row hashing, atomic writes, config."""

import csv
import hashlib
import json
import os
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIELDS = ("review_id", "review_text", "review_rating", "review_likes", "app_version", "review_timestamp")

# ---- model / prompt configuration (versioned; changing any of these invalidates the result cache)
ENRICH_MODEL = "claude-haiku-5-5"
VERIFY_MODEL = "claude-haiku-5-5"
GROUP_MODEL = "claude-haiku-5-5"
MEMO_MODEL = "claude-sonnet-5-5"
FALLBACK_MODEL = "claude-sonnet-5-5"
SMALL_SETTINGS = {"effort": "low", "thinking": "disabled"}
MEMO_SETTINGS = {"effort": "low", "thinking": "adaptive"}  # Sonnet 5.5 cannot disable thinking
ENRICH_PROMPT_VERSION = "enrich-v2"
VERIFY_PROMPT_VERSION = "verify-v1"
GROUP_PROMPT_VERSION = "group-v1"
MEMO_PROMPT_VERSION = "memo-v2"
SCHEMA_VERSION = "schema-v1"
ENTITY_VERSION = "entities-v1"
LABEL_CONFIG = f"{ENRICH_MODEL}+effort-low+nothink+{ENRICH_PROMPT_VERSION}+{SCHEMA_VERSION}+{ENTITY_VERSION}"
FALLBACK_LABEL_CONFIG = f"{FALLBACK_MODEL}+effort-low+adaptive+{ENRICH_PROMPT_VERSION}-single+{SCHEMA_VERSION}+{ENTITY_VERSION}"
MAX_BATCH = 50
SHORT_QUOTE_CHARS = 200  # reviews this short may use their full text as the evidence quote


def canonical(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True, allow_nan=False)


def row_sha(row):
    """Identical to check_submission.row_sha: SHA-256 of compact JSON of the six source strings."""
    return hashlib.sha256(canonical([row[k] for k in FIELDS]).encode("utf-8")).hexdigest()


def text_key(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def file_sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def csv_rows(path):
    """Same parser settings as the course checker: utf-8-sig, strict quoting, multiline-safe."""
    with Path(path).open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f, strict=True)
        missing = set(FIELDS) - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"Input CSV missing fields: {sorted(missing)}")
        for row in reader:
            if None in row or any(v is None for v in row.values()):
                raise ValueError("Malformed CSV row")
            yield {k: row[k] for k in FIELDS}


def atomic_write(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


def write_json(path, value):
    atomic_write(path, json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def load_env():
    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")
    except ImportError:
        pass


def prompt(name):
    return (ROOT / "prompts" / f"{name}.md").read_text(encoding="utf-8")
