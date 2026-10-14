"""The command line keeps the template's tools' argument shapes and exit
codes, so a workflow line changes only in how it names the tool. Each
subcommand is tested through its argv: board's dry run against the
fixture snapshot and its golden page, board's live run and team with the
GitHub adapter replaced by fakes, check against an event file and a
checkout, and guard against the hook's JSON on stdin."""

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sdlc_protocol import board, cli, guard, vendoring  # noqa: E402
from sdlc_protocol.adapters import github  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures"
FIXTURE = FIXTURES / "board.json"
WIDE = FIXTURES / "board-wide.json"
REPO = "octo/widgets"


def run(argv, **kwargs):
    """A subcommand's exit code, stdout and stderr."""
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = cli.main(argv) if not kwargs else cli.COMMANDS[argv[0]](argv[1:], **kwargs)
    return code, out.getvalue(), err.getvalue()


def golden(name):
    return (FIXTURES / name).read_text(encoding="ascii")


class Dispatch(unittest.TestCase):
    def test_no_command_or_an_unknown_one_is_a_usage_error(self):
        for argv in ([], ["boards"], ["--dry-run", str(FIXTURE)]):
            code, out, err = run(argv)
            self.assertEqual((code, out), (2, ""), argv)
            self.assertIn("board,check,team,workers,guard", err)

    def test_help_names_the_commands(self):
        code, out, _ = run(["-h"])
        self.assertEqual(code, 0)
        self.assertIn("board,check,team,workers,guard", out)

    def test_a_subcommands_own_help_exits_0(self):
        for command in ("board", "check", "team"):
            with self.assertRaises(SystemExit) as caught, redirect_stdout(io.StringIO()):
                cli.main([command, "-h"])
            self.assertEqual(caught.exception.code, 0, command)


class BoardArguments(unittest.TestCase):
    def test_the_workflows_line(self):
        # board.yml: --default-branch "$DEFAULT_BRANCH" --sha "$(git rev-parse HEAD)" --out "$RUNNER_TEMP/STATE.md"
        args = cli.board_arguments(["--default-branch", "trunk", "--sha", "abc123", "--out", "/runner/STATE.md"])
        self.assertEqual((args.default_branch, args.sha, args.out, args.dry_run, args.render_only),
                         ("trunk", "abc123", "/runner/STATE.md", None, False))

    def test_the_defaults(self):
        args = cli.board_arguments([])
        self.assertEqual((args.default_branch, args.sha, args.out, args.dry_run, args.render_only),
                         ("main", None, None, None, False))
        self.assertTrue(cli.board_arguments(["--render-only", "--dry-run", "f.json"]).render_only)

    def test_an_unknown_flag_is_refused_as_before(self):
        with self.assertRaises(SystemExit) as caught, redirect_stderr(io.StringIO()):
            cli.board_arguments(["--branch", "main"])
        self.assertEqual(caught.exception.code, 2)


class BoardDryRun(unittest.TestCase):
    """The template's dry-run tests, through the command line."""

    def test_writes_the_board_to_out_and_exits_0(self):
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder) / "STATE.md"
            self.assertEqual(run(["board", "--dry-run", str(FIXTURE), "--out", str(out)])[0], 0)
            self.assertEqual(out.read_text(encoding="utf-8"), golden("board-STATE.md"))
            self.assertEqual(run(["board", "--dry-run", str(FIXTURE), "--out", str(out)])[0], 0)
            self.assertEqual(out.read_text(encoding="utf-8"), golden("board-STATE.md"))

    def test_prints_to_stdout_without_out(self):
        code, out, _ = run(["board", "--dry-run", str(FIXTURE)])
        self.assertEqual((code, out), (0, golden("board-STATE.md")))

    def test_the_wide_fixture_prints_the_templates_page(self):
        self.assertEqual(run(["board", "--dry-run", str(WIDE)])[1], golden("board-wide-STATE.md"))

    def test_the_fixture_names_its_own_default_branch(self):
        # As the template's dry run did: the flag's default does not
        # override the trunk the wide fixture names.
        self.assertIn("on trunk.", run(["board", "--dry-run", str(WIDE)])[1])

    def test_the_default_out_is_state_md_in_the_current_directory(self):
        self.assertEqual(board.STATE_PATH, Path("STATE.md"))

    def test_live_run_without_a_token_exits_1(self):
        code, _, err = run(["board", "--render-only", "--out", "/nonexistent/STATE.md"], environ={})
        self.assertEqual(code, 1)
        self.assertIn("GITHUB_TOKEN", err)


class FakeBoardApi:
    """Stands in for the REST client; the adapter's own calls are tested in
    test_github, so here only the wiring is."""

    def __init__(self, token, repo):
        self.token, self.repo = token, repo
        self.posted = []


class BoardLiveRun(unittest.TestCase):
    ENVIRON = {"GITHUB_TOKEN": "t", "GITHUB_REPOSITORY": REPO, "GITHUB_SHA": "fromenv",
               "GITHUB_EVENT_NAME": "push", "GITHUB_REF_NAME": "main"}

    def live(self, argv, environ=None):
        calls = {}

        def snapshot(api, sha, default_branch, now):
            calls["snapshot"] = (api.token, api.repo, sha, default_branch)
            calls["now"] = now
            return board.Snapshot.from_dict(json.loads(FIXTURE.read_text(encoding="utf-8")))

        def coordinate(api, snap, now):
            calls["coordinated"] = (snap, now)
            return ["#7: claim released, idle since 2026-09-01T00:00:00Z"]

        def post_comments(api, due):
            calls["due"] = list(due)
            return [f"PR #{number}: posted" for number, _, _ in due]

        def release_claims(api, snap):
            calls["released"] = snap
            return ["#12: claim released"]

        with mock.patch.object(github, "GitHub", FakeBoardApi), \
             mock.patch.object(github, "snapshot", snapshot), \
             mock.patch.object(github, "coordinate", coordinate), \
             mock.patch.object(github, "post_comments", post_comments), \
             mock.patch.object(github, "release_claims", release_claims):
            result = run(["board"] + argv, environ=environ or self.ENVIRON)
        return result, calls

    def test_writes_the_board_says_whether_it_changed_and_posts_what_is_due(self):
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder) / "STATE.md"
            (code, printed, _), calls = self.live(["--default-branch", "main", "--sha", "0123abc", "--out", str(out)])
            self.assertEqual(code, 0)
            self.assertEqual(out.read_text(encoding="ascii"), golden("board-STATE.md"))
            self.assertEqual(calls["snapshot"], ("t", REPO, "0123abc", "main"))
            # The fixture's #40 is behind and was last touched in September: a
            # push to main owes it the main-moved comment, and both pulls the idle one.
            self.assertEqual([(n, m) for n, m, _ in calls["due"]],
                             [(40, "<!-- sdlc-board: main-moved 0123abc -->"),
                              (40, board.IDLE_MARKER), (41, board.IDLE_MARKER)])
            # The coordinator acts before the board is written, on the same
            # snapshot and clock; the released claims' branches are renamed
            # after it is written and before the comments, each said on a
            # line of its own.
            self.assertIs(calls["coordinated"][0], calls["released"])
            self.assertIs(calls["coordinated"][1], calls["now"])
            self.assertIsInstance(calls["released"], board.Snapshot)
            self.assertEqual(printed.splitlines(),
                             ["#7: claim released, idle since 2026-09-01T00:00:00Z", f"{out.as_posix()}: rewritten",
                              "#12: claim released", "PR #40: posted", "PR #40: posted", "PR #41: posted"])
            (code, printed, _), _ = self.live(["--render-only", "--out", str(out)])
            self.assertEqual(printed.splitlines(), [f"{out.as_posix()}: unchanged"])

    def test_the_two_halves_apart_say_their_lines_and_the_second_acts_on_the_first_s_snapshot(self):
        """The validation harness runs the halves on its own clock with
        other work between them (validation/cycle.py)."""
        said = []
        with mock.patch.object(github, "snapshot", lambda api, sha, default_branch, now: board.Snapshot(sha=sha)), \
             mock.patch.object(github, "unread_claim_lines", lambda snap: ["unread"]), \
             mock.patch.object(github, "coordinate", lambda api, snap, now: ["coordinated"]), \
             mock.patch.object(github, "release_claims", lambda api, snap: [f"released at {snap.sha}"]), \
             mock.patch.object(github, "post_comments", lambda api, due: ["posted"]):
            snap, page = cli.plan_board(FakeBoardApi("t", REPO), "abc", "main", None, say=said.append)
            self.assertEqual((snap.sha, said), ("abc", ["unread", "coordinated"]))
            self.assertEqual(page, board.render(snap))
            cli.act_on_board(FakeBoardApi("t", REPO), snap, "schedule", "", None, say=said.append)
            self.assertEqual(said, ["unread", "coordinated", "released at abc", "posted"])
            said.clear()
            cli.plan_board(FakeBoardApi("t", REPO), "abc", "main", None, render_only=True, say=said.append)
            self.assertEqual(said, ["unread"])

    def test_render_only_posts_nothing_and_the_sha_defaults_to_github_sha(self):
        with tempfile.TemporaryDirectory() as folder:
            (code, _, _), calls = self.live(["--render-only", "--out", str(Path(folder) / "STATE.md")])
        self.assertEqual(code, 0)
        self.assertNotIn("coordinated", calls)
        self.assertNotIn("due", calls)
        self.assertNotIn("released", calls)
        self.assertEqual(calls["snapshot"][2], "fromenv")

    def test_github_sha_stands_in_only_where_it_is_the_default_branch_s_head(self):
        """Since F12's fix the main-moved marker carries the snapshot's sha
        on every event, so GITHUB_SHA, a pull request's merge commit or
        another branch's head, is not taken there."""
        for event, ref, expected in (("push", "main", "fromenv"), ("schedule", "", "fromenv"),
                                     ("issues", "main", "fromenv"), ("delete", "main", "fromenv"),
                                     ("workflow_dispatch", "main", "fromenv"),
                                     ("pull_request", "main", ""), ("pull_request_target", "main", ""),
                                     ("push", "claude/7", "")):
            environ = {**self.ENVIRON, "GITHUB_EVENT_NAME": event, "GITHUB_REF_NAME": ref}
            with self.subTest(event=event, ref=ref), tempfile.TemporaryDirectory() as folder:
                (code, _, _), calls = self.live(["--render-only", "--out", str(Path(folder) / "STATE.md")], environ)
            self.assertEqual((code, calls["snapshot"][2]), (0, expected))
        environ = {**self.ENVIRON, "GITHUB_EVENT_NAME": "pull_request"}
        with tempfile.TemporaryDirectory() as folder:
            (code, _, _), calls = self.live(["--render-only", "--sha", "given", "--out", str(Path(folder) / "STATE.md")],
                                            environ)
        self.assertEqual((code, calls["snapshot"][2]), (0, "given"), "--sha is taken as given on any event")

    def test_a_refused_call_is_one_line_and_exit_1(self):
        def refused(api, sha, default_branch, now):
            raise github.GitHubError("GET /repos/octo/widgets/issues: HTTP 403", 403)

        with mock.patch.object(github, "GitHub", FakeBoardApi), mock.patch.object(github, "snapshot", refused):
            code, _, err = run(["board", "--out", "/nonexistent/STATE.md"], environ=self.ENVIRON)
        self.assertEqual((code, err), (1, "board: GET /repos/octo/widgets/issues: HTTP 403\n"))


class FakeClient:
    """The team.Client methods over dictionaries, enough for the clerk's
    answers the command line prints."""

    def __init__(self, token="t", repo=REPO):
        self.token, self.repo = token, repo
        self.issues, self.branches, self.labels, self.sub_issues = {}, set(), {"todo", "epic"}, []
        self.next_number = 100

    def add_issue(self, number, labels=(), body="", state="open", title="t"):
        self.issues[number] = {"number": number, "id": 9000 + number, "title": title, "body": body,
                               "state": state, "labels": [{"name": n} for n in labels]}
        return self.issues[number]

    def issue(self, number):
        return self.issues[number]

    def open_issues(self):
        return [i for i in self.issues.values() if i["state"] == "open"]

    def branch_names(self):
        return sorted(self.branches)

    def open_pull(self, branch):
        return None

    def check_runs(self, sha):
        return []

    def create_label(self, name, colour):
        self.labels.add(name)

    def create_issue(self, title, body, labels):
        self.next_number += 1
        return self.add_issue(self.next_number, labels, body, title=title)

    def add_sub_issue(self, parent, child_id):
        self.sub_issues.append((parent, child_id))

    def set_body(self, number, body):
        self.issues[number]["body"] = body

    def remove_label(self, number, name):
        issue = self.issues[number]
        issue["labels"] = [label for label in issue["labels"] if label["name"] != name]


class TeamArguments(unittest.TestCase):
    ENVIRON = {"GITHUB_REPOSITORY": REPO, "CLAUDE_TEAM": '["w1", "w2"]'}

    def test_the_lead_workflows_route_line_with_an_empty_label(self):
        # claude-lead.yml, in copies of the template made before workers:
        # route --event "$EVENT" --issue "$ISSUE" --label "$LABEL"
        args = cli.team_arguments(["route", "--event", "closed", "--issue", "12", "--label", ""], self.ENVIRON)
        self.assertEqual((args.repo, args.command, args.event, args.issue, args.label),
                         (REPO, "route", "closed", 12, ""))

    def test_the_lead_workflows_seed_line_takes_the_team_from_the_variable(self):
        # claude-lead.yml, in copies made before workers:
        # seed --epic "$EPIC" --plan "$RUNNER_TEMP/plan.json"
        args = cli.team_arguments(["seed", "--epic", "3", "--plan", "/runner/plan.json"], self.ENVIRON)
        self.assertEqual((args.command, args.epic, args.plan, args.team), ("seed", 3, "/runner/plan.json", '["w1", "w2"]'))

    def test_the_worker_workflows_next_line(self):
        # claude-worker.yml, in copies made before workers:
        # next --agent "$AGENT" --label "$LABEL"
        args = cli.team_arguments(["next", "--agent", "w2", "--label", "agent:w2"], self.ENVIRON)
        self.assertEqual((args.command, args.agent, args.label, args.team), ("next", "w2", "agent:w2", '["w1", "w2"]'))

    def test_repo_comes_before_the_step_and_wins_over_the_variable(self):
        args = cli.team_arguments(["--repo", "a/b", "next", "--agent", "w1"], self.ENVIRON)
        self.assertEqual(args.repo, "a/b")

    def test_a_step_is_required(self):
        with self.assertRaises(SystemExit) as caught, redirect_stderr(io.StringIO()):
            cli.team_arguments([], self.ENVIRON)
        self.assertEqual(caught.exception.code, 2)


class TeamRun(unittest.TestCase):
    def test_main_needs_a_token(self):
        code, _, err = run(["team", "--repo", REPO, "next", "--agent", "w1"], environ={})
        self.assertEqual(code, 1)
        self.assertIn("GITHUB_TOKEN", err)

    def test_main_makes_the_members_label_before_a_take(self):
        api = FakeClient()
        api.add_issue(4, ["todo"])
        args = cli.team_arguments(["next", "--agent", "main", "--team", "", "--label", "account:main"], {})
        self.assertEqual(cli.team_answer(api, args), "take 4")
        self.assertIn("account:main", api.labels)

    def through_the_adapter(self, argv, api, environ):
        with mock.patch.object(github, "GitHub", lambda token, repo: api):
            return run(["team"] + argv, environ=environ)

    def test_next_prints_the_one_line_the_workflow_reads(self):
        api = FakeClient()
        api.add_issue(4, ["todo"])
        api.add_issue(5, ["todo"])
        environ = {"GH_TOKEN": "t", "GITHUB_REPOSITORY": REPO, "CLAUDE_TEAM": '["w1", "w2"]'}
        self.assertEqual(self.through_the_adapter(["next", "--agent", "w2"], api, environ), (0, "take 5\n", ""))

    def test_route_and_seed(self):
        api = FakeClient()
        api.add_issue(1, ["epic", "todo"], body="Build the widget.")
        environ = {"GITHUB_TOKEN": "t", "GITHUB_REPOSITORY": REPO, "CLAUDE_TEAM": '["w1", "w2"]'}
        self.assertEqual(self.through_the_adapter(["route", "--event", "labeled", "--issue", "1", "--label", "epic"],
                                                  api, environ), (0, "plan 1\n", ""))
        with tempfile.TemporaryDirectory() as folder:
            plan = Path(folder) / "plan.json"
            plan.write_text(json.dumps({"plan": "w1 parses.", "tasks": [{"title": "Parser", "owner": "w1"}]}),
                            encoding="utf-8")
            code, out, _ = self.through_the_adapter(["seed", "--epic", "1", "--plan", str(plan)], api, environ)
        self.assertEqual((code, out), (0, "seeded 101\n"))
        self.assertEqual([label["name"] for label in api.issues[1]["labels"]], ["epic"])

    def test_a_failure_is_one_line_and_exit_1(self):
        environ = {"GITHUB_TOKEN": "t", "GITHUB_REPOSITORY": REPO, "CLAUDE_TEAM": "w1,w2"}
        code, out, err = self.through_the_adapter(["next", "--agent", "w1"], FakeClient(), environ)
        self.assertEqual((code, out), (1, ""))
        self.assertTrue(err.startswith("team: CLAUDE_TEAM"))


def registry(folder, *files):
    """A registry folder holding these workers' files, each a dict."""
    path = Path(folder) / "workers"
    path.mkdir()
    for data in files:
        (path / f"{data['name']}.json").write_text(json.dumps(data), encoding="utf-8")
    return str(path)


class TeamWithTheRegistry(unittest.TestCase):
    ENVIRON = {"GITHUB_TOKEN": "t", "GITHUB_REPOSITORY": REPO}

    def through_the_adapter(self, argv, api):
        with mock.patch.object(github, "GitHub", lambda token, repo: api):
            return run(["team"] + argv, environ=self.ENVIRON)

    def test_next_deals_among_the_workers_in_actions_each_by_its_area(self):
        api = FakeClient()
        api.add_issue(4, ["todo"], body="### Files likely touched\n\nweb/a.js\n")
        api.add_issue(5, ["todo"], body="### Files likely touched\n\napi/b.py\n")
        with tempfile.TemporaryDirectory() as folder:
            where = registry(folder, {"name": "api", "runs": "actions", "area": ["api/"]},
                             {"name": "web", "runs": "actions", "area": ["web/"]},
                             {"name": "alice", "runs": "session"})
            self.assertEqual(self.through_the_adapter(["next", "--agent", "web", "--registry", where,
                                                       "--label", "worker:web"], api), (0, "take 4\n", ""))
            self.assertEqual(self.through_the_adapter(["next", "--agent", "api", "--registry", where], api),
                             (0, "take 5\n", ""))
        self.assertIn("worker:web", api.labels)

    def test_seed_gives_tasks_to_every_registered_worker(self):
        api = FakeClient()
        api.add_issue(1, ["epic"])
        with tempfile.TemporaryDirectory() as folder:
            where = registry(folder, {"name": "alice", "runs": "session"}, {"name": "nightly", "runs": "routine"})
            plan = Path(folder) / "plan.json"
            plan.write_text(json.dumps({"plan": "p", "tasks": [{"title": "A", "owner": "nightly"},
                                                               {"title": "B", "owner": "ghost"}]}), encoding="utf-8")
            code, out, _ = self.through_the_adapter(["seed", "--epic", "1", "--plan", str(plan), "--registry", where],
                                                    api)
        self.assertEqual((code, out), (0, "seeded 101 102\n"))
        self.assertEqual([labels["name"] for labels in api.issues[101]["labels"]], ["todo", "worker:nightly"])
        self.assertEqual([labels["name"] for labels in api.issues[102]["labels"]], ["todo", "worker:alice"])

    def test_schema_needs_no_token_and_names_the_registered_workers(self):
        with tempfile.TemporaryDirectory() as folder:
            where = registry(folder, {"name": "nightly", "runs": "actions"}, {"name": "alice", "runs": "session"})
            code, out, err = run(["team", "schema", "--registry", where], environ={})
        self.assertEqual((code, err), (0, ""))
        owner = json.loads(out)["properties"]["tasks"]["items"]["properties"]["owner"]
        self.assertEqual(owner["enum"], ["alice", "nightly"])
        code, out, _ = run(["team", "schema"], environ={})
        self.assertEqual(json.loads(out)["properties"]["tasks"]["items"]["properties"]["owner"], {"type": "string"})

    def test_a_broken_registry_is_one_line_and_exit_1(self):
        with tempfile.TemporaryDirectory() as folder:
            where = registry(folder, {"name": "Bad Name", "runs": "actions"})
            code, out, err = self.through_the_adapter(["next", "--agent", "x", "--registry", where], FakeClient())
        self.assertEqual((code, out), (1, ""))
        self.assertTrue(err.startswith("team: Bad Name.json: name must be"), err)


FORM_BODY = """### Name

nightly

### Where it runs

routine: a routine on someone's Claude account

### What it is for

Docs fixes, every night.

### Area

docs/
- `README.md`

### How its pull requests land

auto-merge: GitHub merges it once the check is green

### Model

_No response_

### Turns per run

_No response_

### Rounds in a row

3

### Claude account

the owner's Max account
"""


class Workers(unittest.TestCase):
    def run_workers(self, argv, stdin=""):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.run_workers(argv, stdin=io.StringIO(stdin))
        return code, out.getvalue(), err.getvalue()

    def test_check_lists_the_workers_and_list_gives_the_matrix(self):
        with tempfile.TemporaryDirectory() as folder:
            where = registry(folder, {"name": "nightly", "runs": "actions"}, {"name": "alice", "runs": "session"})
            self.assertEqual(self.run_workers(["--dir", where, "check"]),
                             (0, f"2 workers in {where}: alice (session), nightly (actions)\n", ""))
            self.assertEqual(self.run_workers(["--dir", where, "list"]), (0, '["alice", "nightly"]\n', ""))
            self.assertEqual(self.run_workers(["--dir", where, "list", "--runs", "actions"]), (0, '["nightly"]\n', ""))
            self.assertEqual(self.run_workers(["--dir", where, "get", "nightly", "max_turns"]), (0, "80\n", ""))
            self.assertEqual(self.run_workers(["--dir", where, "get", "nightly", "label"]), (0, "worker:nightly\n", ""))
            self.assertEqual(self.run_workers(["--dir", where, "get", "ghost", "runs"]),
                             (1, "", f"workers: no worker named ghost in {where}\n"))

    def test_describe_gives_a_line_per_worker(self):
        with tempfile.TemporaryDirectory() as folder:
            where = registry(folder, {"name": "nightly", "runs": "actions", "area": ["docs/"], "about": "Docs."})
            self.assertEqual(self.run_workers(["--dir", where, "describe"]),
                             (0, "nightly (actions): area docs/; Docs.\n", ""))
            self.assertEqual(self.run_workers(["--dir", str(Path(folder) / "none"), "describe"]),
                             (0, "(no worker is registered)\n", ""))

    def test_no_registry_is_no_workers(self):
        with tempfile.TemporaryDirectory() as folder:
            where = str(Path(folder) / "none")
            self.assertEqual(self.run_workers(["--dir", where, "check"]), (0, f"0 workers in {where}: none\n", ""))
            self.assertEqual(self.run_workers(["--dir", where, "list", "--runs", "actions"]), (0, "[]\n", ""))

    def test_check_names_every_problem_and_exits_1(self):
        with tempfile.TemporaryDirectory() as folder:
            where = registry(folder, {"name": "nightly", "runs": "cron", "colour": "red"})
            code, out, err = self.run_workers(["--dir", where, "check"])
        self.assertEqual((code, out), (1, ""))
        self.assertEqual(err.splitlines(), [
            "workers: nightly.json: colour is not a setting a worker has; the settings are name, runs, about, area, "
            "lands, model, max_turns, rounds, account",
            "workers: nightly.json: runs must be one of actions, routine, session"])

    def test_from_form_writes_the_worker_and_says_its_name_and_how_it_runs(self):
        with tempfile.TemporaryDirectory() as folder:
            where = str(Path(folder) / "workers")
            self.assertEqual(self.run_workers(["--dir", where, "from-form"], FORM_BODY), (0, "nightly routine\n", ""))
            written = json.loads((Path(where) / "nightly.json").read_text(encoding="ascii"))
            self.assertEqual(written, {"name": "nightly", "runs": "routine", "about": "Docs fixes, every night.",
                                       "area": ["docs/", "README.md"], "lands": "auto-merge", "model": "",
                                       "max_turns": 80, "rounds": 3, "account": "the owner's Max account"})
            code, out, err = self.run_workers(["--dir", where, "from-form"], FORM_BODY)
            self.assertEqual((code, out), (1, ""))
            self.assertIn("a worker named nightly exists already", err)

    def test_from_form_refuses_a_number_too_long_for_int_in_one_line(self):
        with tempfile.TemporaryDirectory() as folder:
            body = FORM_BODY.replace("\n3\n", "\n" + "9" * 4301 + "\n")
            self.assertEqual(self.run_workers(["--dir", folder, "from-form"], body),
                             (1, "", "workers: rounds must be a whole number from 1 to 50\n"))
            self.assertEqual(list(Path(folder).iterdir()), [])

    def test_from_form_refuses_answers_that_do_not_hold_together(self):
        with tempfile.TemporaryDirectory() as folder:
            body = FORM_BODY.replace("\nnightly\n", "\nNight Shift\n").replace("\n3\n", "\nthree\n")
            code, out, err = self.run_workers(["--dir", folder, "from-form"], body)
        self.assertEqual((code, out), (1, ""))
        self.assertIn("workers: name must be", err)
        self.assertIn("workers: rounds must be a whole number from 1 to 50", err)


class Check(unittest.TestCase):
    """The template's protocol check cases, from an event file and a checkout."""

    def check(self, pull=None, claim_file=False, event=None, raw=None):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "checkout"
            if claim_file:
                (root / "docs" / "agents" / "claims").mkdir(parents=True)
                (root / "docs" / "agents" / "claims" / "7.md").write_text("# Claim: #7\n", encoding="utf-8")
            where = Path(folder) / "event.json"
            where.write_text(raw if raw is not None else json.dumps(event if event is not None else {"pull_request": pull}),
                             encoding="utf-8")
            return run(["check"], environ={"GITHUB_EVENT_PATH": str(where)}, root=root)

    def pull(self, branch="claude/7", body="Closes #7", draft=True, head_repo="octo/widgets"):
        return {"head": {"ref": branch, "repo": {"full_name": head_repo}},
                "base": {"repo": {"full_name": "octo/widgets"}}, "body": body, "draft": draft}

    def test_a_draft_with_its_claim_file_and_closes_line_is_ok(self):
        self.assertEqual(self.check(self.pull(), claim_file=True), (0, "protocol ok\n", ""))

    def test_a_draft_without_its_claim_file_is_named_and_fails(self):
        code, out, _ = self.check(self.pull())
        self.assertEqual(code, 1)
        self.assertIn("docs/agents/claims/7.md is missing", out)

    def test_a_ready_pull_with_its_claim_file_is_named_and_fails(self):
        code, out, _ = self.check(self.pull(draft=False), claim_file=True)
        self.assertEqual(code, 1)
        self.assertIn("docs/agents/claims/7.md is still here", out)

    def test_every_problem_is_its_own_line(self):
        code, out, _ = self.check(self.pull(body="", head_repo="someone/widgets"), claim_file=True)
        self.assertEqual(code, 1)
        self.assertEqual(len(out.splitlines()), 2)

    def test_another_branch_and_an_event_without_a_pull_request_pass(self):
        self.assertEqual(self.check(self.pull(branch="feature/x", body=""))[:2], (0, "protocol ok\n"))
        self.assertEqual(self.check(event={})[:2], (0, "protocol ok\n"))

    def test_no_event_and_an_unreadable_one_pass(self):
        self.assertEqual(run(["check"], environ={})[:2], (0, "no pull request event to check\n"))
        self.assertEqual(run(["check"], environ={"GITHUB_EVENT_PATH": "/nonexistent/event.json"})[:2],
                         (0, "no pull request event to check\n"))
        for raw in ("not json", "[1, 2]"):
            self.assertEqual(self.check(raw=raw)[:2], (0, "the event is not readable; nothing checked\n"), raw)


class Guard(unittest.TestCase):
    """The template's hook tests (MainTest on claude/11), through the command line."""

    def run_guard(self, command, argv=()):
        payload = json.dumps({"tool_input": {"command": command}, "cwd": str(ROOT)})
        with mock.patch.object(sys, "stdin", io.StringIO(payload)), \
             mock.patch.object(guard, "branches", return_value=("claude/7", "main")):
            code, _, err = run(["guard", *argv])
        return code, err

    def test_forbidden_push_is_refused_with_a_reason(self):
        code, message = self.run_guard("git push origin main")
        self.assertEqual(code, 2)
        self.assertIn("pull requests", message)

    def test_allowed_push_passes(self):
        self.assertEqual(self.run_guard("git push -u origin claude/7"), (0, ""))

    def test_push_to_main_on_a_later_line_is_refused(self):
        code, message = self.run_guard("git add -A\ngit commit -m x\ngit push origin main")
        self.assertEqual(code, 2)
        self.assertIn("pull requests", message)

    def test_claim_push_then_a_line_naming_main_passes(self):
        for command in ("git push -u origin claude/7\ngit fetch origin main",
                        "git push -u origin claude/7\n# then open the pull request against main",
                        "git push -u origin claude/7 # not main"):
            self.assertEqual(self.run_guard(command), (0, ""), command)

    def test_a_here_document_body_is_read_as_commands(self):
        refused, message = self.run_guard("git commit -F - <<'EOF'\nClaim #7\n\ngit push origin main\nEOF\n"
                                          "git push -u origin claude/7")
        self.assertEqual(refused, 2)
        self.assertIn("pull requests", message)
        self.assertEqual(self.run_guard("git commit -F - <<'EOF'\nClaim #7\n\nThe board first.\nEOF\n"
                                        "git push -u origin claude/7"), (0, ""))

    def test_command_without_push_passes_without_git(self):
        payload = json.dumps({"tool_input": {"command": "git status"}})
        with mock.patch.object(sys, "stdin", io.StringIO(payload)), \
             mock.patch.object(guard, "branches", side_effect=AssertionError("git was asked")):
            self.assertEqual(run(["guard"])[0], 0)

    def test_broken_payload_passes(self):
        with mock.patch.object(sys, "stdin", io.StringIO("not json")):
            self.assertEqual(run(["guard"]), (0, "", ""))

    def test_stray_arguments_never_refuse_a_call(self):
        self.assertEqual(self.run_guard("git status", argv=("--unknown",)), (0, ""))
        self.assertEqual(self.run_guard("git push origin main", argv=("--unknown",))[0], 2)

    def test_help_passes_without_reading_stdin(self):
        with mock.patch.object(sys, "stdin", io.StringIO("")) as empty:
            code, out, _ = run(["guard", "-h"])
            self.assertEqual(empty.tell(), 0)
        self.assertEqual(code, 0)
        self.assertIn("PreToolUse", out)


class VerifyVendored(unittest.TestCase):
    """The record's check under the package's own command line; the script
    form a consumer's check runs is tests/test_vendoring.py's."""

    def vendor_dir(self, folder, edit=False):
        vendor = Path(folder)
        (vendor / "sdlc_protocol").mkdir()
        (vendor / "sdlc_protocol" / "guard.py").write_text("rules\n", encoding="ascii")
        digests = vendoring.digests_of(vendor / "sdlc_protocol")
        (vendor / "VENDORED").write_text(vendoring.record_text("0.2.0", "unknown", digests), encoding="ascii")
        if edit:
            (vendor / "sdlc_protocol" / "guard.py").write_text("rules, edited\n", encoding="ascii")
        return vendor

    def test_a_matching_copy_exits_0(self):
        with tempfile.TemporaryDirectory() as folder:
            code, out, _ = run(["verify-vendored", str(self.vendor_dir(folder))])
        self.assertEqual((code, out), (0, "vendored sdlc-protocol 0.2.0 at unknown: 1 files match VENDORED\n"))

    def test_an_edited_copy_exits_1_naming_the_file(self):
        with tempfile.TemporaryDirectory() as folder:
            code, out, _ = run(["verify-vendored", str(self.vendor_dir(folder, edit=True))])
        self.assertEqual(code, 1)
        self.assertIn("guard.py was edited by hand", out)

    def test_no_vendor_dir_is_a_usage_error(self):
        with self.assertRaises(SystemExit) as caught, redirect_stderr(io.StringIO()):
            cli.main(["verify-vendored"])
        self.assertEqual(caught.exception.code, 2)

    def test_the_usage_names_it(self):
        self.assertIn("verify-vendored", run(["-h"])[1])


class Module(unittest.TestCase):
    """python3 -m sdlc_protocol and the vendored path form run the same."""

    def python(self, *argv, stdin=None):
        environ = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
        return subprocess.run([sys.executable, *argv], cwd=ROOT, input=stdin, capture_output=True, text=True,
                              timeout=60, env=environ)

    def test_dash_m_renders_the_fixture(self):
        done = self.python("-m", "sdlc_protocol", "board", "--dry-run", str(FIXTURE))
        self.assertEqual((done.returncode, done.stdout), (0, golden("board-STATE.md")))

    def test_the_package_path_renders_the_fixture(self):
        done = self.python(str(ROOT / "sdlc_protocol"), "board", "--dry-run", str(FIXTURE))
        self.assertEqual((done.returncode, done.stdout), (0, golden("board-STATE.md")))

    def test_the_guard_refuses_through_the_module(self):
        payload = json.dumps({"tool_input": {"command": "git push --force origin claude/7"}})
        done = self.python("-m", "sdlc_protocol", "guard", stdin=payload)
        self.assertEqual(done.returncode, 2)
        self.assertIn("--force", done.stderr)


if __name__ == "__main__":
    unittest.main()
