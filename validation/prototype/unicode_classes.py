"""The standard-library property lane's domain, tried: the Unicode classes
Python's own str methods define (isdigit without isdecimal, and isdecimal
outside ASCII), enumerated whole, with "0", "1" and "+", as one- and
two-character names after claude/, against the four properties CrossHair
broke: a claim branch names one issue, ordering claims never raises, the
board renders any branch, and a + refspec is refused. Prints the size of
each class, the first counterexample to each property, and the time."""

import sys
import time
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # the repository's root: the library

from sdlc_protocol import board, claims, guard  # noqa: E402


def classes():
    every = [chr(code) for code in range(sys.maxunicode + 1)]
    digits = [c for c in every if c.isdigit() and not c.isdecimal()]
    decimals = [c for c in every if c.isdecimal() and not c.isascii()]
    return digits, decimals


def raises(call):
    try:
        call()
    except ValueError:
        return True
    return False


def broken(key):
    """The properties this name after claude/ breaks."""
    branch = "claude/" + key
    number = claims.number_of(branch)
    found = []
    if number is not None and claims.branch_of(number) != branch:
        found.append("a claim branch names one issue")
    if raises(lambda: claims.issue_order(branch)):
        found.append("ordering claims never raises")
    if raises(lambda: board.render({"sha": "", "branches": [branch]})):
        found.append("the board renders any branch")
    if guard.refusal(["origin", "+" + key], "claude/7", "main") is None:
        found.append("a + refspec is refused")
    return found


def main():
    began = time.perf_counter()
    digits, decimals = classes()
    built = time.perf_counter()
    print(f"Unicode {unicodedata.unidata_version}: {len(digits)} code points isdigit but not "
          f"isdecimal, {len(decimals)} isdecimal outside ASCII")
    alphabet = digits + decimals + ["0", "1", "+"]
    first = {}
    for one in alphabet:
        for two in ("", "0", "1"):
            for name in broken(one + two):
                first.setdefault(name, one + two)
    done = time.perf_counter()
    for name, key in first.items():
        print(f"  {name}: broken by {ascii(key)}")
    print(f"classes built in {1000 * (built - began):.0f} ms, {3 * len(alphabet)} names tried in "
          f"{1000 * (done - built):.0f} ms")


if __name__ == "__main__":
    main()
