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
from validation.fake_github import Call  # noqa: E402
from validation.verbs import Scope, Verb  # noqa: E402

BOARD = Verb("board", "board", (cycle.SCHEDULE, ""))
SHARED = Scope(issues=(1, 2), files=((1, ("src/shared.py",)), (2, ("src/shared.py",))))


def board_with(*faults, event=cycle.SCHEDULE, ref_name=""):
    return Verb("board", "board", (event, ref_name, faults))


def run(event, ref_name=""):
    """A run in two halves with nothing between them."""
    return [Verb("board-start", "board", (event, ref_name)), Verb("board-finish", "board")]


def violations(steps, scope=Scope(), known=oracle.AS_BUILT, issues=(1,), labels=("todo",), opaque_pulls=False):
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
    return found


def guarantees(found):
    return sorted({v.guarantee for v in found})


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

    def test_a_refused_release_comment_puts_the_labels_back(self):
        for fault in ("comment-500", "comment-rate-limited", "comment-lost"):
            with self.subTest(fault=fault):
                steps = [Verb("claim", "a", (1,)), Verb("label-on", "a", (1, "claimed")),
                         Verb("label-on", "a", (1, "worker:a")), Verb("wait", "clock", (25,)), board_with(fault), BOARD]
                self.assertEqual(violations(steps), [])

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
    the read of the issue's comments after it, which would say whether it
    wrote the comment anyway; the run leaves the labels as the release
    left them, counts no release and goes on, and the next run settles it
    (SPEC["unsettled"], SPEC["settled next"])."""

    DUE = [Verb("claim", "a", (1,)), Verb("label-on", "a", (1, "claimed")), Verb("label-on", "a", (1, "worker:a")),
           Verb("wait", "clock", (25,))]
    FAULTS = ("comment-lost-then-unread", "comment-500-then-unread")

    def test_the_labels_stay_as_the_release_left_them_and_the_next_run_settles_it(self):
        for fault in self.FAULTS:
            with self.subTest(fault=fault):
                self.assertEqual(violations(self.DUE + [board_with(fault), BOARD]), [])

    def test_putting_the_labels_back_counting_the_release_or_failing_the_run_is_caught(self):
        for fault in self.FAULTS:
            for mutant, guarantee in (("an unsettled release put back", "G3L"),
                                      ("an unsettled release counted", "G3L"),
                                      ("an unsettled release failing the run", "G15")):
                with self.subTest(fault=fault, mutant=mutant), mutants.switched_on(mutants.MUTANTS[mutant]):
                    self.assertEqual(guarantees(violations(self.DUE + [board_with(fault), BOARD])), [guarantee])

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
        answered = Call("GET", "/issues/1/comments", (), None, 200, False)
        self.assertEqual(invariants.unread_releases([posted, answered, unread]), set())
        self.assertEqual(invariants.unread_releases([overlap, unread]), set())
        self.assertFalse(invariants.reads_failed([posted, unread]))
        self.assertTrue(invariants.reads_failed([posted, unread, another]))
        self.assertTrue(invariants.reads_failed([overlap, unread]))
        self.assertEqual(invariants.comments_of("/issues/12/comments"), 12)
        for path in ("/issues/12/labels", "/issues/12", "/pulls/12/comments", "/issues/\u00b2/comments"):
            self.assertIsNone(invariants.comments_of(path), path)


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
