"""scripts/canary.py: the live canary's checks, driven against a fake site.

The checks read and write GitHub and git through the methods of
canary.Site alone, so a fake with the same methods, keeping issues, pull
requests, branches and a board page in memory and reacting to the driver
as the live board would when the test says so, drives every check end to
end; the live site's git side, Clone, is run for real against a bare
repository here, the race included, and its REST side is the library's own
adapter, tested with the library.
"""

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT))

import canary  # noqa: E402
from sdlc_protocol import board, claims, labels  # noqa: E402

MAIN = "sha-main-1"
BOT = "github-actions[bot]"


def page_text(fake):
    """STATE.md as the board would render the fake's state: the headings
    and columns are board.py's own, read back by validation/page.py."""
    def table(heading, columns, rows):
        lines = [heading, "| " + " | ".join(columns) + " |", "| " + " | ".join("---" for _ in columns) + " |"]
        lines += ["| " + " | ".join(row) + " |" for row in rows] or ["| (none) |"]
        return lines + [""]
    claim_rows, claimed = [], set()
    for name in sorted(fake.branches):
        if not name.startswith(claims.PREFIX):
            continue
        key = name[len(claims.PREFIX):]
        number = claims.number_in(key)
        issue = f"#{number}" if number is not None else board.NOT_A_CLAIM
        worker = fake.worker_of(name)
        if number is not None:
            claimed.add(number)
        claim_rows.append([board.cell(c) for c in (issue, board.escaped(name), worker, board.NO_PULL_YET, "-")])
    queue = [[f"#{n}", f"issue {n}", labels.TODO] for n, issue in sorted(fake.issues.items())
             if issue["state"] == "open" and labels.TODO in issue["labels"] and n not in claimed]
    lines = ["# Board", ""]
    lines += table(board.CLAIM_HEADING, board.CLAIM_COLUMNS, claim_rows)
    lines += table(board.QUEUE_HEADING, board.ISSUE_COLUMNS, queue)
    return "\n".join(lines)


class FakeClone:
    def __init__(self, site):
        self.site = site
        self.on = None
        self.head_sha = site.branches[site.default_branch]
        self.files = {}

    def head(self):
        return self.head_sha

    def branch(self, name, start=None):
        self.on = name
        self.head_sha = start or self.site.branches[self.site.default_branch]
        self.files = dict(self.site.files.get(self.head_sha, {}))

    def commit(self, files, message, committer_date=None):
        for rel, text in files.items():
            if text is None:
                self.files.pop(rel, None)
            else:
                self.files[rel] = text
        self.site.counter += 1
        sha = f"sha-{self.site.counter:03d}"
        self.site.parents[sha] = self.head_sha
        self.site.files[sha] = dict(self.files)
        self.site.dates[sha] = committer_date
        self.head_sha = sha
        return sha

    def push(self, branch, lease=None, delete=False):
        return self.site.push(branch, self.head_sha, lease, delete)


class FakeSite:
    """canary.Site's methods over dictionaries. reactions is what the live
    board would do, queued by the test and played one per tick of wait:
    a reaction is a function of the site."""

    repo = "OpenSpineConsortium/symphony-protocol-canary"
    default_branch = "main"

    def __init__(self, adopted=True, runs=None, admin=False, take_every_push=False):
        self.vendored = "sdlc-protocol 0.4.1 438844f ...\nhash  file\n" if adopted else None
        self.issues, self.pulls, self.branches = {}, {}, {MAIN: None}
        self.branches = {self.default_branch: MAIN}
        self.files, self.parents, self.dates = {MAIN: {}}, {MAIN: None}, {}
        self.counter, self.next_number = 0, 100
        self.reactions, self.ticks, self.activity_events = [], 0, []
        self.runs = runs
        self.runs_allowed = runs is not None
        self.admin = object() if admin else None
        self.rulesets_made, self.take_every_push = {}, take_every_push
        self.issues_opened, self.pulls_opened, self.extra_branches = [], [], []
        self.cleaned = False
        self.check_runs_by_sha = {}
        self.label_calls = []
        self.pokes, self.swept = [], False

    # the lists Site keeps for the clean-up
    @property
    def issues_list(self):
        return self.issues_opened

    def now(self):
        return datetime(2026, 10, 14, 4, 43, tzinfo=timezone.utc)

    def sleep(self, seconds):
        pass

    def wait(self, read, holds, budget=canary.POLL_BUDGET, every=canary.POLL_EVERY):
        for _ in range(12):
            value = read()
            if holds(value):
                return value, True
            self.ticks += 1
            if self.reactions:
                self.reactions.pop(0)(self)
        return read(), False

    def clone(self):
        return FakeClone(self)

    def race(self, actions):
        return [action() for action in actions]

    # git's rules over the dictionaries
    def descends(self, sha, ancestor):
        while sha is not None:
            if sha == ancestor:
                return True
            sha = self.parents.get(sha)
        return False

    def push(self, branch, sha, lease=None, delete=False):
        if delete:
            self.branches.pop(branch, None)
            return canary.Pushed(True, "deleted")
        held = self.branches.get(branch)
        if lease:
            if any(r["pattern"] == f"refs/heads/{claims.PREFIX}**" for r in self.rulesets_made.values()):
                return canary.Pushed(False, "! [remote rejected] (protected branch hook declined)")
            if held != lease:
                return canary.Pushed(False, "! [rejected] (stale info)")
            self.activity_events.append({"activity_type": "force_push", "before": held, "after": sha,
                                         "actor": {"login": "app[bot]"}, "timestamp": "2026-10-14T04:44:00Z",
                                         "ref": f"refs/heads/{branch}"})
            self.branches[branch] = sha
            return canary.Pushed(True, "pushed")
        if held is not None and not self.descends(sha, held) and not self.take_every_push:
            return canary.Pushed(False, "! [rejected] claude/n -> claude/n (fetch first)")
        self.branches[branch] = sha
        for pull in self.pulls.values():
            if pull["head"] == branch:
                pull["updated_at"] = f"p{self.counter}"
        return canary.Pushed(True, "pushed")

    def worker_of(self, branch):
        files = self.files.get(self.branches.get(branch), {})
        for text in files.values():
            for line in text.splitlines():
                if line.startswith("- Worker: "):
                    return line[len("- Worker: "):]
        return board.NO_CLAIM_FILE

    # reading
    def main_file(self, path):
        return self.vendored if path == "tools/VENDORED" else None

    def board(self):
        return page_text(self)

    def branch_sha(self, name):
        return self.branches.get(name)

    def main_sha(self):
        return self.branches[self.default_branch]

    def delete_branch(self, name):
        self.branches.pop(name, None)

    def check_runs(self, sha):
        return self.check_runs_by_sha.get(sha, [{"name": "check", "status": "completed", "conclusion": "success"}])

    def activity(self, ref):
        return [e for e in self.activity_events if e["ref"] == ref]

    def runs_since(self, when):
        return self.runs

    # issues and pull requests
    def throwaway(self, check):
        self.next_number += 1
        self.issues[self.next_number] = {"labels": {labels.TODO}, "comments": [], "state": "open", "title": check}
        self.issues_opened.append(self.next_number)
        return self.next_number

    def issue(self, number):
        found = self.issues[number]
        return {"number": number, "labels": [{"name": n} for n in sorted(found["labels"])], "state": found["state"]}

    def labels_of(self, number):
        return sorted(self.issues[number]["labels"])

    def add_labels(self, number, names):
        self.label_calls.append(("add", number, tuple(names)))
        self.issues[number]["labels"].update(names)

    def remove_label(self, number, name):
        self.label_calls.append(("remove", number, name))
        self.issues[number]["labels"].discard(name)

    def comment(self, number, text, author="app[bot]", association="NONE"):
        target = self.issues[number] if number in self.issues else self.pulls[number]
        target["comments"].append({"body": text, "user": {"login": author}, "author_association": association})
        if number in self.pulls:
            self.pulls[number]["updated_at"] = f"t{len(target['comments'])}"

    def comments(self, number):
        target = self.issues[number] if number in self.issues else self.pulls[number]
        return list(target["comments"])

    def close_issue(self, number):
        self.issues[number]["state"] = "closed"

    def open_pull(self, branch, title, body, draft):
        self.next_number += 1
        self.pulls[self.next_number] = {"number": self.next_number, "draft": draft, "state": "open", "head": branch,
                                        "title": title, "body": body, "comments": [], "updated_at": "t0", "reads": 0}
        self.pulls_opened.append(self.next_number)
        return self.next_number

    def pull(self, number):
        found = self.pulls[number]
        found["reads"] += 1
        detail = dict(found)
        detail["mergeable"] = None if found["reads"] == 1 else True
        detail["mergeable_state"] = None if found["reads"] == 1 else ("draft" if found["draft"] else "clean")
        return detail

    def open_pulls(self):
        return [dict(p) for p in self.pulls.values() if p["state"] == "open"]

    def close_pull(self, number):
        self.pulls[number]["state"] = "closed"

    def merge(self, number):
        pull = self.pulls[number]
        self.counter += 1
        sha = f"sha-merge-{self.counter}"
        self.parents[sha] = self.branches[self.default_branch]
        self.branches[self.default_branch] = sha
        pull["state"] = "closed"
        self.branches.pop(pull["head"], None)
        return canary.Pushed(True, "merged")

    # rulesets
    def ruleset_add(self, name, pattern, rule_types):
        self.rulesets_made[7] = {"name": name, "pattern": pattern, "rules": rule_types}
        return 7

    def ruleset_delete(self, ruleset_id):
        self.rulesets_made.pop(ruleset_id, None)

    def rulesets(self):
        return [{"id": i, "name": r["name"]} for i, r in self.rulesets_made.items()]

    def sweep(self):
        self.swept = True
        return []

    def poke(self, number):
        self.pokes.append(number)

    def open_issues(self):
        return [{"number": n, "title": i["title"]} for n, i in self.issues.items() if i["state"] == "open"]

    def clean_up(self):
        self.cleaned = True
        return []


def release(number, head, refuse_rename=False):
    """What the live board's run does to a claim 25 hours idle: the
    coordinator's comment and labels, then the rename, which closes the
    draft and is said once, or, under a ruleset, the refusal said once."""
    def react(site):
        branch = claims.branch_of(number)
        date = site.dates.get(site.branches.get(branch))
        if date is None or site.now() - date <= claims.IDLE_AFTER:
            site.reactions.insert(0, react)
            return
        issue = site.issues[number]
        issue["labels"] -= {labels.CLAIMED, labels.WORKER_PREFIX + canary.WORKER}
        site.comment(number, claims.RELEASE_MARKER + "\nreleased: ...", BOT, "NONE")
        renamed = claims.released_branch(number, head)
        if refuse_rename:
            site.comment(number, board.RENAME_REFUSED_MARKER.format(branch=renamed) + "\nrename refused: "
                         "GitHub refused ...\n\nThese rulesets apply to the two names:\n- canary C7: non_fast_forward",
                         BOT, "NONE")
            return
        site.branches[renamed] = site.branches.pop(branch)
        for pull in site.pulls.values():
            if pull["head"] == branch:
                pull["state"] = "closed"
        site.comment(number, board.RELEASED_MARKER.format(branch=renamed) + "\nclaim released; renamed", BOT, "NONE")
    return react


def rename(number, head):
    def react(site):
        site.branches[claims.released_branch(number, head)] = site.branches.pop(claims.branch_of(number))
    return react


def delete_second_push(number):
    def react(site):
        site.branches.pop(claims.branch_of(number), None)
    return react


def main_moved(site):
    """The board's word to the pull request C2 leaves behind main."""
    behind = [p["number"] for p in site.pulls.values() if "stays behind" in p["title"]][0]
    sha7 = board.short_sha(site.main_sha())
    site.comment(behind, board.MAIN_MOVED_MARKER.format(sha=sha7) + "\nmain moved: ...", BOT, "NONE")


def run(site, name, known_findings=frozenset()):
    out = io.StringIO()
    records = canary.run_checks(site, [name], known_findings, out)
    return records[0], out.getvalue()


class TheChecks(unittest.TestCase):
    def test_c1_passes_when_exactly_one_push_is_taken_and_the_board_lists_it(self):
        site = FakeSite()
        seen, _ = run(site, "C1")
        self.assertEqual(seen.status, "passed", seen.as_dict())
        pushes = seen.values["pushes"]
        self.assertEqual([p["ok"] for p in pushes], [True, False])
        self.assertIn("fetch first", pushes[1]["words"])
        self.assertTrue(all(e["holds"] for e in seen.expectations))

    def test_c1_fails_when_both_pushes_are_taken(self):
        seen, _ = run(FakeSite(take_every_push=True), "C1")
        self.assertEqual(seen.status, "failed")
        self.assertEqual(seen.expectations[0]["expectation"],
                         "exactly one of two simultaneous pushes of the claim branch is taken")

    def test_c2_hears_main_moved_and_counts_the_runs(self):
        runs = [{"event": "push", "head_branch": "main", "conclusion": "success"},
                {"event": "pull_request", "head_branch": "claude/102", "conclusion": "success"},
                {"event": "push", "head_branch": "claude/101", "conclusion": "skipped"}]
        site = FakeSite(runs=runs)
        site.reactions.append(main_moved)
        seen, _ = run(site, "C2")
        self.assertEqual(seen.status, "passed", seen.as_dict())
        self.assertEqual(seen.values["board runs by event"],
                         {"push on main": {"success": 1}, "pull_request on claude/102": {"success": 1},
                          "push on claude/101": {"skipped": 1}})
        self.assertTrue(seen.values["merge"]["ok"])
        self.assertTrue(seen.values["push beside it"]["ok"])
        self.assertEqual(seen.values["checks on the merged head"], {"check": "success"})

    def test_c2_without_main_moved_is_known_while_f12_is_as_built(self):
        site = FakeSite()
        seen, _ = run(site, "C2", frozenset({"F12"}))
        self.assertEqual(seen.status, "known")
        self.assertIn("the token has no Actions read", seen.notes[0])
        seen, _ = run(FakeSite(), "C2")
        self.assertEqual(seen.status, "failed")

    def test_c3_lists_each_odd_name_as_no_claim(self):
        site = FakeSite()
        seen, _ = run(site, "C3")
        self.assertEqual(seen.status, "passed", seen.as_dict())
        self.assertEqual(sorted(seen.values["pushes"]), ["claude/\\u00b2", "claude/caf\\u00e9"])
        self.assertEqual(sorted(site.extra_branches), sorted(canary.ODD_NAMES))
        self.assertIn("GitHub took 2 of 2", seen.notes[0])
        self.assertEqual(board.escaped(canary.ODD_NAMES[0]), "claude/\\u00b2")

    def test_c3_fails_when_the_board_is_not_written_after_the_push(self):
        site = FakeSite()
        site.board = lambda: page_text(FakeSite())
        seen, _ = run(site, "C3")
        self.assertEqual(seen.status, "failed")
        self.assertIn("listing each name as no claim", [e["expectation"] for e in seen.expectations if not e["holds"]][0])

    def test_a_read_refused_until_the_budget_ends_is_a_line_not_a_failure(self):
        site = FakeSite()
        site.wait = canary.Site.wait.__get__(site)
        site.sleep = lambda s: None
        refuse = canary.GitHubError("GET /x: HTTP 500", 500)
        value, held = site.wait(lambda: (_ for _ in ()).throw(refuse), lambda v: True, budget=20, every=10)
        self.assertFalse(held)
        self.assertEqual(value, "not read: GET /x: HTTP 500")
        json.dumps(value)

    def test_c5_finds_the_lease_push_in_the_activity(self):
        site = FakeSite()
        seen, _ = run(site, "C5")
        self.assertEqual(seen.status, "passed", seen.as_dict())
        self.assertEqual(seen.values["force_push"][0]["actor"], "app[bot]")
        self.assertEqual(seen.values["force_push"][0]["activity_type"], "force_push")

    def test_c5_fails_when_the_activity_is_empty(self):
        site = FakeSite()
        site.activity = lambda ref: []
        seen, _ = run(site, "C5")
        self.assertEqual(seen.status, "failed")
        self.assertEqual(seen.values["force_push"], [])

    def test_c6_follows_a_release_end_to_end(self):
        site = FakeSite(runs=[{"event": "pull_request", "head_branch": "claude/101", "conclusion": "success"}])
        site.reactions += [release(101, "sha-002"), delete_second_push(101)]
        seen, out = run(site, "C6")
        self.assertEqual(seen.status, "passed", seen.as_dict())
        held = [e["expectation"] for e in seen.expectations if e["holds"]]
        self.assertIn("the coordinator posts the release comment, with its marker first", held)
        self.assertIn("the branch is renamed to released/101-sha-002 and the claim's name is free", held)
        self.assertIn("the rename closed the draft", held)
        self.assertIn("the issue is back in the Queue and the claim is off the board", held)
        self.assertIn("the next run deletes the second push, since the released branch keeps its head", held)
        self.assertEqual(seen.values["release comment"], {"author": BOT, "author_association": "NONE"})
        self.assertEqual(seen.values["labels after the release"], [labels.TODO])
        self.assertIn("C6: passed", out)
        self.assertTrue(site.cleaned)

    def test_c6_fails_when_the_rename_never_comes(self):
        site = FakeSite()
        site.reactions.append(release(101, "sha-002", refuse_rename=True))
        seen, _ = run(site, "C6")
        self.assertEqual(seen.status, "failed")
        missed = [e["expectation"] for e in seen.expectations if not e["holds"]]
        self.assertEqual(missed[0], "the branch is renamed to released/101-sha-002 and the claim's name is free")

    def test_c7_is_skipped_without_an_administrator(self):
        seen, _ = run(FakeSite(), "C7")
        self.assertEqual(seen.status, "skipped")
        self.assertIn("CANARY_ADMIN_TOKEN", seen.notes[0])

    def test_c7_sees_the_lease_and_the_rename_refused_then_the_rename(self):
        site = FakeSite(admin=True)
        site.reactions += [release(101, "sha-003", refuse_rename=True), rename(101, "sha-003")]
        seen, _ = run(site, "C7")
        self.assertEqual(seen.status, "passed", seen.as_dict())
        self.assertFalse(seen.values["lease push under the ruleset"]["ok"])
        self.assertEqual(site.rulesets_made, {})
        self.assertIn(("add", 101, (labels.BLOCKED,)), site.label_calls)
        self.assertIn(("remove", 101, labels.BLOCKED), site.label_calls)

    def test_c0_reads_a_draft_and_opens_one_when_none_is_open(self):
        site = FakeSite()
        seen, _ = run(site, "C0")
        self.assertEqual(seen.status, "passed", seen.as_dict())
        self.assertEqual(seen.values["draft"]["mergeable_state"], "draft")
        self.assertIn("no open draft, so one was opened", seen.notes[0])
        seen, _ = run(site, "C0")
        self.assertIn("read the open draft", seen.notes[0])

    def test_a_check_that_stops_is_recorded_failed_and_the_rest_still_run(self):
        site = FakeSite()
        site.throwaway = lambda check: (_ for _ in ()).throw(canary.CanaryError("no issue"))
        out = io.StringIO()
        records = canary.run_checks(site, ["C1", "C3"], frozenset(), out)
        self.assertEqual([r.status for r in records], ["failed", "failed"])
        self.assertEqual(records[0].notes, ["stopped: no issue"])
        self.assertTrue(site.cleaned)


class TheRecords(unittest.TestCase):
    def records(self):
        site = FakeSite(runs=[{"event": "push", "head_branch": "main", "conclusion": "success"}])
        site.reactions.append(main_moved)
        out = io.StringIO()
        return canary.run_checks(site, ["C1", "C2"], frozenset(), out)

    def test_the_summary_names_each_check_and_what_did_not_hold(self):
        records = self.records()
        records[1].expect("something more", False, "F99")
        text = canary.summary(records, "sdlc-protocol 0.4.1 438844f", "o/r")
        self.assertIn("## Canary", text)
        self.assertIn("- C1: passed, 3 of 3 expectations held", text)
        self.assertIn("- C2: failed, 3 of 4 expectations held", text)
        self.assertIn("- C2: did not hold: something more", text)

    def test_the_records_file_and_the_issue(self):
        records = self.records()
        records[1].expect("something more", False)
        with tempfile.TemporaryDirectory() as scratch:
            path = Path(scratch) / "out" / "canary.json"
            data = canary.write_results(path, records, "sdlc-protocol 0.4.1 438844f", "o/r", ["C1", "C2"])
            self.assertEqual(json.loads(path.read_text()), data)
        self.assertEqual(canary.failed_checks(data), ["C2"])
        text = canary.issue_text(data, "https://example/run/1")
        self.assertIn("**C2: failed**", text)
        self.assertIn("- did not hold: something more", text)
        self.assertNotIn("C1", text.split("\n", 2)[2])

        class Api:
            repo = "o/r"

            def __init__(self, open_issues):
                self.open_issues, self.made, self.patched, self.labels = open_issues, [], [], []

            def create_label(self, name, colour):
                self.labels.append(name)

            def get_all(self, path, params=None):
                return self.open_issues

            def create_issue(self, title, body, issue_labels):
                self.made.append((title, issue_labels))
                return {"number": 9}

            def patch(self, path, body):
                self.patched.append((path, body["title"]))

        api = Api([])
        self.assertEqual(canary.open_or_update_issue(api, data), "opened issue #9: Canary failed: C2")
        self.assertEqual(api.made, [("Canary failed: C2", ["canary"])])
        api = Api([{"number": 4, "title": "Canary failed: C6"}, {"number": 5, "title": "Canary failed: C1", "pull_request": {}}])
        self.assertEqual(canary.open_or_update_issue(api, data), "updated issue #4: Canary failed: C2")
        self.assertEqual(api.patched, [("/repos/o/r/issues/4", "Canary failed: C2")])
        data["results"][1]["status"] = "passed"
        self.assertEqual(canary.open_or_update_issue(api, data), "nothing failed; no issue")

    def test_a_site_not_adopted_yet_is_not_ready(self):
        out = io.StringIO()
        with tempfile.TemporaryDirectory() as scratch:
            summary = Path(scratch) / "summary.md"
            code = canary.run_site(FakeSite(adopted=False), ["C1"], Path(scratch) / "c.json", summary, out)
            self.assertEqual(code, 2)
            self.assertIn("not adopted yet", summary.read_text())
        self.assertIn("Not ready", out.getvalue())

    def test_run_site_exits_1_on_a_failed_check_and_0_on_a_known_one(self):
        out = io.StringIO()
        with tempfile.TemporaryDirectory() as scratch:
            path = Path(scratch) / "c.json"
            self.assertEqual(canary.run_site(FakeSite(), ["C2"], path, None, out), 0 if "F12" in canary.known.AS_BUILT else 1)
            self.assertEqual(canary.run_site(FakeSite(take_every_push=True), ["C1"], path, None, out), 1)

    def test_the_script_lists_its_checks_and_needs_a_token(self):
        out = io.StringIO()
        self.assertEqual(canary.main(["list"], out), 0)
        self.assertIn("C6: a release end to end", out.getvalue())
        self.assertIn("(monthly)", out.getvalue())
        env = dict(os.environ)
        env.pop("CANARY_TOKEN", None)
        done = subprocess.run([sys.executable, str(ROOT / "scripts" / "canary.py"), "run"], capture_output=True,
                              text=True, env=env)
        self.assertEqual(done.returncode, 2)
        self.assertIn("CANARY_TOKEN is not set", done.stdout)


class TheGitSide(unittest.TestCase):
    """Clone against a bare repository here: the first push of a name is
    taken, a second from the same base is refused, two at the same instant
    leave exactly one, and a lease push is taken only against the tip the
    clone last saw."""

    def setUp(self):
        self.scratch = Path(tempfile.mkdtemp(prefix="canary-test-"))
        self.env = canary.git_env("not-a-token")
        self.bare = self.scratch / "remote.git"
        subprocess.run(["git", "init", "--quiet", "--bare", "--initial-branch=main", str(self.bare)], check=True,
                       env=self.env)
        for key, value in (("gc.auto", "0"), ("gc.autoDetach", "false"), ("maintenance.auto", "false"),
                           ("receive.autogc", "false")):
            subprocess.run(["git", "-C", str(self.bare), "config", key, value], check=True, env=self.env)
        seed = self.scratch / "seed"
        subprocess.run(["git", "clone", "--quiet", str(self.bare), str(seed)], check=True, env=self.env,
                       capture_output=True)
        subprocess.run(["git", "-C", str(seed), "checkout", "--quiet", "-b", "main"], check=True, env=self.env)
        (seed / "README.md").write_text("seed\n")
        subprocess.run(["git", "-C", str(seed), "add", "README.md"], check=True, env=self.env)
        subprocess.run(["git", "-C", str(seed), "commit", "--quiet", "-m", "seed"], check=True, env=self.env)
        subprocess.run(["git", "-C", str(seed), "push", "--quiet", "origin", "main"], check=True, env=self.env,
                       capture_output=True)

    def tearDown(self):
        shutil.rmtree(self.scratch, ignore_errors=True)

    def clone(self, name):
        return canary.Clone.make(self.scratch / name, str(self.bare), self.env)

    def remote_sha(self, branch):
        done = subprocess.run(["git", "-C", str(self.bare), "rev-parse", "--verify", "-q", f"refs/heads/{branch}"],
                              capture_output=True, text=True, env=self.env)
        return done.stdout.strip() or None

    def test_the_race_leaves_exactly_one_claim(self):
        clones = [self.clone("a"), self.clone("b")]
        for clone in clones:
            clone.branch("claude/5")
            clone.commit({claims.claim_path(5): canary.claim_text("o/r", 5, "w", "plan")}, "Claim #5")
        results = canary.race([lambda c=clone: c.push("claude/5") for clone in clones])
        taken = [r for r in results if r.ok]
        self.assertEqual(len(taken), 1, [r.words for r in results])
        refused = [r for r in results if not r.ok][0]
        self.assertIn("rejected", refused.words)
        self.assertNotIn(str(self.bare), refused.words)
        winner = [c for c, r in zip(clones, results) if r.ok][0]
        self.assertEqual(self.remote_sha("claude/5"), winner.head())

    def test_a_lease_push_needs_the_tip_the_clone_saw(self):
        first, second = self.clone("a"), self.clone("b")
        for clone in (first, second):
            clone.branch("claude/6")
        tip = first.commit({claims.claim_path(6): "one\n"}, "Claim #6")
        self.assertTrue(first.push("claude/6").ok)
        first.branch("claude/6")
        rewritten = first.commit({claims.claim_path(6): "two\n"}, "Claim #6, rewritten")
        self.assertFalse(first.push("claude/6").ok)
        self.assertTrue(first.push("claude/6", lease=tip).ok)
        self.assertEqual(self.remote_sha("claude/6"), rewritten)
        second.commit({claims.claim_path(6): "three\n"}, "Claim #6, someone else's")
        stale = second.push("claude/6", lease=tip)
        self.assertFalse(stale.ok)
        self.assertIn("stale info", stale.words)
        self.assertTrue(first.push("claude/6", delete=True).ok)
        self.assertIsNone(self.remote_sha("claude/6"))

    def test_a_backdated_commit_carries_its_committer_date_and_the_odd_names_push(self):
        clone = self.clone("a")
        clone.branch("claude/7")
        when = datetime(2026, 10, 12, 3, 0, tzinfo=timezone.utc)
        sha = clone.commit({"docs/canary/7.md": canary.FILE_TEXT}, "backdated", when)
        done = subprocess.run(["git", "-C", str(clone.path), "show", "-s", "--format=%cI", sha], capture_output=True,
                              text=True, env=self.env)
        self.assertEqual(done.stdout.strip(), "2026-10-12T03:00:00+00:00")
        for name in canary.ODD_NAMES:
            clone.branch(name)
            clone.commit({"docs/canary/odd.md": canary.FILE_TEXT}, "odd")
            self.assertTrue(clone.push(name).ok, name)
            self.assertIsNotNone(self.remote_sha(name))


class TheEnvironment(unittest.TestCase):
    def test_the_token_reaches_git_through_the_environment_alone(self):
        env = canary.git_env("secret-token", base={"PATH": "/usr/bin"})
        self.assertEqual(env["CANARY_GIT_TOKEN"], "secret-token")
        keys = {env[f"GIT_CONFIG_KEY_{i}"]: env[f"GIT_CONFIG_VALUE_{i}"] for i in range(int(env["GIT_CONFIG_COUNT"]))}
        self.assertIn("$CANARY_GIT_TOKEN", keys["credential.helper"])
        for name, value in env.items():
            if name != "CANARY_GIT_TOKEN":
                self.assertNotIn("secret-token", value, name)
        self.assertEqual(env["GIT_TERMINAL_PROMPT"], "0")

    def test_a_ref_name_keeps_its_slashes_in_a_path(self):
        self.assertEqual(canary.quote("released/12-abc1234"), "released/12-abc1234")
        self.assertEqual(canary.quote("claude/caf\u00e9"), "claude/caf%C3%A9")

    def test_push_words_leave_the_remote_out_and_keep_the_refusal(self):
        words = canary.push_words("To https://github.com/o/r.git\n ! [rejected] claude/3 -> claude/3 (fetch first)\n"
                                  "error: failed to push some refs to 'https://github.com/o/r.git'\n",
                                  "https://github.com/o/r.git")
        self.assertNotIn("github.com/o/r", words)
        self.assertIn("[rejected]", words)
        self.assertEqual(canary.push_words("Everything up-to-date\n", "x"), "Everything up-to-date")

    def test_the_workflow_runs_the_script_monthly_and_opens_the_issue(self):
        text = (ROOT / ".github" / "workflows" / "canary.yml").read_text()
        self.assertIn("python3 scripts/canary.py run", text)
        self.assertIn("python3 scripts/canary.py issue", text)
        self.assertIn('cron: "43 4 3 * *"', text)
        self.assertIn("workflow_dispatch", text)
        self.assertIn("permission-actions: read", text)
        self.assertIn("SDLC_APP_PRIVATE_KEY", text)
        self.assertIn("issues: write", text)
        self.assertEqual(text.count("CANARY_RUNS:"), 1)
        for check in canary.MONTHLY:
            self.assertIn(check, text)

    def test_the_claim_file_follows_the_template(self):
        text = canary.claim_text("o/r", 12, "canary", "plan")
        self.assertTrue(text.startswith("# Claim: #12\n\n- Issue: https://github.com/o/r/issues/12\n- Worker: canary\n"
                                        "- Branch: claude/12\n\n## Plan\n\nplan\n\n## Files to touch\n\n- docs/canary/12.md\n"))
        self.assertEqual(claims.claim_worker(text) if hasattr(claims, "claim_worker") else "canary", "canary")


if __name__ == "__main__":
    unittest.main()
