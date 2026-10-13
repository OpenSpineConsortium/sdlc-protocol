"""The harness's board run (validation/cycle.py): the same calls, in the same
order, as cli.board_live_run makes against the same fake, so a change of
order in cli.py fails here until cli.py is split in two (DESIGN.md, 3.3);
the board branch written only when the run exits 0; and a board run that
neither a refused comment nor a hand-made branch name stops (G15, F6 and
F11, fixed in 0.4.1)."""

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
from validation import cycle, model as M, verbs  # noqa: E402
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

    def test_the_busy_state_has_every_kind_of_write_in_it(self):
        record = cycle.board_run(busy(), cycle.PUSH, M.MAIN)
        writes = {(c.method, c.path.split("/")[1]) for c in record.calls if c.method != "GET"}
        self.assertEqual(writes, {("DELETE", "issues"), ("POST", "issues"), ("POST", "branches")})
        self.assertTrue(any("main moved" in str(c.body) for c in record.calls))
        self.assertTrue(any("overlap" in str(c.body) for c in record.calls))


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
