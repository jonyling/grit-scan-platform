"""Distinctive headline words, so two reports of one story can be recognised. Pure.

A word counts when it has five or more letters and is not common in headlines. Plurals fold to the singular, and a
multi-word place counts once, so "Saudi Arabia" does not make two unrelated Saudi stories look alike.
"""

from __future__ import annotations

import re

GENERIC = frozenset("""about after again against alert alerts amid among announces annual another around before between
billion breaking could during first following highlights latest major million month months monthly other report reports
since their there these third today under update updates weekly where which while world would years quarter group
january february march april june july august september october november december""".split())
PLACES = (("saudi arabian", "saudi"), ("saudi arabia", "saudi"), ("united arab emirates", "uae"), ("united states", " "),
          ("hong kong", "hongkong"), ("south korea", "korea"), ("north korea", "northkorea"), ("new zealand", "newzealand"))


def distinctive_terms(text: str) -> frozenset[str]:
    folded = str(text or "").casefold()
    for phrase, token in PLACES:
        folded = folded.replace(phrase, token)
    words = (word for word in re.findall(r"[a-z]{5,}", folded) if word not in GENERIC)
    return frozenset(word[:-1] if len(word) > 5 and word.endswith("s") and not word.endswith("ss") else word for word in words)
