"""Prototype of the guard's exhaustive check: push commands generated from a
structure whose meaning is known by construction, each judged twice, by the
guard (sdlc_protocol.guard, as the hook runs it) and by an oracle that reads
the structure and the board rules (.claude/rules/board.md), and every
disagreement counted by class. Scratch only."""

import itertools
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # the repository's root: the library

from sdlc_protocol import guard  # noqa: E402

CURRENT, DEFAULT = "claude/7", "main"
OTHERS = "claude/8"

PREFIXES = {"": "plain", "FOO=1 ": "assignment", "env ": "env", "command ": "command", "nice ": "nice",
            "time ": "time"}
WRAPPERS = {"{}": "none", "bash -c '{}'": "bash -c", "true && {}": "and-list", "( {} )": "subshell",
            "echo $({})": "substitution"}
GLOBALS = ["", " -C .", " -c push.default=current"]
OPTIONS = {"": None, " --force": "force", " -f": "force", " --force-with-lease": "lease", " --delete": "delete",
           " --all": "wholesale", " --mirror": "wholesale", " -u": None, " --no-verify": None}
# (text, destination, mode) with mode None for an update, "force" for a leading +, "delete" for :dst.
REFSPECS = [
    ("", CURRENT, None), (" HEAD", CURRENT, None), (" claude/7", CURRENT, None), (" HEAD:claude/7", CURRENT, None),
    (" +claude/7", CURRENT, "force"), (" main", DEFAULT, None), (" HEAD:main", DEFAULT, None),
    (" refs/heads/main", DEFAULT, None), (" +HEAD:main", DEFAULT, "force"), (" :main", DEFAULT, "delete"),
    (" claude/7:board", "board", None), (" claude/8", OTHERS, None), (" HEAD:claude/8", OTHERS, None),
    (" +claude/7:claude/8", OTHERS, "force"), (" :claude/8", OTHERS, "delete"), (" feature", "feature", None),
    (" +feature", "feature", "force"),
]


def oracle(option_mode, spec_mode, destination):
    """The verdict the board rules give, from the structure: (forbidden, why).
    'scope' says whether the guard can know it from the command alone."""
    if option_mode == "wholesale":
        return True, "wholesale", "guard"
    mode = spec_mode or option_mode
    if destination in (DEFAULT, "board"):
        return True, "default or board", "guard"
    if mode == "force":
        return True, "forced push", "guard"
    if mode == "lease" and not destination.startswith("claude/"):
        return True, "lease outside claude/", "guard"
    if destination == OTHERS:
        return True, "another worker's claim", "protocol"   # the guard cannot know whose a claim is
    return False, "allowed", "-"


def guard_refuses(command):
    pushes = [a for argv in guard.simple_commands(command) if (a := guard.push_arguments(argv)) is not None]
    return any(guard.refusal(args, CURRENT, DEFAULT) for args in pushes)


def main():
    counts, examples, total = Counter(), {}, 0
    for (prefix, pname), (wrap, wname), glob, (option, omode), (spec, dest, smode) in itertools.product(
            PREFIXES.items(), WRAPPERS.items(), GLOBALS, OPTIONS.items(), REFSPECS):
        if omode == "delete" and not spec.strip():
            continue
        if omode == "delete":
            smode = "delete"
        if omode == "wholesale" and spec.strip():
            continue
        command = wrap.format(f"{prefix}git{glob} push{option} origin{spec}")
        total += 1
        forbidden, why, scope = oracle(omode, smode, dest)
        refused = guard_refuses(command)
        if forbidden and not refused:
            key = ("let through", why, scope, pname, wname)
        elif refused and not forbidden:
            key = ("refused but allowed", why, scope, pname, wname)
        else:
            continue
        counts[key] += 1
        examples.setdefault(key, command)
    print(f"{total} commands; {sum(counts.values())} disagreements in {len(counts)} classes")
    by_cause = Counter()
    for (kind, why, scope, pname, wname), n in counts.items():
        cause = f"{kind}: {why} ({scope})" + ("" if pname == "plain" and wname in ("none", "and-list", "subshell", "substitution") else f" via {pname}/{wname}")
        by_cause[(kind, why, scope, pname != "plain" or wname == "bash -c")] += n
    for (kind, why, scope, wrapped), n in sorted(by_cause.items()):
        sample = next(c for k, c in examples.items() if k[0] == kind and k[1] == why and k[2] == scope
                      and (k[3] != "plain" or k[4] == "bash -c") == wrapped)
        print(f"{n:5d}  {kind}: {why} [{scope}]{' behind a prefix or bash -c' if wrapped else ''}   e.g. {sample}")


if __name__ == "__main__":
    main()
