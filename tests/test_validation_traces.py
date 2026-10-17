"""The regression traces of DESIGN.md 5.3, one per finding, F1 to F22 and
F26 (validation/traces/, played by validation/replay.py), and known.json, the
record of what is red on purpose: every trace loads and plays with no
mismatch; its red results are exactly what known.json records, as built
and with each open finding's switch off in the oracle; a trace of a
finding fixed, in 0.4.1 or on main after it, is green and turns red at
the step the design's table names under the mutant that undoes the fix;
and every entry of
known.json names a trace or scenario, a step it has, a guarantee the
invariants know and a finding of DESIGN.md's section 13."""

import re
import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import sdlc_protocol  # noqa: E402
from validation import invariants, known, mutants, oracle, replay, scenarios  # noqa: E402

DESIGN = (ROOT / "validation" / "DESIGN.md").read_text(encoding="ascii")
FINDINGS_IN_DESIGN = set(re.findall(r"^\*\*(F\d+)\. ", DESIGN, re.M))
BUDGET_SECONDS = 10
# A fixed finding, each mutant of validation/mutants.py that undoes a part
# of its fix, the step the trace then goes red at (numbered as played, an
# announce counting as three) and the guarantee DESIGN.md's table names;
# F6's fix has two parts, the render's sort key and the quoted path. F8's
# trace goes red at its first board run, step 13, where b's claim, its
# head dated 48 hours back, was pushed an hour before and is released
# once idleness is read by the committer date, and where the activity
# lists a push from a's head after the run read the branch, which the
# run reads the activity again for once the before side of an entry is
# ignored; at its second, step 14, where a's claim, its head dated 72
# hours ahead, a day idle by its push, is held once idleness is read by
# the committer date; at its third, step 15, where GitHub answers the
# first read of b's push with a 500 and the run reads it no more once the
# push is read once; at the run after c pushes its released head again,
# step 23, where that branch stands as a claim, not deleted, once the
# release that kept the head counts by its time alone, or only from the
# push on, since it came before; and at the last, step 25, where a's
# branch, whose release the label claimed undid, has gone a day without
# a push and is not released once a release comment holds a kept head
# whatever the labels say, and b's, the board counting its kept release,
# is released twice once no head is read as kept; and, since F8's second
# review, at step 28, where a's kept head, pushed again a day after the
# board deleted it, meets an activity a push behind whose newest entry is
# that delete, and at step 37, where c's claim commit, pushed back a day
# after a commit moved claude/3 off it, meets one whose newest entry is
# that commit's push, each claim released in the run its push started
# once a stale entry off the head dates it; and at step 29, where a's
# kept head, its release undone, is released again by a run whose
# comment GitHub refuses, and the labels stay off once the earlier
# release of the head is taken for the comment the post would have
# written; and, since F8's fix check, at the five runs after c's two
# commits dated back: at step 39, where both reads are a push behind and
# the floor is the commit's own minute, the log says the claim is read
# from its floor once a floor at the committer date is taken for later;
# at step 41, where the floor is later than a commit dated two days
# back, c's claim is released in the run its push started once the floor
# is dropped; at step 42, where the second read is refused, likewise once
# the floor is the last read's alone; at step 43, where the second read
# lists a push after the run read the branch, the log says the claim is
# read from its floor once that read is judged with no moment of the
# run; and at step 44, where the newest of two such pushes names the head
# on neither side, the run reads again once a fresh entry dates only
# where it moved the branch off the head; and, since F8's third review,
# at step 45, where the first read is two pushes behind and the second
# one, c's claim is released once the floor is the first read's alone
# or the earlier of the two; at step 47, where the read lists the
# branch's delete after the run read it, the run reads again and the
# log says the claim is read from its floor once a fresh delete dates
# nothing; and at step 49, where a's claim anew, its commit dated two
# days back, meets reads both a push behind in the run its own push
# started, it is released once the run's event is ignored; and, since F8's
# follow-up's second review, at step 53, where c has pushed claude/3 back
# to its claim commit, the head the branch's making at 101 set, and the
# run its push started reads the activity two pushes behind, listing that
# making newest, c's claim is released once such a push is trusted in
# that run. F9's
# trace goes red at its first board run, step 2, where the draft level
# with main is told it moved once the adapter reads the comparison's
# ahead_by for behind_by, and at its second, step 4, where the draft
# behind main hears nothing once the adapter takes GitHub's word alone;
# F13's at its first board run, step 4, where b's draft lands before a's,
# whose branch was made first, once the order is by number again or once
# a draft is dated by its opening; at its second, step 5, where GitHub
# refuses the first read of the activity, the run fails once the refusal
# escapes, and the drafts land by their openings once no branch is asked
# about on its own; and at its third, step 7, where GitHub refuses every
# read, c's claim, which nothing dates then, lands first once such work
# counts first. F15's at its run that fails at the comment, step 6, where
# the todo the release put on stays on once the putting back forgets it,
# and at the next run, step 7, where the release puts no todo on once the
# rule is gone, or the page that run writes leaves #1 out of the Queue once
# the snapshot forgets the todo. F16's at its second board run, step 4,
# where the draft GitHub calls clean while it is behind main hears "main
# moved", which the spec says the rules do not hold it for, once such a
# word is compared. F17's at its second board run, step 7, where a's idle
# claim is due while sam's ready pull request from a fork bears its
# branch's name: the claim is not released once that pull request is
# read as the claim's, by the coordinator, by the adapter's ready
# branches or by the board's reading of a release, and sam's pull request
# hears nothing, or is left off the page, once the release's rename is
# read as closing it; and at its last run, step 9, after main has moved
# past sam's branch, which hears no "main moved" once a pull request from
# a fork is left out of the word (F17's review). F18's at its first deal,
# step 1, where #1, which the labels of a and b give, goes to neither once
# such a task is dealt as one given alone, or a takes it before #2, given
# to it alone, once the task given alone waits for the rest; and at its
# second, step 5, after b has claimed #1, its Announce still to come, and
# c has claimed #3 and labelled it blocked, where a is told to carry b's
# claim once whose a claim is is read from the labels alone, and c its
# waiting claim once a claim labelled blocked is carried. F19's at its
# first board run, step 6, where sam, who may not write, has opened a
# pull request marked ready from a's own claude/1 before a opened its
# draft: the claim's row names sam's pull request, or the Overlaps fold
# it into the claim's work, once it is read as the claim's own; and at
# its second, step 7, where a's claim is due: it is not released once
# sam's ready pull request is read as finishing it, by the adapter's
# marking, the coordinator, the adapter's ready branches or the board's
# reading of a release, and sam's pull request, which the release's
# rename closes, is listed or spoken to once the rename is read as
# leaving it open. F20's at its first board run, step 8, where GitHub
# writes a's release comment and loses its answer, then refuses the read
# of the issue's comments after it: the labels go back beside the
# comment once a refused read finds no comment, the release counts and
# the branch is renamed in that run once the run takes the refused read
# for the comment standing, and the run fails once the refused read is
# let through; each again at its third, step 10, for b's comment GitHub
# never wrote. F21's at b's first lease from its second clone, step 1,
# by git -C, which is taken once the guard ignores git's and env's -C,
# at its second, step 2, by cd, taken once the guard does not follow the
# shell's moves, and at its third, step 3, after a cd that is made and one
# that fails, taken once the guard takes the second cd as made; and at
# b's leases from its own clone, step 4, after a here-document whose body
# holds a cd, taken once a push is judged only where the shell's moves
# take it, and step 5, by a -C the shell expands, taken once a push whose
# clone cannot be told is judged by its words alone; and, from F21's
# fourth review, step 6, by a -C relative to where a cd behind a test
# that fails would have taken the shell, taken once such a -C is judged
# where it names alone, step 7, with GIT_DIR exported before an absolute
# -C, taken once GIT_DIR is forgotten when an absolute path names a place,
# and step 8, by a -C from a ~ after HOME is set in the command, taken
# once such a -C is judged where it names alone. F22's at its first board
# run, step 10, after #1 was reopened once a's pull request merged and
# the repository deleted claude/1: claimed, in-review and worker:a stay on
# once the coordinator leaves a reopened issue as it is, and a label goes
# on after one came off once todo is put on last; at its second, step 21,
# where b's pull request, marked ready, finishes the claim on #1 closed
# and reopened, its in-review comes off once a ready pull request is not
# read; at its third, step 24, where b's merged branch is kept and #1
# reopened, in-review stays once the claim branch is not read, the
# coordinator then planning to free #1 wholly and, reading claude/1 again,
# freeing nothing, or once in-review is left on where a branch stands; at
# its fifth, step 36, where a's merged pull request closed #1 after b's
# release and the owner reopened it, b's released branch still standing,
# claimed and worker:a stay once a release before the last close is read
# as an undo (F22's review); at its sixth, step 47, where a's merged
# branch, kept a day after its last commit, is not released in the run
# that takes its in-review off once the releases due are read before the
# freeing; at its seventh, step 49, where the owner put claimed back on
# the reopened #1 to undo the release that run made, claimed comes off
# once the released branch is not read; at its eighth, step 54, where
# the owner closed #1 and reopened it after that release and put claimed
# back again, claimed comes off once claimed put on after the close is
# read as the ended claim's (F22's second review); and at its last since
# F22's third review, step 67, where b claimed #1 after it was closed and
# reopened again and the owner closed b's ready pull request unmerged and
# deleted claude/1, claimed and worker:b stay once a claim made since the
# close is read as an undo. F26's at its first
# guard step, step 1, env --uns FOO before a push to main, which passes
# once a wrapper's long option is read only by its whole name, as do its
# second and third, nice --adj 5 and timeout --sig KILL 60; at its fourth,
# step 4, env --def before a push to main, which passes once a value an
# option may take is taken from the next word; and at b's lease from its
# second clone by env --ch, step 5, taken once env's --chdir is read only
# by its whole name.
UNDONE = {
    "F1": [("the comment before the labels", 5, "G3L")],
    "F2": [("a piece of work named by the number it is shown by", 4, "G7"),
           ("a marker counted whenever it came", 8, "G7"),
           ("the markers on a claim's own pull request never read", 15, "G7"),
           ("an undated claim taking no marker", 16, "G7")],
    "F3": [("the guard passes --force", 1, "G11a")],
    "F4": [("each leg dealt from its own look", 9, "G14")],
    "F14": [("given to workers needing the label todo", 0, "G9")],
    "F5": [("a claim number with leading zeros", 2, "G1")],
    "F6": [("a sort key that calls int after isdigit", 1, "G15"), ("a branch name sent unquoted", 3, "G15")],
    "F7": [("the guard passes a lease onto another's claim", 1, "G11a")],
    "F8": [("idleness by the committer date", 13, "G2"), ("idleness by the committer date", 14, "G3"),
           ("the before side ignored", 13, "G2"), ("the push read once", 15, "G2"),
           ("a kept head dated by its push", 23, "G5"), ("kept counts only at or after the push", 23, "G5"),
           ("a kept release undone holds for good", 25, "G3"), ("a kept head released again", 25, "G2"),
           ("a stale entry off the head dates it", 28, "G2"), ("a stale entry off the head dates it", 37, "G2"),
           ("a kept head's old release taken for the lost comment", 29, "G3L"),
           ("a floor at the committer date taken for later", 39, "G2"), ("the floor dropped", 41, "G3"),
           ("the floor of the last read alone", 42, "G3"),
           ("the second read judged with no moment of the run", 43, "G2"),
           ("a fresh entry dating only off the head", 44, "G2"),
           ("the floor of the first read alone", 45, "G3"), ("the earlier floor", 45, "G3"),
           ("a fresh delete dates nothing", 47, "G2"), ("the run's event ignored", 49, "G3"),
           ("an older push trusted in the run its push started", 53, "G3")],
    "F9": [("an opaque state read by GitHub's word alone", 4, "G10"), ("the comparison read by ahead_by", 2, "G10")],
    "F10": [("a finished draft's release by hand not seen", 9, "G5")],
    "F11": [("a refused comment stops the run", 6, "G15")],
    "F12": [("main moved told only in the push's run", 8, "G10")],
    "F13": [("the landing order by number", 4, "G7"), ("a pull request dated by its opening", 4, "G7"),
            ("a refused activity read stops the run", 5, "G15"), ("a branch never asked about on its own", 5, "G7"),
            ("work nothing dates landing first", 7, "G7")],
    "F15": [("a release that leaves todo off", 7, "G3"), ("a failed release leaves its todo on", 6, "G3L"),
            ("the release's own page without its todo", 7, "G5")],
    "F16": [("a clean word compared", 4, "G10")],
    "F17": [("a fork's ready pull request finishes a claim", 7, "G3"),
            ("a fork's ready pull request skipped as a claim's", 7, "G3"),
            ("a fork's ready pull request keeps a release from counting", 7, "G3"),
            ("a fork's pull request silenced with a released claim", 7, "G10"),
            ("a fork's pull request left off with a released claim", 7, "G9"),
            ("a fork's pull request never told main moved", 9, "G10")],
    "F18": [("a shared task dealt to none", 1, "G14"), ("a shared task before one given alone", 1, "G14"),
            ("the clerk carrying by labels alone", 5, "G14"), ("a blocked claim carried", 5, "G14")],
    "F19": [("no pull request taken for a stranger's", 7, "G3"),
            ("a stranger's ready pull request finishes a claim", 7, "G3"),
            ("a stranger's ready pull request skipped as a claim's", 7, "G3"),
            ("a stranger's ready pull request keeps a release from counting", 7, "G3"),
            ("a stranger's pull request named on a claim's row", 6, "G9"),
            ("a stranger's pull request taken for a claim's work", 6, "G7"),
            ("a stranger's pull request listed after its release", 7, "G9"),
            ("a stranger's pull request spoken to after its release", 7, "G10")],
    "F20": [("an unsettled release put back", 8, "G3L"), ("an unsettled release counted", 8, "G3L"),
            ("an unsettled release failing the run", 8, "G15")],
    "F21": [("the guard ignores git's and env's -C", 1, "G11a"),
            ("the guard does not follow the shell's moves", 2, "G11a"),
            ("a cd to what is not a directory taken as made", 3, "G11a"),
            ("a push judged only where the shell's moves take it", 4, "G11a"),
            ("a push whose clone cannot be told judged by its words alone", 5, "G11a"),
            ("a relative -C after a move judged where it names alone", 6, "G11a"),
            ("GIT_DIR forgotten once an absolute path names a place", 7, "G11a"),
            ("a -C by ~ judged where it names alone", 8, "G11a")],
    "F22": [("a reopened issue left as it is", 10, "G8"), ("todo put on last", 10, "G8"),
            ("a ready pull request's in-review taken off", 21, "G8"),
            ("a claim branch not read for a reopened issue", 24, "G8"),
            ("in-review left on where a branch stands", 24, "G8"),
            ("a release before the last close read as an undo", 36, "G8"),
            ("the releases due read before the freeing", 47, "G3"),
            ("a released branch not read for a reopened issue", 49, "G8"),
            ("claimed put back after the close read as the ended claim's", 54, "G8"),
            ("a claim made since the close read as an undo", 67, "G8")],
    "F26": [("a long option read only in full", 1, "G11a"),
            ("an optional value taken from the next word", 4, "G11a"),
            ("env's abbreviated --chdir not read", 5, "G11a")],
}
# The findings with a trace, every one settled since F22's fix: F1, F3,
# F5, F6, F10 and F11 fixed in 0.4.1; F2, F4, F7, F8, F9, F12, F13, F14,
# F15, F17, F18, F19 and F20 fixed in 0.5.0, and F21, F22 and F26 on main
# after it (known.json's fixed_after); and F16, fixed in the spec's words,
# which the library as built already keeps, so known.json names no
# version for it.
FIXED = ("F1", "F2", "F3", "F4", "F5", "F6", "F7", "F8", "F9", "F10", "F11", "F12", "F13", "F14", "F15", "F16", "F17",
         "F18", "F19", "F20", "F21", "F22", "F26")
# Findings red as built, which no rule of the library answers: none since
# F4's fix; the test keeps the case for the next one.
RED_AS_BUILT = ()


class TheTraces(unittest.TestCase):
    def setUp(self):
        self.traces = replay.traces()

    def test_one_trace_per_finding_of_the_design(self):
        self.assertEqual(sorted(self.traces, key=lambda f: int(f[1:])), list(replay.FINDINGS))
        self.assertEqual(set(replay.FINDINGS), FINDINGS_IN_DESIGN)
        for finding, trace in self.traces.items():
            with self.subTest(finding=finding):
                self.assertEqual(trace.path.stem, finding)
                self.assertEqual(trace.scenario.name, finding)
                self.assertTrue(trace.title and trace.status)
                self.assertTrue(trace.scenario.steps)
                self.assertTrue(set(trace.breaks) <= set(invariants.GUARANTEES))

    def test_every_trace_plays_with_no_mismatch_and_is_red_where_known_json_says(self):
        """As built, and with each switch of the oracle off in turn: a
        switch off shows its own finding where the file records it and
        nothing else, and the traces of the fixed findings stay green."""
        began = time.perf_counter()
        for finding, trace in self.traces.items():
            for switched_off in (None, *sorted(known.AS_BUILT)):
                with self.subTest(finding=finding, switched_off=switched_off):
                    reading = oracle.AS_BUILT if switched_off is None else oracle.AS_BUILT - {switched_off}
                    record = replay.replay(trace, reading)
                    self.assertEqual(record.mismatches, [], "\n".join(record.timeline))
                    found = replay.results(record)
                    self.assertEqual(known.differences("trace", finding, found, switched_off), [],
                                     "\n".join(record.timeline))
        self.assertLess(time.perf_counter() - began, BUDGET_SECONDS)

    def test_the_fixed_findings_are_green_and_the_open_ones_red_as_the_design_says(self):
        for finding, trace in self.traces.items():
            with self.subTest(finding=finding):
                entries = known.entries_of("trace", finding)
                if finding in FIXED:
                    self.assertIn("fixed", trace.status)
                    self.assertEqual(entries, [])
                elif finding in RED_AS_BUILT:
                    self.assertTrue(entries and all(e.finding == finding for e in entries))
                    self.assertNotIn(finding, known.AS_BUILT)
                else:
                    self.assertIn(finding, known.AS_BUILT)
                    self.assertTrue(entries and all(e.finding == finding for e in entries))
                self.assertTrue({e.guarantee for e in entries} <= set(trace.breaks))

    def test_each_fixed_finding_s_trace_turns_red_under_the_mutant_that_undoes_its_fix(self):
        """So a trace recorded green from the start is known to bite: the
        step and the guarantee are the design's table's (DESIGN.md, 5.3)."""
        self.assertEqual(sorted(UNDONE), sorted(FIXED))
        for finding, undoings in UNDONE.items():
            for mutant, step, guarantee in undoings:
                with self.subTest(finding=finding, mutant=mutant):
                    trace = self.traces[finding]
                    self.assertEqual(replay.results(replay.replay(trace)), set())
                    with mutants.switched_on(mutants.MUTANTS[mutant]):
                        record = replay.replay(trace)
                    self.assertIn((step, guarantee), replay.results(record), "\n".join(record.timeline))
                    self.assertIn(guarantee, trace.breaks)

    def test_a_step_naming_a_released_branch_the_run_never_made_is_a_mismatch_and_not_played(self):
        """The mutation lane plays every trace under every mutant: F8's
        push of c's released head names a branch a mutant that skips the
        rename never makes, so the step is a mismatch the record names and
        the lane reads the violations of the steps that played."""
        trace = self.traces["F8"]
        with mutants.switched_on(mutants.MUTANTS["the rename skipped"]):
            record = replay.replay(trace)
        self.assertIn((18, "not played: no released branch of #3"), record.mismatches)
        self.assertIn((13, "G3"), replay.results(record))

    def test_f4_s_trace_deals_from_the_run_s_one_look_and_twice_under_its_mutant(self):
        """The run looks before main moves, so c is dealt nothing; with
        each leg looking for itself, c's later look deals #6 twice."""
        trace = self.traces["F4"]
        record = replay.replay(trace)
        self.assertEqual(replay.results(record), set())
        self.assertIn("{'a': 'take 5', 'b': 'take 6', 'c': 'none'}", record.timeline[-1])
        with mutants.switched_on(mutants.MUTANTS["each leg dealt from its own look"]):
            record = replay.replay(trace)
        self.assertEqual({(v.guarantee, v.detail) for _, v in record.violations},
                         {("G14", "#6 dealt twice from differing looks: {'a': 'take 5', 'b': 'take 6', 'c': 'take 6'}")})

    def test_f18_s_trace_deals_by_pick_and_is_red_under_the_labels_alone_mutant(self):
        """Each deal of F18's trace is judged by Pick on the run's look,
        where no claim has a pull request yet (dealing.pick_problems); with
        the fix, #1 goes to b, the first of a and b with nothing else to
        do, and b, once it holds #1, carries it, before its Announce has
        come and while a's label is still on. The mutant that reads the
        holder from the labels alone, one rule of the clerk as built until
        the fix, sends a to b's claim at step 5. The Announce then takes
        a's label off, so the labels name the holder alone (the spec's
        Announce, from F18's review)."""
        record = replay.replay(self.traces["F18"])
        self.assertEqual(replay.results(record), set())
        deals = [line for line in record.timeline if " deal " in line]
        self.assertEqual(len(deals), 2)
        self.assertIn("{'a': 'take 2', 'b': 'take 1', 'c': 'take 3'}", deals[0])
        self.assertIn("{'a': 'take 2', 'b': 'carry 1', 'c': 'none'}", deals[1])
        self.assertTrue(any("label-off b 1 worker:a: label taken off" in line for line in record.timeline),
                        "\n".join(record.timeline))
        with mutants.switched_on(mutants.MUTANTS["the clerk carrying by labels alone"]):
            record = replay.replay(self.traces["F18"])
        self.assertEqual({(at, v.detail) for at, v in record.violations},
                         {(5, "a carries #1, whose claim is b's"),
                          (5, "b carries nothing though its claim on #1 is its own")})

    def test_f7_s_trace_is_green_and_red_for_g1_too_under_its_mutant(self):
        """The fence refuses the careless lease (G11a at the lease); with
        it gone git takes the lease and the board names b (G1 too)."""
        trace = self.traces["F7"]
        self.assertEqual(replay.results(replay.replay(trace)), set())
        with mutants.switched_on(mutants.MUTANTS["the guard passes a lease onto another's claim"]):
            record = replay.replay(trace)
        self.assertEqual(replay.results(record), {(1, "G11a"), (1, "G1"), (2, "G1")})
        self.assertIn(("G1", "the board names b for claude/1, whose holder is a"),
                      {(v.guarantee, v.detail) for _, v in record.violations})

    def test_f3_s_commands_are_judged_by_git_s_recorded_answers(self):
        record = replay.replay(self.traces["F3"])
        self.assertEqual(record.checks, 6)
        self.assertTrue(all("judged by git" in line for line in record.timeline))

    def test_a_trace_reads_as_a_report(self):
        trace = self.traces["F1"]
        text = replay.report(trace, replay.replay(trace))
        self.assertIn("F1 (fixed in 0.4.1 (#11))", text)
        self.assertIn("red: none", text)
        self.assertIn("board board schedule", text)


class KnownJson(unittest.TestCase):
    def test_the_oracle_s_switches_are_the_file_s(self):
        self.assertEqual(oracle.AS_BUILT, known.AS_BUILT)
        self.assertEqual(known.AS_BUILT, frozenset(), "no finding is read as built since F2's fix")

    def test_every_entry_names_a_step_of_a_trace_or_scenario_a_guarantee_and_a_finding(self):
        traces = replay.traces()
        for entry in known.ENTRIES:
            with self.subTest(entry=entry):
                steps = (traces[entry.name].scenario if entry.kind == "trace" else scenarios.SCENARIOS[entry.name]).steps
                self.assertTrue(0 <= entry.step < len(steps))
                self.assertIn(entry.guarantee, invariants.GUARANTEES)
                self.assertIn(entry.finding, FINDINGS_IN_DESIGN)
                self.assertEqual(steps[entry.step].shows, entry.finding)

    def test_every_step_a_trace_or_scenario_says_shows_a_finding_is_an_entry(self):
        shown = {("trace", f, i, s.shows) for f, t in replay.traces().items() for i, s in enumerate(t.scenario.steps)
                 if s.shows}
        shown |= {("scenario", n, i, s.shows) for n, sc in scenarios.SCENARIOS.items() for i, s in enumerate(sc.steps)
                  if s.shows}
        self.assertEqual(shown, {(e.kind, e.name, e.step, e.finding) for e in known.ENTRIES})

    def test_a_finding_s_switch_in_the_oracle_has_entries_to_show_it(self):
        for finding in sorted(known.AS_BUILT):
            with self.subTest(finding=finding):
                self.assertTrue([e for e in known.ENTRIES if e.finding == finding])

    def test_expected_reads_the_file_as_its_docstring_says(self):
        self.assertEqual(known.expected("trace", "F4"), set())
        self.assertEqual(known.expected("trace", "F7"), set())
        self.assertEqual(known.expected("trace", "F2"), set())
        self.assertEqual(known.expected("trace", "F2", "F2"), set(), "F2's entries went with its fix")
        self.assertEqual(known.expected("scenario", "S05q", "F2"), set())
        self.assertEqual(known.expected("scenario", "S05q", "F12"), set())
        self.assertEqual(known.differences("trace", "F2", {(4, "G7")}, "F2"),
                         ["trace F2 with F2 read from the spec: red and not recorded: step 4, G7"])
        self.assertEqual(known.differences("trace", "F1", {(0, "G3")}),
                         ["trace F1 as built: red and not recorded: step 0, G3"])

    def test_a_finding_fixed_on_main_is_recorded_with_the_last_version_that_has_it(self):
        """fixed_after is the canary's: it runs the release the canary
        repository vendors, so a finding fixed on main is still known there
        at that version and before, and new from the next release on."""
        self.assertEqual(known.FIXED_AFTER,
                         {"F2": "0.4.1", "F4": "0.4.1", "F7": "0.4.1", "F8": "0.4.1", "F9": "0.4.1", "F12": "0.4.1",
                          "F13": "0.4.1", "F14": "0.4.1", "F15": "0.4.1", "F17": "0.4.1", "F18": "0.4.1",
                          "F19": "0.4.1", "F20": "0.4.1", "F21": "0.5.0", "F22": "0.5.0", "F26": "0.5.0"})
        current = known.version_key(sdlc_protocol.__version__)
        for finding, version in known.FIXED_AFTER.items():
            with self.subTest(finding=finding):
                self.assertIn(finding, FINDINGS_IN_DESIGN)
                self.assertNotIn(finding, known.AS_BUILT, "a finding is as built or fixed, never both")
                self.assertIsNotNone(known.version_key(version))
                self.assertLessEqual(known.version_key(version), current,
                                     "the last version with a finding is one main has stated, never a later one")
        fixed_in_0_5_0 = {"F2", "F4", "F7", "F8", "F9", "F12", "F13", "F14", "F15", "F17", "F18", "F19", "F20"}
        fixed_after_0_5_0 = {"F21", "F22", "F26"}
        self.assertEqual(known.as_built_for("0.4.1"), known.AS_BUILT | fixed_in_0_5_0 | fixed_after_0_5_0)
        self.assertEqual(known.as_built_for("0.4.0"), known.AS_BUILT | fixed_in_0_5_0 | fixed_after_0_5_0)
        self.assertEqual(known.as_built_for("0.4.2"), known.AS_BUILT | fixed_after_0_5_0,
                         "the next release carries the fixes")
        self.assertEqual(known.as_built_for("0.5.0"), known.AS_BUILT | fixed_after_0_5_0)
        self.assertEqual(known.as_built_for("0.5.1"), known.AS_BUILT,
                         "the release after 0.5.0 carries F21's, F22's and F26's fixes")
        self.assertEqual(known.as_built_for("1.0.0"), known.AS_BUILT)
        for none in (None, "", "unknown", "0.4", "0.4.1-dirty", "1" * 10 + ".0.0"):
            self.assertEqual(known.as_built_for(none), known.AS_BUILT, none)
        self.assertEqual(known.version_key("0.4.1"), (0, 4, 1))
        self.assertGreater(known.version_key("0.10.0"), known.version_key("0.9.9"))


if __name__ == "__main__":
    unittest.main()
