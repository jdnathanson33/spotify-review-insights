"""Stage 3 — Verify (model role `verify`, independent of enrichment).

Code draws a declared, seeded random sample of completed distinct texts. The
verifier sees ONLY the original text and its own rubric (different prompt),
never the first prediction. Code then compares fields and writes a
disagreement report. Planted-error tests reuse these verifier labels against a
deliberately corrupted test copy (see evals/).
"""

import hashlib
import json
from collections import Counter

from .common import VERIFY_MODEL, VERIFY_PROMPT_VERSION, SMALL_SETTINGS, LABEL_CONFIG, prompt, write_json
from .enrich import build_user, parse_json
from .labels import TOPICS, INTENTS
from .llm import CallFailed
from .state import now

SYSTEM = prompt(VERIFY_PROMPT_VERSION)
VERIFY_CONFIG = f"{VERIFY_MODEL}+effort-low+nothink+{VERIFY_PROMPT_VERSION}"
SEED = "verify-sample-v1"


def sample(st, input_sha, fraction, minimum, maximum):
    rows = st.db.execute("""SELECT DISTINCT r.tkey FROM input_members m JOIN reviews r USING(review_id)
        JOIN results x ON x.tkey=r.tkey AND x.config_family=? AND x.status='completed' WHERE m.input_sha=?""",
                         (LABEL_CONFIG, input_sha)).fetchall()
    keys = sorted((r[0] for r in rows), key=lambda k: hashlib.sha256((SEED + k).encode()).hexdigest())
    n = min(len(keys), maximum, max(minimum, round(fraction * len(keys))))
    return keys[:n], len(keys)


def run_verify(st, caller, input_sha, fraction=0.01, minimum=20, maximum=5000, batch=25, workers=4, log=print):
    keys, population = sample(st, input_sha, fraction, minimum, maximum)
    done = {r[0] for r in st.db.execute("SELECT tkey FROM verify WHERE verify_config=?", (VERIFY_CONFIG,))}
    todo = [k for k in keys if k not in done]
    log(f"verify: sample {len(keys)} of {population} completed distinct texts ({len(todo)} not yet verified)")
    from concurrent.futures import ThreadPoolExecutor
    chunks = [todo[i:i + batch] for i in range(0, len(todo), batch)]

    infos = {k: st.db.execute("SELECT t.text, x.sent_review_id FROM texts t JOIN results x ON x.tkey=t.tkey AND x.config_family=? WHERE t.tkey=?",
                              (LABEL_CONFIG, k)).fetchone() for k in todo}  # read in the main thread; workers never touch SQLite

    def work(chunk):
        info = [infos[k] for k in chunk]
        try:
            text, rec = caller.call(role="verify", model=VERIFY_MODEL, system=SYSTEM,
                                    user=build_user([(j + 1, inf[0]) for j, inf in enumerate(info)]),
                                    max_tokens=60 * len(chunk) + 300, settings=SMALL_SETTINGS, est_output_tokens=35 * len(chunk) + 50,
                                    review_ids=[inf[1] for inf in info], prompt_version=VERIFY_PROMPT_VERSION)
            arr = {int(o["i"]): o for o in parse_json(text).get("r", []) if isinstance(o, dict) and "i" in o}
            return chunk, rec, arr
        except (CallFailed, ValueError) as e:
            log(f"  verify batch failed: {e}")
            return chunk, None, {}
    calls = 0
    with ThreadPoolExecutor(workers) as pool:
        for chunk, rec, arr in pool.map(work, chunks):
            if not rec:
                continue
            calls += 1
            with st.tx() as db:
                for j, k in enumerate(chunk, 1):
                    o = arr.get(j)
                    if not o or o.get("t") not in TOPICS or o.get("n") not in INTENTS:
                        continue
                    lab = {"topic": o["t"], "intent": o["n"], "severity": int(o["v"]) if str(o.get("v", "")).isdigit() else None,
                           "sentiment": o.get("m")}
                    db.execute("INSERT OR REPLACE INTO verify VALUES(?,?,?,?,?,?)",
                               (k, VERIFY_CONFIG, json.dumps(lab), rec["request_id"], caller.run_id, now()))
    report = compare(st, keys)
    report.update(sample_declared={"fraction": fraction, "min": minimum, "max": maximum, "seed": SEED,
                                   "population_distinct_texts": population, "sampled": len(keys)}, new_calls=calls)
    write_json(st.out / "verify_report.json", report)
    return report


def compare(st, keys, corrupt=None):
    """Compare first-pass labels with verifier labels. `corrupt` optionally overrides first-pass labels (planted errors)."""
    agree, n, dis = Counter(), 0, []
    sev_abs = 0
    for k in keys:
        row = st.db.execute("""SELECT x.labels, v.labels, x.sent_review_id FROM results x JOIN verify v ON v.tkey=x.tkey AND v.verify_config=?
                               WHERE x.tkey=? AND x.config_family=?""", (VERIFY_CONFIG, k, LABEL_CONFIG)).fetchone()
        if not row:
            continue
        first, ver, rid = json.loads(row[0]), json.loads(row[1]), row[2]
        if corrupt and rid in corrupt:
            first = {**first, **corrupt[rid]}
        n += 1
        fields = {}
        for f in ("topic", "intent", "severity"):
            ok = first[f] == ver.get(f)
            agree[f] += ok
            fields[f] = ok
        if isinstance(ver.get("severity"), int):
            sev_abs += abs(first["severity"] - ver["severity"])
        if not all(fields.values()):
            dis.append({"review_id": rid, "enrich": {f: first[f] for f in ("topic", "intent", "severity")},
                        "verify": {f: ver.get(f) for f in ("topic", "intent", "severity")},
                        "fields_disagree": [f for f, ok in fields.items() if not ok]})
    return {"compared": n, "agreement": {f: round(agree[f] / n, 4) if n else None for f in ("topic", "intent", "severity")},
            "severity_mae": round(sev_abs / n, 4) if n else None, "disagreements": len(dis), "disagreement_rows": dis}
