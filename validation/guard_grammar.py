"""The push guard against git's own answers (DESIGN.md, section 8).

commands() generates push commands from parts: a prefix the shell reads
before git, a wrapper that runs the command another way, git's global
options, push options plain, bundled and abbreviated, and refspecs that
name the worker's own claim branch, another worker's, the default and the
board branch under every spelling, a branch that is no claim and a name
that reads as a claim and claims nothing; and, with no wrapper or global
option around them, the prefixes whose wrapper is given a long option by
a start of its name (ABBREVIATED, F26). Each command is judged twice:
by the guard, as the hook runs it, and by the board rules read off what
git would do with it.

Git is the oracle. Each distinct git argument list runs in two scratch
worlds whose bare remote has a pre-receive hook that records every ref
update it is offered, with its kind (create, delete, fast-forward or
forced), and refuses them all, so nothing ever moves. In the fresh world
the clone's remote-tracking refs match the remote; in the stale world the
remote's claim branch, and the branch that is no claim, have moved behind
the clone's back, where git refuses a --force-with-lease itself ("stale
info") but still offers a plain force.
The prefix and the wrapper change whether the guard can read a command,
never what git does, so git runs once per argument list, and record()
writes the answers to guard_git.json, keyed by git's version, for the
fast lane to judge the guard against in under a second (judge()). A
command the file holds no answer for fails the lane.

The rules read off git's offers: an update of main or board is a push to
the default or the board branch; a forced update offered in the stale
world is a plain force, since a lease would have been refused there; a
forced update offered only in the fresh world, on a ref outside claude/,
is a lease outside claude/; more than one ref offered is a wholesale
push; a claude/ name created or updated that reads as a claim and claims
nothing is the misnamed claim the protocol's Claim rule names; and a
deletion of any other branch is judged by whose it is, which only the
second fence knows (G11b), so it is not judged here.

A refusal of a push git shows harmless is listed by shape, and fails the
lane only for the shapes the rules name as allowed: a plain push of one's
own claim branch, its lease after a rebase, and a plain push of a branch
that is no claim; the rules forbid other shapes whatever their effect
("your own claim branch takes --force-with-lease, and nothing else ever
does", symphony-template's .claude/rules/board.md). A forbidden push let
through fails the lane unless the guard cannot read the command (inside
a string another program runs: G11b) or the lane withholds the fact
(whose a claim branch is: the lane runs the guard with no claim-file
reader, so a lease onto another worker's claim passes here as it does
in a clone that cannot show the claim files; the fence that reads them
is tests/test_guard.py's, over a real clone, since F7's fix).
"""

import itertools
import json
import os
import re
import shlex
import shutil
import subprocess
import tempfile
from collections import Counter
from pathlib import Path
from typing import NamedTuple

from sdlc_protocol import guard

ANSWERS = Path(__file__).resolve().parent / "guard_git.json"

CURRENT, DEFAULT, BOARD, OTHER, NO_CLAIM = "claude/7", "main", "board", "claude/8", "feature"
# The directory the hook is run from: the scratch world's clone, which
# git runs every argument list in.
HERE = "/w"
MISNAMED = ("claude/07", "claude/\u0667")  # a leading zero; an Arabic-Indic seven, a digit but no ASCII one
CLAIM_NUMBER = re.compile(r"0|[1-9][0-9]{0,8}")

PREFIXES = ("", "FOO=1 ", "env ", "command ", "nice ", "time ")
# A wrapper given a long option by a start of its name, as GNU's
# getopt_long reads it: one that needs a value, with its value as the next
# word, and one that may take a value, with none, which it takes only
# attached. The guard compared a long option with the names that take a
# value, in full, so it read the word after --uns, --adj or --sig as the
# command (F26). Like every prefix they change what the guard reads and
# never git's arguments, so they are taken with the command alone.
ABBREVIATED = ("env --uns FOO ", "env --def ", "nice --adj 5 ", "timeout --sig KILL 60 ")
# The wrapper that hides the command in a string another program runs is
# the one the guard cannot read (G11b).
WRAPPERS = ("{}", "bash -c '{}'", "true && {}", "( {} )", "echo $({})")
UNREADABLE = "bash -c '{}'"
# git's own options before the verb: a directory, the empty one git -C
# takes as no change, a setting, a setting from the environment, whose
# value is the next word (F21's review), and a shallow file, whose value
# is the next word too (its third review).
GLOBALS = ("", " -C .", ' -C ""', " -c push.default=current", " --config-env user.name=HOME",
           " --shallow-file x")
PLAIN_OPTIONS = ("", " -u", " --no-verify")
LEASE = " --force-with-lease"
FORCE_OPTIONS = (" --force", " -f", " -fu")
WHOLESALE_OPTIONS = (" --all", " --al", " --branches", " --mirror", " --mirr")
OPTIONS = PLAIN_OPTIONS + (LEASE,) + FORCE_OPTIONS + WHOLESALE_OPTIONS + (" --delete",)
OWN = ("", " HEAD", " @", " claude/7", " HEAD:claude/7", " refs/heads/claude/7", " +claude/7", " +HEAD:claude/7")
TO_DEFAULT = (" main", " HEAD:main", " refs/heads/main", " heads/main", " HEAD:heads/main", " +HEAD:main", " :main")
TO_BOARD = (" HEAD:board", " claude/7:board", " +HEAD:board", " :board")
TO_OTHER = (" claude/8", " HEAD:claude/8", " +claude/7:claude/8", " :claude/8")
TO_NO_CLAIM = (" feature", " +feature", " HEAD:feature")
TO_MISNAMED = tuple(f" {spelling}:{name}" for name in MISNAMED for spelling in ("HEAD", "+HEAD"))
REFSPECS = OWN + TO_DEFAULT + TO_BOARD + TO_OTHER + TO_NO_CLAIM + TO_MISNAMED
ALLOWED_SHAPES = ({(option, spec) for option in PLAIN_OPTIONS for spec in OWN if "+" not in spec}
                  | {(LEASE, spec) for spec in OWN if "+" not in spec}
                  | {(option, spec) for option in PLAIN_OPTIONS for spec in TO_NO_CLAIM if "+" not in spec})


class Command(NamedTuple):
    text: str
    argv: str  # the words after git, the key git's answer is recorded under
    prefix: str
    wrapper: str
    option: str
    spec: str

    @property
    def readable(self):
        return self.wrapper != UNREADABLE

    @property
    def allowed_shape(self):
        return (self.option, self.spec) in ALLOWED_SHAPES


def commands():
    """Every command of the grammar: each prefix with every wrapper and
    global option, and each ABBREVIATED one with the command alone. A
    wholesale option takes no refspec and --delete needs one, as git reads
    them."""
    parts = itertools.chain(itertools.product(PREFIXES, WRAPPERS, GLOBALS, OPTIONS, REFSPECS),
                            itertools.product(ABBREVIATED, ("{}",), ("",), OPTIONS, REFSPECS))
    for prefix, wrapper, glob, option, spec in parts:
        if option in WHOLESALE_OPTIONS and spec:
            continue
        if option == " --delete" and (not spec or spec.startswith(" +") or spec.startswith(" :")):
            continue
        argv = f"{glob} push{option} origin{spec}".strip()
        yield Command(wrapper.format(f"{prefix}git {argv}"), argv, prefix, wrapper, option, spec)


def argument_lists():
    return sorted({c.argv for c in commands()})


# --- the rules, from git's offers ---------------------------------------------

def misnamed(ref):
    name = ref.removeprefix("refs/heads/")
    key = name[len("claude/"):] if name.startswith("claude/") else ""
    return key.isdigit() and not CLAIM_NUMBER.fullmatch(key)


def rules_verdict(fresh, stale):
    """What the board rules say of the effects git offered: why the push
    is forbidden, or None. Offers are (ref, kind) pairs."""
    every = set(fresh) | set(stale)
    if any(ref in ("refs/heads/main", "refs/heads/board") for ref, _ in every):
        return "updates the default or the board branch"
    judged = {(ref, kind) for ref, kind in every if kind != "delete"}
    if any(kind == "forced" for ref, kind in stale if (ref, kind) in judged):
        return "a plain forced update: offered where a lease would have been refused"
    if any(kind == "forced" and not ref.startswith("refs/heads/claude/") for ref, kind in fresh):
        return "a lease outside claude/"
    if len({ref for ref, _ in every}) > 1:
        return "updates more than one ref"
    if any(misnamed(ref) for ref, _ in judged):
        return "a name that reads as a claim and claims nothing"
    return None


def guard_refuses(text):
    """Whether the guard refuses the command, run as the hook runs it
    (guard.refused_push) from the scratch world's clone, which is on
    CURRENT, with DEFAULT its default branch, wherever the command's -C
    takes it, since every directory it names is that clone, and with no
    claim-file reader; the grammar makes no cd."""
    return guard.refused_push(text, HERE, lambda place: (CURRENT, DEFAULT), lambda place: None,
                              lambda place: True) is not None


class Disagreement(NamedTuple):
    kind: str  # "let through" or "refused but allowed"
    why: str
    fails: bool
    example: str


class Judgement(NamedTuple):
    commands: int
    agreed: int
    classes: dict  # Disagreement -> count
    missing: tuple

    @property
    def failures(self):
        return {d: n for d, n in self.classes.items() if d.fails}


def judge(answers=None, commands=None):
    """Every command of the grammar, or the ones given, judged twice, with
    the disagreements counted by class and the argument lists the answers
    lack."""
    answers = read_answers() if answers is None else answers
    classes, missing, agreed, total = Counter(), set(), 0, 0
    examples = {}
    for command in (globals()["commands"]() if commands is None else commands):
        total += 1
        answer = answers.get(command.argv)
        if answer is None:
            missing.add(command.argv)
            continue
        forbidden = rules_verdict(pairs(answer["fresh"]), pairs(answer["stale"]))
        refused = guard_refuses(command.text)
        if bool(forbidden) == refused:
            agreed += 1
            continue
        if forbidden:
            others = command.spec in TO_OTHER
            fails = command.readable and not others
            why = forbidden + ("" if command.readable else " (unreadable: G11b)") + \
                (" (another worker's claim: no claim-file reader here; G11b)" if others else "")
            key = Disagreement("let through", why, fails, "")
        else:
            key = Disagreement("refused but allowed", "git offers " + describe(answer), command.allowed_shape, "")
        examples.setdefault(key, command.text)
        classes[key] += 1
    counted = {d._replace(example=examples[d]): n for d, n in classes.items()}
    return Judgement(total, agreed, counted, tuple(sorted(missing)))


def judged_by_git(text, answers=None):
    """One command judged by git's recorded answer, for a trace's guard
    step (replay.py): the disagreements that fail, as (guarantee, detail)
    pairs, empty when the guard and git agree; None when the command is
    not one of the grammar's, so git recorded no answer for it."""
    command = next((c for c in commands() if c.text == text), None)
    if command is None:
        return None
    judgement = judge(answers, [command])
    found = [("G11a", f"{d.kind}: {d.why}") for d in judgement.failures]
    found += [("G11a", "no answer recorded") for _ in judgement.missing]
    return found


def pairs(offers):
    return {tuple(offer) for offer in offers}


def describe(answer):
    """What git offered, in words: nothing, when git refuses the push
    itself (an unknown branch, a push that is up to date or not a fast
    forward), or the refs and kinds."""
    offers = sorted(pairs(answer["fresh"]) | pairs(answer["stale"]))
    if not offers:
        return "nothing, refusing the push itself"
    return ", ".join(f"{kind} of {ref.removeprefix('refs/heads/')}" for ref, kind in offers)


def read_answers(path=ANSWERS):
    return json.loads(path.read_text(encoding="ascii"))["answers"]


# --- recording git's answers --------------------------------------------------

ENV = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C", "GIT_CONFIG_GLOBAL": os.devnull,
       "GIT_CONFIG_NOSYSTEM": "1"}
IDENTITY = ["-c", "user.name=probe", "-c", "user.email=probe@example.invalid", "-c", "commit.gpgsign=false",
            "-c", "init.defaultBranch=main", "-c", "gc.auto=0", "-c", "maintenance.auto=false",
            "-c", "receive.autogc=false"]
ZERO = "0" * 40
HOOK = f"""#!/bin/sh
while read old new ref; do
  if [ "$old" = {ZERO} ]; then kind=create
  elif [ "$new" = {ZERO} ]; then kind=delete
  elif git merge-base --is-ancestor "$old" "$new"; then kind=fast-forward
  else kind=forced; fi
  echo "$ref $kind" >> "$GIT_DIR/offered"
done
exit 1
"""


def git(cwd, *args, check=True):
    done = subprocess.run(["git", *IDENTITY, *args], cwd=cwd, capture_output=True, text=True, env=ENV, timeout=60)
    if check and done.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {done.stderr.strip()}")
    return done


def commit(clone, name):
    (clone / "a").write_text(name + "\n", encoding="ascii")
    git(clone, "add", "a")
    git(clone, "commit", "-q", "-m", name)


def world(root, stale):
    """A bare remote with main, board, claude/7, claude/8 and feature, and
    a clone on claude/7 that has diverged from the remote's, as after a
    rebase, with its main a commit ahead, its claude/8 a commit ahead of
    another worker's, and its feature, a branch that is no claim, diverged
    from the remote's too, so a lease on it is offered forced where the
    lease holds; in the stale world claude/7 and feature moved on the
    remote after the clone's last fetch."""
    remote, clone = root / "remote.git", root / "w"
    git(root, "init", "-q", "--bare", str(remote))
    git(root, "clone", "-q", str(remote), str(clone))
    commit(clone, "a")
    git(clone, "push", "-q", "origin", "main", "main:board", "main:claude/8")
    git(clone, "switch", "-q", "-c", "claude/7")
    commit(clone, "b")
    git(clone, "push", "-q", "-u", "origin", "claude/7", "claude/7:feature")
    git(clone, "reset", "-q", "--hard", "main")
    commit(clone, "c")
    git(clone, "switch", "-q", "main")
    commit(clone, "e")
    git(clone, "branch", "-q", "claude/8")
    git(clone, "branch", "-q", "feature")
    git(clone, "switch", "-q", "claude/7")
    if stale:
        other = root / "other"
        git(root, "clone", "-q", "-b", "claude/7", str(remote), str(other))
        commit(other, "x")
        git(other, "push", "-q", "origin", "claude/7", "claude/7:feature")
    hook = remote / "hooks" / "pre-receive"
    hook.write_text(HOOK, encoding="ascii")
    hook.chmod(0o755)
    return remote, clone


def offered(remote, clone, argv):
    """The (ref, kind) pairs git offered the remote for one argument list."""
    log = remote / "offered"
    log.write_text("", encoding="ascii")
    git(clone, *shlex.split(argv), check=False)
    return sorted(tuple(line.split()) for line in log.read_text(encoding="utf-8").splitlines() if line.strip())


def record(path=ANSWERS, lists=None):
    """Run every argument list of the grammar in both worlds and write the
    answers; returns the count."""
    version = git(".", "--version").stdout.strip()
    roots = {stale: Path(tempfile.mkdtemp(prefix="guard-grammar-")) for stale in (False, True)}
    answers = {}
    try:
        worlds = {stale: world(root, stale) for stale, root in roots.items()}
        for argv in lists or argument_lists():
            answers[argv] = {"fresh": offered(*worlds[False], argv), "stale": offered(*worlds[True], argv)}
    finally:
        for root in roots.values():
            shutil.rmtree(root, ignore_errors=True)
    path.write_text(json.dumps({"git": version, "answers": answers}, indent=1, sort_keys=True) + "\n",
                    encoding="ascii")
    return len(answers)


def report(judgement):
    lines = [f"{judgement.commands} commands, {judgement.agreed} agreed, {len(judgement.missing)} without an answer"]
    for d, n in sorted(judgement.classes.items(), key=lambda item: (-item[0].fails, item[0].kind, -item[1])):
        lines.append(f"{n:6d}  {'FAIL ' if d.fails else 'noted'} {d.kind}: {d.why}   e.g. {d.example}")
    return "\n".join(lines)


def main(argv=None):
    import sys
    args = sys.argv[1:] if argv is None else argv
    if args[:1] == ["--record"]:
        print(f"recorded {record()} argument lists in {ANSWERS.name}")
    judgement = judge()
    print(report(judgement))
    return 1 if judgement.failures or judgement.missing else 0


if __name__ == "__main__":
    raise SystemExit(main())
