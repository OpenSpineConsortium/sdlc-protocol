"""Properties of the library's pure rules (DESIGN.md, 9.2 and 9.3).

Each property is a plain function: its leading asserts are the
preconditions and the asserts after them are the property, the shape
CrossHair's asserts analysis reads when a person runs it by hand (9.4),
and the shape tests/test_validation_properties.py calls over the
enumerated inputs of domains.py with nothing installed. CASES gives each
property its inputs. Under unittest a failing precondition would look
like a failing property, so the test tells the two apart by the line the
assert is on (leading_assert_lines) and filters the inputs by the
preconditions first; a property whose inputs the filter empties fails,
since it checked nothing, and one written as asserts alone is refused,
since its last assert would count as a precondition too.

UNPROVEN (unproven.json) names what no property may call, because it is
not pure: the adapters, the command line, the guard's two readers of git
and the board's writer of its file. The test prints the list and refuses
an entry that names nothing in the package.
"""

import ast
import inspect
import itertools
import json
import textwrap
from pathlib import Path

from sdlc_protocol import board, claims, coordinator, gate, guard, labels, protocol_check, team, workers

from . import domains, guard_grammar
from .domains import HOURS, WORKERS, at_hour, iso

UNPROVEN_PATH = Path(__file__).resolve().parent / "unproven.json"
RELEASER = {"author_association": "MEMBER", "user": {"login": "x"}}
STRANGER = {"author_association": "NONE", "user": {"login": "sam"}}
BRANCH, SHA = "claude/5", "f" * 40


# --- claim names ----------------------------------------------------------------

def a_claim_branch_names_one_issue(key: str) -> None:
    """A claim branch names one issue: the number is None, or branch_of
    spells the branch back, so claude/05 names no issue."""
    assert len(key) <= 3
    number = claims.number_of("claude/" + key)
    assert number is None or claims.branch_of(number) == "claude/" + key
    assert not (claims.reads_as_claim("claude/" + key) and number is not None)


def ordering_claims_never_raises(key: str) -> None:
    """The board orders every claim branch, numbers before names."""
    assert len(key) <= 3
    first, second = claims.issue_order("claude/" + key), claims.by_number(key)
    assert first == second
    assert first[0] == (0 if claims.number_in(key) is not None else 1)
    assert claims.issue_order("claude/9") < claims.issue_order("claude/100") < claims.issue_order("claude/x")


def the_board_renders_any_branch(name: str) -> None:
    """The board never fails on a branch name a hand made, and its page is
    ASCII."""
    assert len(name) <= 3
    page = board.render({"sha": "", "branches": ["claude/" + name]})
    assert page.isascii()


def a_released_branch_keeps_two_heads_apart(key: str, first: str, second: str) -> None:
    """released/<issue>-<short sha>: two heads with different short shas
    give two names."""
    assert len(key) <= 3 and len(first) == len(second) == 40
    one, two = claims.released_branch(key, first), claims.released_branch(key, second)
    assert one.startswith(claims.RELEASED_PREFIX) and one.startswith(f"released/{key}-")
    assert (one == two) == (first[:7] == second[:7])


# --- time -------------------------------------------------------------------------

def an_idle_unreleased_claim_is_due(commit_h: int, now_h: int) -> None:
    """A claim with no commit for more than a day and no release comment is
    due, exactly then: 24 hours is not idle, a minute more is."""
    assert 0 <= commit_h <= now_h <= 1000
    due = claims.release_due({"5": at_hour(commit_h)}, at_hour(now_h))
    assert due == (["5"] if now_h - commit_h > 24 else [])
    assert claims.is_idle(iso(commit_h), at_hour(now_h)) == (now_h - commit_h > 24)


def a_release_is_not_due_twice(commit_h: int, released_h: int, now_h: int) -> None:
    """A claim a release comment released is never due again, until a new
    commit; a comment made before the claim was idle releases nothing."""
    assert 0 <= commit_h <= released_h <= now_h <= 1000
    released = iso(released_h)
    due = claims.release_due({"5": at_hour(commit_h)}, at_hour(now_h), released={"5": released})
    was = claims.was_released(released, at_hour(commit_h))
    assert was == (released_h - commit_h > 24)
    assert not (was and due)
    assert not (now_h - commit_h > 24 and not was and not due)


# --- comments -------------------------------------------------------------------

def a_release_comment_has_the_marker_first(body: str, association: str) -> None:
    """The marker counts only on the first line, and an untrusted author
    never releases."""
    assert len(body) <= 80
    comment = {"body": body, "author_association": association, "user": {"login": "x"}}
    releases = claims.is_release_comment(comment)
    lines = body.strip().splitlines()
    # The spec's own set (Release: "by the repository's owner, a member of
    # its organization or a collaborator"), not the library's.
    trusted = association in ("OWNER", "MEMBER", "COLLABORATOR")
    assert releases == (bool(lines) and lines[0].strip() == "<!-- sdlc-coordinator: claim-released -->" and trusted)


def only_the_latest_comment_says_finished(first: str, second: str, author: str) -> None:
    """Only the author's latest comment decides, whatever an earlier one
    said and whatever anyone else said."""
    assert author in WORKERS
    comments = [{"body": first, "user": {"login": "a"}, "created_at": iso(1)},
                {"body": second, "user": {"login": author}, "created_at": iso(2)}]
    latest = second if author == "a" else (first if author == "a" else None)
    theirs = [c["body"] for c in comments if c["user"]["login"] == author]
    expected = bool(theirs) and theirs[-1].strip().lower().startswith("finished; ready to mark")
    assert claims.said_finished(comments, author) == expected
    assert not claims.said_finished(comments, "")
    del latest


# --- claim files ------------------------------------------------------------------

def the_worker_line_round_trips(worker: str, spelling: str) -> None:
    """The worker a claim file names comes back as written, in the Markdown
    spellings a hand gives the line; the files come back sorted, and the
    issue's files sorted and each once."""
    assert worker in WORKERS and "{}" in spelling
    text = "# Claim\n\n" + spelling.format(worker) + "\n\n## Files to touch\n\n- b.py\n- `a.py`\n- b.py\n\n## Plan\n\nfix it\n"
    assert claims.claim_worker(text) == worker
    assert claims.claim_files(text) == sorted(claims.claim_files(text))
    assert set(claims.claim_files(text)) == {"a.py", "b.py"}
    assert claims.claim_plan(text) == "fix it"
    assert claims.issue_files("### Files likely touched\n\n- b.py\nb.py\n\nprose a.py\n") == ["b.py"]


# --- labels -----------------------------------------------------------------------

def holder_names_each_once_in_order(names: list) -> None:
    """Each worker once, in the order its first label comes; any holder
    label reserves the issue, and so does epic."""
    assert len(names) <= 10
    found = labels.holder_names(names)
    assert len(found) == len(set(found))
    firsts = []
    for name in names:
        for prefix in labels.HOLDER_PREFIXES:
            if name.startswith(prefix) and name[len(prefix):] and name[len(prefix):] not in firsts:
                firsts.append(name[len(prefix):])
    assert found == firsts
    assert labels.reserved(names) == ("epic" in names or bool(found))
    issue = {"labels": names}
    assert labels.worker_of(issue) in (set(found) | {None})
    assert (labels.worker_of(issue) is None) == (not found)


# --- the page ---------------------------------------------------------------------

def state_word_is_one_of_five(state: str) -> None:
    assert len(state) <= 12
    word = board.state_word(state)
    assert word in board.STATE_WORDS


def a_cell_is_one_ascii_line(value: str) -> None:
    """A cell is one ASCII line with every pipe escaped."""
    assert len(value) <= 3
    text = board.cell(value)
    assert text.isascii() and "\n" not in text and "\r" not in text
    assert text.count("|") == text.count("\\|")


# --- overlaps ---------------------------------------------------------------------

# When each of the three pieces began, by the time GitHub gives for the push
# that made its branch, "" where it gives none: nothing said of any, an order
# against the numbers' with one piece nothing dates, and a tie.
BEGAN = (("", "", ""), (domains.iso(5), domains.iso(3), ""), (domains.iso(1), domains.iso(1), domains.iso(0)))


def overlap_snapshot(drafts, blockers, files, began=("", "", "")):
    pulls, issues = [], {}
    for n, (draft, blocker, touched) in enumerate(zip(drafts, blockers, files), start=1):
        pulls.append({"number": 100 + n, "head": {"ref": f"claude/{n}", "sha": SHA}, "draft": draft,
                      "files": [{"filename": f} for f in touched], "mergeable_state": "clean"})
        issues[str(n)] = {"state": "open", "labels": ["claimed"] + (["blocker"] if blocker else [])}
    return {"sha": SHA, "pulls": pulls, "branches": ["main"] + [f"claude/{n}" for n in range(1, len(pulls) + 1)],
            "claim_issues": issues, "began": {f"claude/{n}": at for n, at in enumerate(began, start=1) if at}}


def overlaps_list_only_meeting_work_in_landing_order(drafts: tuple, blockers: tuple, files: tuple,
                                                      began: tuple) -> None:
    """Each listed group has two or more pieces, every piece touches the
    file, the groups come in landing order, and a comment is owed to each
    piece exactly when it meets work it was not told of."""
    assert len(drafts) == len(blockers) == len(files) == len(began) == 3
    snapshot = overlap_snapshot(drafts, blockers, files, began)
    found = board.overlaps(snapshot)
    for path, works in found.items():
        assert len(works) >= 2
        assert all(path in w.files for w in works)
        # The spec's order, written out: a blocker first, then work marked
        # ready, then the oldest, the work that began first, work nothing
        # dates first of all, the number breaking a tie; not landing_key
        # itself, which would check nothing.
        for first, second in zip(works, works[1:]):
            assert first.blocker or not second.blocker
            assert first.blocker != second.blocker or first.ready or not second.ready
            if (first.blocker, first.ready) == (second.blocker, second.ready):
                assert (first.began != "", first.began, first.number) < (second.began != "", second.began,
                                                                       second.number)
    expected = {f for f in itertools.chain(*files) if sum(f in touched for touched in files) > 1}
    assert set(found) == expected
    told = {101: {102}}
    for due in coordinator.overlap_comments_due(snapshot, told):
        named = {int(n) for n in coordinator.OVERLAP_LINE.fullmatch(due.marker).group(1).split()}
        assert not named <= told.get(due.number, set())
        assert claims.number_in(str(due.number)) is not None


def told_numbers_reads_the_first_line_of_a_trusted_comment(body: str, trusted: bool) -> None:
    assert len(body) <= 80
    comment = {"body": body, **(RELEASER if trusted else STRANGER)}
    lines = body.strip().splitlines()
    match = coordinator.OVERLAP_LINE.fullmatch(lines[0].strip()) if lines else None
    numbers = [claims.number_in(n) for n in match.group(1).split()] if match else []
    expected = set(numbers) if numbers and None not in numbers and trusted else set()
    assert coordinator.told_numbers([comment]) == expected


# --- releases ---------------------------------------------------------------------

def release_snapshot(issue_labels, draft, commit_h, released_h):
    pull = {"number": 100, "head": {"ref": BRANCH, "sha": SHA}, "draft": draft, "files": [], "mergeable_state": "clean"}
    return {"sha": SHA, "pulls": [pull] if draft is not None else [], "branches": ["main", BRANCH],
            "claims": {"5": "- Worker: a\n"}, "claim_issues": {"5": {"state": "open", "labels": list(issue_labels)}},
            "releases": {"5": {"released_at": iso(released_h) if released_h is not None else "", "sha": SHA,
                               "committed_at": iso(commit_h)}}}


def a_release_takes_both_acts(issue_labels: list, draft: object, commit_h: int, released_h: object) -> None:
    """released_keys is a subset of the claim keys; released implies the
    issue open and labelled neither claimed nor in-review, no ready pull
    request, and a release comment after the claim went idle; a released
    claim's pull request is owed no comment; and the release text starts
    with the marker while the labels taken off are claimed and the
    holders' alone."""
    assert commit_h in HOURS and (released_h is None or released_h in HOURS)
    snapshot = release_snapshot(issue_labels, draft, commit_h, released_h)
    released = board.released_keys(snapshot)
    assert released <= {"5"}
    expected = (released_h is not None and released_h - commit_h > 24 and "claimed" not in issue_labels
                and "in-review" not in issue_labels and draft is not False)
    assert ("5" in released) == expected
    due = board.comments_due(snapshot, "push", "main", at_hour(1000))
    assert not (expected and any(d.number == 100 for d in due))
    text = coordinator.release_text(snapshot, "5", coordinator.Holding(1, "work", ("a.py",)))
    assert claims.starts_with_release_marker({"body": text})
    assert claims.is_release_comment({"body": text, **RELEASER})
    off = coordinator.labels_released(snapshot, "5")
    assert set(off) == {n for n in issue_labels if n == "claimed" or n.startswith(labels.HOLDER_PREFIXES)}


# --- the guard ---------------------------------------------------------------------

def the_guard_refuses_the_forbidden_shapes(option: str, spec: str) -> None:
    """A + refspec, -f, --force and a bundle that holds f, the default and
    board branches however spelled, wholesale pushes and their
    abbreviations, and a lease outside claude/ are refused; one's own claim
    branch pushed plain or by lease, a lease on any claude/ branch, since
    whose it is the guard cannot know (G11b), and a plain push of a branch
    that is no claim, pass."""
    assert option in guard_grammar.OPTIONS and spec in guard_grammar.REFSPECS
    argv = f"git push{option} origin{spec}".split()
    reason = guard.refusal(guard.push_arguments(argv), guard_grammar.CURRENT, guard_grammar.DEFAULT)
    forbidden = (option in guard_grammar.FORCE_OPTIONS or option in guard_grammar.WHOLESALE_OPTIONS
                 or spec.strip().startswith("+") or spec in guard_grammar.TO_DEFAULT or spec in guard_grammar.TO_BOARD
                 or (option == guard_grammar.LEASE and spec in guard_grammar.TO_NO_CLAIM)
                 or (spec in guard_grammar.TO_MISNAMED and option != " --delete"))
    assert (reason is not None) == forbidden


# --- the protocol check ------------------------------------------------------------

def the_protocol_check_has_four_rules(number: int, closes: bool, draft: bool, claim_file: bool, fork: bool) -> None:
    assert 1 <= number <= 9999
    head_repo = "o/fork" if fork else "o/r"
    pull = {"head": {"ref": f"claude/{number}", "repo": {"full_name": head_repo}}, "base": {"repo": {"full_name": "o/r"}},
            "body": f"Closes #{number}" if closes else "work", "draft": draft}
    problems = protocol_check.problems_for(pull, claim_file)
    assert len(problems) == fork + (not closes) + (draft != claim_file)
    assert protocol_check.problems_for({**pull, "head": {"ref": f"claude/0{number}", "repo": {"full_name": "o/r"}}},
                                       claim_file) != []
    assert protocol_check.problems_for({**pull, "head": {"ref": "feature", "repo": {"full_name": "o/r"}}}, claim_file) == []


# --- dealing and areas -----------------------------------------------------------------

def dealing_gives_each_issue_once_to_a_fitting_worker(members: int, files: tuple, area: tuple) -> None:
    """Each issue to at most one worker; every dealt issue fits its worker's
    area; greedy-maximal: a member left with none fits no issue left."""
    assert 1 <= members <= 3 and len(files) == 3
    pool = [{"number": n, "body": "### Files to touch\n\n" + "\n".join(f) + "\n"} for n, f in zip((5, 6, 7), files)]
    names = WORKERS[:members]
    areas = {"a": area}
    dealt = team.deal(list(names), pool, areas)
    numbers = [issue["number"] for issue in dealt.values()]
    assert len(numbers) == len(set(numbers))
    for member, issue in dealt.items():
        assert workers.fits(areas.get(member, ()), claims.issue_files(issue["body"]))
    for member in names:
        if member not in dealt:
            left = [i for i in pool if i["number"] not in numbers]
            assert not any(workers.fits(areas.get(member, ()), claims.issue_files(i["body"])) for i in left)


def fitting_work_is_in_the_area(area: tuple, files: tuple) -> None:
    assert len(area) <= 2 and len(files) <= 2
    fits = workers.fits(area, files)
    assert fits == (not area or not files or all(workers.in_area(area, f) for f in files))
    for pattern in area:
        regex = workers.pattern_regex(pattern)
        plain = pattern.rstrip("/")
        if "*" not in pattern and "?" not in pattern:
            assert regex.match(plain + "/under.py") and (bool(regex.match(plain)) == (not pattern.endswith("/")))
        assert regex.match("elsewhere/" + plain) is None


# --- the gate -----------------------------------------------------------------------------

def issue_number_raises_only_refused(value: str) -> None:
    assert len(value) <= 3
    try:
        number = gate.issue_number(value)
    except gate.Refused:
        return
    assert 0 <= number <= 999999999 and str(number).isascii()
    assert gate.issue_number(f"#{number}") == number


def the_main_moved_instruction_names_the_lease_when_the_policy_rebases(bring: str, merges: str) -> None:
    assert bring in gate.BRING_MAIN_IN and merges in gate.WHO_MERGES
    text = gate.main_moved_instruction(gate.Policy(bring_main_in=bring, who_merges=merges))
    assert ("--force-with-lease" in text) == (bring == gate.REBASE)
    assert text.startswith("main moved: ")


# --- the inputs -------------------------------------------------------------------------------

MARKER_BODIES = (claims.RELEASE_MARKER, claims.RELEASE_MARKER + "\nwhy", "  " + claims.RELEASE_MARKER + "  \nwhy",
                 "reply:\n" + claims.RELEASE_MARKER, "", "released", "<!-- sdlc-coordinator: claim-released -->x",
                 coordinator.OVERLAP_MARKER.format(numbers="2 3"), coordinator.OVERLAP_MARKER.format(numbers="03"),
                 "x\n" + coordinator.OVERLAP_MARKER.format(numbers="2"), coordinator.OVERLAP_MARKER.format(numbers=""))
FINISHED_BODIES = ("finished; ready to mark", "Finished; ready to mark, see above", "finished", "not finished", "")
SPELLINGS = ("- Worker: {}", "Worker: {}", "**Worker**: {}", "- Account: {} (by hand)", "* worker: `{}`")
STATES = ("clean", "behind", "dirty", "blocked", "unknown", "has_hooks", "unstable", "draft", "", "x")
AREAS = ((), ("src/",), ("src/**/*.py",), ("docs/a.md",), ("src/", "docs/"))
PATHS = ((), ("src/one.py",), ("docs/a.md",), ("src/one.py", "docs/a.md"))


def cases():
    """Each property's inputs, as argument tuples."""
    names, short = domains.names(), domains.short_names()
    bools = (False, True)
    return {
        a_claim_branch_names_one_issue: [(k,) for k in names],
        ordering_claims_never_raises: [(k,) for k in names],
        the_board_renders_any_branch: [(k,) for k in short],
        a_released_branch_keeps_two_heads_apart: [(k, "a" * 40, s) for k in ("5", "05", "x") for s in ("a" * 40, "a" * 7 + "b" * 33, "b" * 40)],
        an_idle_unreleased_claim_is_due: [(c, n) for c in HOURS for n in HOURS if c <= n],
        a_release_is_not_due_twice: [(c, r, n) for c in HOURS for r in HOURS for n in HOURS if c <= r <= n],
        a_release_comment_has_the_marker_first: [(b, a) for b in MARKER_BODIES for a in ("MEMBER", "OWNER", "NONE", "CONTRIBUTOR")],
        only_the_latest_comment_says_finished: [(f, s, a) for f in FINISHED_BODIES for s in FINISHED_BODIES for a in WORKERS],
        the_worker_line_round_trips: [(w, s) for w in WORKERS for s in SPELLINGS],
        holder_names_each_once_in_order: [(names,) for names in domains.label_sets()],
        state_word_is_one_of_five: [(s,) for s in STATES],
        a_cell_is_one_ascii_line: [(k,) for k in short],
        overlaps_list_only_meeting_work_in_landing_order: [
            (drafts, blockers, files, began) for drafts in itertools.product(bools, repeat=3)
            for blockers in itertools.product(bools, repeat=3) for files in itertools.product(domains.file_sets(), repeat=3)
            for began in BEGAN],
        told_numbers_reads_the_first_line_of_a_trusted_comment: [(b, t) for b in MARKER_BODIES for t in bools],
        a_release_takes_both_acts: [
            (ls, d, c, r) for ls in ([], ["claimed"], ["in-review"], ["claimed", "worker:a"], ["todo", "account:a"])
            for d in (None, True, False) for c in (0, 24) for r in (None, 1, 24, 25, 49)],
        the_guard_refuses_the_forbidden_shapes: sorted({(c.option, c.spec) for c in guard_grammar.commands()}),
        the_protocol_check_has_four_rules: [(n, c, d, f, k) for n in (1, 9999) for c in bools for d in bools
                                           for f in bools for k in bools],
        dealing_gives_each_issue_once_to_a_fitting_worker: [
            (m, files, area) for m in (1, 2, 3) for files in itertools.product(PATHS, repeat=3) for area in AREAS],
        fitting_work_is_in_the_area: [(a, f) for a in AREAS for f in PATHS],
        issue_number_raises_only_refused: [(k,) for k in names],
        the_main_moved_instruction_names_the_lease_when_the_policy_rebases: [
            (b, m) for b in gate.BRING_MAIN_IN for m in gate.WHO_MERGES],
    }




def properties():
    """Every property, in the order written."""
    found = [f for _, f in inspect.getmembers(inspect.getmodule(cases), inspect.isfunction)
             if f.__module__ == cases.__module__ and f.__annotations__.get("return", object) is None]
    return sorted(found, key=lambda f: inspect.getsourcelines(f)[1])


def leading_assert_lines(func):
    """The line numbers of the asserts that open the function's body: its
    preconditions."""
    source, first = inspect.getsourcelines(func)
    tree = ast.parse(textwrap.dedent("".join(source)))
    body = tree.body[0].body
    if body and isinstance(body[0], ast.Expr) and isinstance(getattr(body[0], "value", None), ast.Constant):
        body = body[1:]
    lines = []
    for node in body:
        if not isinstance(node, ast.Assert):
            break
        lines.append(first + node.lineno - 1)
    else:
        raise ValueError(f"{func.__name__} is all preconditions: no statement follows them, so no line is the property")
    return set(lines)


class Outcome:
    def __init__(self, name):
        self.name, self.checked, self.filtered, self.failures = name, 0, 0, []


def check(func, inputs):
    """Run a property over its inputs: the count checked, the count the
    preconditions filtered, and the failures (inputs, error)."""
    out, pre = Outcome(func.__name__), leading_assert_lines(func)
    for args in inputs:
        try:
            func(*args)
        except AssertionError as error:
            line = error.__traceback__.tb_next.tb_lineno if error.__traceback__.tb_next else None
            if line in pre:
                out.filtered += 1
                continue
            out.failures.append((args, f"line {line}"))
        except Exception as error:  # noqa: BLE001 - a property that raises has failed
            out.failures.append((args, repr(error)))
        else:
            out.checked += 1
    return out


def unproven():
    return json.loads(UNPROVEN_PATH.read_text(encoding="ascii"))


def stale_unproven(entries=None):
    """The unproven entries that name nothing in the package."""
    import sdlc_protocol
    root = Path(sdlc_protocol.__file__).resolve().parent
    stale = []
    for entry in entries if entries is not None else unproven():
        name = entry["name"]
        if name.endswith("/") or name.endswith(".py"):
            if not (root / name).exists():
                stale.append(name)
            continue
        module, _, attribute = name.partition(".")
        found = getattr(sdlc_protocol, module, None)
        if found is None:
            import importlib
            try:
                found = importlib.import_module(f"sdlc_protocol.{module}")
            except ImportError:
                found = None
        if found is None or not hasattr(found, attribute):
            stale.append(name)
    return stale


def run_lane():
    return [check(func, inputs) for func, inputs in cases().items()]


def report(outcomes):
    lines = []
    for out in outcomes:
        word = "FAIL" if out.failures or not out.checked else "ok"
        lines.append(f"{word:4} {out.name}: {out.checked} checked, {out.filtered} filtered"
                     + (f", first failure {out.failures[0]!r}" if out.failures else ""))
    return "\n".join(lines)


def main():
    stale = stale_unproven()
    print("unproven: " + "; ".join(f"{e['name']} ({e['reason']})" for e in unproven()))
    outcomes = run_lane()
    print(report(outcomes))
    if stale:
        print(f"stale unproven entries: {stale}")
    return 1 if stale or any(o.failures or not o.checked for o in outcomes) else 0


if __name__ == "__main__":
    raise SystemExit(main())
