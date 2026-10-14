"""The inputs the property lane enumerates (DESIGN.md, 9.2).

Names after claude/: every string of up to three characters over a small
ASCII alphabet (the digits, the slash, a space, a plus, a hash, a dash
and NUL), together with the Unicode classes Python's own str methods
define, enumerated whole rather than sampled: the code points for which
isdigit() holds and isdecimal() does not, and the non-ASCII ones for
which isdecimal() holds, each alone and followed by 0 or 1, since int()
takes the second class and the branch rules must not. Whole hours around
the thresholds. Label sets from the label names the spec gives, in the
three spellings of a worker's label, with a bare worker: that names
nobody. The worker names of a three-worker
scope. Everything here is deterministic, so a failure replays.
"""

import itertools
import sys
from datetime import datetime, timedelta, timezone

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
ALPHABET = "0123456789/ +#-\x00"
HOURS = (0, 1, 23, 24, 25, 48, 49)
WORKERS = ("a", "b", "c")
SPEC_LABELS = ("todo", "claimed", "in-review", "blocker", "epic", "blocked")
HOLDER_SPELLINGS = ("worker:", "account:", "agent:")


def unicode_classes():
    """(isdigit but not isdecimal, isdecimal outside ASCII), each whole."""
    every = map(chr, range(sys.maxunicode + 1))
    digits, decimals = [], []
    for c in every:
        if c.isdigit() and not c.isdecimal():
            digits.append(c)
        elif c.isdecimal() and not c.isascii():
            decimals.append(c)
    return digits, decimals


def ascii_names(longest=3):
    for length in range(longest + 1):
        for letters in itertools.product(ALPHABET, repeat=length):
            yield "".join(letters)


def unicode_names():
    digits, decimals = unicode_classes()
    for c in digits + decimals:
        for tail in ("", "0", "1"):
            yield c + tail


def names():
    """Every name after claude/ the lane tries."""
    return list(ascii_names()) + list(unicode_names())


def short_names():
    """The names of up to two characters and the Unicode ones, for a
    property whose subject is slow."""
    return list(ascii_names(2)) + list(unicode_names())


def at_hour(hours):
    return T0 + timedelta(hours=hours)


def iso(hours):
    return at_hour(hours).isoformat()


def label_sets():
    """Every subset of the spec's labels with at most one holder label in
    each spelling for worker a, b's worker label, and a bare worker:,
    which names nobody (F15's fourth review: a release took it off as a
    worker's label)."""
    holders = tuple(p + "a" for p in HOLDER_SPELLINGS) + ("worker:b", HOLDER_SPELLINGS[0])
    names = SPEC_LABELS + holders
    for bits in itertools.product((False, True), repeat=len(names)):
        yield [name for name, on in zip(names, bits) if on]


def file_sets():
    return [(), ("src/one.py",), ("src/two.py",), ("src/one.py", "src/two.py")]
