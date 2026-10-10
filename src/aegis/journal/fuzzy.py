"""The Journal view's search box, which is for people: one box over every field
of an entry, forgiving of a phone keyboard's capitals and of a word typed in
part. Agents keep journal.search's exact filters.

A word matches a field when its characters appear in it in order, in any case.
A word of three or more characters must fall within SPREAD times its length, so
`scra` finds scratch-repo but not three letters scattered over a long line.
"""

from __future__ import annotations

SPREAD = 4


def find(word: str, field: str) -> list[int] | None:
    """The indices in ``field`` that ``word`` matches over its tightest span, or
    None."""
    w = word.lower()
    f = field.lower()
    if len(f) != len(field):  # a character that lowers to two: keep the indices
        f = "".join(c if len(c.lower()) != 1 else c.lower() for c in field)
    best: list[int] | None = None
    start = f.find(w[0])
    while start != -1:
        idx = [start]
        for ch in w[1:]:
            j = f.find(ch, idx[-1] + 1)
            if j == -1:
                break
            idx.append(j)
        else:
            if best is None or idx[-1] - idx[0] < best[-1] - best[0]:
                best = idx
            start = f.find(w[0], start + 1)
            continue
        break  # no later start can match what this one could not
    if best is None:
        return None
    if len(w) >= 3 and best[-1] - best[0] + 1 > SPREAD * len(w):
        return None
    return best


def match(words: list[str], text: str, fields: list[str]) -> list[int] | None:
    """None unless every word matches ``text`` or one of ``fields``; else the
    indices in ``text`` its words matched, for the view to highlight."""
    marks: set[int] = set()
    for w in words:
        hit = find(w, text)
        if hit is not None:
            marks.update(hit)
        elif not any(find(w, f) is not None for f in fields):
            return None
    return sorted(marks)
