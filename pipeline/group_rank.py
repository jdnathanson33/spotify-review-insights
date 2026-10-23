"""Stage 4 — Group (code membership + model role `group` for names) and Stage 5 — Rank (code only).

Membership is deterministic: every completed complaint/cancellation record joins
exactly one issue, `topic.subtopic` (one issue per complaint; allow_multi_issue
false). The `group` model only names/summarizes issues from a bounded sample and
flags misfits; its output never changes membership, so ranking needs no model.
"""

import csv
import hashlib
import io
import json
from collections import defaultdict
from decimal import Decimal, ROUND_HALF_UP

from .common import GROUP_MODEL, GROUP_PROMPT_VERSION, LABEL_CONFIG, SMALL_SETTINGS, atomic_write, prompt, write_json
from .enrich import parse_json
from .labels import AREA_OF_TOPIC, COMPLAINT_INTENTS, SUBTOPICS, issue_id
from .llm import CallFailed

SYSTEM = prompt(GROUP_PROMPT_VERSION)
GROUP_CONFIG = f"{GROUP_MODEL}+effort-low+nothink+{GROUP_PROMPT_VERSION}"
SAMPLE_PER_ISSUE = 12


def final_records(st, input_sha):
    """Completed records in scope: list of dicts with review_id and labels (each review counted once)."""
    out = []
    for rev, res in st.scope_records(input_sha, LABEL_CONFIG):
        if res and res["status"] == "completed":
            out.append({"review_id": rev["review_id"], "ts": rev["ts"], "likes": rev["likes"], "rating": rev["rating"],
                        **res["labels"], "label_config": res["label_config"]})
    return out


def membership(records):
    return [(issue_id(r["topic"], r["subtopic"]), r["review_id"]) for r in records if r["intent"] in COMPLAINT_INTENTS]


def mean_string(total, n):
    return str((Decimal(total) / Decimal(n)).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP))


def rank(records, members):
    sev = {r["review_id"]: r["severity"] for r in records}
    by = defaultdict(list)
    for iid, rid in members:
        by[iid].append(sev[rid])
    rows = [{"issue_id": i, "complaint_count": len(v), "severity_sum": sum(v), "mean_severity": mean_string(sum(v), len(v)),
             "priority_score": sum(v)} for i, v in by.items()]
    rows.sort(key=lambda x: (-x["priority_score"], x["issue_id"]))
    for k, r in enumerate(rows, 1):
        r["rank"] = k
    return rows


def write_membership_and_ranking(out_dir, members, ranking):
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["issue_id", "review_id"])
    w.writerows(sorted(members))
    atomic_write(out_dir / "membership.csv", buf.getvalue())
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    cols = ["rank", "issue_id", "complaint_count", "severity_sum", "mean_severity", "priority_score"]
    w.writerow(cols)
    for r in ranking:
        w.writerow([str(r[c]) for c in cols])
    atomic_write(out_dir / "ranking.csv", buf.getvalue())


def aggregates(records, members, ranking, issues):
    rec = {r["review_id"]: r for r in records}
    by = defaultdict(list)
    for iid, rid in members:
        by[iid].append(rec[rid])
    rows = []
    for r in ranking:
        ms = by[r["issue_id"]]
        topic = r["issue_id"].split(".")[0]
        rows.append({**r, "area": AREA_OF_TOPIC[topic], "topic": topic,
                     "name": (issues.get(r["issue_id"]) or {}).get("name", r["issue_id"]),
                     "summary": (issues.get(r["issue_id"]) or {}).get("summary", ""),
                     "cancellation_count": sum(m["intent"] == "cancellation" for m in ms),
                     "severity_4plus": sum(m["severity"] >= 4 for m in ms),
                     "mean_sentiment": round(sum(m["sentiment"] for m in ms) / len(ms), 4)})
    return rows


def run_group(st, caller, input_sha, records, members, log=print):
    """Name issues from bounded deterministic samples. Cached by input+config hash (warm runs make no call)."""
    by = defaultdict(list)
    rec = {r["review_id"]: r for r in records}
    for iid, rid in members:
        by[iid].append(rid)
    packs = []
    for iid in sorted(by):
        ids = sorted(by[iid], key=lambda x: hashlib.sha256((iid + x).encode()).hexdigest())[:SAMPLE_PER_ISSUE]
        t, s = iid.split(".", 1)
        packs.append({"issue_id": iid, "rubric": SUBTOPICS[t][s], "member_count": len(by[iid]),
                      "sample": [{"review_id": i, "quote": rec[i]["evidence_quote"][:300]} for i in ids]})
    key = "group:" + hashlib.sha256((GROUP_CONFIG + json.dumps(packs, sort_keys=True)).encode()).hexdigest()
    cached = st.get_artifact(key)
    if cached:
        log(f"group: reused cached issue names for {len(cached)} issues (0 calls)")
        return cached, 0
    issues, calls = {}, 0
    for i in range(0, len(packs), 6):
        chunk = packs[i:i + 6]
        try:
            text, rec_ = caller.call(role="group", model=GROUP_MODEL, system=SYSTEM,
                                     user=json.dumps({"issues": chunk}, ensure_ascii=False, indent=1),
                                     max_tokens=250 * len(chunk) + 200, settings=SMALL_SETTINGS, review_ids=[],
                                     prompt_version=GROUP_PROMPT_VERSION,
                                     meta={"input_artifact": "issue_samples", "issue_ids": [p["issue_id"] for p in chunk]})
            calls += 1
            for o in parse_json(text).get("issues", []):
                p = next((p for p in chunk if p["issue_id"] == o.get("issue_id")), None)
                if not p:
                    continue  # reject unknown issue IDs
                sample_ids = {s["review_id"] for s in p["sample"]}
                issues[p["issue_id"]] = {"name": str(o.get("name", ""))[:80], "summary": str(o.get("summary", ""))[:300],
                                         "misfit_ids": [m for m in o.get("misfit_ids", []) if m in sample_ids],
                                         "sample_ids": sorted(sample_ids), "request_id": rec_["request_id"]}
        except (CallFailed, ValueError) as e:
            log(f"  group call failed: {e}")
    for p in packs:
        issues.setdefault(p["issue_id"], {"name": p["issue_id"], "summary": p["rubric"], "misfit_ids": [],
                                          "sample_ids": [s["review_id"] for s in p["sample"]], "request_id": None})
    st.put_artifact(key, issues)
    log(f"group: named {len(issues)} issues with {calls} calls")
    return issues, calls


def run_rank(st, input_sha, issues=None, log=print):
    """Deterministic: recompute membership, ranking and aggregates from saved records. No model calls."""
    records = final_records(st, input_sha)
    members = membership(records)
    ranking = rank(records, members)
    write_membership_and_ranking(st.out, members, ranking)
    if issues is None:
        issues = json.loads((st.out / "issues.json").read_text()) if (st.out / "issues.json").exists() else {}
    aggs = aggregates(records, members, ranking, issues)
    buf = io.StringIO()
    if aggs:
        w = csv.DictWriter(buf, fieldnames=list(aggs[0].keys()), lineterminator="\n")
        w.writeheader()
        w.writerows(aggs)
    atomic_write(st.out / "aggregates.csv", buf.getvalue())
    log(f"rank: {len(records)} completed records, {len(members)} complaint memberships, {len(ranking)} issues")
    return records, members, ranking, aggs
