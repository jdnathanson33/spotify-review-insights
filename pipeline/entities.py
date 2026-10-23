"""Deterministic entity extraction (entities-v1): explicit feature / competitor / platform terms.

Code, not a model, fills `entities`: each entity is emitted only when one of its
patterns literally appears in the review text (case-insensitive), so entities
are always supported by the source. Evaluated on the golden set like any field.
"""

import re

LEXICON = {
    "premium": r"\bpremi?um\b|\bprimium\b",
    "ads": r"\bads?\b|\badds\b|advertis",
    "skip": r"\bskip",
    "shuffle": r"shuffl",
    "lyrics": r"lyric",
    "playlist": r"play ?list",
    "queue": r"\bqueue",
    "downloads": r"download",
    "offline": r"off ?line",
    "podcasts": r"podcast",
    "login": r"\blog ?in\b|\bsign ?in\b|password",
    "update": r"\bupdat",
    "home screen": r"home ?screen|home ?page",
    "recommendations": r"recommend|suggest",
    "search": r"\bsearch",
    "bluetooth": r"bluetooth",
    "android auto": r"android auto",
    "widget": r"widget",
    "support": r"customer (?:service|support)|\bsupport team|contact(?:ed)? (?:spotify|support)",
    "family plan": r"family (?:plan|premium|sub)",
    "student plan": r"student",
    "YouTube Music": r"youtube",
    "Apple Music": r"apple music",
    "Amazon Music": r"amazon",
    "SoundCloud": r"soundcloud",
    "Wynk": r"\bwynk",
    "JioSaavn": r"saavn",
    "Pandora": r"pandora",
}
_COMPILED = [(name, re.compile(p, re.I)) for name, p in LEXICON.items()]


def extract(text):
    return [name for name, rx in _COMPILED if rx.search(text)]
