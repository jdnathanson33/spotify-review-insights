"""Stage 6 — Recommend (model role `memo`, code-checked).

Code builds a bounded evidence pack from saved aggregates (never the raw CSV).
The memo model returns structured JSON; code validates every claim against the
deterministic ranking, every cited issue/review ID against the pack, and every
number in the prose against numbers present in the pack. One repair attempt is
allowed with the validation errors; remaining problems are reported, not hidden.
"""

import csv
import hashlib
import io
import json
import re
from collections import Counter, defaultdict

from .common import LABEL_CONFIG, MEMO_MODEL, MEMO_PROMPT_VERSION, MEMO_SETTINGS, atomic_write, prompt, write_json
from .enrich import parse_json
from .labels import AREA_OF_TOPIC, COMPLAINT_INTENTS
from .llm import CallFailed

SYSTEM = prompt(MEMO_PROMPT_VERSION)
MEMO_CONFIG = f"{MEMO_MODEL}+effort-low+adaptive+{MEMO_PROMPT_VERSION}"
METRICS = ("complaint_count", "severity_sum", "mean_severity", "priority_score")


def evidence_pack(st, input_sha, records, members, ranking, aggs, top_n=12, evidence_issues=6, per_issue=4):
    status = Counter()
    reasons = Counter()
    for rev, res in st.scope_records(input_sha, LABEL_CONFIG):
        s = res["status"] if res else "pending"
        status[s] += 1
        if res and res["status"] == "quarantined":
            reasons[res["reason"].split(":")[0]] += 1
    rec = {r["review_id"]: r for r in records}
    by_issue = defaultdict(list)
    for iid, rid in members:
        by_issue[iid].append(rid)
    area = defaultdict(lambda: {"complaint_count": 0, "severity_sum": 0, "cancellation_count": 0, "issues": 0})
    for a in aggs:
        x = area[a["area"]]
        x["complaint_count"] += a["complaint_count"]
        x["severity_sum"] += a["severity_sum"]
        x["cancellation_count"] += a["cancellation_count"]
        x["issues"] += 1
    months_all = Counter(r["ts"][:7] for r in records if r["intent"] in COMPLAINT_INTENTS)
    top = aggs[:top_n]
    monthly = {}
    for a in top[:5]:
        c = Counter(rec[rid]["ts"][:7] for rid in by_issue[a["issue_id"]])
        monthly[a["issue_id"]] = [{"month": m, "issue_complaints": c.get(m, 0), "all_complaints": months_all[m]} for m in sorted(months_all)]
    evidence = {}
    for a in top[:evidence_issues]:
        ids = sorted(by_issue[a["issue_id"]], key=lambda i: (-int(rec[i]["likes"] or 0), -rec[i]["severity"], i))[:per_issue]
        evidence[a["issue_id"]] = [{"review_id": i, "quote": rec[i]["evidence_quote"][:250], "severity": rec[i]["severity"],
                                    "intent": rec[i]["intent"], "helpful_votes": rec[i]["likes"]} for i in ids]
    verify = json.loads((st.out / "verify_report.json").read_text()) if (st.out / "verify_report.json").exists() else None
    return {
        "scope": {"reviews_in_scope": sum(status.values()), "completed": status["completed"], "quarantined": status["quarantined"],
                  "pending_unresolved": status["pending"], "quarantine_reasons": dict(reasons),
                  "window": "2022-05-17 to 2023-11-15 (first and last calendar months are partial)"},
        "intent_counts": dict(Counter(r["intent"] for r in records)),
        "complaint_or_cancellation_records": len(members),
        "area_totals": dict(area),
        "area_mapping": {"access": ["access"], "usability": ["usability", "catalog"], "playback": ["playback", "downloads"],
                         "billing/support": ["billing", "support"], "none": ["other (generic, boycott, unclear; not actionable)"]},
        "ranking_top": [{k: str(a[k]) for k in ("rank", "issue_id", "complaint_count", "severity_sum", "mean_severity", "priority_score")}
                        | {"name": a["name"], "area": a["area"], "cancellation_count": str(a["cancellation_count"]),
                           "severity_4plus": str(a["severity_4plus"])} for a in top],
        "monthly_complaints_top5": monthly,
        "evidence_reviews": evidence,
        "verification": {k: verify[k] for k in ("compared", "agreement", "severity_mae", "disagreements")} if verify else None,
        "ranking_rule": "priority_score = complaint_count x mean_severity = severity_sum; ties by issue_id ascending",
    }


def validate(memo, pack, ranking):
    errs = []
    calc = {r["issue_id"]: {m: str(r[m]) for m in METRICS} for r in ranking}
    ev_ids = {e["review_id"] for v in pack["evidence_reviews"].values() for e in v}
    claims, seen = [], set()
    for c in memo.get("claims", []):
        cid, iid, met, val = str(c.get("claim_id", "")), c.get("issue_id"), c.get("metric"), str(c.get("value", ""))
        if not cid or cid in seen:
            errs.append(f"duplicate/blank claim id {cid}")
            continue
        seen.add(cid)
        if met not in METRICS or iid not in calc or calc[iid][met] != val:
            errs.append(f"claim {cid} ({iid} {met}={val}) does not match calculation {calc.get(iid, {}).get(met)}")
            continue
        claims.append({"claim_id": cid, "issue_id": iid, "metric": met, "value": val})
    text = memo.get("memo_markdown", "")
    for cid in set(re.findall(r"\[(C\d+)\]", text)) - {c["claim_id"] for c in claims}:
        errs.append(f"memo cites unknown/invalid claim {cid}")
    for iid in set(re.findall(r"`([a-z]+\.[a-z_]+)`", text)) - set(calc):
        errs.append(f"memo cites unknown issue {iid}")
    for rid in set(re.findall(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", text)) - ev_ids:
        errs.append(f"memo cites review {rid} not in evidence pack")
    pack_numbers = set(re.findall(r"\d+(?:\.\d+)?", json.dumps(pack)))
    prose = re.sub(r"[0-9a-f]{8}-[0-9a-f-]{27}|`[^`]*`|\[C\d+\]", " ", text)
    unsupported = sorted({n for n in re.findall(r"(?<![\w.])\d[\d,]*(?:\.\d+)?%?", prose)
                          if n.replace(",", "").rstrip("%") not in pack_numbers and len(n.replace(",", "")) > 1})
    if unsupported:
        errs.append(f"numbers not found in evidence pack: {unsupported[:12]}")
    if memo.get("priority_area") not in ("access", "usability", "playback", "billing/support"):
        errs.append("priority_area not one of the four areas")
    return claims, errs


def run_memo(st, caller, input_sha, records, members, ranking, aggs, log=print):
    pack = evidence_pack(st, input_sha, records, members, ranking, aggs)
    write_json(st.out / "evidence_pack.json", pack)
    key = "memo:" + hashlib.sha256((MEMO_CONFIG + json.dumps(pack, sort_keys=True)).encode()).hexdigest()
    cached = st.get_artifact(key)
    calls = 0
    if cached:
        memo, claims, errs = cached["memo"], cached["claims"], cached["errors"]
        log("memo: reused cached memo for identical evidence pack (0 calls)")
    else:
        user = "Evidence pack (JSON):\n" + json.dumps(pack, ensure_ascii=False, indent=1)
        memo, claims, errs = None, [], ["memo call failed"]
        for attempt in (1, 2):
            try:
                text, rec = caller.call(role="memo", model=MEMO_MODEL, system=SYSTEM, user=user, max_tokens=6000,
                                        settings=MEMO_SETTINGS, review_ids=[], prompt_version=MEMO_PROMPT_VERSION,
                                        meta={"input_artifact": "evidence_pack.json", "attempt_kind": "repair" if attempt == 2 else "draft"})
                calls += 1
                memo = parse_json(text)
                claims, errs = validate(memo, pack, ranking)
            except (CallFailed, ValueError) as e:
                errs = [f"memo attempt {attempt} failed: {e}"]
            if not errs:
                break
            user = (user + "\n\nYour previous answer failed validation:\n- " + "\n- ".join(errs) +
                    "\nReturn corrected JSON. Copy numbers exactly from the pack.")
        st.put_artifact(key, {"memo": memo, "claims": claims, "errors": errs})
    write_json(st.out / "memo.json", {"memo": memo, "validation_errors": errs, "config": MEMO_CONFIG})
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["claim_id", "issue_id", "metric", "value"])
    for c in claims:
        w.writerow([c["claim_id"], c["issue_id"], c["metric"], c["value"]])
    atomic_write(st.out / "claims.csv", buf.getvalue())
    if memo:
        md = memo.get("memo_markdown", "") + "\n\n---\n\n## Claims (checked by code against ranking.csv)\n\n| claim | issue | metric | value |\n|---|---|---|---|\n"
        md += "".join(f"| {c['claim_id']} | `{c['issue_id']}` | {c['metric']} | {c['value']} |\n" for c in claims)
        md += "\n**Automated validation:** " + ("passed — all claims match the deterministic ranking, all IDs are in the evidence pack, all numbers appear in the pack." if not errs else "issues found: " + "; ".join(errs))
        md += f"\n\n_Model: {MEMO_CONFIG}. Input: `evidence_pack.json` (bounded, code-built)._\n"
        atomic_write(st.out / "memo.md", md)
    log(f"memo: {len(claims)} valid claims, {len(errs)} validation issues, {calls} calls")
    return memo, claims, errs, calls
