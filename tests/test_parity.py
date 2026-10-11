"""The library against the template it was taken from: the same input
through the template's tools and through sdlc_protocol gives the same
output, byte for byte.

Four tools moved here, and each is held to its old self on the inputs the
template's own tests use. The board renders the same STATE.md for every
snapshot the template's tests/test_board.py renders, collects the same
snapshot from the same GitHub, and posts the same comments. The protocol
check names the same problems for the template's events, as a function and
as a command. The push guard, the version on the template's branch
claude/11, gives the same exit code and the same reason for each hook
payload of its tests. The team clerk gives the same answers and makes the
same writes for each scenario of the template's tests/test_team.py.

Both sides talk to one GitHub held in memory and served as a urllib
opener, so each runs through its own REST client as it would against
api.github.com, and the two runs are compared by their writes as well as
their answers. The template is read where SDLC_TEMPLATE points, by default
a clone named symphony-template beside this repository, and only read: its
tools are loaded from their source text, so no bytecode cache is written
into it, and its commands run with -B. Since the template vendored this
library, its tools are shims over the copy, so they are read from its git
as they stood before that commit. Every test here skips when the template,
or that history, is not there.

The differences the extraction declared on purpose are pinned at the end,
so a change on either side shows."""

import atexit
import base64
import copy
import functools
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
import urllib.error
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sdlc_protocol import board, claims, cli, protocol_check, team  # noqa: E402
from sdlc_protocol.adapters import github  # noqa: E402

# A clone of the template beside this one, by the name the rename gives
# it or, for a clone made before, the old one, unless SDLC_TEMPLATE names
# another.
TEMPLATE_NAMES = ("symphony-template", "sdlc-template")


def template_beside(parent=ROOT.parent):
    """The template's clone beside this one, by its new name or the old,
    or the new name when neither is there, so the skip message says
    where it was looked for."""
    for name in TEMPLATE_NAMES:
        if (parent / name).is_dir():
            return parent / name
    return parent / TEMPLATE_NAMES[0]


TEMPLATE = Path(os.environ.get("SDLC_TEMPLATE") or template_beside())
TOOL_NAMES = ("board", "check_protocol", "team")


def tools_before_the_shims():
    """The template's tools as they stood before it vendored this library,
    read from its git into a directory of their own: the parent of the
    commit that added tools/VENDORED. None when git or that history is not
    there."""
    def shown(*args):
        env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
        try:
            done = subprocess.run(["git", "-C", str(TEMPLATE), *args], capture_output=True, env=env, timeout=60)
        except OSError:
            return None
        return done.stdout if done.returncode == 0 else None
    added = (shown("log", "--diff-filter=A", "--format=%H", "--", "tools/VENDORED") or b"").split()
    if not added:
        return None
    folder = Path(tempfile.mkdtemp(prefix="template-tools-"))
    atexit.register(shutil.rmtree, folder, True)
    for name in TOOL_NAMES:
        source = shown("show", f"{added[-1].decode()}^:tools/{name}.py")
        if source is None:
            return None
        (folder / f"{name}.py").write_bytes(source)
    return folder


def template_tools():
    """The template's own board, check and team tools. Once the template
    vendored this library they became shims over its copy, and holding the
    library to a shim over itself would prove nothing, so the tools are
    then the ones from before the shims."""
    tools = TEMPLATE / "tools"
    return tools_before_the_shims() if (tools / "sdlc_protocol").is_dir() else tools


TOOLS = template_tools()
GUARD_SOURCE = "origin/claude/11:tools/guard_push.py"
FIXTURES = ROOT / "tests" / "fixtures"
FIXTURE = FIXTURES / "board.json"
WIDE = FIXTURES / "board-wide.json"
REPO = "octo/widgets"
PREFIX = f"/repos/{REPO}/"

TEMPLATE_PRESENT = TOOLS is not None and all((TOOLS / f"{name}.py").is_file() for name in TOOL_NAMES)
needs_template = unittest.skipUnless(TEMPLATE_PRESENT, f"the template's own tools are not at {TEMPLATE}")


# --- the template's tools, loaded without touching the template -----------------

def load_template(name, **preload):
    """One of the template's tools as a module named template_<name>, run
    from its source text so no bytecode cache lands in the template, with
    sys.path as it was. preload hands a tool that imports a sibling by its
    bare name (team.py's "from board import ...") the copy already loaded."""
    path = TOOLS / f"{name}.py"
    module = types.ModuleType(f"template_{name}")
    module.__file__ = str(path)
    saved_path, saved = list(sys.path), {key: sys.modules.get(key) for key in preload}
    sys.modules.update(preload)
    sys.modules[module.__name__] = module
    try:
        exec(compile(path.read_text(encoding="utf-8"), str(path), "exec"), module.__dict__)
    finally:
        sys.path[:] = saved_path
        for key, value in saved.items():
            if value is None:
                sys.modules.pop(key, None)
            else:
                sys.modules[key] = value
    return module


@functools.cache
def template_board():
    return load_template("board")


@functools.cache
def template_team():
    return load_template("team", board=template_board())


@functools.cache
def template_check():
    return load_template("check_protocol")


def environment(**extra):
    """The environment a command runs in: this one without GitHub's,
    git's or Claude's variables, with the library importable."""
    drop = ("GITHUB_", "GH_", "GIT_", "CLAUDE_")
    env = {key: value for key, value in os.environ.items() if not key.startswith(drop)}
    env["PYTHONPATH"] = str(ROOT)
    env.update(extra)
    return env


def run_command(argv, cwd, stdin="", **extra):
    """A command's exit code, stdout and stderr."""
    done = subprocess.run([sys.executable, "-B", *argv], cwd=cwd, input=stdin, capture_output=True,
                          text=True, env=environment(**extra), timeout=60)
    return done.returncode, done.stdout, done.stderr


def library_command(*argv):
    return ["-m", "sdlc_protocol", *argv]


# --- GitHub, in memory ------------------------------------------------------------

class Answer:
    """What urllib's opener hands back: a body to read and the headers,
    none of which names a next page."""

    def __init__(self, data):
        self.raw = b"" if data is None else json.dumps(data).encode("utf-8")
        self.headers = {}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self.raw


class Refused(Exception):
    def __init__(self, status):
        super().__init__(status)
        self.status = status


def found(thing):
    if thing is None:
        raise Refused(404)
    return thing


class FakeRepo:
    """GitHub's REST API for one repository, held in dictionaries and served
    as an opener. Both clients send it the requests they would send to
    api.github.com; it answers from its state as GitHub would (a JSON copy,
    404 for what is not there, 422 for a label that exists) and keeps every
    write, so two runs compare by what they changed as well as by what they
    answered. The setters are the template's FakeGitHub's."""

    def __init__(self):
        self.issues, self.pulls, self.files, self.contents = {}, {}, {}, {}
        self.checks, self.comments = {}, {}
        self.branches = set()
        self.labels, self.sub_issues = {"todo", "epic"}, []
        self.refuse_sub_issues = False
        self.next_number = 100
        self.writes = []

    def add_issue(self, number, labels=(), body="", state="open", title="t"):
        self.issues[number] = {"number": number, "id": 9000 + number, "title": title, "body": body,
                               "state": state, "labels": [{"name": n} for n in labels]}
        return self.issues[number]

    def add_pull(self, issue, draft=False, state="clean", conclusion="success", check="check"):
        sha, number = f"sha{issue}", 500 + issue
        self.pulls[number] = {"number": number, "state": "open", "draft": draft, "mergeable": True,
                              "mergeable_state": state, "head": {"ref": f"claude/{issue}", "sha": sha}}
        self.checks[sha] = [{"name": check, "conclusion": conclusion}]

    def state(self):
        return {"issues": self.issues, "labels": sorted(self.labels), "sub_issues": self.sub_issues,
                "comments": self.comments}

    def open(self, request, timeout=None):
        url = urllib.parse.urlsplit(request.full_url)
        method = request.get_method()
        body = json.loads(request.data) if request.data else None
        if method != "GET":
            self.writes.append((method, url.path, body))
        assert url.path.startswith(PREFIX), url.path
        parts = [urllib.parse.unquote(part) for part in url.path[len(PREFIX):].split("/")]
        query = dict(urllib.parse.parse_qsl(url.query))
        handler = getattr(self, f"{method.lower()}_{parts[0].replace('-', '_')}")
        try:
            return Answer(handler(parts[1:], query, body))
        except Refused as refused:
            raise urllib.error.HTTPError(request.full_url, refused.status, "refused", {},
                                         io.BytesIO(b'{"message": "refused"}')) from None

    def get_issues(self, rest, query, body):
        if not rest:
            return [i for i in self.issues.values() if i["state"] == query.get("state", "open")]
        if rest[1:] == ["comments"]:
            return [{"body": text} for text in self.comments.get(int(rest[0]), [])]
        return found(self.issues.get(int(rest[0])))

    def post_issues(self, rest, query, body):
        if not rest:
            self.next_number += 1
            return self.add_issue(self.next_number, body["labels"], body["body"], title=body["title"])
        number = int(rest[0])
        if rest[1:] == ["comments"]:
            self.comments.setdefault(number, []).append(body["body"])
            return {"body": body["body"]}
        assert rest[1:] == ["sub_issues"], rest
        if self.refuse_sub_issues:
            raise Refused(422)
        self.sub_issues.append([number, body["sub_issue_id"]])
        return {}

    def patch_issues(self, rest, query, body):
        issue = found(self.issues.get(int(rest[0])))
        issue.update(body)
        return issue

    def delete_issues(self, rest, query, body):
        issue = found(self.issues.get(int(rest[0])))
        assert rest[1] == "labels", rest
        if rest[2] not in [label["name"] for label in issue["labels"]]:
            raise Refused(404)
        issue["labels"] = [label for label in issue["labels"] if label["name"] != rest[2]]
        return issue["labels"]

    def post_labels(self, rest, query, body):
        if body["name"] in self.labels:
            raise Refused(422)
        self.labels.add(body["name"])
        return {"name": body["name"]}

    def get_branches(self, rest, query, body):
        return [{"name": name} for name in sorted(self.branches)]

    def get_pulls(self, rest, query, body):
        if not rest:
            pulls = [p for p in self.pulls.values() if p["state"] == query.get("state", "open")]
            if "head" in query:
                branch = query["head"].split(":", 1)[1]
                pulls = [p for p in pulls if p["head"]["ref"] == branch]
            return pulls
        pull = found(self.pulls.get(int(rest[0])))
        if rest[1:] == ["files"]:
            return [{"filename": name} for name in self.files.get(pull["number"], [])]
        return pull

    def get_commits(self, rest, query, body):
        runs = self.checks.get(rest[0], [])
        return {"total_count": len(runs), "check_runs": runs}

    def get_contents(self, rest, query, body):
        text = found(self.contents.get((query.get("ref"), "/".join(rest))))
        return {"content": base64.b64encode(text.encode("utf-8")).decode("ascii")}


class Sleeps:
    """A sleep that only counts, so both clients' retries can be compared."""

    def __init__(self):
        self.calls = []

    def __call__(self, seconds):
        self.calls.append(seconds)


def template_client(repo, sleep=None):
    return template_board().GitHub("token", REPO, opener=repo, sleep=sleep or Sleeps())


def library_client(repo, sleep=None):
    return github.GitHub("token", REPO, opener=repo, sleep=sleep or Sleeps())


# --- the board ----------------------------------------------------------------------

# The claim file the template's board tests write, in the claim template's shape.
CLAIM_TEXT = "\n".join([
    "# Claim: #16", "", "- Issue: https://github.com/octo/widgets/issues/16", "- Account: main",
    "- Branch: claude/16", "", "## Plan", "", "Two sentences on what changes.", "", "## Files to touch", "",
    "- docs/b.md", "- `docs/a.md`", "",
])


def fixture(path=FIXTURE):
    return json.loads(path.read_text(encoding="utf-8"))


def closed_claim(snap):
    snap["branches"].append("claude/3")
    snap["claim_issues"] = {"3": {"state": "closed", "labels": ["account:main", "claimed", "todo"]}}


def released_claim(snap):
    snap["claim_issues"] = {"13": {"state": "open", "labels": ["account:second", "claimed"]}}


def merged_out_of_order(snap):
    snap["merged"] = [
        {"number": 30, "title": "Older", "head": {"ref": "claude/30"}, "merged_at": "2026-09-20T10:00:00Z"},
        {"number": 29, "title": "Newer", "head": {"ref": "claude/29"}, "merged_at": "2026-09-21T10:00:00Z"},
    ]


def claims_without_pulls(snap):
    snap["branches"] += ["claude/16", "claude/17"]
    snap["claims"]["16"] = CLAIM_TEXT
    snap["claims"]["17"] = CLAIM_TEXT.replace("## Files to touch\n\n- docs/b.md\n- `docs/a.md`\n", "")


def emptied(snap):
    snap.clear()
    snap.update({"sha": "", "default_branch": "main", "issues": [], "pulls": [], "branches": [],
                 "claims": {}, "merged": []})


def folded_intro(snap):
    snap["default_branch"] = "tr\u00fcnk"
    snap["sha"] = "\u00e9123abcdef"


def numbered_pulls(snap):
    for number, title in ((9, "Nine"), (100, "Hundred")):
        snap["pulls"].append({"number": number, "title": title, "head": {"ref": f"claude/{number}"},
                              "mergeable_state": "clean", "updated_at": "2026-09-01T00:00:00Z", "files": []})


def claimed_todos(snap):
    snap["issues"].append({"number": 12, "title": "Claimed by branch", "labels": [{"name": "todo"}]})
    snap["issues"].append({"number": 16, "title": "Claimed by label", "labels": [{"name": "todo"}, {"name": "claimed"}]})


def team_work(snap):
    snap["issues"].append({"number": 20, "title": "An epic", "labels": [{"name": "todo"}, {"name": "epic"}]})
    snap["issues"].append({"number": 21, "title": "A member's task", "labels": [{"name": "todo"}, {"name": "agent:w1"}]})


def unsettled_pull(snap):
    snap["pulls"][1]["mergeable"] = None


# Every snapshot the template's tests/test_board.py renders, by what its test
# looks at, and the wide fixture, which walks every branch of the renderer.
BOARD_SCENARIOS = (
    ("the fixture", FIXTURE, None),
    ("a claim whose issue is closed", FIXTURE, closed_claim),
    ("a released claim file", FIXTURE, released_claim),
    ("merged rows newest first", FIXTURE, merged_out_of_order),
    ("claims without a pull request", FIXTURE, claims_without_pulls),
    ("an empty snapshot", FIXTURE, emptied),
    ("the intro line folded to ascii", FIXTURE, folded_intro),
    ("rows sorted by number", FIXTURE, numbered_pulls),
    ("claimed todos off the queue", FIXTURE, claimed_todos),
    ("team work off the queue", FIXTURE, team_work),
    ("the wide fixture", WIDE, None),
)


def scenario_snapshot(path, change):
    snap = fixture(path)
    if change:
        change(snap)
    return snap


def repo_from_snapshot(snap):
    """A GitHub whose answers collect into this snapshot: its open issues,
    its open pull requests with their files, its branches, its claim files
    on their branches, the issues behind its claims, and its merges."""
    repo = FakeRepo()
    for issue in snap.get("issues") or []:
        repo.issues[issue["number"]] = dict(issue, state="open")
    for key, info in (snap.get("claim_issues") or {}).items():
        names = [label if isinstance(label, str) else label["name"] for label in info["labels"]]
        repo.issues.setdefault(int(key), {"number": int(key), "title": "", "state": info["state"],
                                          "labels": [{"name": name} for name in names]})
    for pull in snap.get("pulls") or []:
        detail = {key: value for key, value in pull.items() if key != "files"}
        repo.pulls[pull["number"]] = {"mergeable": True, **detail, "state": "open"}
        repo.files[pull["number"]] = [board.file_name(name) for name in pull.get("files") or []]
    for merged in snap.get("merged") or []:
        repo.pulls[merged["number"]] = dict(merged, state="closed")
    repo.branches = {board.branch_name(branch) for branch in snap.get("branches") or []}
    for key, text in (snap.get("claims") or {}).items():
        if text is not None:
            repo.contents[(f"claude/{key}", f"{claims.CLAIM_DIR}/{key}.md")] = text
    return repo


@needs_template
class BoardParity(unittest.TestCase):
    def test_every_snapshot_renders_the_same_page(self):
        for name, path, change in BOARD_SCENARIOS:
            snap = scenario_snapshot(path, change)
            old = template_board().render(copy.deepcopy(snap))
            with self.subTest(name):
                self.assertEqual(board.render(copy.deepcopy(snap)).encode("ascii"), old.encode("ascii"))
                self.assertEqual(board.render(board.Snapshot.from_dict(snap)), old)

    def test_the_same_github_collects_the_same_snapshot_and_page(self):
        for name, path, change in BOARD_SCENARIOS + (("an unsettled pull request", FIXTURE, unsettled_pull),):
            snap = scenario_snapshot(path, change)
            sha, trunk = snap.get("sha", ""), snap.get("default_branch") or "main"
            old_repo, new_repo = repo_from_snapshot(snap), repo_from_snapshot(snap)
            old_sleep, new_sleep = Sleeps(), Sleeps()
            old = template_board().collect(template_client(old_repo, old_sleep), sha, trunk)
            new = github.collect(library_client(new_repo, new_sleep), sha, trunk)
            with self.subTest(name):
                self.assertEqual(new, old)
                self.assertEqual(new_sleep.calls, old_sleep.calls)
                self.assertEqual(board.render(github.snapshot(library_client(new_repo), sha, trunk)),
                                 template_board().render(old))

    def test_the_same_comments_are_posted_once(self):
        now = datetime(2026, 9, 23, 12, 0, tzinfo=timezone.utc)
        idle = board.IDLE_MARKER + "\nasked before"
        moved = board.MAIN_MOVED_MARKER.format(sha="0123abc") + "\nsaid before"
        cases = (
            (FIXTURE, "push", "main", {}), (FIXTURE, "push", "main", {40: [moved]}),
            (FIXTURE, "push", "trunk", {}), (FIXTURE, "schedule", "", {40: [idle]}),
            (WIDE, "push", "trunk", {}), (WIDE, "push", "trunk", {61: [idle], 66: ["a person's words"]}),
            (WIDE, "schedule", "", {}), (WIDE, "pull_request", "claude/22", {}),
        )
        for path, event_name, ref_name, already in cases:
            snap = fixture(path)
            old_repo, new_repo = FakeRepo(), FakeRepo()
            old_repo.comments, new_repo.comments = copy.deepcopy(already), copy.deepcopy(already)
            old = template_board().post_comments(template_client(old_repo), snap, event_name, ref_name, now)
            new = github.post_comments(library_client(new_repo),
                                       board.comments_due(snap, event_name, ref_name, now))
            with self.subTest(f"{path.name} {event_name} {ref_name} {sorted(already)}"):
                self.assertEqual(new, old)
                self.assertEqual(new_repo.writes, old_repo.writes)

    def test_the_rules_give_the_same_answers(self):
        old = template_board()
        for given in ("clean", "behind", "has_hooks", "unstable", "dirty", "blocked", "unknown", "draft", None):
            self.assertEqual(board.pr_state({"mergeable_state": given}), old.pr_state({"mergeable_state": given}), given)
        for state in ("behind", "conflicting"):
            self.assertEqual(board.main_moved_text(state, "main", "0123abc"), old.main_moved_text(state, "main", "0123abc"))
        self.assertEqual((board.MAIN_MOVED_MARKER, board.IDLE_MARKER, board.IDLE_TEXT),
                         (old.MAIN_MOVED_MARKER, old.IDLE_MARKER, old.IDLE_TEXT))
        spellings = ("Account: main", "- Account: main", "**Account**: main", "- **Account:** `main`",
                     "- Account: main (label account:main)", "Plan: nothing named", CLAIM_TEXT)
        for text in spellings:
            self.assertEqual(claims.claim_account(text + "\n"), old.claim_account(text + "\n"), text)
        for text in (CLAIM_TEXT, "- Account: main\n", "## Files to touch\n\n- x.md\n\n## Notes\n\n- not a file\n"):
            self.assertEqual(claims.claim_files(text), old.claim_files(text), text)
        for stamp in ("2026-09-22T08:00:00Z", "2026-09-22T10:00:00+02:00", "2026-09-22T08:00:00"):
            self.assertEqual(claims.parse_time(stamp), old.parse_time(stamp), stamp)

    def test_the_dry_run_prints_and_writes_the_same_page(self):
        with tempfile.TemporaryDirectory() as folder:
            for path in (FIXTURE, WIDE):
                old = run_command([str(TOOLS / "board.py"), "--dry-run", str(path)], folder)
                new = run_command(library_command("board", "--dry-run", str(path)), folder)
                self.assertEqual(new, old, path.name)
                self.assertEqual(old[0], 0)
                old_out, new_out = Path(folder) / "old.md", Path(folder) / "new.md"
                run_command([str(TOOLS / "board.py"), "--dry-run", str(path), "--out", str(old_out)], folder)
                run_command(library_command("board", "--dry-run", str(path), "--out", str(new_out)), folder)
                self.assertEqual(new_out.read_bytes(), old_out.read_bytes(), path.name)

    def test_a_live_run_without_a_token_fails_the_same_way(self):
        with tempfile.TemporaryDirectory() as folder:
            old = run_command([str(TOOLS / "board.py"), "--render-only"], folder)
            new = run_command(library_command("board", "--render-only"), folder)
        self.assertEqual(new, old)
        self.assertEqual(old[0], 1)


# --- the protocol check ---------------------------------------------------------------

def event(branch="claude/7", body="Closes #7", draft=True, head_repo="octo/widgets", base_repo="octo/widgets"):
    return {"pull_request": {
        "head": {"ref": branch, "repo": {"full_name": head_repo}},
        "base": {"repo": {"full_name": base_repo}},
        "body": body,
        "draft": draft,
    }}


# The events of the template's tests/test_check_protocol.py, and a few shapes
# a hand-made or truncated event takes.
EVENTS = (
    ("another branch", event(branch="dependabot/actions/checkout-8")),
    ("a draft", event()),
    ("ready", event(draft=False)),
    ("no closes line", event(body="A fine change.")),
    ("the wrong issue", event(body="Closes #8")),
    ("fixes", event(body="fixes #7")),
    ("fixed", event(body="Fixed #7 at last")),
    ("resolves", event(body="resolves #7")),
    ("close", event(body="close #7")),
    ("a fork", event(head_repo="someone/widgets")),
    ("no body", event(body=None)),
    ("a claim branch that is no number", event(branch="claude/seven")),
    ("no pull request", {}),
    ("a null pull request", {"pull_request": None}),
)


def checkout(folder, with_claim):
    """A checkout, with or without docs/agents/claims/7.md."""
    if with_claim:
        claim = Path(folder) / claims.claim_path(7)
        claim.parent.mkdir(parents=True)
        claim.write_text("# Claim: #7\n", encoding="utf-8")
    return folder


@needs_template
class CheckParity(unittest.TestCase):
    def test_the_same_problems_for_every_event(self):
        for with_claim in (True, False):
            with tempfile.TemporaryDirectory() as folder:
                root = checkout(folder, with_claim)
                for name, the_event in EVENTS:
                    old = template_check().problems_for(copy.deepcopy(the_event), root)
                    exists = (Path(root) / claims.claim_path(7)).is_file()
                    new = protocol_check.problems_for(the_event.get("pull_request") or {}, exists)
                    self.assertEqual(new, old, f"{name}, claim file {with_claim}")

    def test_the_command_says_the_same_and_exits_the_same(self):
        for with_claim in (True, False):
            with tempfile.TemporaryDirectory() as folder, tempfile.TemporaryDirectory() as events:
                root = checkout(folder, with_claim)
                for index, (name, the_event) in enumerate(EVENTS):
                    where = Path(events) / f"{index}.json"
                    where.write_text(json.dumps(the_event), encoding="utf-8")
                    self.assert_same_check(root, f"{name}, claim file {with_claim}", GITHUB_EVENT_PATH=str(where))

    def test_no_event_or_an_unreadable_one_passes_the_same_way(self):
        with tempfile.TemporaryDirectory() as folder:
            unreadable = Path(folder) / "event.json"
            unreadable.write_text("{not json", encoding="utf-8")
            self.assert_same_check(folder, "no event")
            self.assert_same_check(folder, "a missing file", GITHUB_EVENT_PATH=str(Path(folder) / "gone.json"))
            self.assert_same_check(folder, "not JSON", GITHUB_EVENT_PATH=str(unreadable))

    def assert_same_check(self, root, name, **env):
        old = run_command([str(TOOLS / "check_protocol.py")], root, **env)
        new = run_command(library_command("check"), root, **env)
        self.assertEqual(new, old, name)


# --- the push guard ------------------------------------------------------------------

def guard_source():
    """The claude/11 guard's source from the template's git, or None when
    git or the branch is not there."""
    try:
        shown = subprocess.run(["git", "-C", str(TEMPLATE), "show", GUARD_SOURCE], capture_output=True,
                               env=environment(), timeout=60)
    except OSError:
        return None
    return shown.stdout if shown.returncode == 0 else None


def git(folder, *args):
    subprocess.run(["git", "-c", "user.name=parity", "-c", "user.email=parity@example.invalid",
                    "-c", "commit.gpgsign=false", *args], cwd=folder, check=True, capture_output=True,
                   env=environment(), timeout=60)


def make_checkout(folder, current, default="main"):
    """A clone-like repository whose origin/HEAD names default, on the
    branch current (None for a detached head)."""
    folder.mkdir()
    git(folder, "init", "-q", "-b", default)
    git(folder, "commit", "-q", "--allow-empty", "-m", "start")
    git(folder, "update-ref", f"refs/remotes/origin/{default}", "HEAD")
    git(folder, "symbolic-ref", "refs/remotes/origin/HEAD", f"refs/remotes/origin/{default}")
    if current is None:
        git(folder, "checkout", "-q", "--detach")
    elif current != default:
        git(folder, "checkout", "-q", "-b", current)
    return folder


CHECKOUTS = {"claim": ("claude/7", "main"), "main": ("main", "main"), "feature": ("feature", "main"),
             "detached": (None, "main"), "trunk": ("claude/7", "trunk")}

# The commands of the template's tests/test_guard_push.py on claude/11: each
# refusal rule as the push that meets it, each parsing case, and each case
# of its main(), run from a checkout on the branch the case names.
GUARD_CASES = (
    ("claim", "git push origin main"),
    ("claim", "git push origin HEAD:main"),
    ("claim", "git push origin :board"),
    ("claim", "git push --delete origin board"),
    ("main", "git push"),
    ("main", "git push -u origin HEAD"),
    ("claim", "git push --force origin claude/7"),
    ("claim", "git push -f origin claude/7"),
    ("claim", "git push --force-with-lease origin claude/7"),
    ("claim", "git push -u --force-with-lease=claude/7 origin claude/7"),
    ("feature", "git push --force-with-lease origin feature"),
    ("feature", "git push --force-with-lease"),
    ("claim", "git push --mirror origin"),
    ("claim", "git push --all origin"),
    ("claim", "git push origin claude/7"),
    ("claim", "git push -u origin HEAD"),
    ("claim", "git push"),
    ("claim", "git push origin refs/heads/main"),
    ("detached", "git push"),
    ("claim", "git fetch origin && git push origin main"),
    ("claim", "echo 'git push origin main'"),
    ("claim", "git push origin claude/7 2>&1"),
    ("claim", "git -C clone push origin x"),
    ("claim", "git pull origin main"),
    ("claim", "git add -A\ngit commit -m x\ngit push origin main"),
    ("claim", "git status # look first\ngit push origin main"),
    ("claim", "# look first\ngit push origin main"),
    ("claim", "true; # a note ending in a backslash \\\ngit push origin main"),
    ("claim", "git commit -m 'Claim #7' && git push origin main"),
    ("claim", "echo a#b; git push origin main"),
    ("claim", "git push \\\n  origin main"),
    ("claim", "git commit -m 'one\ngit push origin main' && git push -u origin claude/7"),
    ("claim", 'git commit -m "one\ngit push origin main" && git push -u origin claude/7'),
    ("claim", "git push 'unclosed"),
    ("claim", "echo 'unclosed\ngit push origin main"),
    ("claim", "git push -u origin claude/7"),
    ("claim", "git push -u origin claude/7\ngit fetch origin main"),
    ("claim", "git push -u origin claude/7\n# then open the pull request against main"),
    ("claim", "git push -u origin claude/7 # not main"),
    ("claim", "git commit -F - <<'EOF'\nClaim #7\n\ngit push origin main\nEOF\ngit push -u origin claude/7"),
    ("claim", "git commit -F - <<'EOF'\nClaim #7\n\nThe board first.\nEOF\ngit push -u origin claude/7"),
    ("claim", "git status"),
    ("trunk", "git push origin trunk"),
    ("trunk", "git push origin main"),
)


@needs_template
class GuardParity(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = tempfile.TemporaryDirectory()
        top = Path(cls.folder.name)
        source = guard_source()
        if source is None:
            cls.folder.cleanup()
            raise unittest.SkipTest(f"the template has no {GUARD_SOURCE}, or git is missing")
        cls.old_guard = top / "guard_push.py"
        cls.old_guard.write_bytes(source)
        cls.checkouts = {name: make_checkout(top / name, current, default)
                         for name, (current, default) in CHECKOUTS.items()}

    @classmethod
    def tearDownClass(cls):
        cls.folder.cleanup()

    def assert_same_answer(self, payload, cwd, name):
        old = run_command([str(self.old_guard)], cwd, stdin=payload)
        new = run_command(library_command("guard"), cwd, stdin=payload)
        self.assertEqual(new, old, name)
        return old

    def test_every_payload_gets_the_same_exit_code_and_reason(self):
        codes = set()
        for where, command in GUARD_CASES:
            payload = json.dumps({"tool_input": {"command": command}, "cwd": str(self.checkouts[where])})
            code, _, _ = self.assert_same_answer(payload, self.checkouts["claim"], f"{where}: {command!r}")
            codes.add(code)
        self.assertEqual(codes, {0, 2})

    def test_payloads_without_a_cwd_or_json_pass_or_refuse_alike(self):
        here = self.checkouts["claim"]
        payloads = (
            ("not JSON", "not json"),
            ("no cwd, no push", json.dumps({"tool_input": {"command": "git status"}})),
            ("no cwd, a push to main", json.dumps({"tool_input": {"command": "git push origin main"}})),
            ("a cwd that is gone", json.dumps({"tool_input": {"command": "git push"},
                                               "cwd": str(Path(self.folder.name) / "gone")})),
            ("no tool input", json.dumps({"cwd": str(here)})),
            ("an empty object", "{}"),
        )
        for name, payload in payloads:
            self.assert_same_answer(payload, here, name)


# --- the team clerk ------------------------------------------------------------------

PLAN = {"plan": "w1 owns the parser, w2 the printer.",
        "tasks": [{"title": "Parser", "owner": "w1", "files": ["src/parse.py"], "body": "Parse it."},
                  {"title": "Printer", "owner": "nobody", "files": [], "body": "Print it."}]}
TEAM = json.dumps(["w1", "w2"])


def route(event_name, number, label=None):
    return ["route", "--event", event_name, "--issue", str(number)] + (["--label", label] if label else [])


def next_for(agent, members=None, label=None):
    argv = ["next", "--agent", agent, "--team", json.dumps(members) if members else ""]
    return argv + (["--label", label] if label else [])


def seed(epic, plan_file, members=TEAM):
    return ["seed", "--epic", str(epic), "--plan", plan_file, "--team", members]


# Each scenario of the template's tests/test_team.py, as the clerk's
# questions in its argument shapes over a repository the scenario sets up
# and changes between questions; ask answers one question.

def a_new_epic_is_planned_once(repo, ask, plan):
    repo.add_issue(1, ["epic"], body="Build the widget.")
    first = ask(route("labeled", 1, "epic"))
    repo.issues[1]["body"] += "\n" + team.PLAN_MARKER
    return [first, ask(route("labeled", 1, "epic"))]


def other_labels_and_closed_epics_plan_nothing(repo, ask, plan):
    repo.add_issue(1, ["epic"])
    repo.add_issue(2, ["epic"], state="closed")
    return [ask(route("labeled", 1, "todo")), ask(route("labeled", 2, "epic"))]


def the_last_task_closing_calls_for_integration(repo, ask, plan):
    repo.add_issue(1, ["epic"], body="spec\n<!-- team:tasks 2 3 -->")
    repo.add_issue(2, body="Epic: #1\n\nfirst", state="closed")
    repo.add_issue(3, body="Epic: #1\n\nsecond")
    first = ask(route("closed", 2))
    repo.issues[3]["state"] = "closed"
    return [first, ask(route("closed", 3))]


def integration_by_the_epics_own_number(repo, ask, plan):
    repo.add_issue(1, ["epic"], body="spec\n<!-- team:tasks 2 -->")
    repo.add_issue(2, body="Epic: #1", state="closed")
    return [ask(route("closed", 1))]


def an_integration_already_claimed_is_not_started_twice(repo, ask, plan):
    repo.add_issue(1, ["epic"], body="spec\n<!-- team:tasks 2 -->")
    repo.add_issue(2, body="Epic: #1", state="closed")
    repo.branches.add("claude/1")
    repo.add_pull(1)
    first = ask(route("closed", 2))
    repo.add_pull(1, draft=True)
    return [first, ask(route("closed", 2))]


def a_blocked_task_calls_the_lead(repo, ask, plan):
    repo.add_issue(2, ["todo", "agent:w1", "blocked"], body="Epic: #1")
    repo.add_issue(3, ["blocked"], body="not a task")
    answers = [ask(route("labeled", 2, "blocked")), ask(route("labeled", 3, "blocked"))]
    repo.issues[2]["labels"] = [{"name": "todo"}]
    return answers + [ask(route("labeled", 2, "blocked"))]


def a_closed_issue_outside_any_epic_routes_nowhere(repo, ask, plan):
    repo.add_issue(5, body="no epic here", state="closed")
    return [ask(route("closed", 5))]


def with_an_epic(repo):
    repo.add_issue(1, ["epic"])


def a_member_takes_its_own_task_first(repo, ask, plan):
    with_an_epic(repo)
    repo.add_issue(2, ["todo"])
    repo.add_issue(3, ["todo", "agent:w1"])
    return [ask(next_for("w1")), ask(next_for("w2"))]


def another_members_task_and_the_epic_are_never_taken(repo, ask, plan):
    with_an_epic(repo)
    repo.add_issue(2, ["todo", "agent:w2"])
    return [ask(next_for("w1"))]


def a_claimed_task_is_not_taken_again(repo, ask, plan):
    with_an_epic(repo)
    repo.add_issue(2, ["todo"])
    repo.branches.add("claude/2")
    return [ask(next_for("w1"))]


def a_claim_without_a_pull_request_or_in_draft_is_carried(repo, ask, plan):
    with_an_epic(repo)
    repo.add_issue(2, ["claimed", "agent:w1"])
    repo.branches.add("claude/2")
    first = ask(next_for("w1"))
    repo.add_pull(2, draft=True)
    return [first, ask(next_for("w1"))]


def a_claim_that_fell_behind_or_failed_is_carried(repo, ask, plan):
    with_an_epic(repo)
    repo.add_issue(2, ["claimed", "agent:w1"])
    repo.branches.add("claude/2")
    repo.add_pull(2, state="behind")
    first = ask(next_for("w1"))
    repo.add_pull(2, state="blocked", conclusion="failure")
    return [first, ask(next_for("w1"))]


def a_claim_waiting_on_its_checks_or_merge_costs_no_run(repo, ask, plan):
    with_an_epic(repo)
    repo.add_issue(2, ["claimed", "agent:w1"])
    repo.branches.add("claude/2")
    repo.add_pull(2, state="blocked", conclusion=None)
    first = ask(next_for("w1"))
    repo.add_pull(2, state="clean")
    return [first, ask(next_for("w1"))]


def a_blocked_task_waits_for_help(repo, ask, plan):
    with_an_epic(repo)
    repo.add_issue(2, ["todo", "agent:w1", "blocked"])
    return [ask(next_for("w1"))]


def a_task_another_account_claimed_is_never_carried(repo, ask, plan):
    with_an_epic(repo)
    repo.add_issue(6, ["todo", "agent:w1", "claimed", "account:second"])
    repo.branches.add("claude/6")
    return [ask(next_for("w1"))]


def a_failure_outside_the_required_check_costs_no_run(repo, ask, plan):
    with_an_epic(repo)
    repo.add_issue(2, ["claimed", "agent:w1"])
    repo.branches.add("claude/2")
    repo.add_pull(2, state="unstable", conclusion="failure", check="board")
    return [ask(next_for("w1"))]


def the_plain_worker_carries_its_accounts_claims(repo, ask, plan):
    with_an_epic(repo)
    repo.add_issue(5, ["claimed", "account:main"])
    repo.branches.add("claude/5")
    return [ask(next_for("main"))]


def an_unclaimed_blocker_comes_first(repo, ask, plan):
    with_an_epic(repo)
    repo.add_issue(3, ["todo"])
    repo.add_issue(9, ["blocker"])
    return [ask(next_for("main"))]


def members_divide_the_shared_work(repo, ask, plan):
    with_an_epic(repo)
    repo.add_issue(4, ["todo"])
    repo.add_issue(5, ["todo"])
    members = ["w1", "w2", "w3"]
    return [ask(next_for(member, members)) for member in members]


def a_busy_member_leaves_its_share_to_the_rest(repo, ask, plan):
    with_an_epic(repo)
    repo.add_issue(3, ["todo", "agent:w1"])
    repo.add_issue(4, ["todo"])
    members = ["w1", "w2", "w3"]
    return [ask(next_for(member, members)) for member in members]


def pull_requests_in_the_issue_list_are_left_out(repo, ask, plan):
    with_an_epic(repo)
    repo.add_issue(3, ["todo"])["pull_request"] = {"url": "pulls/3"}
    repo.add_issue(4, ["todo"])
    return [ask(next_for("w1"))]


def the_members_label_is_made_before_a_take(repo, ask, plan):
    repo.add_issue(4, ["todo"])
    first = ask(next_for("main", label="account:main"))
    repo.branches.add("claude/4")
    return [first, ask(next_for("main", label="account:second"))]


def seed_opens_owned_tasks_and_writes_the_plan_once(repo, ask, plan):
    repo.add_issue(1, ["epic"], body="Build the widget.")
    return [ask(seed(1, plan)), ask(seed(1, plan))]


def seed_takes_the_epic_off_the_queue(repo, ask, plan):
    repo.add_issue(1, ["epic", "todo"])
    return [ask(seed(1, plan))]


def seeded_tasks_route_to_integration_when_all_close(repo, ask, plan):
    repo.add_issue(1, ["epic"])
    first = ask(seed(1, plan))
    for number in (101, 102):
        repo.issues[number]["state"] = "closed"
    return [first, ask(route("closed", 102))]


def a_sub_issue_link_that_fails_does_not_fail_the_seed(repo, ask, plan):
    repo.add_issue(1, ["epic"])
    repo.refuse_sub_issues = True
    return [ask(seed(1, plan))]


def a_seed_whose_labels_exist_already(repo, ask, plan):
    repo.labels |= {"agent:w1", "agent:w2"}
    repo.add_issue(1, ["epic"])
    return [ask(seed(1, plan))]


def a_plan_without_tasks_is_refused(repo, ask, plan):
    repo.add_issue(1, ["epic"])
    empty = Path(plan).with_name("empty.json")
    empty.write_text(json.dumps({"plan": "x", "tasks": []}), encoding="utf-8")
    return [ask(seed(1, str(empty), json.dumps(["w1"]))), ask(seed(1, plan, "w1,w2"))]


def an_issue_that_is_not_there(repo, ask, plan):
    return [ask(route("closed", 404))]


TEAM_SCENARIOS = (
    a_new_epic_is_planned_once, other_labels_and_closed_epics_plan_nothing,
    the_last_task_closing_calls_for_integration, integration_by_the_epics_own_number,
    an_integration_already_claimed_is_not_started_twice, a_blocked_task_calls_the_lead,
    a_closed_issue_outside_any_epic_routes_nowhere, a_member_takes_its_own_task_first,
    another_members_task_and_the_epic_are_never_taken, a_claimed_task_is_not_taken_again,
    a_claim_without_a_pull_request_or_in_draft_is_carried, a_claim_that_fell_behind_or_failed_is_carried,
    a_claim_waiting_on_its_checks_or_merge_costs_no_run, a_blocked_task_waits_for_help,
    a_task_another_account_claimed_is_never_carried, a_failure_outside_the_required_check_costs_no_run,
    the_plain_worker_carries_its_accounts_claims, an_unclaimed_blocker_comes_first,
    members_divide_the_shared_work, a_busy_member_leaves_its_share_to_the_rest,
    pull_requests_in_the_issue_list_are_left_out, the_members_label_is_made_before_a_take,
    seed_opens_owned_tasks_and_writes_the_plan_once, seed_takes_the_epic_off_the_queue,
    seeded_tasks_route_to_integration_when_all_close, a_sub_issue_link_that_fails_does_not_fail_the_seed,
    a_seed_whose_labels_exist_already, a_plan_without_tasks_is_refused, an_issue_that_is_not_there,
)


def template_ask(repo):
    """The template's clerk as its main() runs it, without the printing:
    run(api, parse_args(argv)), a refusal as its type and message."""
    old = template_team()
    api = old.GitHub("token", REPO, opener=repo, sleep=Sleeps())

    def ask(argv):
        try:
            return old.run(api, old.parse_args(argv))
        except old.TeamError as problem:
            return f"refused: {problem}"
        except old.BoardError as problem:
            return f"failed: {problem}"
    return ask


def library_ask(repo):
    """The library's clerk as cli.py runs it, without the printing."""
    client = library_client(repo)

    def ask(argv):
        try:
            return cli.team_answer(client, cli.team_arguments(argv, {}))
        except team.TeamError as problem:
            return f"refused: {problem}"
        except team.ClientError as problem:
            return f"failed: {problem}"
    return ask


@needs_template
class TeamParity(unittest.TestCase):
    def test_every_scenario_gets_the_same_answers_and_writes(self):
        with tempfile.TemporaryDirectory() as folder:
            plan = Path(folder) / "plan.json"
            plan.write_text(json.dumps(PLAN), encoding="utf-8")
            for scenario in TEAM_SCENARIOS:
                old_repo, new_repo = FakeRepo(), FakeRepo()
                old = scenario(old_repo, template_ask(old_repo), str(plan))
                new = scenario(new_repo, library_ask(new_repo), str(plan))
                with self.subTest(scenario.__name__):
                    self.assertEqual(new, old)
                    self.assertEqual(new_repo.writes, old_repo.writes)
                    self.assertEqual(new_repo.state(), old_repo.state())

    def test_the_pure_rules_give_the_same_answers(self):
        old = template_team()
        cases = (([{"owner": "w2"}, {"owner": "W1"}, {"owner": "w3"}], ["w1", "w2", "w3"]),
                 ([{}, {}, {}], ["w1", "w2"]), ([{"owner": "w1"}, {"owner": "w1"}], ["w1", "w2"]))
        for tasks, members in cases:
            self.assertEqual(team.owners_for(tasks, members), old.owners_for(tasks, members))
        for text in ('["w1", "w2"]', "", "w1,w2", '{"w1": 1}', '[""]', "[1]"):
            self.assertEqual(self.members(team, text), self.members(old, text), text)
        for body in ("Epic: #12\n\nwork", "no epic", "spec\n<!-- team:tasks 4 5 6 -->", None):
            issue = {"body": body}
            self.assertEqual((team.epic_of(issue), team.task_numbers(issue)),
                             (old.epic_of(issue), old.task_numbers(issue)), body)
        self.assertEqual(team.task_body(3, PLAN["tasks"][0]), old.task_body(3, PLAN["tasks"][0]))
        made = [(101, "Parser", "w1"), (102, "Printer", "w2")]
        self.assertEqual(team.plan_section(PLAN["plan"], made), old.plan_section(PLAN["plan"], made))

    @staticmethod
    def members(module, text):
        try:
            return module.team_members(text)
        except module.TeamError as problem:
            return f"refused: {problem}"

    def test_the_command_refuses_alike_before_calling_github(self):
        with tempfile.TemporaryDirectory() as folder:
            plan = Path(folder) / "plan.json"
            plan.write_text(json.dumps(PLAN), encoding="utf-8")
            cases = (
                ({}, ["--repo", REPO, "next", "--agent", "w1"]),
                ({"GITHUB_TOKEN": "token"}, ["next", "--agent", "w1"]),
                ({"GH_TOKEN": "token"}, ["--repo", REPO, "seed", "--epic", "1", "--plan", str(plan), "--team", "w1,w2"]),
                ({"GITHUB_TOKEN": "token", "GITHUB_REPOSITORY": REPO}, ["next", "--agent", "w1", "--team", '[""]']),
            )
            for env, argv in cases:
                old = run_command([str(TOOLS / "team.py"), *argv], folder, **env)
                new = run_command(library_command("team", *argv), folder, **env)
                self.assertEqual(new, old, (env, argv))
                self.assertEqual(old[0], 1)


# --- what changed on purpose -----------------------------------------------------------

@needs_template
class DeclaredDifferences(unittest.TestCase):
    """The differences the extraction declared, each pinned on both sides,
    so the record of them stays true."""

    def test_the_board_lets_its_own_word_conflicting_through(self):
        pull = {"mergeable_state": "conflicting"}
        self.assertEqual((template_board().pr_state(pull), board.pr_state(pull)), ("unknown", "conflicting"))

    def test_a_null_title_renders_empty_where_the_template_printed_none(self):
        snap = fixture()
        snap["pulls"][0]["title"] = None
        old, new = template_board().render(copy.deepcopy(snap)), board.render(snap)
        self.assertIn("| #40 | None | claude/12 |", old)
        self.assertIn("| #40 |  | claude/12 |", new)

    def test_the_render_can_name_the_default_branch(self):
        snap = fixture()
        self.assertEqual(board.render(snap, None), template_board().render(snap))
        self.assertIn("on trunk.", board.render(snap, "trunk"))

    def test_a_check_event_that_is_not_an_object_passes_instead_of_crashing(self):
        with tempfile.TemporaryDirectory() as folder:
            where = Path(folder) / "event.json"
            where.write_text("[1]", encoding="utf-8")
            old = run_command([str(TOOLS / "check_protocol.py")], folder, GITHUB_EVENT_PATH=str(where))
            new = run_command(library_command("check"), folder, GITHUB_EVENT_PATH=str(where))
        self.assertEqual(old[0], 1)
        self.assertIn("AttributeError", old[2])
        self.assertEqual(new, (0, "the event is not readable; nothing checked\n", ""))

    def test_a_guard_payload_that_is_not_an_object_passes_instead_of_crashing(self):
        source = guard_source()
        if source is None:
            self.skipTest(f"the template has no {GUARD_SOURCE}, or git is missing")
        with tempfile.TemporaryDirectory() as folder:
            old_guard = Path(folder) / "guard_push.py"
            old_guard.write_bytes(source)
            for payload in ("[]", '{"tool_input": "git push origin main"}'):
                old = run_command([str(old_guard)], folder, stdin=payload)
                new = run_command(library_command("guard"), folder, stdin=payload)
                self.assertEqual(old[0], 1, payload)
                self.assertEqual(new, (0, "", ""), payload)


if __name__ == "__main__":
    unittest.main()
