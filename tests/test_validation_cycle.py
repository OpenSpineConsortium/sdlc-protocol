"""The harness's board run (validation/cycle.py): the library's own two
halves, cli.plan_board and cli.act_on_board, making the same calls in the
same order as cli.board_live_run against the same fake (DESIGN.md, 3.3);
the halves apart, with the holder coming back between them; the model's
clock when a run began the now its reads of a claim's push are dated by
(F8's second review); the board branch written only when the run exits
0; and a board run that neither a refused comment nor a hand-made branch
name stops (G15, F6 and F11, fixed in 0.4.1)."""

import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sdlc_protocol import board, cli  # noqa: E402
from sdlc_protocol.adapters import github  # noqa: E402
from validation import clerk, cycle, model as M, verbs  # noqa: E402
from validation.fake_github import FakeGitHub  # noqa: E402
from validation.verbs import Scope, Verb  # noqa: E402

SHARED = Scope(issues=(1, 2, 3), files=((2, ("src/shared.py",)), (3, ("src/shared.py",))))


def busy():
    """A due release on #1; two pieces of work meeting in a file, #2's
    draft, which changes only its claim file, and #3's pull request, marked
    ready, which main has moved past and nobody has touched for a day."""
    model = M.seeded((1, 2, 3))
    steps = [Verb("claim", "a", (1,)), Verb("label-on", "a", (1, "claimed")), Verb("label-on", "a", (1, "worker:a")),
             Verb("claim", "b", (2,)), Verb("open-draft", "b", (2,)), Verb("claim", "c", (3,)),
             Verb("commit", "c", (3,)), Verb("open-draft", "c", (3,)), Verb("drop-claim-file", "c", (3,)),
             Verb("mark-ready", "c", (101,)), Verb("move-main", M.OWNER, ("README.md",)), Verb("wait", "clock", (25,)),
             Verb("commit", "b", (2,))]
    for verb in steps:
        verbs.apply(model, verb, SHARED)
    return model


class FrozenClock:
    """cli.datetime with now() answering the model's clock."""

    def __init__(self, minute):
        self.minute = minute

    def now(self, tz=None):
        return M.moment(self.minute)


def through_cli(model, event, ref_name):
    """cli.board_live_run against a fake over the model: its calls, STATE.md
    and the lines it printed."""
    fake = FakeGitHub(model)
    real = github.GitHub
    with tempfile.TemporaryDirectory() as scratch, \
            mock.patch.object(github, "GitHub", lambda token, repo: real(token, repo, opener=fake, sleep=lambda s: None)), \
            mock.patch.object(cli, "datetime", FrozenClock(model.now)), \
            contextlib.redirect_stdout(io.StringIO()) as printed:
        out = Path(scratch) / "STATE.md"
        args = cli.board_arguments(["--out", str(out), "--sha", model.refs[M.MAIN]])
        environ = {"GITHUB_TOKEN": "t", "GITHUB_REPOSITORY": M.REPO, "GITHUB_EVENT_NAME": event,
                   "GITHUB_REF_NAME": ref_name}
        code = cli.board_live_run(args, environ)
        page = out.read_text(encoding="ascii")
    lines = [line for line in printed.getvalue().splitlines() if not line.endswith(("rewritten", "unchanged"))]
    return code, fake.calls, page, lines


class SameOrderAsTheCli(unittest.TestCase):
    def test_the_cycle_makes_the_cli_s_calls_in_the_cli_s_order(self):
        for event, ref_name in ((cycle.PUSH, M.MAIN), (cycle.SCHEDULE, "")):
            with self.subTest(event=event):
                one, two = busy(), busy()
                record = cycle.board_run(one, event, ref_name)
                code, calls, page, lines = through_cli(two, event, ref_name)
                self.assertEqual(code, record.exit_code)
                self.assertEqual([c[:5] for c in calls], [c[:5] for c in record.calls])
                self.assertEqual(page, record.page)
                self.assertEqual(lines, list(record.lines))
                two.board_text = page
                self.assertEqual(one.canonical(), two.canonical())

    def test_the_cycle_runs_the_cli_s_own_halves(self):
        """A copy of the lines in cycle.py would let a change of order in
        cli.py past the harness unseen."""
        called = []
        plan, act = cli.plan_board, cli.act_on_board

        def planning(*args, **kw):
            called.append("plan")
            return plan(*args, **kw)

        def acting(*args, **kw):
            called.append("act")
            return act(*args, **kw)

        with mock.patch.object(cli, "plan_board", planning), mock.patch.object(cli, "act_on_board", acting):
            cycle.board_run(busy(), cycle.PUSH, M.MAIN)
        self.assertEqual(called, ["plan", "act"])

    def test_the_busy_state_has_every_kind_of_write_in_it(self):
        record = cycle.board_run(busy(), cycle.PUSH, M.MAIN)
        writes = {(c.method, c.path.split("/")[1]) for c in record.calls if c.method != "GET"}
        self.assertEqual(writes, {("DELETE", "issues"), ("POST", "issues"), ("POST", "branches")})
        self.assertTrue(any("main moved" in str(c.body) for c in record.calls))
        self.assertTrue(any("overlap" in str(c.body) for c in record.calls))


class TheRunInTwoHalves(unittest.TestCase):
    def test_with_nothing_between_them_is_the_run_made_whole(self):
        one, two = busy(), busy()
        whole = cycle.board_run(one, cycle.PUSH, M.MAIN)
        half = cycle.start_run(two, cycle.PUSH, M.MAIN)
        self.assertIs(two.running, half)
        self.assertEqual((half.page, half.raised), (whole.page, None))
        self.assertEqual(two.board_text, "", "the branch waits for the run to end")
        record = cycle.finish_run(two)
        self.assertIsNone(two.running)
        self.assertEqual(record[:9], whole[:9])
        self.assertEqual(one.canonical(), two.canonical())

    def test_carries_the_states_its_halves_read(self):
        model = busy()
        before = model.clone()
        record = cycle.finish_run(model, cycle.start_run(model, cycle.PUSH, M.MAIN))
        self.assertEqual(record.started_from.canonical(), before.canonical())
        self.assertEqual(record.planned.issues[1].labels, model.issues[1].labels)
        self.assertIn("claude/1", record.planned.refs, "the first half renames nothing")
        self.assertNotIn("claude/1", model.refs)
        self.assertIsNone(cycle.board_run(busy()).started_from)

    def test_the_holder_back_between_the_halves_keeps_the_claim(self):
        """S09: the release comment is posted and the labels come off in
        the first half; a commit lands; the second half reads the branch
        again and leaves its name alone (spec, Release)."""
        model = M.seeded()
        for verb in (Verb("claim", "a", (1,)), Verb("label-on", "a", (1, "claimed")), Verb("wait", "clock", (25,))):
            verbs.apply(model, verb)
        cycle.start_run(model)
        self.assertEqual(model.issues[1].labels, frozenset({"todo"}))
        verbs.apply(model, Verb("commit", "a", (1,)))
        record = cycle.finish_run(model)
        self.assertEqual(record.exit_code, 0)
        self.assertIn("claude/1: no longer at", " ".join(record.lines))
        self.assertEqual(sorted(model.refs), ["claude/1", "main"])
        self.assertEqual([c.path for c in record.calls if c.method == "POST" and "rename" in c.path], [])

    def test_every_read_of_a_claim_s_push_is_dated_by_the_model_s_clock_when_it_began(self):
        """The now the library is handed, by the board's run and by the
        clerk's look alike, is when that run or look began, on the model's
        clock, and stays so though the clock moves before the run ends: an
        entry that names another head dates it only when it came after
        that (SPEC["moved off"], F8's second review and its fix check),
        so the wall clock in its place would leave a push after the run
        read the branch dating nothing in the harness, and a later now
        would date a stale one."""
        model = M.seeded()
        for verb in (Verb("claim", "a", (1,)), Verb("label-on", "a", (1, "claimed")), Verb("wait", "clock", (25,))):
            verbs.apply(model, verb)
        began, seen, real = model.now, [], github.head_since
        looked = model.clone()
        verbs.apply(looked, Verb("wait", "clock", (3,)))

        def reading(api, branch, sha, refused=None, now=None):
            seen.append(now)
            return real(api, branch, sha, refused, now)

        with mock.patch.object(github, "head_since", reading):
            cycle.start_run(model)
            verbs.apply(model, Verb("wait", "clock", (2,)))
            cycle.finish_run(model)
            self.assertEqual(seen, [M.moment(began)], "the board's run reads a's push once, at its start")
            clerk.ModelClient(looked).released("claude/1")
        self.assertEqual(seen[1:], [M.moment(looked.now)], "the clerk reads it at its own look's start")

    def test_a_first_half_that_raises_leaves_the_second_undone(self):
        model = M.seeded()
        with mock.patch.object(board, "render", side_effect=ValueError("broken")):
            half = cycle.start_run(model)
        record = cycle.finish_run(model, half)
        self.assertEqual((record.exit_code, model.board_text), (1, ""))
        self.assertIn("ValueError: broken", record.raised)
        self.assertEqual([c for c in record.calls if c.method != "GET"], [])


class TheBoardBranch(unittest.TestCase):
    def test_takes_the_page_when_the_run_exits_0(self):
        model = M.seeded()
        record = cycle.board_run(model)
        self.assertEqual((record.exit_code, model.board_text), (0, record.page))

    def test_keeps_the_last_page_when_the_run_fails(self):
        model = M.seeded()
        cycle.board_run(model)
        before = model.board_text
        with mock.patch.object(board, "render", side_effect=ValueError("broken")):
            record = cycle.board_run(model)
        self.assertEqual(record.exit_code, 1)
        self.assertIn("ValueError: broken", record.raised)
        self.assertEqual(model.board_text, before)


class NeverStopped(unittest.TestCase):
    def test_by_a_refused_idle_comment(self):
        """F11: the idle comment's POST refused, and the run still exits 0."""
        model = M.seeded()
        for verb in (Verb("claim", "a", (1,)), Verb("open-draft", "a", (1,)), Verb("mark-ready", "a", (100,)),
                     Verb("wait", "clock", (25,))):
            verbs.apply(model, verb)
        record = verbs.apply(model, Verb("board", "board", (cycle.SCHEDULE, "", ("comment-500",)))).run
        self.assertEqual(record.exit_code, 0)
        self.assertTrue(any("idle comment failed" in line for line in record.lines))
        self.assertEqual(model.board_text, record.page)

    def test_by_a_branch_name_outside_ascii(self):
        """F6: claude/\\u00b2 in the render and claude/caf\\u00e9 in a live run."""
        for name in ("claude/\u00b2", "claude/caf\u00e9"):
            with self.subTest(name=name):
                model = M.seeded()
                verbs.apply(model, Verb("push-by-hand", M.OWNER, (1, name)))
                record = cycle.board_run(model)
                self.assertEqual((record.exit_code, record.raised), (0, None))
                self.assertIn(board.escaped(name), record.page)


if __name__ == "__main__":
    unittest.main()
