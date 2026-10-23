"""Offline mock provider for zero-cost tests of the orchestration (never used for reported results).

Produces schema-shaped answers from keyword heuristics. Failure injection via env:
  MOCK_TRANSIENT_FAILS=N   first N calls raise a retryable error
  MOCK_MALFORMED_EVERY=K   every K-th call returns malformed JSON
  MOCK_DROP_EVERY=K        every K-th call omits one result
"""

import json
import os
import re
import threading
import uuid


class TransientMockError(Exception):
    status_code = 529


class _Obj:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def _guess(text):
    t = text.lower()
    if re.search(r"premium|skip|choose|pick|rewind|repeat", t): topic, sub, sev = "billing", "premium_only_controls", 3
    elif "ad" in t.split() or "ads" in t: topic, sub, sev = "usability", "ads_frequency_volume", 2
    elif re.search(r"crash|open|loading", t): topic, sub, sev = "playback", "crash_or_wont_open", 4
    elif re.search(r"login|log in|password|account", t): topic, sub, sev = "access", "login_failure", 4
    elif re.search(r"offline|download", t): topic, sub, sev = "downloads", "offline_playback_failure", 4
    elif "lyric" in t: topic, sub, sev = "catalog", "lyrics_availability", 3
    else: topic, sub, sev = "other", "generic_criticism", 2
    neg = re.search(r"bad|worst|hate|trash|useless|not|can't|cannot|sucks|annoy|stop|crash", t)
    intent = "cancellation" if re.search(r"uninstall|delet|leaving|switch", t) else ("complaint" if neg else "praise")
    if intent == "praise":
        topic, sub, sev = "other", "praise_general", 1
    return topic, sub, intent, sev, (-0.7 if neg else 0.8)


class _Messages:
    def __init__(self):
        self.n = 0
        self.lock = threading.Lock()

    def create(self, model, max_tokens, system, messages, **kw):
        with self.lock:
            self.n += 1
            n = self.n
        if n <= int(os.environ.get("MOCK_TRANSIENT_FAILS", "0")):
            raise TransientMockError("mock overloaded")
        user = messages[0]["content"]
        sys_text = system[0]["text"] if isinstance(system, list) else system
        items = []
        for line in user.splitlines():
            line = line.strip()
            if line.startswith("{") and '"i"' in line:
                try:
                    items.append(json.loads(line))
                except ValueError:
                    pass
        if "independent auditor" in sys_text:
            out = {"r": [{"i": it["i"], "t": g[0], "n": g[2], "v": g[3], "m": g[4]} for it in items for g in [_guess(it["text"])]]}
        elif "candidate complaint issues" in sys_text:
            ids = re.findall(r'"issue_id": "([^"]+)"', user)
            out = {"issues": [{"issue_id": i, "name": i.replace(".", " ").replace("_", " ").title(), "summary": "Mock summary.", "misfit_ids": []} for i in ids]}
        elif "decision memo" in sys_text:
            pack = json.loads(user[user.index("{"):])
            top = pack["ranking_top"][0]
            out = {"priority_area": "billing/support", "headline": "Mock headline.",
                   "recommendations": [{"title": "Mock", "detail": "Mock", "issue_ids": [top["issue_id"]], "claim_ids": ["C1"]}],
                   "alternatives": [{"area": "playback", "why_not_first": "mock"}], "limitations": ["mock"],
                   "claims": [{"claim_id": "C1", "issue_id": top["issue_id"], "metric": "priority_score", "value": top["priority_score"]}],
                   "memo_markdown": f"# Mock memo\nTop issue `{top['issue_id']}` scores {top['priority_score']} [C1]."}
        else:
            res = []
            for it in items:
                g = _guess(it["text"])
                res.append({"i": it["i"], "t": g[0], "s": g[1], "n": g[2], "v": g[3], "m": g[4], "x": False,
                            "q": "*" if len(it["text"]) <= 200 else it["text"][:80]})
            k = int(os.environ.get("MOCK_DROP_EVERY", "0"))
            if k and n % k == 0 and res:
                res.pop()
            out = {"r": res}
        text = json.dumps(out, ensure_ascii=False)
        k = int(os.environ.get("MOCK_MALFORMED_EVERY", "0"))
        if k and n % k == 0:
            text = text[: len(text) // 2]
        return _Obj(id="mock_" + uuid.uuid4().hex[:16], content=[_Obj(type="text", text=text)], stop_reason="end_turn",
                    usage=_Obj(input_tokens=(len(sys_text) + len(user)) // 4, output_tokens=len(text) // 3,
                               cache_creation_input_tokens=0, cache_read_input_tokens=0))


class MockClient:
    def __init__(self):
        self.messages = _Messages()
