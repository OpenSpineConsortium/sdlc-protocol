"""The harness's guarantees (validation/invariants.py) on hand-written
traces: an honest release and reclaim breaks none; the 0.4.1 fixes hold;
a run in two halves is judged by the states its halves read; F2's claim
that opens its pull request is told nothing again, and is under the
mutant that names a piece by the number it is shown by; F7's careless
lease is refused, F9's draft misses "main moved" under the mutant that
takes GitHub's opaque word alone, and is told it while level with main
under the one that reads the comparison's other count, and F12's lost run
tells "main moved" under its mutant alone, by the run the concurrency
group cancels.
The mutants, the prototype's among them, are
tests/test_validation_mutants.py's."""

import re
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sdlc_protocol.adapters import github  # noqa: E402
from sdlc_protocol import board, guard  # noqa: E402
from validation import cycle, dealing, invariants, model as M, mutants, oracle, verbs  # noqa: E402
from validation.fake_github import Call, Fault  # noqa: E402
from validation.verbs import Scope, Verb  # noqa: E402

BOARD = Verb("board", "board", (cycle.SCHEDULE, ""))
SHARED = Scope(issues=(1, 2), files=((1, ("src/shared.py",)), (2, ("src/shared.py",))))


def board_with(*faults, event=cycle.SCHEDULE, ref_name=""):
    return Verb("board", "board", (event, ref_name, faults))


def run(event, ref_name=""):
    """A run in two halves with nothing between them."""
    return [Verb("board-start", "board", (event, ref_name)), Verb("board-finish", "board")]


def violations(steps, scope=Scope(), known=oracle.AS_BUILT, issues=(1,), labels=("todo",), opaque_pulls=False):
    return played(steps, scope, known, issues, labels, opaque_pulls)[1]


def played(steps, scope=Scope(), known=oracle.AS_BUILT, issues=(1,), labels=("todo",), opaque_pulls=False):
    """The model once the steps are played, and the violations found on
    the way."""
    model = M.seeded(issues or scope.issues, writers=("a", "b", "c", verbs.CAROL, verbs.DANA), labels=labels)
    # As validation/run.py sets it from a scenario: with it on the fake
    # answers blocked for every mergeable pull request, as GitHub does
    # under a required check (F9).
    model.opaque_pulls = opaque_pulls
    cycle.board_run(model)
    found = []
    for step in steps:
        before = model.clone()
        outcome = verbs.apply(model, step, scope)
        found += invariants.step_checks(before, model, step, outcome, known)
    return model, found


def guarantees(found):
    return sorted({v.guarantee for v in found})


def release_comments(model, number):
    """How many release comments the issue carries."""
    return sum(oracle.comment_kind(c.body) == "claim-released" for c in model.comments_on(number))


IDLE_RELEASE = [Verb("claim", "a", (1,)), Verb("label-on", "a", (1, "claimed")), Verb("label-on", "a", (1, "worker:a")),
                Verb("plan", "a", (1,)), Verb("open-draft", "a", (1,)), Verb("commit", "a", (1,)),
                Verb("wait", "clock", (24,)), BOARD, Verb("wait", "clock", (1,)), BOARD,
                Verb("wait", "clock", (1,)), BOARD, BOARD]


class AnHonestRun(unittest.TestCase):
    def test_an_idle_claim_released_once_then_claimed_again_breaks_nothing(self):
        steps = IDLE_RELEASE + [Verb("claim", "c", (1,)), Verb("label-on", "c", (1, "claimed")),
                                Verb("label-on", "c", (1, "worker:c")), BOARD]
        self.assertEqual(violations(steps), [])

    def test_a_person_releases_by_hand_a_stranger_cannot_and_a_holder_who_comes_back_keeps_it(self):
        steps = [Verb("claim", "a", (1,)), Verb("label-on", "a", (1, "claimed")), Verb("marker", M.OWNER, (1,)),
                 Verb("label-off", M.OWNER, (1, "claimed")), BOARD, Verb("wait", "clock", (25,)),
                 Verb("marker", verbs.SAM, (1,)), Verb("label-on", "a", (1, "claimed")), Verb("marker", M.OWNER, (1,)),
                 Verb("label-off", M.OWNER, (1, "claimed")), Verb("commit", "a", (1,)), BOARD,
                 Verb("wait", "clock", (25,)), BOARD]
        self.assertEqual(violations(steps, Scope(people=(M.OWNER, verbs.SAM))), [])

    def test_a_finished_draft_waits_and_a_person_s_release_of_it_counts(self):
        """F10, fixed in 0.4.1."""
        steps = [Verb("claim", "a", (1,)), Verb("label-on", "a", (1, "claimed")), Verb("open-draft", "a", (1,)),
                 Verb("finished", "a", (100,)), Verb("wait", "clock", (50,)), BOARD, Verb("marker", M.OWNER, (1,)),
                 Verb("label-off", M.OWNER, (1, "claimed")), BOARD]
        self.assertEqual(violations(steps), [])


class TheWorkerCell(unittest.TestCase):
    """G1 holds the board's Worker cell to the branch's holder by the claim
    file, and, once the finishing push has deleted the file, to the issue's
    worker label, which the spec leaves the board; the soak's G1 leads
    (seeds 585 and 959) were a closed issue's leftover branch whose stale
    label named the worker that had merged."""

    def claim(self, pusher, named, labels=("todo",)):
        model = M.seeded((1,), writers=("a", "b", "e"), labels=labels)
        tree = model.tree(model.refs[M.MAIN])
        tree["docs/agents/claims/1.md"] = M.claim_text(1, named)
        model.push(pusher, "claude/1", model.commit((model.refs[M.MAIN],), pusher, tree, "Claim #1"))
        return model

    def drop_file(self, model, pusher):
        tree = model.tree(model.refs["claude/1"])
        del tree["docs/agents/claims/1.md"]
        model.push(pusher, "claude/1", model.commit((model.refs["claude/1"],), pusher, tree, "Finish #1"))

    def test_the_cell_names_the_holder_by_the_claim_file(self):
        self.assertEqual(invariants.one_name_one_holder(self.claim("b", "b")), [])
        found = invariants.one_name_one_holder(self.claim("b", "e"))
        self.assertEqual([v.guarantee for v in found], ["G1"])
        self.assertIn("names e for claude/1, whose holder is b", found[0].detail)

    def test_once_the_file_is_gone_the_cell_names_the_issue_s_label_or_says_there_is_none(self):
        model = self.claim("b", "b", labels=("todo", "worker:e"))
        self.drop_file(model, "b")
        self.assertEqual(invariants.one_name_one_holder(model), [])
        model.issues[1].state = "closed"
        self.assertEqual(invariants.one_name_one_holder(model), [])
        model = self.claim("b", "b")
        self.drop_file(model, "b")
        self.assertEqual(invariants.one_name_one_holder(model), [])

    def test_a_board_that_shows_another_cell_than_the_rule_owes_is_seen(self):
        """The cell is held to one value: a board that drops the label once
        the file is gone, or says there is no claim file while one is
        there, breaks G1."""
        no_file = mock.patch.object(invariants.board, "claim_row",
                                    lambda key, branch, text, pull, issue: ["", branch, board.NO_CLAIM_FILE, "", ""])
        model = self.claim("b", "b", labels=("todo", "worker:e"))
        self.drop_file(model, "b")
        with no_file:
            found = invariants.one_name_one_holder(model)
        self.assertEqual([(v.guarantee, v.detail) for v in found],
                         [("G1", "the board names no claim file yet for claude/1, whose claim file is gone and whose "
                                 "issue's label names e")])
        with no_file:
            found = invariants.one_name_one_holder(self.claim("b", "b"))
        self.assertEqual([(v.guarantee, v.detail) for v in found],
                         [("G1", "the board names no claim file yet for claude/1, whose holder is b")])

    def test_the_oracle_reads_the_worker_label_by_itself(self):
        self.assertEqual(oracle.label_worker(["todo", "account:main", "worker:e"]), "e")
        self.assertEqual(oracle.label_worker(["agent:w1", "todo"]), "w1")
        self.assertIsNone(oracle.label_worker(["todo", "worker:"]))

    def test_a_worker_line_that_names_nobody_owes_the_cell_unnamed(self):
        """F18's second review: the cell for a claim file that is there and
        whose Worker line names nobody, or that has no such line, is the
        library's documented word, unnamed, since the file is there to be
        corrected, and not the pusher, whom the oracle expected, nor the
        issue's label, which the clerk and a release fall back to; a board
        that names the label's worker, or the pusher, for such a file is
        seen. The picking lane's three file forms (dealing.CLAIM_STATES a,
        nobody and blank) go through one board run green."""
        for line in ("- Worker: (to be filled)", "- Worker: ", ""):
            model = M.seeded((1,), writers=("a", "b", "e"), labels=("todo", "worker:e"))
            tree = model.tree(model.refs[M.MAIN])
            tree["docs/agents/claims/1.md"] = f"# Claim: #1\n\n{line}\n- Branch: claude/1\n"
            model.push("b", "claude/1", model.commit((model.refs[M.MAIN],), "b", tree, "Claim #1"))
            with self.subTest(line=line):
                self.assertEqual(invariants.one_name_one_holder(model), [])
                self.assertEqual(invariants.shown_worker(model, "claude/1", 1), "unnamed")
                names_the_label = mock.patch.object(invariants.board, "claim_row",
                                                    lambda key, branch, text, pull, issue: ["", branch, "e", "", ""])
                with names_the_label:
                    found = invariants.one_name_one_holder(model)
                self.assertEqual([(v.guarantee, v.detail) for v in found],
                                 [("G1", "the board names e for claude/1, whose holder is b and whose claim file "
                                         "names nobody")])
        for state in ("a", "nobody", "blank"):
            model = dealing.case_model(dealing.Case({1: ("todo", "worker:b")}, 1, state, ("a", "b")))
            before = model.clone()
            run = verbs.Verb("board", "board", (cycle.SCHEDULE, "", ()))
            outcome = verbs.apply(model, run)
            self.assertEqual(invariants.step_checks(before, model, run, outcome), [], state)


class TheProtocolCheckIsReadAsGitHubReadsABody(unittest.TestCase):
    def test_a_body_closing_another_issue_or_in_another_keyword(self):
        """G12's "the body closes its issue" is GitHub's reading: any closing
        keyword in any case, and #12 does not close #1."""
        for body, closes in (("Closes #1", True), ("closes #1", True), ("Fixes #1", True), ("Closes #12", False),
                             ("Resolves #1.", True), ("See #1", False)):
            with self.subTest(body=body):
                model = M.seeded()
                verbs.apply(model, Verb("claim", "a", (1,)))
                verbs.apply(model, Verb("open-draft", "a", (1,)))
                model.pulls[100].body = body
                found = invariants.protocol_check_agrees(model)
                self.assertEqual(found, [], body)
                self.assertEqual(re.search(invariants.CLOSES % 1, body) is not None, closes)


class TheFixesOf041Hold(unittest.TestCase):
    def test_a_refused_label_removal_leaves_the_claim_whole_and_the_next_run_releases_it(self):
        """F1."""
        steps = [Verb("claim", "a", (1,)), Verb("label-on", "a", (1, "claimed")), Verb("wait", "clock", (25,)),
                 board_with("label-delete-500"), BOARD]
        self.assertEqual(violations(steps), [])

    IDLE = [Verb("claim", "a", (1,)), Verb("label-on", "a", (1, "claimed")), Verb("label-on", "a", (1, "worker:a")),
            Verb("wait", "clock", (25,))]

    def test_a_refused_release_comment_puts_the_labels_back(self):
        """F1's fix carried to the comment: GitHub refuses the release
        comment with a 4xx, which it answers in place of writing anything,
        so the run puts the labels back and the next run releases the claim
        whole; a comment GitHub wrote and lost its answer to, and lists,
        makes the release whole in the run (SPEC["refused"],
        SPEC["whole"]). The secondary rate limit, which the client waits
        out, has a test of its own (below)."""
        for fault, labels in (("comment-422", {"claimed", "todo", "worker:a"}), ("comment-lost", {"todo"})):
            with self.subTest(fault=fault):
                model, found = played(self.IDLE + [board_with(fault)])
                self.assertEqual(found, [])
                self.assertEqual(model.issues[1].labels, labels)
                model, found = played(self.IDLE + [board_with(fault), BOARD])
                self.assertEqual(found, [])
                self.assertEqual((model.issues[1].labels, "claude/1" in model.refs), ({"todo"}, False))
                self.assertEqual(release_comments(model, 1), 1)

    def test_a_release_comment_refused_for_a_rate_limit_is_written_after_the_wait(self):
        """F11's secondary rate limit, since the measurement pass's third
        pull request: the client waits as GitHub asks and posts the comment
        again (adapters.github.rate_wait), so the run that met the limit
        releases the claim whole, the comment written once."""
        self.assertEqual(violations(self.IDLE + [board_with("comment-rate-limited"), BOARD]), [])
        model = M.seeded((1,), writers=("a",))
        cycle.board_run(model)
        for step in self.IDLE:
            verbs.apply(model, step)
        record = cycle.board_run(model, faults=(verbs.FAULTS["comment-rate-limited"],))
        posted = [call.status for call in record.calls if call.method == "POST" and call.path.endswith("/comments")
                  and oracle.first_line(call.body["body"]) == oracle.RELEASE_MARKER]
        self.assertEqual(posted, [403, 200])
        released = [c for c in model.issues[1].comments if oracle.first_line(c.body) == oracle.RELEASE_MARKER]
        self.assertEqual((len(released), "claimed" in model.issues[1].labels), (1, False))

    def test_a_refused_rename_is_said_once_and_tried_again(self):
        steps = [Verb("claim", "a", (1,)), Verb("wait", "clock", (25,)), board_with("rename-403"), BOARD,
                 board_with("rename-422"), BOARD]
        self.assertEqual(violations(steps), [])

    def test_a_misnamed_claim_is_refused_by_the_guard_and_claims_nothing(self):
        """F5."""
        steps = [Verb("claim", "a", (1,)), Verb("claim-misnamed", "b", (1, "claude/01")),
                 Verb("push-by-hand", M.OWNER, (1, "claude/01")), Verb("wait", "clock", (25,)), BOARD]
        self.assertEqual(violations(steps), [])


class ARunInTwoHalves(unittest.TestCase):
    HELD = [Verb("claim", "a", (1,)), Verb("label-on", "a", (1, "claimed")), Verb("label-on", "a", (1, "worker:a")),
            Verb("wait", "clock", (25,)), Verb("board-start", "board", (cycle.SCHEDULE, ""))]
    SPLIT = Scope(split_board=True)

    def test_the_holder_back_between_the_halves_keeps_the_claim_and_breaks_nothing(self):
        """S09: the rename the oracle owes from the state the run started
        from is rightly left alone, with the word on the issue."""
        steps = self.HELD + [Verb("commit", "a", (1,)), Verb("board-finish", "board")]
        self.assertEqual(violations(steps, self.SPLIT), [])
        with mock.patch.object(github, "free_name", mutants.renamed_unread):
            self.assertEqual(sorted({(v.guarantee, v.detail) for v in violations(steps, self.SPLIT)}),
                             [("G10", "made and not owed: ('comment', 1, 'released')"),
                              ("G3", "made and not owed: ('rename', 'claude/1', 'released/1-6d6ae03')")])

    def test_a_release_undone_between_the_halves_is_the_holder_s_not_the_run_s(self):
        """The holder puts claimed back after the first half took it off:
        the run released the claim, and what the issue carries at the end
        is not the run's doing (G8)."""
        steps = self.HELD + [Verb("label-on", "a", (1, "claimed")), Verb("board-finish", "board")]
        self.assertEqual(violations(steps, self.SPLIT), [])

    def test_main_moved_between_the_halves_owes_the_next_run_nothing_new_as_built(self):
        steps = self.HELD + [Verb("move-main", M.OWNER), Verb("board-finish", "board")]
        self.assertEqual(violations(steps, self.SPLIT), [])

    def test_a_second_run_s_quiet_is_asked_only_when_nothing_changed_between_the_halves(self):
        """A pull request from a fork opened between the halves makes the
        next run owe the overlap comments the first half could not know
        of (a draft the holder opens owes none since F2's fix, the claim
        and its pull request being one piece); with nothing between the
        halves, a second run that writes again is red as a whole one is."""
        steps = [Verb("claim", "a", (1,)), Verb("claim", "b", (2,)), Verb("board-start", "board", (cycle.SCHEDULE, "")),
                 Verb("fork-pull", verbs.SAM, (1, True)), Verb("board-finish", "board")]
        scope = SHARED._replace(split_board=True, forks=True)
        self.assertEqual(violations(steps, scope, issues=None), [])
        checks = invariants.cycle_checks

        def asked_anyway(before, after, record, known=oracle.AS_BUILT):
            return checks(before, after, record, known) + invariants.quiet_second_run(after, record)

        with mock.patch.object(invariants, "cycle_checks", asked_anyway):
            self.assertEqual(guarantees(violations(steps, scope, issues=None)), ["G9"])
        told_again = mutants.MUTANTS["overlaps told on every run"]
        quiet = steps[:3] + [Verb("board-finish", "board"), Verb("board-start", "board", (cycle.SCHEDULE, "")),
                             Verb("board-finish", "board")]
        with mock.patch.object(told_again.module, told_again.name, told_again.replacement):
            self.assertIn("G9", guarantees(violations(quiet, scope, issues=None)))

    def test_the_second_half_s_writes_are_judged_whatever_their_kind(self):
        """The second half's writes are the calls after the first half's,
        so a label it takes off, the first half's kind of write, is made
        and not owed (G3), where a split by kind would judge it by neither
        half."""
        steps = [Verb("claim", "a", (1,)), Verb("label-on", "a", (1, "claimed")),
                 Verb("board-start", "board", (cycle.SCHEDULE, "")), Verb("board-finish", "board")]
        self.assertEqual(violations(steps, self.SPLIT), [])
        releases = github.release_claims

        def claimed_off_too(api, snapshot):
            lines = releases(api, snapshot)
            api.remove_label(1, "claimed")
            return lines

        with mock.patch.object(github, "release_claims", claimed_off_too):
            found = violations(steps, self.SPLIT)
        self.assertIn(("G3", "made and not owed: ('label-off', 1, 'claimed')"), [(v.guarantee, v.detail) for v in found])
        self.assertEqual(guarantees(found), ["G3", "G9"])

    def test_the_first_half_is_judged_when_it_is_made(self):
        """A search judges a first half at its last level as it judges a
        whole run: a release made at exactly 24 hours is red on the
        board-start step, not two steps later."""
        steps = [Verb("claim", "a", (1,)), Verb("wait", "clock", (24,)),
                 Verb("board-start", "board", (cycle.SCHEDULE, ""))]
        self.assertEqual(violations(steps, self.SPLIT), [])
        with mock.patch.object(github.claims, "is_idle", mutants.MUTANTS["idle at exactly 24 hours"].replacement):
            self.assertEqual(guarantees(violations(steps, self.SPLIT)), ["G2", "G3", "G5"])


class TheOpenFindingsShow(unittest.TestCase):
    """Each fixed finding is green, and red under the mutant that undoes its
    fix; none is read as built since F2's fix (known.json's as_built)."""

    def test_f2_a_claim_that_opens_its_pull_request_is_told_nothing_again(self):
        """Fixed after 0.4.1: the issue names the claim's work in the
        markers and is where it is told, for the whole claim; named by its
        pull request's number as soon as it has one, the claim is told
        again of #2 and #2 of it."""
        steps = [Verb("claim", "a", (1,)), Verb("claim", "b", (2,)), BOARD, Verb("open-draft", "a", (1,)), BOARD]
        self.assertEqual(violations(steps, SHARED, issues=None), [])
        self.assertEqual(oracle.AS_BUILT, frozenset())
        with mutants.switched_on(mutants.MUTANTS["a piece of work named by the number it is shown by"]):
            self.assertEqual(guarantees(violations(steps, SHARED, issues=None)), ["G7"])

    def test_f9_a_draft_behind_main_hears_main_moved_once_its_standing_is_read(self):
        """Fixed after 0.4.1: with the model's opaque_pulls on GitHub's word
        for the draft is blocked, as for a draft whose required check has
        not passed, and the adapter reads how its branch stands through
        mergeable and one compare, so the run owes the draft the word only
        once main has moved; under the mutant that takes GitHub's word
        alone nothing is told, and under the one that reads the
        comparison's ahead_by the draft, its claim commit ahead and level
        with main, is told before main moves. With opaque_pulls off the
        fake answers behind itself and the mutants have nothing to bite,
        so the finding is checked with it on."""
        level = [Verb("claim", "a", (1,)), Verb("open-draft", "a", (1,)), board_with(event=cycle.PULL_REQUEST)]
        steps = level + [Verb("move-main", M.OWNER), board_with(event=cycle.PUSH, ref_name=M.MAIN)]
        self.assertEqual(violations(steps), [])
        self.assertEqual(violations(steps, opaque_pulls=True), [])
        with mutants.switched_on(mutants.MUTANTS["an opaque state read by GitHub's word alone"]):
            self.assertEqual(guarantees(violations(level, opaque_pulls=True)), [])
            self.assertEqual(guarantees(violations(steps, opaque_pulls=True)), ["G10"])
        with mutants.switched_on(mutants.MUTANTS["the comparison read by ahead_by"]):
            self.assertEqual(guarantees(violations(level, opaque_pulls=True)), ["G10"])

    def test_f12_main_moved_is_told_by_whichever_run_finds_the_branch_behind(self):
        """Fixed after 0.4.1; the mutant keys the word on the push's run again."""
        steps = [Verb("claim", "a", (1,)), Verb("open-draft", "a", (1,)), Verb("mark-ready", "a", (100,)),
                 Verb("move-main", M.OWNER), board_with(event=cycle.PULL_REQUEST)]
        self.assertEqual(violations(steps), [])
        with mutants.switched_on(mutants.MUTANTS["main moved told only in the push's run"]):
            self.assertEqual(guarantees(violations(steps)), ["G10"])

    def test_f12_the_run_of_the_push_to_main_is_cancelled_behind_the_worker_s_own_push(self):
        """The mechanism (DESIGN.md, F12): the worker's push to the claim
        branch, with its pull request marked ready, holds the group while
        its job is skipped, main moves behind it, the worker's next push
        replaces the run that waited, and the runs that follow tell the
        pull request, since the word depends on the state (F12's fix);
        under the mutant that keys it on the push's run they owe it and
        stay silent."""
        scope = Scope(events=cycle.EVENTS, split_board=True)
        steps = ([Verb("claim", "a", (1,))] + run(cycle.CREATE, "claude/1") + run(cycle.PUSH, "claude/1")
                 + [Verb("open-draft", "a", (1,))] + run(cycle.PULL_REQUEST) + [Verb("mark-ready", "a", (100,)),
                 Verb("commit", "a", (1,)), Verb("board-start", "board", (cycle.PUSH, "claude/1")),
                 Verb("move-main", M.OWNER), Verb("commit", "a", (1,)), Verb("board-finish", "board")]
                 + run(cycle.PULL_REQUEST) + run(cycle.PUSH, "claude/1"))
        self.assertEqual(violations(steps, scope), [])
        with mutants.switched_on(mutants.MUTANTS["main moved told only in the push's run"]):
            self.assertEqual(guarantees(violations(steps, scope)), ["G10"])

    def test_f7_a_lease_after_a_fetch_onto_another_s_claim_is_refused(self):
        """Fixed after 0.4.1: git would take the lease (test_validation_model),
        and the guard refuses it by the claim file on origin."""
        steps = [Verb("claim", "a", (1,)), Verb("lease-onto", "b", (1,)), BOARD]
        self.assertEqual(violations(steps, Scope(careless=True)), [])
        with mock.patch.object(guard, "other_holder", lambda *args: None):
            found = violations(steps, Scope(careless=True))
        self.assertEqual(guarantees(found), ["G1", "G11a"])

    def test_f13_the_oldest_work_lands_first(self):
        """Found by this step, fixed after 0.4.1: a draft whose branch was
        made two hours before another claim, and opened two hours after it,
        lands first, as the spec reads the oldest; until the fix the claim
        landed first, its issue's number being below the pull request's,
        which the mutant that orders by number again shows, and so it does
        under the mutant that dates the draft by its opening (F13's
        review)."""
        steps = [Verb("claim", "a", (1,)), Verb("wait", "clock", (2,)), Verb("claim", "b", (2,)),
                 Verb("wait", "clock", (2,)), Verb("open-draft", "a", (1,)), BOARD]
        self.assertEqual(violations(steps, SHARED, issues=None), [])
        for mutant in ("the landing order by number", "a pull request dated by its opening"):
            with self.subTest(mutant=mutant), mutants.switched_on(mutants.MUTANTS[mutant]):
                found = violations(steps, SHARED, issues=None)
                self.assertEqual([(v.guarantee, v.detail) for v in found],
                                 [("G7", "the board's Overlaps are {'src/shared.py': [2, 100]}, the oracle's "
                                         "{'src/shared.py': [100, 2]}")])

    def test_f13_work_github_will_not_date_lands_as_the_spec_reads_it(self):
        """F13's second review: with every read of the activity refused, the
        draft is dated by its opening and the claim, which nothing dates,
        lands last; the oracle reads the branches GitHub refused from the
        run's calls, so the page the spec asks for is green, and under the
        mutant that puts such work first it is not."""
        steps = [Verb("claim", "a", (1,)), Verb("wait", "clock", (2,)), Verb("claim", "b", (2,)),
                 Verb("wait", "clock", (2,)), Verb("open-draft", "b", (2,)), board_with("activity-500-all")]
        self.assertEqual(violations(steps, SHARED, issues=None), [])
        with mutants.switched_on(mutants.MUTANTS["work nothing dates landing first"]):
            found = violations(steps, SHARED, issues=None)
        self.assertEqual([(v.guarantee, v.detail) for v in found],
                         [("G7", "the board's Overlaps are {'src/shared.py': [1, 100]}, the oracle's "
                                 "{'src/shared.py': [100, 1]}")])

    def test_unsaid_branches_are_those_whose_own_read_github_refused(self):
        """A refused read of the newest creations of the whole repository
        leaves each branch to its own read, so it makes no branch unsaid."""
        def read(status, ref=None, injected=False):
            query = (("activity_type", "branch_creation"),) + ((("ref", ref),) if ref else ())
            return Call("GET", "/activity", query, None, status, False, injected)

        calls = [read(500, injected=True), read(500, "refs/heads/claude/1", True), read(200, "refs/heads/claude/2"),
                 read(404, "refs/heads/claude/3")]
        self.assertEqual(invariants.unsaid_branches(calls), {"claude/1", "claude/3"})
        self.assertEqual(invariants.unsaid_branches(calls[:1]), set())

    def test_f8_a_claim_is_released_by_its_push_and_by_its_committer_date_where_github_will_not_date_it(self):
        """Fixed after 0.4.1: a's claim commit is dated 72 hours ahead, and
        GitHub received it 25 hours before the run, which releases it; the
        mutant that reads the committer date holds it. A first read GitHub
        gives no answer that holds, or answers before it lists the push,
        is answered by the second, so the run releases the claim by its
        push there too; with every read refused for good, or both reads
        answered before the activity lists the push, which then lists no
        entry of the branch, its making being its only one, so no floor
        either, the run dates the head by its committer date and holds it,
        which the oracle reads from the run's calls, so the run is green
        there too; and where a read lists a push after the run read the
        branch, the claim is dated by it and held."""
        steps = [Verb("claim", "a", (1, 72 * M.HOUR)), Verb("label-on", "a", (1, "claimed")),
                 Verb("label-on", "a", (1, "worker:a")), Verb("wait", "clock", (25,))]
        for faults in ((), ("pushes-500",), ("pushes-lagging",), ("pushes-403",), ("pushes-lagging-twice",),
                       ("activity-500-all",), ("pushes-moved-twice",), ("pushes-500-then-moved",),
                       ("pushes-lagging-then-500",)):
            with self.subTest(faults=faults):
                self.assertEqual(violations(steps + [board_with(*faults)]), [])
        with mutants.switched_on(mutants.MUTANTS["idleness by the committer date"]):
            found = violations(steps + [BOARD])
        self.assertIn(("G3", "owed and not made: ('comment', 1, 'claim-released')"),
                      [(v.guarantee, v.detail) for v in found])

    def test_f8_a_first_read_that_may_tell_more_is_made_again_and_only_then(self):
        """F8's review: the run reads the push once more where its first
        read may tell more, a 500 or a list that does not show the push
        yet, and once where it dates the push or GitHub refuses for good;
        the mutant that reads once breaks G2 under the first, and the one
        that reads only the after side of an entry under a push after the
        run read the branch."""
        steps = [Verb("claim", "a", (1,)), Verb("label-on", "a", (1, "claimed")), Verb("wait", "clock", (2,))]
        for faults in ((), ("pushes-500",), ("pushes-lagging",), ("pushes-403",), ("pushes-moved",),
                       ("pushes-moved-twice",), ("pushes-500-then-moved",), ("pushes-lagging-then-500",)):
            with self.subTest(faults=faults):
                self.assertEqual(violations(steps + [board_with(*faults)]), [])
        with mutants.switched_on(mutants.MUTANTS["the push read once"]):
            found = violations(steps + [board_with("pushes-500")])
        self.assertIn(("G2", "the run read the push of claude/1's head 1 times, where its first read owes 2"),
                      [(v.guarantee, v.detail) for v in found])
        with mutants.switched_on(mutants.MUTANTS["the before side ignored"]):
            found = violations(steps + [board_with("pushes-moved")])
        self.assertIn(("G2", "the run read the push of claude/1's head 2 times, where its first read owes 1"),
                      [(v.guarantee, v.detail) for v in found])

    def test_a_first_read_with_no_answer_at_all_may_tell_more(self):
        """F23's review: since F23 a Fault may have no status, which the
        fake answers with http.client's timeout and the library reads as a
        call GitHub may answer later (github.may_answer_later), so the run
        reads the push once more; the check reads a status None so too,
        where it compared None with a number and raised. No fault of
        verbs.FAULTS leaves a read of the activity unanswered, so the test
        adds one for itself."""
        ref = "claude/1"
        unanswered = Call("GET", "/activity", (("ref", f"refs/heads/{ref}"),), None, None, False, True)
        self.assertTrue(invariants.may_tell_more(unanswered, ref, "f" * 40, 0))
        timed_out = verbs.Fault("GET", "/activity", None, message="timed out", query=verbs.PUSHES)
        steps = [Verb("claim", "a", (1,)), Verb("label-on", "a", (1, "claimed")), Verb("wait", "clock", (2,)),
                 board_with("pushes-unanswered")]
        with mock.patch.dict(verbs.FAULTS, {"pushes-unanswered": timed_out}):
            self.assertEqual(violations(steps), [])

    def test_f8_a_stale_entry_off_the_head_owes_a_second_read_and_dates_nothing(self):
        """F8's second review: an entry that names another head, one that
        moved the branch off the head or, since the fix check, one that
        names it on neither side or deleted the branch, dates it only when
        it came after the run began (SPEC["moved off"]): an older one lists
        no push that set the head, so the first read owes a second, and
        read so last it leaves the floor, that entry's time, to date the
        claim where it is later than the committer date, and the committer
        date to stand in where not (SPEC["floor"]). a's claim commit, dated
        two days ahead, is pushed back a day after a commit moved the
        branch off it, and the activity a push behind lists that commit's
        push newest: the run reads once more and holds the claim, and the
        mutant that dates the head by the stale entry reads once and
        releases it in the run its own push started, where even the
        committer date, which a run that read once must fall back to,
        holds it."""
        model = M.seeded()
        ref = "claude/1"
        model.set_ref(ref, model.refs[M.MAIN])
        head, model.now = model.refs[ref], 25 * M.HOUR

        def read(minute, before=head):
            answered = [{"ref": f"refs/heads/{ref}", "timestamp": M.iso(minute), "after": "f" * 40, "before": before}]
            return Call("GET", "/activity", (("ref", f"refs/heads/{ref}"),), None, 200, False, True, None, answered)

        for minute, dates in ((M.HOUR, None), (model.now - 6, None), (model.now - 5, model.now - 5),
                              (model.now, model.now)):
            for before in (head, "e" * 40, "0" * 40):
                with self.subTest(minute=minute, before=before):
                    self.assertEqual(invariants.read_dates(read(minute, before), ref, head, model.now), dates)
                    self.assertEqual(invariants.may_tell_more(read(minute, before), ref, head, model.now),
                                     dates is None)
        # Read twice so, the stale entry is the floor, later than the head's
        # committer date, the seeded main's at minute 0 (SPEC["floor"]).
        self.assertEqual(invariants.push_readings([read(M.HOUR), read(M.HOUR)], model),
                         {ref: invariants.PushReading(M.HOUR, "floor", "listed none", M.iso(M.HOUR))})
        self.assertEqual(invariants.pushes_read_again([read(M.HOUR)], model)[0].detail,
                         "the run read the push of claude/1's head 1 times, where its first read owes 2")
        self.assertEqual(invariants.pushes_read_again([read(model.now)], model), [])
        steps = [Verb("claim", "a", (1, 48 * M.HOUR)), Verb("label-on", "a", (1, "claimed")),
                 Verb("wait", "clock", (1,)), Verb("commit", "a", (1,)), Verb("wait", "clock", (25,)),
                 Verb("drop-commits", "a", (1,)),
                 board_with("pushes-lagging")]
        self.assertEqual(violations(steps), [])
        with mutants.switched_on(mutants.MUTANTS["a stale entry off the head dates it"]):
            found = [(v.guarantee, v.detail) for v in violations(steps)]
        self.assertIn(("G2", "the run read the push of claude/1's head 1 times, where its first read owes 2"), found)
        self.assertIn(("G3", "made and not owed: ('comment', 1, 'claim-released')"), found)

    def test_undated_branches_are_those_whose_last_read_of_their_head_s_push_dates_none(self):
        """A first read refused and the second answered dates the branch by
        its push; a branch whose last read GitHub refused, or answered with
        no push of its head, is dated by its committer date where no read
        listed an entry of it later than that date, and by that entry, the
        floor, where one did (SPEC["floor"]); a read of a branch's making
        says nothing of its push. The run began a day after the entries
        listed, so an entry naming another head is stale."""
        model = M.seeded((1, 2, 3, 4, 5))
        model.now = 25 * M.HOUR
        heads = {}
        for number in (1, 2, 3, 4, 5):
            ref = f"claude/{number}"
            model.set_ref(ref, model.refs[M.MAIN])
            heads[ref] = model.refs[ref]

        def read(status, ref, kind=None, after=None, minute=60):
            query = ((("activity_type", kind),) if kind else ()) + (("ref", f"refs/heads/{ref}"),)
            answered = [{"ref": f"refs/heads/{ref}", "timestamp": M.iso(minute), "after": after,
                         "before": "0" * 40}] if after else []
            return Call("GET", "/activity", query, None, status, False, True, None, answered)

        committed = model.commits[heads["claude/5"]].committed_at
        calls = [read(500, "claude/1"), read(500, "claude/2", "branch_creation"),
                 read(200, "claude/2", after=heads["claude/2"]), read(200, "claude/3", after="f" * 40),
                 read(200, "claude/3", after="f" * 40),
                 read(500, "claude/4"), read(200, "claude/4", after=heads["claude/4"]),
                 read(200, "claude/5", after="f" * 40, minute=committed), read(200, "claude/5")]
        self.assertEqual(invariants.undated_branches(calls, model), {"claude/1", "claude/5"})
        self.assertEqual(invariants.unsaid_branches(calls), {"claude/2"})
        self.assertEqual(invariants.push_dates(calls, model),
                         {"claude/1": None, "claude/2": 60, "claude/3": 60, "claude/4": 60, "claude/5": None})
        self.assertEqual([(r.by, r.why) for _, r in sorted(invariants.push_readings(calls, model).items())],
                         [("committer date", "refused"), ("push", ""), ("floor", "listed none"), ("push", ""),
                          ("committer date", "listed none")])

    def test_f8_s_fix_check_the_floor_dates_a_claim_both_reads_leave_undated(self):
        """F8's fix check: where both reads of b's push are a push behind,
        the newest entry listed is the making of b's branch, stale, a day
        before the push of b's commit dated two days back; the floor, the
        making's time, is later than that commit's date and dates the
        claim, so the run holds it, and the oracle reads it so. Where the
        floor is the commit's own minute it is no later, and the committer
        date stands in. The mutant that drops the floor releases b's claim;
        the one that takes a floor at the committer date for later says so
        in the log alone."""
        back = [Verb("claim", "b", (2,)), Verb("label-on", "b", (2, "claimed")), Verb("wait", "clock", (20,)),
                Verb("commit", "b", (2, -48 * M.HOUR)), board_with("pushes-lagging-twice")]
        self.assertEqual(violations(back, issues=(1, 2)), [])
        with mutants.switched_on(mutants.MUTANTS["the floor dropped"]):
            found = [(v.guarantee, v.detail) for v in violations(back, issues=(1, 2))]
        self.assertIn(("G2", "released #2, which was not due"), found)
        self.assertIn(("G2", "the run's log says claude/2's idleness is read from its committer date, after a list "
                             "naming no push, where its reads owe its newest entry's time, after a list naming no "
                             "push"), found)
        at_the_floor = [Verb("claim", "b", (2,)), Verb("label-on", "b", (2, "claimed")), Verb("wait", "clock", (2,)),
                        Verb("commit", "b", (2, -2 * M.HOUR)), Verb("wait", "clock", (1,)),
                        board_with("pushes-lagging-twice")]
        self.assertEqual(violations(at_the_floor, issues=(1, 2)), [])
        with mutants.switched_on(mutants.MUTANTS["a floor at the committer date taken for later"]):
            found = [(v.guarantee, v.detail) for v in violations(at_the_floor, issues=(1, 2))]
        self.assertEqual(found, [("G2", "the run's log says claude/2's idleness is read from its newest entry's "
                                        "time, after a list naming no push, where its reads owe its committer date, "
                                        "after a list naming no push")])

    def test_f8_s_fix_check_each_read_s_floor_and_moment_count(self):
        """F8's fix check: a first read a push behind and a second GitHub
        refuses keep the first read's floor; a first read refused and a
        second listing a push after the run read the branch date the claim
        by that push, the second judged by when the run began as the first
        is; and the newest of two pushes since the run read the branch,
        which names the head on neither side, dates the claim at the first
        read. Each mutant that undoes one of them is red."""
        held = [Verb("claim", "b", (2,)), Verb("label-on", "b", (2, "claimed")), Verb("wait", "clock", (20,)),
                Verb("commit", "b", (2, -48 * M.HOUR))]
        for fault, mutant, shown in (
                ("pushes-lagging-then-500", "the floor of the last read alone",
                 ("G2", "released #2, which was not due")),
                ("pushes-500-then-moved", "the second read judged with no moment of the run",
                 ("G2", "the run's log says claude/2's idleness is read from its newest entry's time, after a list "
                        "naming no push, where its reads owe no line")),
                ("pushes-moved-twice", "a fresh entry dating only off the head",
                 ("G2", "the run read the push of claude/2's head 2 times, where its first read owes 1"))):
            with self.subTest(fault=fault):
                self.assertEqual(violations(held + [board_with(fault)], issues=(1, 2)), [])
                with mutants.switched_on(mutants.MUTANTS[mutant]):
                    found = [(v.guarantee, v.detail) for v in violations(held + [board_with(fault)], issues=(1, 2))]
                self.assertIn(shown, found)

    def test_f8_s_third_review_the_run_a_claim_s_push_started_dates_an_undated_head_at_its_start(self):
        """F8's third review: b's claim commit, dated two days back, makes
        its branch, whose create event starts the run; both reads of its
        push are answered before the activity lists the making, an empty
        list with no floor, so the head is dated at the run's start and
        held, the log saying so; the mutant that dates the branch as any
        other releases the claim in the run its own push started. A
        schedule run with the same reads dates the committer date, as
        before, and releases it, which the oracle reads the same."""
        fresh = [Verb("claim", "b", (2, -48 * M.HOUR)), Verb("label-on", "b", (2, "claimed"))]
        started = fresh + [board_with("pushes-lagging-twice", event="create", ref_name="claude/2")]
        self.assertEqual(violations(started, issues=(1, 2)), [])
        with mutants.switched_on(mutants.MUTANTS["the run's event ignored"]):
            found = [(v.guarantee, v.detail) for v in violations(started, issues=(1, 2))]
        self.assertIn(("G2", "released #2, which was not due"), found)
        self.assertIn(("G2", "the run's log says claude/2's idleness is read from its committer date, after a list "
                             "naming no push, where its reads owe the start of this run, after a list naming no "
                             "push"), found)
        self.assertEqual(violations(fresh + [board_with("pushes-lagging-twice")], issues=(1, 2)), [])

    def test_f8_s_third_review_the_floor_is_the_newest_entry_of_either_read(self):
        """F8's third review: an activity catching up, two pushes behind on
        the first read and one on the second, lists two stale entries, the
        second's the newer, which the floor is; a first read refused and a
        second a push behind keep the second's; the branch's delete after
        the run read it dates the claim as a push would. Each mutant that
        undoes one of them is red."""
        three = [Verb("claim", "b", (2,)), Verb("label-on", "b", (2, "claimed")), Verb("wait", "clock", (20,)),
                 Verb("commit", "b", (2,)), Verb("wait", "clock", (5,)), Verb("commit", "b", (2, -48 * M.HOUR))]
        two = [Verb("claim", "b", (2,)), Verb("label-on", "b", (2, "claimed")), Verb("wait", "clock", (20,)),
               Verb("commit", "b", (2, -48 * M.HOUR))]
        for steps, fault, mutant, shown in (
                (three, "pushes-catching-up", "the earlier floor", ("G2", "released #2, which was not due")),
                (three, "pushes-catching-up", "the floor of the first read alone",
                 ("G2", "released #2, which was not due")),
                (two, "pushes-catching-up", "the floor of the first read alone",
                 ("G2", "released #2, which was not due")),
                (two, "pushes-500-then-lagging", "the floor of the first read alone",
                 ("G2", "released #2, which was not due")),
                (two, "pushes-deleted", "a fresh delete dates nothing",
                 ("G2", "the run read the push of claude/2's head 2 times, where its first read owes 1")),
                (two, "pushes-deleted", "a fresh delete dates nothing",
                 ("G2", "the run's log says claude/2's idleness is read from its newest entry's time, after a list "
                        "naming no push, where its reads owe no line"))):
            with self.subTest(fault=fault, mutant=mutant):
                self.assertEqual(violations(steps + [board_with(fault)], issues=(1, 2)), [])
                with mutants.switched_on(mutants.MUTANTS[mutant]):
                    found = [(v.guarantee, v.detail) for v in violations(steps + [board_with(fault)], issues=(1, 2))]
                self.assertIn(shown, found)

    def test_the_run_s_log_line_is_held_to_what_its_reads_owe(self):
        """G2's log (pushes_logged): one line for a claim no read dated by
        its push, its start the run's words for why and its end the clause
        the floor rule picks, GitHub's answer in parentheses after a
        refusal; none for a claim dated by its push or a 404; and none for
        a branch whose push the run never read."""
        model = M.seeded((1,))
        model.now = 25 * M.HOUR
        ref = "claude/1"
        model.set_ref(ref, model.refs[M.MAIN])
        query = (("ref", f"refs/heads/{ref}"),)
        stale = [{"ref": f"refs/heads/{ref}", "timestamp": M.iso(M.HOUR), "after": "f" * 40, "before": "0" * 40}]
        lagging = Call("GET", "/activity", query, None, 200, False, True, None, stale)
        refused = Call("GET", "/activity", query, None, 500, False, True)
        missing = Call("GET", "/activity", query, None, 404, False)
        floor = oracle.LOG_WORDS["floor"].format(floor=M.iso(M.HOUR))
        listed = f"{ref}: {oracle.LOG_WORDS['listed none']}"
        said = f"{ref}: {oracle.LOG_WORDS['refused']}500 injected)"
        self.assertEqual(invariants.pushes_logged([lagging, lagging], model, [listed + floor]), [])
        self.assertEqual(invariants.pushes_logged([lagging, refused], model, [said + floor]), [])
        self.assertEqual(invariants.pushes_logged([refused, refused], model,
                                                  [said + oracle.LOG_WORDS["committer date"]]), [])
        self.assertEqual(invariants.pushes_logged([missing], model, []), [])
        # A second read's 404 after a first that listed an entry keeps the
        # first's words and floor; after one that was refused it says what a
        # 404 always said, and no line is owed (F8's third review).
        self.assertEqual(invariants.pushes_logged([lagging, missing], model, [listed + floor]), [])
        self.assertEqual(invariants.pushes_logged([refused, missing], model, []), [])
        # In the run the branch's push started the line reads the run's
        # start, and the head is dated at it (SPEC["started by"]).
        begun = oracle.LOG_WORDS["run start"].format(at=M.iso(model.now))
        self.assertEqual(invariants.pushes_logged([lagging, lagging], model, [listed + begun], ref), [])
        self.assertEqual(invariants.pushes_logged([refused, refused], model, [said + begun], ref), [])
        self.assertEqual(invariants.pushes_logged([missing], model, [], ref), [])
        self.assertEqual(invariants.push_dates([lagging, lagging], model, ref), {ref: model.now})
        self.assertEqual(invariants.push_dates([missing], model, ref), {ref: model.now})
        # An entry that set the head a day before the run began dates it
        # outside the run, and in the run the branch's push started it is an
        # older push of the head: one read is enough, the run's start dates
        # the head, and the line says the newest push listed came before the
        # run began (SPEC["started by"]; F8's follow-up's second review).
        own = [{"ref": f"refs/heads/{ref}", "timestamp": M.iso(M.HOUR), "after": model.refs[ref],
                "before": "0" * 40}]
        older = Call("GET", "/activity", query, None, 200, False, True, None, own)
        older_line = f"{ref}: {oracle.LOG_WORDS['older push']}" + begun
        self.assertEqual(invariants.push_readings([older], model),
                         {ref: invariants.PushReading(M.HOUR, "push")})
        self.assertEqual(invariants.push_readings([older], model, ref),
                         {ref: invariants.PushReading(model.now, "run start", "older push", M.iso(model.now))})
        self.assertEqual(invariants.pushes_read_again([older], model), [])
        self.assertEqual(invariants.pushes_logged([older], model, [older_line], ref), [])
        self.assertEqual([v.guarantee for v in invariants.pushes_logged([older], model, [], ref)], ["G2"])
        self.assertEqual([v.guarantee for v in invariants.pushes_logged([older], model, [listed + begun], ref)],
                         ["G2"])
        # Another branch's push starting the run leaves the floor rule to
        # date the head: the stale entry's time is later than its commit's.
        self.assertEqual(invariants.push_dates([lagging, lagging], model, "claude/2"), {ref: M.HOUR})
        for calls, lines in (([lagging, lagging], [listed + floor]), ([lagging, lagging], [listed + begun] * 2),
                             ([lagging, lagging], [listed + begun.replace("01:00", "02:00")]),
                             ([missing], [listed + begun])):
            with self.subTest(calls=[c.status for c in calls], lines=lines, started_by=ref):
                self.assertEqual([v.guarantee for v in invariants.pushes_logged(calls, model, lines, ref)], ["G2"])
        for calls, lines in (([lagging, lagging], [listed + oracle.LOG_WORDS["committer date"]]),
                             ([lagging, lagging], [said + floor]),
                             ([lagging, lagging], [listed + floor.replace("01:00", "02:00")]),
                             ([lagging, lagging], []),
                             ([lagging, lagging], [listed + floor] * 2),
                             ([lagging, refused], [f"{ref}: {oracle.LOG_WORDS['refused']}500 injected" + floor]),
                             ([missing], [listed + oracle.LOG_WORDS["committer date"]]),
                             ([], [listed + floor])):
            with self.subTest(calls=[c.status for c in calls], lines=lines):
                self.assertEqual([v.guarantee for v in invariants.pushes_logged(calls, model, lines)], ["G2"])

    def test_f14_an_issue_a_worker_s_label_names_is_shown_under_given_to_workers(self):
        """Found by this step, fixed after 0.4.1: an issue a worker's label
        names, with no todo, is shown under Given to workers as the spec
        reads; until the fix it was shown in neither section, which the
        mutant that asks for todo again shows."""
        self.assertEqual(violations([BOARD], labels=("worker:a",)), [])
        with mutants.switched_on(mutants.MUTANTS["given to workers needing the label todo"]):
            found = violations([BOARD], labels=("worker:a",))
        self.assertEqual([(v.guarantee, v.detail) for v in found],
                         [("G9", "Given to workers is {}, the oracle's {1: ['a']}")])


class AReleaseThatPutsTodoOn(unittest.TestCase):
    """F15's review: a release puts todo on a task a worker's label alone
    gave, and a failure there leaves the issue as the run found it."""

    DUE = [Verb("claim", "a", (1,)), Verb("label-on", "a", (1, "claimed")), Verb("label-on", "a", (1, "worker:a")),
           Verb("wait", "clock", (25,))]

    def test_a_todo_github_refused_and_never_wrote_still_holds_the_run_to_putting_the_labels_back(self):
        """Taking off a todo GitHub never wrote answers 404, which is no
        refused putting back, so a run that puts no label back is caught
        (G3L), where until the review the check was skipped."""
        steps = self.DUE + [board_with("label-post-500"), BOARD]
        self.assertEqual(violations(steps, labels=("worker:a",)), [])
        with mutants.switched_on(mutants.MUTANTS["a refused todo puts no label back"]):
            self.assertIn("G3L", guarantees(violations(steps, labels=("worker:a",))))

    def test_a_todo_github_wrote_and_lost_its_answer_to_is_taken_off_again(self):
        steps = self.DUE + [board_with("label-post-lost"), BOARD]
        self.assertEqual(violations(steps, labels=("worker:a",)), [])
        with mutants.switched_on(mutants.MUTANTS["a todo written and lost stays on"]):
            self.assertIn("G3L", guarantees(violations(steps, labels=("worker:a",))))

    def test_a_blocker_and_an_epic_get_no_todo(self):
        for label, mutant in (("blocker", "a blocker gets todo"), ("epic", "an epic gets todo")):
            with self.subTest(label=label):
                steps = self.DUE + [BOARD]
                self.assertEqual(violations(steps, labels=(label,)), [])
                with mutants.switched_on(mutants.MUTANTS[mutant]):
                    self.assertIn("G3", guarantees(violations(steps, labels=(label,))))

    def test_only_a_refusal_to_take_the_todo_off_is_a_refused_putting_back(self):
        refused = Call("DELETE", "/issues/1/labels/todo", (), None, 500, False, True)
        not_there = Call("DELETE", "/issues/1/labels/todo", (), None, 404, False)
        self.assertTrue(invariants.put_back_refused([refused], 1))
        self.assertFalse(invariants.put_back_refused([not_there], 1))


class AReleaseThatCannotReadItsComment(unittest.TestCase):
    """F20: GitHub answers the release comment with an error and refuses
    the read of the issue's comments after it, which would say whether a
    release comment stands, the run's own or, whatever GitHub answered,
    one an earlier run's post or someone else wrote; the run leaves the
    labels as the release left them, counts no release and goes on, and
    the next run settles it (SPEC["unread"], SPEC["unsettled"],
    SPEC["settled next"])."""

    DUE = [Verb("claim", "a", (1,)), Verb("label-on", "a", (1, "claimed")), Verb("label-on", "a", (1, "worker:a")),
           Verb("wait", "clock", (25,))]
    # The comment written and its answer lost, nothing written after a
    # 500, and nothing written after a refusal, a 4xx, where a release
    # comment someone else wrote may still stand (F23's review), whether
    # GitHub refused it for the comment, a 422, or for a rate limit on
    # each of the client's tries, after which the read is refused for a
    # rate limit the client, its waits spent, does not wait out.
    FAULTS = ("comment-lost-then-unread", "comment-500-then-unread", "comment-422-then-unread",
              "comment-limited-then-unread")

    def test_the_labels_stay_as_the_release_left_them_and_the_next_run_settles_it(self):
        for fault in self.FAULTS:
            with self.subTest(fault=fault):
                self.assertEqual(violations(self.DUE + [board_with(fault), BOARD]), [])

    def test_putting_the_labels_back_counting_the_release_or_failing_the_run_is_caught(self):
        """Counting the release or failing the run under each fault; putting
        the labels back, F20's mutant as built until its fix, a refused read
        taken for one that lists none, under the refusal alone: since F23's
        fix a read that lists none after a server error, the answer F20's
        first two faults give the post, leaves the labels off too, so there
        the mutant does what the library does, and only after a refusal
        does it put them back (F23's review, which found the refusal
        untested)."""
        for fault in self.FAULTS:
            for mutant, guarantee in (("an unsettled release counted", "G3L"),
                                      ("an unsettled release failing the run", "G15")):
                with self.subTest(fault=fault, mutant=mutant), mutants.switched_on(mutants.MUTANTS[mutant]):
                    self.assertEqual(guarantees(violations(self.DUE + [board_with(fault), BOARD])), [guarantee])
        with mutants.switched_on(mutants.MUTANTS["an unsettled release put back"]):
            for fault in self.FAULTS:
                with self.subTest(fault=fault, mutant="an unsettled release put back"):
                    found = guarantees(violations(self.DUE + [board_with(fault), BOARD]))
                    refused = fault in ("comment-422-then-unread", "comment-limited-then-unread")
                    self.assertEqual(found, ["G3L"] if refused else [])

    def test_a_holder_who_pushes_before_the_next_run_keeps_the_claim_with_its_labels_off(self):
        """F20's review: the holder pushes between the unsettled run and the
        next, which finds any comment GitHub wrote older than the push and
        so no release: the claim stays the holder's with its labels off,
        nothing making a holder announce again, until it goes a day without
        a commit and is released again."""
        for fault in self.FAULTS:
            with self.subTest(fault=fault):
                self.assertEqual(violations(self.DUE + [board_with(fault), Verb("commit", "a", (1,)), BOARD,
                                                        Verb("wait", "clock", (25,)), BOARD]), [])

    def test_the_read_after_a_failed_release_comment_is_one_the_run_does_without(self):
        """A refused read excuses a run that fails, but not this one, which
        the run does without; nor a refused read after another kind of
        comment, or of another issue's comments, nor one after the re-read
        GitHub answered (F20's review)."""
        posted = Call("POST", "/issues/1/comments", (), {"body": oracle.RELEASE_MARKER + "\nreleased"}, 502, True,
                      True)
        unread = Call("GET", "/issues/1/comments", (), None, 500, False, True)
        another = Call("GET", "/issues/2/comments", (), None, 500, False, True)
        overlap = Call("POST", "/issues/1/comments", (), {"body": "<!-- sdlc-coordinator: overlap 2 -->"}, 502,
                       True, True)
        self.assertEqual(invariants.unread_releases([posted, unread]), {1})
        self.assertEqual(invariants.unread_releases([unread, posted]), set())
        # A refusal wrote nothing, but the read after it would still have
        # said whether a release comment stands (F23's review).
        refused = Call("POST", "/issues/1/comments", (), {"body": oracle.RELEASE_MARKER + "\nreleased"}, 422, False,
                       True)
        self.assertEqual(invariants.unread_releases([refused, unread]), {1})
        answered = Call("GET", "/issues/1/comments", (), None, 200, False)
        self.assertEqual(invariants.unread_releases([posted, answered, unread]), set())
        self.assertEqual(invariants.unread_releases([overlap, unread]), set())
        self.assertFalse(invariants.reads_failed([posted, unread]))
        self.assertTrue(invariants.reads_failed([posted, unread, another]))
        self.assertTrue(invariants.reads_failed([overlap, unread]))
        self.assertEqual(invariants.comments_of("/issues/12/comments"), 12)
        for path in ("/issues/12/labels", "/issues/12", "/pulls/12/comments", "/issues/\u00b2/comments"):
            self.assertIsNone(invariants.comments_of(path), path)


class AReleaseThatCannotTellWhetherItsCommentStands(unittest.TestCase):
    """F23: GitHub answers the release comment with a server error or with
    no answer at all, either of which may come after it wrote the comment,
    and the read of the issue's comments after it lists none, the comment
    written and listed late or never written; the run leaves the labels as
    the release left them, counts no release and goes on, and the next run
    settles it (SPEC["listed late"], SPEC["unsettled"], SPEC["settled
    next"])."""

    DUE = [Verb("claim", "a", (1,)), Verb("label-on", "a", (1, "claimed")), Verb("label-on", "a", (1, "worker:a")),
           Verb("wait", "clock", (25,))]
    # Written and listed late after a 502 or no answer, and never written
    # after a 500.
    FAULTS = (("comment-late", 1), ("comment-unanswered-late", 1), ("comment-500", 0))

    def test_the_labels_stay_off_and_the_next_run_settles_it(self):
        for fault, written in self.FAULTS:
            with self.subTest(fault=fault):
                model, found = played(self.DUE + [board_with(fault)])
                self.assertEqual(found, [])
                self.assertEqual((model.issues[1].labels, "claude/1" in model.refs), ({"todo"}, True))
                self.assertEqual(release_comments(model, 1), written)
                model, found = played(self.DUE + [board_with(fault), BOARD])
                self.assertEqual(found, [])
                self.assertEqual((model.issues[1].labels, "claude/1" in model.refs), ({"todo"}, False))
                self.assertEqual(release_comments(model, 1), 1, "the next run counts the comment or makes one")

    def test_each_of_f23_s_mutants_is_caught_under_its_fault(self):
        """The rule as built until the fix, under each of the three faults;
        no answer read as a refusal, under the fault that has none; and a
        refusal read as a failure that may have written, under a 422."""
        cases = [(fault, "a late comment's labels put back") for fault, _ in self.FAULTS]
        cases += [("comment-unanswered-late", "no answer taken for a refusal"),
                  ("comment-422", "a refusal taken for a late comment")]
        for fault, mutant in cases:
            with self.subTest(fault=fault, mutant=mutant), mutants.switched_on(mutants.MUTANTS[mutant]):
                self.assertEqual(guarantees(violations(self.DUE + [board_with(fault), BOARD])), ["G3L"])

    def test_a_holder_who_pushes_before_the_next_run_keeps_the_claim_with_its_labels_off(self):
        """As after F20's faults: a comment GitHub wrote is older than the
        holder's push, so the next run finds no release, and the claim stays
        the holder's with its labels off until it goes a day without a
        commit and is released again."""
        for fault, _ in self.FAULTS:
            with self.subTest(fault=fault):
                self.assertEqual(violations(self.DUE + [board_with(fault), Verb("commit", "a", (1,)), BOARD,
                                                        Verb("wait", "clock", (25,)), BOARD]), [])

    def test_unlisted_releases_are_read_from_the_calls(self):
        """A failed post of the release comment that may have written it, a
        server error or no answer, with a re-read GitHub answered without
        the comment that post wrote, or with none since it wrote none; a
        refusal, a re-read that lists the comment, one GitHub refused,
        which is F20's, or a read before the post, is none."""
        release = {"body": oracle.RELEASE_MARKER + "\nreleased"}
        late = verbs.FAULTS["comment-late"].then

        def post(status, written):
            return Call("POST", "/issues/1/comments", (), release, status, written, True)

        listed = Call("GET", "/issues/1/comments", (), None, 200, False)
        listed_late = Call("GET", "/issues/1/comments", (), None, 200, False, True, late)
        refused = Call("GET", "/issues/1/comments", (), None, 500, False, True)
        self.assertEqual(invariants.unlisted_releases([post(502, True), listed_late]), {1})
        self.assertEqual(invariants.unlisted_releases([post(None, True), listed_late]), {1})
        self.assertEqual(invariants.unlisted_releases([post(500, False), listed]), {1})
        self.assertEqual(invariants.unlisted_releases([post(502, True), listed]), set())
        self.assertEqual(invariants.unlisted_releases([post(422, False), listed]), set())
        self.assertEqual(invariants.unlisted_releases([post(500, False), refused]), set())
        self.assertEqual(invariants.unlisted_releases([listed_late, post(502, True)]), set())
        overlap = Call("POST", "/issues/1/comments", (), {"body": "<!-- sdlc-coordinator: overlap 2 -->"}, 502, True,
                       True)
        self.assertEqual(invariants.unlisted_releases([overlap, listed_late]), set())
        self.assertEqual(invariants.unread_releases([post(500, False), refused]), {1})
        self.assertEqual(invariants.unread_releases([post(None, True), listed_late]), {1})
        self.assertTrue(invariants.failed(post(None, True)))
        self.assertFalse(invariants.may_have_written(post(422, False)))


class TheClientsWaitsForARateLimit(unittest.TestCase):
    """The library's client waits as GitHub asks for a rate limit and makes
    the call again (adapters.github.rate_wait), so one call the library
    asks for is up to three on the fake, each try after the first kept with
    the wait the client made before it (Call.waited): the harness judges
    the call by its last try (invariants.answered), holds each wait to the
    one GitHub asked for (invariants.waits_as_asked), and holds every run,
    a faulted or a failed one too, to one release comment to an issue and
    one overlap comment to a piece (invariants.written_once)."""

    DUE = [Verb("claim", "a", (1,)), Verb("label-on", "a", (1, "claimed")), Verb("label-on", "a", (1, "worker:a")),
           Verb("wait", "clock", (25,))]
    # F11's trace's shape: a ready pull request a day idle, whose comment
    # post_comments makes.
    READY = [Verb("claim", "a", (1,)), Verb("label-on", "a", (1, "claimed")), Verb("label-on", "a", (1, "worker:a")),
             Verb("open-draft", "a", (1,)), Verb("mark-ready", "a", (100,)), Verb("wait", "clock", (25,))]

    def due_model(self):
        model = M.seeded((1,), writers=("a",))
        cycle.board_run(model)
        for step in self.DUE:
            verbs.apply(model, step)
        return model

    def record_of(self, fault):
        return cycle.board_run(self.due_model(), faults=(verbs.FAULTS[fault],))

    def judged(self, *faults):
        """The checks of one board run made whole on a due claim, under the
        faults given as the fake takes them."""
        model = self.due_model()
        before = model.clone()
        return invariants.cycle_checks(before, model, cycle.board_run(model, faults=faults))

    def test_a_call_the_client_made_again_is_one_judged_by_its_last_try(self):
        """A call the fake kept a wait before is the client's next try of
        the call before it; a call the library makes again of its own,
        after a pause of its own, as head_since's second read of a push,
        was made at once by the client and stands for itself, whatever the
        refusal before it."""
        limited = Call("GET", "/branches", (("per_page", "100"),), None, 429, False, True, verbs.FAULTS["branches-429"])
        read = limited._replace(status=200, injected=False, fault=None, waited=60)
        self.assertEqual(invariants.answered([limited, read]), [(1, read)])
        self.assertTrue(invariants.reads_failed([limited]))
        self.assertFalse(invariants.reads_failed([limited, read]))
        tries = [limited, limited._replace(waited=60), read._replace(waited=120)]
        self.assertEqual(invariants.answered(tries), [(2, tries[2])])
        again = read._replace(waited=None)
        self.assertEqual(invariants.answered([limited, again]), [(0, limited), (1, again)], "made again of its own")

    def test_each_wait_is_the_one_github_asked_for(self):
        """The wait owed is read from the fault, never from the library's
        rule: the longest GitHub asks for, retry-after's ten seconds or a
        second past the hour's end, a hundred seconds on
        (branches-limited-for-the-hour); a minute where it asks for none
        (branches-429); and none once the run's three minutes are spent,
        as by the comment refused on every try before the read in
        comment-limited-then-unread, or for a 403 that names neither a
        wait nor a rate limit (pushes-403). The library waits as owed, and
        each mutant of its waits is seen by them in the run it waits
        wrong in, whatever the run did after."""
        cases = (
            ("retry-after waited in place of the hour's end", [], "branches-limited-for-the-hour",
             "GET /branches made again after 10 s, where GitHub asked for 101 s"),
            ("retry-after not telling a rate limit", [], "branches-limited-for-the-hour",
             "GET /branches not made again after the 101 s GitHub asked for"),
            ("the client never waiting", [], "branches-429", "GET /branches not made again after the 60 s GitHub "
                                                             "asked for"),
            ("no bound on a run's waits", self.DUE, "comment-limited-then-unread",
             "GET /issues/1/comments made again after 60 s, where the client owed no wait"),
            ("any 403 taken for a rate limit", self.DUE, "pushes-403",
             "GET /activity made again after 60 s, where the client owed no wait"),
        )
        for mutant, prefix, fault, detail in cases:
            with self.subTest(mutant=mutant):
                steps = prefix + [board_with(fault)]
                self.assertEqual(violations(steps), [])
                with mutants.switched_on(mutants.MUTANTS[mutant]):
                    self.assertIn(invariants.Violation("G15", detail), violations(steps))

    def test_a_run_that_failed_is_held_to_its_waits_and_its_writes(self):
        """A run that failed after a refused read is excused for failing,
        but not for how it waited or what it wrote, which it did all the
        same: the client that never waits fails the read of the branches
        that a minute's wait would have had, seen in a run made whole and
        in the first half of one made in two; and a run that wrote one
        release comment twice before a refused read is seen too."""
        never = mutants.MUTANTS["the client never waiting"]
        detail = "GET /branches not made again after the 60 s GitHub asked for"
        halves = [Verb("board-start", "board", (cycle.SCHEDULE, "", ("branches-429",))), Verb("board-finish", "board")]
        for steps in ([board_with("branches-429")], halves):
            with self.subTest(steps=steps[0].name), mutants.switched_on(never):
                self.assertEqual(violations(steps), [invariants.Violation("G15", detail)])
        posted = Call("POST", "/issues/1/comments", (), {"body": oracle.RELEASE_MARKER + "\nreleased"}, 200, True)
        unread = Call("GET", "/branches", (("per_page", "100"),), None, 500, False, True)
        record = cycle.RunRecord(cycle.SCHEDULE, "", 0, 1, "GitHubError: HTTP 500", "", (), (posted, posted, unread),
                                 None)
        self.assertTrue(invariants.reads_failed(record.calls))
        self.assertEqual(invariants.cycle_checks(None, None, record),
                         [invariants.Violation("G3", "#1 heard claim-released 2 times in one run")])

    def test_a_run_that_fails_after_a_read_answered_on_its_second_try_is_not_excused(self):
        """F11's mutant stops the run at the refused comment; the read of
        the branches before it, refused once for a rate limit and answered
        after the client's wait, excuses nothing, where it excused the
        failed run while each try counted as a read of its own."""
        steps = self.READY + [board_with("branches-429", "comment-500"), BOARD]
        self.assertEqual(violations(steps), [])
        self.assertEqual(violations(self.READY + [board_with("branches-429"), BOARD]), [])
        with mutants.switched_on(mutants.MUTANTS["a refused comment stops the run"]):
            self.assertEqual(guarantees(violations(steps)), ["G15"])

    def test_a_release_comment_refused_on_every_try_is_a_refused_write(self):
        """comment-limited-for-the-run: the release comment is refused on
        each of the client's three tries, its waits spent, a 4xx, so the
        release fails as under comment-422, its labels go back and the next
        run releases the claim (SPEC["refused"]); with the read after it
        refused too, comment-limited-then-unread, the run cannot tell
        whether a release comment stands and leaves the release to the
        next run (F20)."""
        self.assertEqual(violations(self.DUE + [board_with("comment-limited-for-the-run"), BOARD]), [])
        for fault, unread in (("comment-limited-for-the-run", set()), ("comment-limited-then-unread", {1})):
            with self.subTest(fault=fault):
                record = self.record_of(fault)
                posted = [c.status for c in record.calls if c.method == "POST" and c.path == "/issues/1/comments"]
                self.assertEqual((posted, invariants.unread_releases(record.calls)), ([403] * 3, unread))

    def test_a_push_read_refused_on_every_try_is_one_read_and_the_committer_date_stands_in(self):
        """pushes-429-for-the-run: three tries of one read, a minute and
        then two apart, after which G2 owes no second read (may_tell_more),
        as the library makes none (github.may_answer_later); the library
        as it was, reading such a refusal again after its pause, breaks G2
        here."""
        steps = self.DUE + [board_with("pushes-429-for-the-run"), BOARD]
        self.assertEqual(violations(steps), [])
        record = self.record_of("pushes-429-for-the-run")
        tries = [(c.status, c.waited) for c in record.calls if c.path == "/activity"
                 and "activity_type" not in dict(c.query)]
        self.assertEqual(tries, [(429, None), (429, 60), (429, 120)])
        self.assertEqual([len(reads) for reads in invariants.activity_reads(record.calls, None).values()], [1])
        with mutants.switched_on(mutants.MUTANTS["a rate limit's refusal read again after the pause"]):
            self.assertEqual(guarantees(violations(steps)), ["G2"])

    def test_a_push_read_the_client_gave_up_on_at_once_is_one_read_too(self):
        """The second review of the client's waits: a push's read refused
        for a wait longer than a run waits (pushes-limited-too-long), or
        once the run's waits are spent, here by a read of the branches
        refused twice before it, is one read the client made once, and the
        library as it was reads it again after its pause, two reads where
        the spec owes one. Until the fake kept the client's waits the
        harness took that second read for the client's next try of the
        first and saw nothing."""
        spent = (Fault("GET", "/branches", 429, times=2), Fault("GET", "/activity", 429, query=verbs.PUSHES))
        broken = [invariants.Violation("G2", "the run read the push of claude/1's head 2 times, where its first "
                                             "read owes 1")]
        for name, faults in (("pushes-limited-too-long", (verbs.FAULTS["pushes-limited-too-long"],)),
                             ("the waits spent", spent)):
            with self.subTest(case=name):
                self.assertEqual(self.judged(*faults), [])
                with mutants.switched_on(mutants.MUTANTS["a rate limit's refusal read again after the pause"]):
                    self.assertEqual(self.judged(*faults), broken)

    def test_a_comment_written_twice_in_one_run_is_seen_though_a_fault_touched_the_run(self):
        """Under the mutant that takes a 5xx for a rate limit, the client
        posts comment-lost's release comment again after GitHub wrote it;
        the writes of a run a fault touched are compared with nothing else,
        so the check of a run's writes is what sees it, for an overlap
        comment as for a release comment."""
        steps = self.DUE + [board_with("comment-lost"), BOARD]
        self.assertEqual(violations(steps), [])
        with mutants.switched_on(mutants.MUTANTS["a 5xx retried as a rate limit"]):
            self.assertIn(invariants.Violation("G3", "#1 heard claim-released 2 times in one run"), violations(steps))
        told = oracle.Effect("comment", 2, "overlap")
        self.assertEqual(invariants.written_once([told, told]),
                         [invariants.Violation("G7", "#2 heard overlap 2 times in one run")])
        self.assertEqual(invariants.written_once([told, oracle.Effect("comment", 3, "overlap")]), [])

    def test_a_rename_refused_for_a_rate_limit_owes_no_word_on_the_issue(self):
        """A rename a rate limit refuses on every try is refused for the
        moment, as after a 500 (rename-limited): the library says nothing
        on the issue (github.explain_refusal), and the harness, which reads
        a refusal for good by its status, leaves it out of the names a run
        failed to free (invariants.names_not_freed), or it would owe a word
        for a 403 that names no rule and no right. The library as it was,
        saying so, and a classifier deaf to the rate limit's words, which
        then does not wait it out either, are both seen."""
        steps = [Verb("claim", "a", (1,)), Verb("wait", "clock", (25,)), board_with("rename-limited"), BOARD]
        self.assertEqual(violations(steps), [])
        for mutant, found in (("a rename a rate limit refused said refused for good", ["G10"]),
                              ("a rate limit's words not telling one", ["G10", "G15"])):
            with self.subTest(mutant=mutant), mutants.switched_on(mutants.MUTANTS[mutant]):
                self.assertEqual(guarantees(violations(steps)), found)


class TheChecksHoldUnderAFaultThatNeverFires(unittest.TestCase):
    """The review of this step found the whole effects comparison skipped
    for any board verb that named a fault, fired or not."""

    def test_the_release_order_is_checked_in_a_run_whose_fault_never_fired(self):
        steps = [Verb("claim", "a", (1,)), Verb("label-on", "a", (1, "claimed")), Verb("wait", "clock", (25,)),
                 board_with("label-post-500")]
        self.assertEqual(violations(steps), [])
        with mock.patch.object(github, "release_one", mutants.comment_then_labels):
            self.assertEqual(guarantees(violations(steps)), ["G3"])


if __name__ == "__main__":
    unittest.main()
