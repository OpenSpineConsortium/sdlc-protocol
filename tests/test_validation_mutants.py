"""The mutation lane's mutants (validation/mutants.py), in the required
check: the lane's size, every mutant's target still in the library, and
each mutant caught on its shortest trace, which the library itself passes
(or, for a lane, on something beyond what the lane knows red as built).
The lane proper, every mutant against every search, runs in validate.yml
(python3 -m validation.mutants), since it takes about two minutes; these
traces are what the lane found, kept so a change that stops the harness
catching a mutant fails here too, naming the rule. A mutant of a rule no
search runs names the cheap lane that catches it instead (step 4), and
the test runs that lane."""

import ast
import inspect
import sys
import textwrap
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sdlc_protocol.adapters import github  # noqa: E402
from validation import cycle, dealing, invariants, model as M, mutants, oracle, replay, verbs  # noqa: E402
from validation.verbs import DANA, SAM, Scope, Verb  # noqa: E402

BOARD = Verb("board", "board", (cycle.SCHEDULE, ""))
CLAIM, CLAIMED, WORKER = mutants.CLAIM, mutants.CLAIMED, mutants.WORKER
HELD = [CLAIM, CLAIMED, WORKER]
WAIT = Verb("wait", "clock", (25,))
SHARED = Scope(issues=(1, 2), files=((1, mutants.SHARED), (2, mutants.SHARED)))
# The seeded issue carries todo; the searches F15's review added seed it
# with other labels, which a trace here puts on by hand first.
TODO_OFF = Verb("label-off", M.OWNER, (1, "todo"))
BLOCKER = Verb("label-on", M.OWNER, (1, "blocker"))
# sam's draft from a fork whose branch is named claude/1 there (F17).
FORK_DRAFT = Verb("fork-pull", SAM, (1, False))
FORKS = Scope(forks=True)
# sam's draft from the claim's own branch here, to which sam may not
# write (F19).
STRANGER_DRAFT = Verb("stranger-pull", SAM, (1, False))
STRANGER_READY = Verb("stranger-pull", SAM, (1, True))
STRANGERS = Scope(strangers=True)
# b's claim on #2 beside a's on #1, both listing one file, #2 then
# labelled a blocker (F15's fifth review).
LATER_BLOCKER = [CLAIM, Verb("claim", "b", (2,)), Verb("label-on", M.OWNER, (2, "blocker"))]


def labelled(name):
    return Verb("label-on", M.OWNER, (1, name))


def board_with(fault):
    return Verb("board", "board", (cycle.SCHEDULE, "", (fault,)))


# Each mutant's shortest trace, as the lane found it, with the scope it needs.
TRACES = {
    "idle at exactly 24 hours": (HELD + [Verb("wait", "clock", (24,)), BOARD], Scope()),
    "a 36-hour idle time": (HELD + [WAIT, BOARD], Scope()),
    "the worker label left on at release": (HELD + [WAIT, BOARD], Scope()),
    "a release comment accepted from any author": (HELD + [WAIT, Verb("marker", SAM, (1,)), BOARD], Scope()),
    "an idle comment to a released claim's pull request": (HELD + [Verb("open-draft", "a", (1,)), WAIT, BOARD],
                                                           Scope()),
    "the comment before the labels": (HELD + [WAIT, BOARD], Scope()),
    "a refused rename never said": (HELD + [WAIT, board_with("rename-403")], Scope()),
    "a release ignores the label claimed": (HELD + [WAIT, Verb("marker", M.OWNER, (1,)), BOARD], Scope()),
    "a finished draft released by the coordinator": (HELD + [WAIT, Verb("open-draft", "a", (1,)),
                                                             Verb("finished", "a", (100,)), BOARD], Scope()),
    "any comment counts as a release": (HELD + [WAIT, Verb("plan", "a", (1,)), BOARD], Scope()),
    "a board comment posted again": (HELD + [WAIT, BOARD, Verb("push-old-head", "a", (1, "released/1-6d6ae03")),
                                             BOARD], Scope()),
    "an opaque state read by GitHub's word alone": "the regression traces",
    "a clean word compared": "the regression traces",
    "the comparison read by ahead_by": "the regression traces",
    "main moved told only in the push's run": "the regression traces",
    "the rename skipped": (HELD + [WAIT, BOARD], Scope()),
    "overlaps told on every run": ([CLAIM, Verb("claim", "b", (2,)), BOARD], SHARED),
    "the labels not put back": (HELD + [WAIT, board_with("comment-500")], Scope()),
    "a lost comment's release not seen": (HELD + [WAIT, board_with("comment-lost")], Scope()),
    "an unsettled release put back": "the regression traces",
    "an unsettled release counted": "the regression traces",
    "an unsettled release failing the run": "the regression traces",
    "a writer's release not vouched for": (HELD + [WAIT, Verb("marker", DANA, (1,)), BOARD], Scope()),
    "a claim number with leading zeros": ([CLAIM, CLAIMED, Verb("push-by-hand", M.OWNER, (1, "claude/01"))],
                                          Scope(careless=True)),
    "the guard passes a misnamed claim": ([CLAIM, CLAIMED, Verb("claim-misnamed", "a", (1, "claude/01"))],
                                          Scope(careless=True)),
    "the rename made without reading the head again": (HELD + [WAIT, Verb("board-start", "board", (cycle.SCHEDULE, "")),
                                                               Verb("commit", "a", (1,)), Verb("board-finish", "board")],
                                                       Scope(split_board=True)),
    "the guard passes --force": "the guard grammar against git's answers",
    "the guard passes a lease outside claude/": "the guard grammar against git's answers",
    "the guard passes a lease onto another's claim": "the regression traces",
    "dealing without its taken set": "dealing at every look",
    "each leg dealt from its own look": "the regression traces",
    "given to workers needing the label todo": "the regression traces",
    "a release that leaves todo off": "the regression traces",
    "a failed release leaves its todo on": "the regression traces",
    "the release's own page without its todo": "the regression traces",
    "a refused todo puts no label back": ([TODO_OFF] + HELD + [WAIT, board_with("label-post-500")], Scope()),
    "a todo written and lost stays on": ([TODO_OFF] + HELD + [WAIT, board_with("label-post-lost")], Scope()),
    "an epic gets todo": ([TODO_OFF, Verb("label-on", M.OWNER, (1, "epic"))] + HELD + [WAIT, BOARD], Scope()),
    "a blocker gets todo": ([TODO_OFF, Verb("label-on", M.OWNER, (1, "blocker"))] + HELD + [WAIT, BOARD], Scope()),
    "a blocked task gets no todo": ([TODO_OFF, Verb("label-on", M.OWNER, (1, "blocked"))] + HELD + [WAIT, BOARD],
                                    Scope()),
    "the Blockers section left empty": ([BLOCKER, BOARD], Scope()),
    "the Blockers section without the held blockers": ([BLOCKER] + HELD + [BOARD], Scope()),
    "a held blocker's row without claimed": ([BLOCKER] + HELD + [BOARD], Scope()),
    "a row without blocked": ([labelled("blocked"), BOARD], Scope()),
    "a row without epic": ([BLOCKER, labelled("epic"), BOARD], Scope()),
    "the Blockers section without epic blockers": ([BLOCKER, labelled("epic"), BOARD], Scope()),
    "the Blockers section without blocked blockers": ([BLOCKER, labelled("blocked"), BOARD], Scope()),
    "the Blockers section without the given blockers": ([BLOCKER, labelled("worker:b"), BOARD], Scope()),
    "the Queue without the blockers labelled todo": ([BLOCKER, BOARD], Scope()),
    "a release relabels the board before its comment": ([BLOCKER] + HELD + [WAIT, board_with("comment-500")],
                                                         Scope()),
    "a bare worker: taken off at release": ([labelled("worker:")] + HELD + [WAIT, BOARD], Scope()),
    "the landing order by number": "the regression traces",
    "a pull request dated by its opening": "the regression traces",
    "a refused activity read stops the run": "the regression traces",
    "work nothing dates landing first": "the regression traces",
    "a branch never asked about on its own": "the regression traces",
    "an overlap marker read on any line": ([CLAIM, Verb("claim", "b", (2,)), Verb("overlap-quote", "a", (1, (2,))),
                                            BOARD], SHARED),
    "an overlap marker trusted from any author": ([CLAIM, Verb("claim", "b", (2,)),
                                                   Verb("overlap-marker", SAM, (1, (2,))), BOARD],
                                                  SHARED._replace(people=(SAM,))),
    "a sort key that calls int after isdigit": "the regression traces",
    "a branch name sent unquoted": "the regression traces",
    "a finished draft's release by hand not seen": "the regression traces",
    "a refused comment stops the run": "the regression traces",
    "a fork's ready pull request finishes a claim": "the regression traces",
    "a fork's ready pull request skipped as a claim's": "the regression traces",
    "a fork's finished draft holds a claim": (HELD + [WAIT, FORK_DRAFT, Verb("finished", SAM, (100,)), BOARD],
                                              FORKS),
    "a fork's ready pull request keeps a release from counting": "the regression traces",
    "a fork's pull request silenced with a released claim": "the regression traces",
    "a fork's pull request left off with a released claim": "the regression traces",
    "a fork's pull request named on a claim's row": (HELD + [FORK_DRAFT, BOARD], FORKS),
    "a fork's pull request taken for a claim's work": (HELD + [FORK_DRAFT, BOARD], FORKS),
    "a fork's pull request never told main moved": "the regression traces",
    "no pull request taken for a stranger's": "the regression traces",
    "a stranger's ready pull request finishes a claim": "the regression traces",
    "a stranger's ready pull request skipped as a claim's": "the regression traces",
    "a stranger's finished draft holds a claim": (HELD + [WAIT, STRANGER_DRAFT, Verb("finished", SAM, (100,)),
                                                         BOARD], STRANGERS),
    # GitHub will not say whether sam, then dana, may write (the third
    # element seeds the model's unanswered): sam's ready pull request from
    # the claim's branch stays the claim's, which is finished, and dana's
    # release marker counts for nothing, so the coordinator releases the
    # idle claim itself (F19's review).
    "a refused permission answer taken as no for a pull request": (HELD + [STRANGER_READY, WAIT, BOARD], STRANGERS,
                                                                   (SAM,)),
    "a refused permission answer taken as yes for a release comment": (
        HELD + [WAIT, Verb("marker", DANA, (1,)), Verb("label-off", M.OWNER, (1, "claimed")), BOARD],
        Scope(people=(M.OWNER, DANA)), (DANA,)),
    "a stranger's ready pull request keeps a release from counting": "the regression traces",
    "a piece of work named by the number it is shown by": "the regression traces",
    "the markers on a claim's own pull request never read": "the regression traces",
    "a marker counted whenever it came": "the regression traces",
    "an undated claim taking no marker": "the regression traces",
    "idleness by the committer date": "the regression traces",
    "a kept head dated by its push": "the regression traces",
    "kept counts only at or after the push": "the regression traces",
    "a kept head released again": "the regression traces",
    "a kept release undone holds for good": "the regression traces",
    "the push read once": "the regression traces",
    "the before side ignored": "the regression traces",
    # F8's fix check: c's commits dated back, read with both reads a push
    # behind, a second refused, a second listing a push after the run read
    # the branch, and two such pushes, at the end of F8's trace.
    "a fresh entry dating only off the head": "the regression traces",
    "the floor dropped": "the regression traces",
    "the floor of the last read alone": "the regression traces",
    "the second read judged with no moment of the run": "the regression traces",
    "a floor at the committer date taken for later": "the regression traces",
    # F8's third review: an activity catching up, a refusal then a read a
    # push behind, the branch's delete after the run read it, and the run a
    # claim's first push started, at the end of F8's trace.
    "the floor of the first read alone": "the regression traces",
    "the earlier floor": "the regression traces",
    "a fresh delete dates nothing": "the regression traces",
    "the run's event ignored": "the regression traces",
    "an older push trusted in the run its push started": "the regression traces",
    # F8's second review: a's claim commit, dated two days ahead, pushed
    # back with a lease a day after a commit moved the branch off it, the
    # activity a push behind; and a kept head pushed again, its release
    # undone by the label claimed, released once idle by a run whose
    # comment GitHub refuses. No search reaches either: none offers the
    # drop of commits, and the kept head needs a release, a push and a
    # day; F8's trace plays both.
    "a stale entry off the head dates it": ([Verb("claim", "a", (1, 48 * M.HOUR)), CLAIMED, Verb("wait", "clock", (1,)),
                                             Verb("commit", "a", (1,)), WAIT, Verb("drop-commits", "a", (1,)),
                                             board_with("pushes-lagging")], Scope()),
    "a kept head's old release taken for the lost comment": (
        HELD + [WAIT, BOARD, Verb("push-old-head", "a", (1, "released/1-6d6ae03")), CLAIMED, WAIT,
                board_with("comment-500")], Scope()),
    "a stranger's pull request named on a claim's row": "the regression traces",
    "a stranger's pull request taken for a claim's work": "the regression traces",
    "a stranger's pull request listed after its release": "the regression traces",
    "a stranger's pull request spoken to after its release": "the regression traces",
    "the clerk carrying by labels alone": "the regression traces",
    "a shared task dealt to none": "the regression traces",
    "a shared task before one given alone": "the regression traces",
    "a blocked claim carried": "the regression traces",
    # Two rules of the clerk F18's trace does not reach are caught by the
    # clerk's deal from one look against Pick (dealing.picking_lane), each
    # on a case of that lane, judged with each worker's run alone too, as
    # the lane's first setting is; so are the three the review of F18's
    # fix added: a released claim, a Worker line naming nobody, and a run
    # of one worker dealt over the run alone, which gives b a task the
    # team's deal gives a.
    "the clerk dealing blockers in number order": dealing.Case({1: ("todo",), 2: ("blocker",)}, 2, "none", ("a",)),
    "the clerk dealing an issue labelled blocked": dealing.Case({1: ("blocked", "todo")}, 1, "none", ("a", "b")),
    "the Overlaps landing a blocker after other work": (LATER_BLOCKER + [BOARD], SHARED),
    "a released claim carried by its holder": dealing.Case({1: ("todo",)}, 1, "released", ("a", "b")),
    "a Worker line naming nobody taken for a name": dealing.Case({1: ("worker:b",)}, 1, "blank", ("a", "b")),
    "a run dealt as though it were the whole team": dealing.Case({1: ("todo", "worker:a", "worker:b")}, 1, "none",
                                                                 ("a", "b")),
    # F8's review: the clerk reads a release as the board does, a claim
    # dated by its push though its commit is dated ahead, and a head a
    # release kept counted released whenever its comment came.
    "the clerk dating by the committer date": dealing.Case({1: ("todo",)}, 1, "released-ahead", ("a", "b")),
    "the clerk ignoring a kept head": dealing.Case({1: ("todo",)}, 1, "kept", ("a", "b")),
    # The release comment GitHub writes and answers with a 502: the client
    # asks again only after a rate limit's refusal, so under the mutant it
    # posts the comment a second time, two release comments in one run.
    "a 5xx retried as a rate limit": (HELD + [WAIT, board_with("comment-lost")], Scope()),
    # The second review of the client's waits, each mutant on the fault
    # that shows it: the read of the branches refused with a short
    # retry-after and the token's hour spent, owed a wait until the hour's
    # end, and, its words naming no rate limit, owed one at all; a rename
    # refused for a rate limit on every try, owed a wait before each try
    # and no word on the issue; a push's read refused for good, owed no
    # wait; a push's read refused for longer than a run waits, read once; a
    # 429, owed a minute's wait; and the read after a release comment
    # refused on every try, owed none once the run's waits are spent.
    "retry-after waited in place of the hour's end": ([board_with("branches-limited-for-the-hour")], Scope()),
    "retry-after not telling a rate limit": ([board_with("branches-limited-for-the-hour")], Scope()),
    "a rate limit's words not telling one": (HELD + [WAIT, board_with("rename-limited")], Scope()),
    "any 403 taken for a rate limit": (HELD + [WAIT, board_with("pushes-403")], Scope()),
    "a rate limit's refusal read again after the pause": (HELD + [WAIT, board_with("pushes-limited-too-long")],
                                                          Scope()),
    "the client never waiting": ([board_with("branches-429")], Scope()),
    "no bound on a run's waits": (HELD + [WAIT, board_with("comment-limited-then-unread")], Scope()),
    "a rename a rate limit refused said refused for good": (HELD + [WAIT, board_with("rename-limited")], Scope()),
}
LANES = {lane.name: lane for lane in mutants.LANES}


def body_lines(function):
    """The function's body after its docstring, a line each, stripped."""
    source = textwrap.dedent(inspect.getsource(function))
    statements = ast.parse(source).body[0].body
    first = statements[1] if isinstance(statements[0], ast.Expr) else statements[0]
    return [line.strip() for line in source.splitlines()[first.lineno - 1:] if line.strip()]


def violations(steps, scope, unanswered=()):
    model = M.seeded(scope.issues, writers=("a", "b", "c", verbs.CAROL, DANA))
    model.unanswered = frozenset(unanswered)
    cycle.board_run(model)
    found = []
    for step in steps:
        before = model.clone()
        outcome = verbs.apply(model, step, scope)
        found += invariants.step_checks(before, model, step, outcome, oracle.AS_BUILT)
    return found


class TheLane(unittest.TestCase):
    def test_it_holds_10_to_100_mutants_each_with_a_trace(self):
        """Section 7 asks for 10 to 20; step 4 added five for the rules its
        lanes judge, step 5 four that undo fixes of 0.4.1, and each fix
        since at least one that undoes it (F7's first), and the reviews of
        F13's and F15's fixes more, and F17's fix one for each place that
        read a claim's pull request by its branch's name it has a check for
        and its review one more, F18's fix four, with three for the rules
        F15's fifth review found no catcher of, and F19's fix nine, one
        for each reading that took a pull request from a claim's own
        branch for the claim's and one for the adapter's marking, and its
        review two that read a permission answer GitHub refused the other
        way, and F2's fix one, a piece of work named by the number it is
        shown by, with three from its two reviews, and F8's fix two,
        idleness by the committer date and a kept head dated by its push,
        and its review seven: a kept head released again, a kept release
        undone holding for good, kept counting only from the push on, the
        push read once, the before side ignored, and the clerk dating by
        the committer date or ignoring a kept head, and its second review
        two: a stale entry off the head dating it, and a kept head's old
        release taken for the comment a failed post would have written, and
        its fix check five: a fresh entry dating only where it moved the
        branch off the head, the floor dropped, the floor of the last read
        alone, the second read judged with no moment of the run, and a
        floor at the committer date taken for later, and its third review
        four: the floor of the first read alone, the earlier floor, a fresh
        delete dating nothing, and the run's event ignored, so the bound is
        widened to 120 and the count noted; its follow-up's second review
        one, an older push trusted in the run its push started; F20's fix
        three, an unsettled release put back, counted, or failing the run;
        and one from the review of the client's waits for a rate limit, a
        5xx retried as a rate limit, and eight from its second review, one
        for each rule of the client's waits, so the bound is widened to
        150."""
        self.assertTrue(10 <= len(mutants.MUTANTS) <= 150)
        self.assertEqual(len(mutants.MUTANTS), 126)
        self.assertEqual(sorted(TRACES), sorted(mutants.MUTANTS))

    def test_every_mutant_names_a_rule_the_library_still_has(self):
        """A mutant whose target was renamed would patch nothing the library
        calls, and so be caught by nothing; one that changes a value the
        library reads, as board.UNDATED, puts one of the same type in."""
        for name, mutant in mutants.MUTANTS.items():
            with self.subTest(mutant=name):
                target = getattr(mutant.module, mutant.name)
                if callable(mutant.replacement):
                    self.assertTrue(callable(target))
                else:
                    self.assertIs(type(target), type(mutant.replacement))
                self.assertIn(mutant.guarantee, invariants.GUARANTEES)

    def test_the_copies_of_releases_follow_the_library_but_for_the_rule_each_changes(self):
        """A mutant that copies github.releases to change one rule tests an
        older library once the rest of the copy falls behind, as F8's
        review checked of the copies' dating of a head (head_since, from
        3c0ee51 on). Each line of a copy's body, the module's name taken
        off, is a line of the library's, but for the lines the mutant
        changes; and the library's lines the copy lacks are the ones it
        leaves out on purpose."""
        library = body_lines(github.releases)
        for copy, changed, dropped in (
                (mutants.releases_reading_drafts,
                 ['drafts = {(p.get("head") or {}).get("ref"): p for p in pulls if p.get("draft") and counted(p)}'],
                 ['drafts = {(p.get("head") or {}).get("ref"): p for p in pulls if p.get("draft") and is_claims(p)}']),
                (mutants.releases_skipping_finished,
                 ["if finished_draft(api, drafts.get(branch)):"],
                 ["finished = finished_draft(api, drafts.get(branch))",
                  "if finished and labels.CLAIMED in found_issues[key][\"labels\"]:",
                  "if finished and not board.release_counts(release):"])):
            with self.subTest(copy=copy.__name__):
                lines = [line.replace("github.", "") for line in body_lines(copy)]
                self.assertEqual([line for line in lines if line not in library], changed)
                self.assertEqual([line for line in library if line not in lines], dropped)

    def test_the_copy_of_head_since_follows_the_library_but_for_the_rules_it_takes(self):
        """The mutants of head_since since F8's fix check change one rule
        each of one copy (mutants.head_since_copy), which would test an
        older library once the rest fell behind; so each line of its body,
        the module's name taken off, is one of the library's but for the
        six lines that take the rules as arguments, and the library's
        lines it lacks are those six as the library writes them. With
        every argument left as it is, the copy plays F8's trace green, as
        the library does."""
        lines = [line.replace("github.", "") for line in body_lines(mutants.head_since_copy)]
        library = body_lines(github.head_since)
        self.assertEqual([line for line in lines if line not in library],
                         ["if first.again and again:",
                          "read = read_push(api, branch, sha, now if now_again else None)",
                          "if read.pushed and (branch != started_by or not (by_the_run and older) "
                          "or came_since(read.pushed, now)):",
                          "floor, why = floor_of(read, first), read.why or (first.why if first.floor else \"\")",
                          "if branch == started_by and by_the_run:",
                          "if floor and later(floor, committed):"])
        self.assertEqual([line for line in library if line not in lines],
                         ["if first.again:", "read = read_push(api, branch, sha, now)",
                          "if read.pushed and (branch != started_by or came_since(read.pushed, now)):",
                          "floor, why = latest(read.floor, first.floor), read.why or (first.why if first.floor else \"\")",
                          "if branch == started_by:",
                          "if floor and latest(committed, floor) != committed:"])

        def as_built(api, branch, sha, refused=None, now=None, started_by=""):
            return mutants.head_since_copy(api, branch, sha, refused, now, started_by)

        with mock.patch.object(github, "head_since", as_built):
            record = replay.replay(replay.traces()["F8"])
        self.assertEqual(replay.results(record), set(), "\n".join(record.timeline))

    def test_the_copy_of_explain_refusal_follows_the_library_but_for_the_skip(self):
        """The mutant that says a rename a rate limit refused was refused for
        good copies explain_refusal without its skip of a rate limit's
        refusal; each line of its body, the module's name taken off, is the
        library's but for that one test, so the copy tests the library as
        it stands but for the rule it changes."""
        lines = [line.replace("github.", "") for line in body_lines(mutants.explain_refusal_of_a_rate_limit_too)]
        library = body_lines(github.explain_refusal)
        self.assertEqual([line for line in lines if line not in library],
                         ["if problem.status not in REFUSED_FOR_GOOD:"])
        self.assertEqual([line for line in library if line not in lines],
                         ["if problem.status not in REFUSED_FOR_GOOD or rate_limited(problem.status, problem.headers, "
                          "problem.detail):"])

    def test_every_replacement_takes_what_its_target_is_called_with(self):
        """A replacement that no longer takes its target's arguments is
        caught only by the TypeError it raises, which proves nothing of the
        rule it stands for: the second review of F15's fix found "the labels
        not put back" caught so once put_back took the todo too. Each
        replacement of a function takes every argument the function
        declares, and, as the third review asked, the function's required
        arguments alone, as a caller that leaves the optional ones out
        calls it."""
        for name, mutant in mutants.MUTANTS.items():
            target = getattr(mutant.module, mutant.name)
            if not inspect.isfunction(target):
                continue
            with self.subTest(mutant=name):
                declared = inspect.signature(target).parameters.values()
                positional = [p for p in declared if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]
                keywords = [p for p in declared if p.kind == p.KEYWORD_ONLY]
                replacement = inspect.signature(mutant.replacement)
                replacement.bind(*range(len(positional)), **{p.name: None for p in keywords})
                required = [p for p in positional if p.default is p.empty]
                replacement.bind(*range(len(required)), **{p.name: None for p in keywords if p.default is p.empty})

    def test_the_lanes_come_first_then_the_searches_cheapest_first_then_the_two_widest_lanes(self):
        self.assertEqual([c.name for c in mutants.CATCHERS[:len(LANES)]], list(LANES))
        self.assertEqual(mutants.SEARCHES[-5].name, "a stranger's pull request from a fork on a due claim")
        self.assertEqual(mutants.SEARCHES[-4].name, "a stranger's pull request from a due claim's own branch")
        self.assertEqual(mutants.SEARCHES[-3].name, "a stranger's pull request from a due claim's own branch, GitHub "
                                                    "refusing to say who may write")
        self.assertEqual(mutants.SEARCHES[-2].name, "the holder back between the halves of a run")
        self.assertEqual(mutants.SEARCHES[-1].name, "a stranger's marker on overlapping claims")
        self.assertEqual(mutants.CATCHERS[len(LANES):-2], mutants.SEARCHES)
        self.assertIs(mutants.CATCHERS[-2], mutants.PICKING)
        self.assertIs(mutants.CATCHERS[-1], mutants.LABEL_MIXES)
        self.assertEqual([s.known for s in mutants.CATCHERS if s.known], [], "nothing is known on the library now")


class EachMutantIsCaught(unittest.TestCase):
    def test_on_its_shortest_trace_which_the_library_passes(self):
        for name, trace in TRACES.items():
            with self.subTest(mutant=name):
                if isinstance(trace, str):
                    lane = LANES[trace]
                    plain = lane.run()
                    self.assertEqual(set(plain) - set(lane.known), set(), "the library itself passes the lane")
                    with mutants.switched_on(mutants.MUTANTS[name]):
                        found = lane.run()
                    self.assertNotEqual(set(found) - set(plain), set())
                    continue
                if isinstance(trace, dealing.Case):
                    self.assertEqual(dealing.judge_case(trace, runs=True)[1], [],
                                     "the library itself deals the case by Pick")
                    with mutants.switched_on(mutants.MUTANTS[name]):
                        self.assertNotEqual(dealing.judge_case(trace, runs=True)[1], [])
                    continue
                steps, scope, *unanswered = trace
                self.assertEqual(violations(steps, scope, *unanswered), [], "the library itself passes the trace")
                with mutants.switched_on(mutants.MUTANTS[name]):
                    found = violations(steps, scope, *unanswered)
                self.assertNotEqual(found, [])


class TheSearchesCatchWhatTheyWereAddedFor(unittest.TestCase):
    def test_the_search_under_every_fault_catches_the_client_s_mutants_on_the_faults_added_for_them(self):
        """The lane credits a mutant to the shortest violation it meets, so
        a rate limit's refusal read again after the pause is credited to
        pushes-429-for-the-run, where the client made all its tries. The
        search under every fault is held here to catch it on
        pushes-limited-too-long too, where the client made one try, as the
        harness could only once the fake kept the client's waits, and to
        catch each mutant of the client on the fault the second review of
        the client's waits added for it: it finds nothing on the library
        with that fault alone, and the violation the mutant names."""
        search = next(s for s in mutants.SEARCHES if s.name == "a released claim under every fault")
        for name, fault in (("a rate limit's refusal read again after the pause", "pushes-limited-too-long"),
                            ("retry-after waited in place of the hour's end", "branches-limited-for-the-hour"),
                            ("retry-after not telling a rate limit", "branches-limited-for-the-hour"),
                            ("a rate limit's words not telling one", "rename-limited"),
                            ("a rename a rate limit refused said refused for good", "rename-limited")):
            alone = search._replace(scope=search.scope._replace(faults=(fault,)))
            mutant = mutants.MUTANTS[name]
            with self.subTest(mutant=name):
                self.assertEqual(mutants.violations(alone), {})
                with mutants.switched_on(mutant):
                    self.assertIn(mutant.guarantee, {g for g, _ in mutants.violations(alone)})

    def test_a_draft_github_calls_blocked_catches_both_of_f9_s_mutants(self):
        """The lane credits a mutant to the first catcher, the regression
        traces for F9's two, so the search added with F9's fix (DESIGN.md,
        7 and section 13) is held here: it finds nothing on the library and
        a G10 violation under each mutant, which it can only while the
        search starts with GitHub calling the draft blocked."""
        search = next(s for s in mutants.SEARCHES if s.name == "a draft GitHub calls blocked")
        self.assertTrue(search.opaque_pulls)
        self.assertEqual(mutants.violations(search), {})
        for name in ("an opaque state read by GitHub's word alone", "the comparison read by ahead_by"):
            with self.subTest(mutant=name), mutants.switched_on(mutants.MUTANTS[name]):
                self.assertIn("G10", {guarantee for guarantee, _ in mutants.violations(search)})

    def test_a_draft_github_calls_clean_while_it_is_behind_catches_f16_s_mutant(self):
        """As F9's search: the lane credits F16's mutant to the regression
        traces, so the search added with F16 is held here. It finds nothing
        on the library, which tells a branch GitHub calls clean nothing, as
        the spec says, and finds "main moved" made and not owed under the
        mutant that compares such a branch, which it can only while GitHub
        calls the branch clean when it is behind."""
        search = next(s for s in mutants.SEARCHES if s.name == "a draft GitHub calls clean while it is behind")
        self.assertTrue(search.lenient_rules)
        self.assertEqual(mutants.violations(search), {})
        with mutants.switched_on(mutants.MUTANTS["a clean word compared"]):
            found = mutants.violations(search)
        self.assertIn("G10", {guarantee for guarantee, _ in found})
        self.assertTrue(any("main-moved" in detail and "not owed" in detail for _, detail in found), found)

    def test_a_stranger_s_pull_request_from_a_fork_catches_every_mutant_of_f17_s_fix(self):
        """The lane credits six of F17's nine mutants to the regression
        traces, so the search added with F17's fix is held here to all
        nine: it finds nothing on the library, and the violation each
        mutant names, which it can only while a stranger may open a pull
        request from a fork whose branch bears the claim's name, and, for
        "main moved", while the owner may move main (F17's review)."""
        search = next(s for s in mutants.SEARCHES if s.name == "a stranger's pull request from a fork on a due claim")
        self.assertTrue(search.scope.forks)
        self.assertIn(M.OWNER, search.scope.people)
        self.assertEqual(mutants.violations(search), {})
        for name, mutant in mutants.MUTANTS.items():
            if "(F17)" not in mutant.why:
                continue
            with self.subTest(mutant=name), mutants.switched_on(mutant):
                self.assertIn(mutant.guarantee, {g for g, _ in mutants.violations(search)})

    def test_a_stranger_s_pull_request_from_a_due_claim_s_own_branch_catches_every_mutant_of_f19_s_fix(self):
        """The lane credits eight of F19's nine mutants to the regression
        traces, so the search added with F19's fix is held here to all
        nine: it finds nothing on the library, and the violation each
        mutant names, which it can only while sam, who may not write, may
        open a pull request from the claim's own branch here."""
        search = next(s for s in mutants.SEARCHES
                      if s.name == "a stranger's pull request from a due claim's own branch")
        self.assertTrue(search.scope.strangers)
        self.assertIn(M.OWNER, search.scope.people)
        self.assertEqual(mutants.violations(search), {})
        for name, mutant in mutants.MUTANTS.items():
            if "(F19)" not in mutant.why:
                continue
            with self.subTest(mutant=name), mutants.switched_on(mutant):
                self.assertIn(mutant.guarantee, {g for g, _ in mutants.violations(search)})

    def test_github_refusing_to_say_who_may_write_catches_the_two_readings_of_a_refusal(self):
        """F19's review: the spec's sentence on an answer GitHub will not
        give had no state in the harness. The search added with the review
        seeds sam and dana as logins GitHub answers 403 for; it finds
        nothing on the library, and catches the mutant that reads the
        refusal as no for a pull request, which frees a finished claim,
        and the one that reads it as yes for a release comment, which the
        first search, where GitHub answers, cannot tell from the library."""
        search = next(s for s in mutants.SEARCHES if s.name.endswith("GitHub refusing to say who may write"))
        plain = next(s for s in mutants.SEARCHES if s.name == "a stranger's pull request from a due claim's own branch")
        self.assertEqual(search.unanswered, (SAM, DANA))
        self.assertTrue(search.scope.strangers)
        self.assertIn(DANA, search.scope.people)
        self.assertEqual(mutants.violations(search), {})
        for name in ("a refused permission answer taken as no for a pull request",
                     "a refused permission answer taken as yes for a release comment"):
            mutant = mutants.MUTANTS[name]
            self.assertIn("(F19's review)", mutant.why)
            with self.subTest(mutant=name), mutants.switched_on(mutant):
                self.assertIn(mutant.guarantee, {g for g, _ in mutants.violations(search)})
                self.assertEqual(mutants.violations(plain), {}, "told from the library only where GitHub refuses")

    def test_two_claims_on_one_file_the_later_a_blocker_catches_the_landing_order_without_blockers(self):
        """The search added with F18's fix for a rule F15's fifth review
        found no catcher of: it finds nothing on the library and, under the
        mutant that lands a blocker by when it began, the Overlaps in
        another order than the oracle's, which it can only while the later
        claim's issue is labelled a blocker."""
        search = next(s for s in mutants.SEARCHES if s.name == "two claims on one file, the later a blocker")
        self.assertEqual(search.prefix[-1], Verb("label-on", M.OWNER, (2, "blocker")))
        self.assertEqual(mutants.violations(search), {})
        with mutants.switched_on(mutants.MUTANTS["the Overlaps landing a blocker after other work"]):
            found = mutants.violations(search)
        self.assertTrue(any(g == "G7" and "Overlaps" in detail for g, detail in found), found)

    def test_the_searches_from_f15_s_review_catch_its_mutants(self):
        """The lane credits these mutants to the searches F15's three
        reviews added (DESIGN.md, section 13), which seed the issue with
        labels other than todo: each search finds nothing on the library and
        the violation named under each of its mutants, which it can only
        while its issue is seeded so, since a release puts no todo on an
        issue that has it, and only a blocker is listed under Blockers."""
        caught = {"a released task given by a label alone, under every fault": (
                      ("worker:a",), {"a refused todo puts no label back": "G3L",
                                      "a todo written and lost stays on": "G3L"}),
                  "a released epic": (("epic",), {"an epic gets todo": "G3"}),
                  "a released blocker": (("blocker",), {"a blocker gets todo": "G3",
                                                        "the Blockers section left empty": "G9"}),
                  "a released blocked task": (("blocked",), {"a blocked task gets no todo": "G3",
                                                             "a row without blocked": "G9"}),
                  "a held blocker": (("blocker",), {"the Blockers section without the held blockers": "G9",
                                                    "a held blocker's row without claimed": "G9"}),
                  "a released blocker under every fault": (("blocker",), {
                      "a release relabels the board before its comment": "G9"})}
        for name, (seeded, guarantees) in caught.items():
            search = next(s for s in mutants.SEARCHES if s.name == name)
            self.assertEqual(search.labels, seeded)
            self.assertEqual(mutants.violations(search), {})
            for mutant, guarantee in guarantees.items():
                with self.subTest(search=name, mutant=mutant), mutants.switched_on(mutants.MUTANTS[mutant]):
                    self.assertIn(guarantee, {g for g, _ in mutants.violations(search)})


class TheHarnessFixesTheLaneAskedFor(unittest.TestCase):
    def test_a_head_pushed_again_and_deleted_is_no_refused_rename(self):
        """The rename of a branch pushed again at the head a release kept is
        refused with 422 and the board deletes the branch, as the spec says;
        that is no refusal for good to say on the issue."""
        steps, scope = TRACES["a board comment posted again"]
        self.assertEqual(violations(steps, scope), [])

    def test_a_head_pushed_again_whose_rename_or_delete_is_refused_for_good_is_said(self):
        """Only the 422 the library answers by deleting is excused: a 403 on
        the rename, or a refused delete, leaves the name taken, and the
        issue hears so once."""
        steps, scope = TRACES["a board comment posted again"]
        for fault in ("rename-403", "ref-delete-422"):
            with self.subTest(fault=fault):
                self.assertEqual(violations(steps[:-1] + [board_with(fault)], scope), [])

    def test_the_guard_must_refuse_what_the_rules_forbid(self):
        outcome = verbs.Outcome(True, "taken", "git push origin claude/01", None, allowed_shape=False)
        self.assertEqual([v.guarantee for v in invariants.command_checks(outcome)], ["G11a"])
        by_hand = outcome._replace(guarded=False)
        self.assertEqual(invariants.command_checks(by_hand), [])


if __name__ == "__main__":
    unittest.main()
