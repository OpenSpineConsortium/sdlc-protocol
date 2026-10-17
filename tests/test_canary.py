"""scripts/canary.py: the live canary's checks, driven against a fake site.

The checks read and write GitHub and git through the methods of
canary.Site alone, so a fake with the same methods, keeping issues, pull
requests, branches and a board page in memory and reacting to the driver
as the live board would when the test says so, drives every check end to
end; the live site's git side, Clone, is run for real against a bare
repository here, the race included, and its REST side, canary.Site's own
calls, against the library's FakeOpener, which answers each path as
GitHub's API does.
"""

import base64
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT))

import canary  # noqa: E402
from sdlc_protocol import board, claims, labels  # noqa: E402
from sdlc_protocol.adapters import github  # noqa: E402
from test_github import FakeAnswer, FakeOpener, Refusal  # noqa: E402

MAIN = "sha-main-1"
BOT = "github-actions[bot]"
# The head GitHub's activity names on the side of a push that made or
# deleted a branch.
NO_HEAD = "0" * 40
COMMIT = "438844fbaca69df9e38a59ce4aa09635e39baf09"
# A release that carries every fix on main, so a copy of it knows no
# finding: the release after 0.5.0, whose fixes stop at F20's and which
# still has F21 and F26 (known.json's fixed_after).
EVERY_FIX = "0.5.1"


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
    # The Queue lists an issue once a board run after its opening has
    # rendered, a tick of the fake's wait (rendered_through), so a page read
    # at once does not list an issue the board has not seen yet.
    queue = [[f"#{n}", f"issue {n}", labels.TODO] for n, issue in sorted(fake.issues.items())
             if issue["state"] == "open" and labels.TODO in issue["labels"] and n not in claimed
             and n <= fake.rendered_through]
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
        self.site.dates[sha] = committer_date or self.site.now()
        self.head_sha = sha
        return sha

    def push(self, branch, lease=None, delete=False):
        return self.site.push(branch, self.head_sha, lease, delete)


class FakeSite:
    """canary.Site's methods over dictionaries. reactions is what the live
    board would do, queued by the test and played one per tick of wait,
    each tick a board run: a reaction is a function of the site. The
    release reaction dates a claim as the copy's version does, by its push
    once the copy carries F8's fix (last_commit), and the clock stands
    still within a run, so a test moves it to run a check again a day or a
    month on, on the same fake, as the hold across runs needs."""

    repo = "OpenSpineConsortium/symphony-protocol-canary"
    default_branch = "main"

    def __init__(self, adopted=True, runs=None, admin=False, take_every_push=False, vendoring="0.4.1", queued=True,
                 ready_word="behind"):
        self.vendored = f"sdlc-protocol {vendoring} {COMMIT}\nhash  file\n" if adopted else None
        self.version = vendoring
        # The copy's board.yml, with or without F12's queue: max.
        self.board_yml = "concurrency:\n  group: board\n" + ("  queue: max\n" if queued else "")
        # GitHub's word for a ready pull request once computed: behind, as
        # under the canary's ruleset, which asks for an up-to-date branch,
        # for the one C2 leaves behind main; clean under one that does not.
        self.ready_word = ready_word
        self.issues, self.pulls, self.branches = {}, {}, {MAIN: None}
        self.branches = {self.default_branch: MAIN}
        self.files, self.parents, self.dates = {MAIN: {}}, {MAIN: None}, {}
        self.counter, self.next_number = 0, 100
        self.reactions, self.ticks, self.activity_events = [], 0, []
        # The clock, which a test moves between two runs on the same fake,
        # and when each push set a branch's head, by branch and head, as the
        # repository's activity records it.
        self.clock = datetime(2026, 10, 14, 4, 43, tzinfo=timezone.utc)
        self.pushed_at = {}
        # A copy with F8's fix dates a claim by its push, one before it by
        # the head's committer date (known.json's fixed_after).
        self.carries_f8 = "F8" not in canary.known_for(f"sdlc-protocol {vendoring} {COMMIT}")
        self.rendered_through = self.next_number
        self.held = []
        # A run is completed unless the test says otherwise, as the runs a
        # check reads at the end of its wait mostly are.
        self.runs = None if runs is None else [{"status": "completed", **found} for found in runs]
        self.runs_allowed = runs is not None
        self.admin = object() if admin else None
        self.rulesets_made, self.take_every_push = {}, take_every_push
        self.issues_opened, self.pulls_opened, self.extra_branches = [], [], []
        self.cleaned = False
        self.check_runs_by_sha = {}
        self.label_calls = []
        self.pokes, self.swept = [], False
        # The token's hour: expired from the start, or from a tick of wait.
        self.expired, self.expire_at_tick = False, None

    # the lists Site keeps for the clean-up
    @property
    def issues_list(self):
        return self.issues_opened

    def now(self):
        return self.clock

    def last_commit(self, branch, by_committer_date=False):
        """When the branch's last commit came, as the board reads it: when
        GitHub received the push that set its head on a copy with F8's fix,
        the head's committer date on one before it or where by_committer_date
        says the activity listed no push."""
        sha = self.branches.get(branch)
        if self.carries_f8 and not by_committer_date:
            return self.pushed_at.get((branch, sha))
        return self.dates.get(sha)

    def sleep(self, seconds):
        pass

    def out_of_time(self):
        return self.expired

    def wait(self, read, holds, budget=canary.POLL_BUDGET, every=canary.POLL_EVERY):
        for _ in range(12):
            if self.expire_at_tick is not None and self.ticks >= self.expire_at_tick:
                self.expired = True
            if self.expired:
                raise canary.OutOfTime("the run reached its token's hour")
            value = read()
            if holds(value):
                return value, True
            self.ticks += 1
            if self.reactions:
                self.reactions.pop(0)(self)
            self.rendered_through = self.next_number
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

    def record(self, kind, branch, before, after):
        """An entry of the repository's activity, as GitHub shapes one,
        with an id and the actor's id the canary's record leaves out."""
        self.activity_events.append({"id": len(self.activity_events) + 1, "activity_type": kind, "before": before,
                                     "after": after, "actor": {"login": "app[bot]", "id": 9},
                                     "timestamp": self.now().strftime("%Y-%m-%dT%H:%M:%SZ"),
                                     "ref": f"refs/heads/{branch}"})

    def push(self, branch, sha, lease=None, delete=False):
        if delete:
            self.delete_branch(branch)
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
            self.pushed_at[(branch, sha)] = self.now()
            return canary.Pushed(True, "pushed")
        if held is not None and not self.descends(sha, held) and not self.take_every_push:
            return canary.Pushed(False, "! [rejected] claude/n -> claude/n (fetch first)")
        if held != sha:
            self.record("branch_creation" if held is None else "push", branch, held or NO_HEAD, sha)
        self.branches[branch] = sha
        self.pushed_at[(branch, sha)] = self.now()
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
        return {"tools/VENDORED": self.vendored, ".github/workflows/board.yml": self.board_yml}.get(path)

    def board(self):
        return page_text(self)

    def branch_sha(self, name):
        return self.branches.get(name)

    def main_sha(self):
        return self.branches[self.default_branch]

    def delete_branch(self, name):
        """GitHub's REST delete, listed in the activity as a branch_deletion
        naming the head it took away."""
        gone = self.branches.pop(name, None)
        if gone is not None:
            self.record("branch_deletion", name, gone, NO_HEAD)

    def rename_branch(self, old, new):
        """GitHub's rename, whose activity the fake carries over to the new
        name, as the harness's model assumes (model.rename_ref) and C6
        records of the live service."""
        self.branches[new] = self.branches.pop(old)
        for entry in self.activity_events:
            if entry["ref"] == f"refs/heads/{old}":
                entry["ref"] = f"refs/heads/{new}"

    def check_runs(self, sha):
        return self.check_runs_by_sha.get(sha, [{"name": "check", "status": "completed", "conclusion": "success"}])

    def activity(self, ref, kind="force_push", newest=None):
        """The ref's entries of one kind, or of every kind where kind is
        None, oldest first, or the newest first and that many with newest,
        as canary.Site asks GitHub for them."""
        found = [e for e in self.activity_events if e["ref"] == ref and (kind is None or e["activity_type"] == kind)]
        return found if newest is None else found[::-1][:newest]

    def runs_since(self, when):
        self.runs_asked_since = when
        return self.runs

    # issues and pull requests
    def throwaway(self, check):
        self.next_number += 1
        self.issues[self.next_number] = {"labels": {labels.TODO}, "comments": [], "state": "open",
                                         "title": canary.throwaway_title(check),
                                         "created_at": self.now().strftime("%Y-%m-%dT%H:%M:%SZ")}
        self.issues_opened.append(self.next_number)
        return self.next_number

    def issue(self, number):
        found = self.issues[number]
        return {"number": number, "labels": [{"name": n} for n in sorted(found["labels"])], "state": found["state"],
                "title": found["title"], "created_at": found["created_at"]}

    def hold(self, number, pull):
        self.add_labels(number, [labels.IN_REVIEW])
        self.issues_opened = [n for n in self.issues_opened if n != number]
        self.pulls_opened = [n for n in self.pulls_opened if n != pull]
        self.held.append(number)

    def take_over(self, number, pull):
        if number not in self.issues_opened:
            self.issues_opened.append(number)
        if pull is not None and pull not in self.pulls_opened:
            self.pulls_opened.append(pull)

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
        detail["mergeable_state"] = None if found["reads"] == 1 else ("draft" if found["draft"] else self.ready_word)
        return detail

    def open_pulls(self):
        """As the API lists them, the head branch under head.ref."""
        return [{**p, "head": {"ref": p["head"]}} for p in self.pulls.values() if p["state"] == "open"]

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
        return [self.issue(n) for n, i in self.issues.items() if i["state"] == "open"]

    def clean_up(self):
        """What the live clean-up does: the run's pull requests and issues
        closed, which leaves standing a claim a check held."""
        for number in self.pulls_opened:
            self.pulls[number]["state"] = "closed"
        for number in self.issues_opened:
            self.issues[number]["state"] = "closed"
        self.cleaned = True
        return []


def release(number, head=None, refuse_rename=False, by_committer_date=False):
    """What the live board's coordinator does to the claim on issue number,
    played once a run: nothing while its issue is labelled in-review or the
    claim has gone no more than a day without a commit, when the reaction
    waits at the front for the next run; else the comment and the labels,
    then the rename, which closes the draft and is said once, or, under a
    ruleset, the refusal said once. The last commit is the fake's reading
    (last_commit): the push on a copy with F8's fix, the committer date on
    one before it, or, with by_committer_date, on a copy with the fix whose
    activity listed no push. head is the branch's head when not given."""
    def react(site):
        branch = claims.branch_of(number)
        last = site.last_commit(branch, by_committer_date)
        issue = site.issues[number]
        if labels.IN_REVIEW in issue["labels"] or last is None or site.now() - last <= claims.IDLE_AFTER:
            site.reactions.insert(0, react)
            return
        sha = head or site.branches[branch]
        issue["labels"] -= {labels.CLAIMED, labels.WORKER_PREFIX + canary.WORKER}
        site.comment(number, claims.RELEASE_MARKER + "\nreleased: ...", BOT, "NONE")
        renamed = claims.released_branch(number, sha)
        if refuse_rename:
            site.comment(number, board.RENAME_REFUSED_MARKER.format(branch=renamed) + "\nrename refused: "
                         "GitHub refused ...\n\nThese rulesets apply to the two names:\n- canary C7: non_fast_forward",
                         BOT, "NONE")
            return
        site.rename_branch(branch, renamed)
        for pull in site.pulls.values():
            if pull["head"] == branch:
                pull["state"] = "closed"
        site.comment(number, board.RELEASED_MARKER.format(branch=renamed) + "\nclaim released; renamed", BOT, "NONE")
    return react


def rename(number, head=None):
    def react(site):
        sha = head or site.branches[claims.branch_of(number)]
        site.rename_branch(claims.branch_of(number), claims.released_branch(number, sha))
    return react


def delete_second_push(number, closes=True):
    """The board's delete of the second push of a freed name, which closes
    the open draft from it, as GitHub does on a delete; with closes off,
    a GitHub that left it open."""
    def react(site):
        branch = claims.branch_of(number)
        site.delete_branch(branch)
        for pull in site.pulls.values():
            if closes and pull["head"] == branch and pull["state"] == "open":
                pull["state"] = "closed"
    return react


def main_moved(site):
    """The board's word to the pull request and the draft C2 leaves behind
    main."""
    sha7 = board.short_sha(site.main_sha())
    for behind in [p["number"] for p in site.pulls.values() if "behind" in p["title"]]:
        site.comment(behind, board.MAIN_MOVED_MARKER.format(sha=sha7) + "\nmain moved: ...", BOT, "NONE")


def nothing(site):
    """A tick on which the live board does nothing the test watches."""


def run(site, name, known_findings=frozenset()):
    out = io.StringIO()
    records = canary.run_checks(site, [name], known_findings, out)
    return records[0], out.getvalue()


def known_of(site):
    """What a run reads as known on the fake: what its copy's version still
    has, as run_site reads it from the copy's record."""
    return canary.known_for(f"sdlc-protocol {site.version} {COMMIT}")


def missed(seen):
    """The expectations that did not hold and are not known."""
    return [e["expectation"] for e in seen.expectations if not e["holds"] and not e["known"]]


def held_open(site):
    """The open issues labelled in-review, the claims the next run of a
    check finds held (canary.is_held)."""
    return sorted(n for n, issue in site.issues.items()
                  if issue["state"] == "open" and labels.IN_REVIEW in issue["labels"])


class JobStopped(BaseException):
    """A job stopped in the middle of a step, as a cancel stops it: no
    except Exception in the driver catches it, so nothing after it runs
    but the clean-up's finally."""


NOT_AT_ONCE = ("the board does not release at once a claim whose commit is dated 25 hours back, since a claim is idle "
               "by when GitHub received its push (F8)")
WAITED = "the claim an earlier run held in review waited unreleased, as a finished claim waits however long it takes"
RUN_SEEN = ("a board run after the backdated push is seen: the release, or the board listing the claim and an issue "
            "opened after the push")


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
        self.assertEqual(seen.values["checks on the behind head"], {"check": "success"})
        self.assertEqual(seen.values["the pull request left behind after the merge"], "behind")
        self.assertEqual(seen.values["the draft left behind after the merge"], "draft")
        self.assertEqual([e["expectation"] for e in seen.expectations if "main moved" in e["expectation"]],
                         ["the pull request behind main hears main moved for main's new commit",
                          "the draft behind main hears main moved for main's new commit"])
        self.assertTrue(seen.values["board.yml queues its runs"])
        self.assertEqual(seen.notes, [])
        self.assertEqual(site.runs_asked_since, site.now() - canary.RUNS_MARGIN,
                         "the runs are asked for from a minute before the merge, against the clocks' skew")

    def test_c2_owes_no_main_moved_where_github_says_the_pull_request_may_merge_as_it_stands(self):
        """F16: under a ruleset that does not ask for an up-to-date branch
        GitHub calls the pull request left behind clean, unstable or
        has_hooks, and the spec owes it nothing, so C2 does not judge the
        word and passes without it; the note says why."""
        for word in canary.AS_IT_STANDS:
            site = FakeSite(runs=[{"event": "push", "head_branch": "main", "conclusion": "success"}], ready_word=word)
            site.reactions.append(main_moved)
            seen, _ = run(site, "C2")
            self.assertEqual(seen.status, "passed", seen.as_dict())
            self.assertEqual(seen.values["the pull request left behind after the merge"], word)
            self.assertNotIn("the pull request behind main hears main moved for main's new commit",
                             [e["expectation"] for e in seen.expectations])
            self.assertTrue(any("(F16)" in note for note in seen.notes), seen.notes)

    def test_c2_without_main_moved_is_known_while_f12_and_f9_are_known(self):
        """The pull request left behind misses the word for F12 and the
        draft for F9, as under a release with neither fix."""
        site = FakeSite()
        seen, _ = run(site, "C2", frozenset({"F9", "F12"}))
        self.assertEqual(seen.status, "known")
        missed = [(e["expectation"], e["finding"], e["known"]) for e in seen.expectations if not e["holds"]]
        self.assertEqual(missed, [("the pull request behind main hears main moved for main's new commit", "F12", True),
                                  ("the draft behind main hears main moved for main's new commit", "F9", True)])
        self.assertIn("the token may not list workflow runs", seen.notes[0])
        self.assertTrue(seen.values["board.yml queues its runs"], "recorded whether the runs are read or not")
        seen, _ = run(FakeSite(), "C2")
        self.assertEqual(seen.status, "failed")

    def test_c2_fails_when_the_push_to_main_s_run_was_cancelled_whatever_is_known(self):
        """The word arrived, told by another run's state, and the run the
        push to main started was cancelled all the same: the copy's queue:
        max, F12's other half, did not keep it. That half is the template's,
        which no release of the library changes, so its miss is never read
        as known."""
        runs = [{"event": "push", "head_branch": "main", "conclusion": "cancelled"},
                {"event": "pull_request", "head_branch": "claude/102", "conclusion": "success"}]
        for queued in (False, True):
            site = FakeSite(runs=runs, queued=queued)
            site.reactions.append(main_moved)
            seen, _ = run(site, "C2", frozenset({"F12"}))
            self.assertEqual(seen.status, "failed", seen.as_dict())
            missed = [(e["expectation"], e["finding"], e["known"]) for e in seen.expectations if not e["holds"]]
            self.assertEqual(missed, [("the push to main started a board run that was not cancelled", None, False)])
            self.assertEqual(seen.values["board.yml queues its runs"], queued)
            self.assertEqual(seen.values["board runs by event"]["push on main"], {"cancelled": 1})
            self.assertEqual(any("does not queue its runs" in note for note in seen.notes), not queued)

    def test_c2_waits_for_the_push_to_main_s_run_to_finish(self):
        """The merge's runs queue behind each other, so the run the push to
        main started may still be waiting when the word arrives: C2 reads
        the runs again until it has finished, and one that has not finished
        within the budget is a miss, never a pass."""
        runs = [{"event": "push", "head_branch": "main", "status": "queued", "conclusion": None}]

        def finish(site):
            site.runs[0].update(status="completed", conclusion="success")
        site = FakeSite(runs=runs)
        site.reactions += [main_moved, nothing, finish]
        seen, _ = run(site, "C2")
        self.assertEqual(seen.status, "passed", seen.as_dict())
        self.assertEqual(seen.values["board runs by event"], {"push on main": {"success": 1}})
        site = FakeSite(runs=runs)
        site.reactions.append(main_moved)
        seen, _ = run(site, "C2")
        self.assertEqual(seen.status, "failed", seen.as_dict())
        self.assertEqual(seen.values["board runs by event"], {"push on main": {"queued": 1}})
        self.assertIn("no board run the push to main started had finished within the budget", seen.notes)

    def test_c2_does_not_judge_the_word_while_the_check_left_behind_has_not_passed(self):
        """GitHub reads a pull request whose required check failed or is
        pending as blocked, which hides how it stands to main, so the word
        is not judged then: the miss is the check's, and never read as F12."""
        for word in ("failure", None):
            site = FakeSite(runs=[{"event": "push", "head_branch": "main", "conclusion": "success"}])
            behind = claims.branch_of(site.next_number + 1)

            def check_runs(sha, site=site, behind=behind, word=word):
                if sha != site.branches.get(behind):
                    return [{"name": "check", "status": "completed", "conclusion": "success"}]
                return [{"name": "check", "status": "completed" if word else "in_progress", "conclusion": word}]
            site.check_runs = check_runs
            # The wait for the check left behind takes twelve ticks; the
            # word comes after the merge, to the draft as well.
            site.reactions += [nothing] * 12 + [main_moved]
            seen, _ = run(site, "C2", frozenset({"F12"}))
            self.assertEqual(seen.status, "failed", seen.as_dict())
            missed = [e["expectation"] for e in seen.expectations if not e["holds"]]
            self.assertEqual(missed, ["the check passed on the pull request left behind, so GitHub can say how it "
                                      "stands to main"])
            self.assertNotIn("the pull request behind main hears main moved for main's new commit",
                             [e["expectation"] for e in seen.expectations])
            self.assertIn(f"stood at {word or 'in_progress'}", " ".join(seen.notes))

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
        site.mind_the_clock = lambda: None
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
        """On a copy before F8's fix, as the canary's 0.4.1 is, the board
        releases the backdated push at once, which misses F8's expectation
        as known, and the release is followed in the same run; nothing is
        held for a later one."""
        site = FakeSite(runs=[{"event": "pull_request", "head_branch": "claude/101", "conclusion": "success"}])
        self.assertFalse(site.carries_f8)
        site.reactions += [release(101, "sha-002"), delete_second_push(101)]
        seen, out = run(site, "C6", known_of(site))
        self.assertEqual(seen.status, "known", seen.as_dict())
        self.assertEqual(missed(seen), [])
        self.assertEqual([(e["expectation"], e["finding"]) for e in seen.expectations if not e["holds"]],
                         [(NOT_AT_ONCE, "F8")])
        self.assertTrue(seen.values["released at once"])
        self.assertEqual(site.held, [])
        self.assertNotIn("held claim", seen.values)
        self.assertEqual(site.issues[101]["state"], "closed", "the run's clean-up closed its claim")
        self.assertNotIn(labels.IN_REVIEW, site.issues[101]["labels"])
        held = [e["expectation"] for e in seen.expectations if e["holds"]]
        self.assertIn("the coordinator posts the release comment, with its marker first", held)
        self.assertIn("the branch is renamed to released/101-sha-002 and the claim's name is free", held)
        self.assertIn("the rename closed the draft", held)
        self.assertIn("the issue is back in the Queue and the claim is off the board", held)
        self.assertIn("the next run deletes the second push, since the released branch keeps its head", held)
        self.assertIn("the delete closed the draft opened from the second push", held)
        self.assertEqual(len(site.pulls_opened), 2)
        self.assertEqual([p["state"] for p in site.pulls.values()], ["closed", "closed"])
        self.assertIn("the comment moved the draft's updated_at", held)
        self.assertIn("the push moved the draft's updated_at", held)
        self.assertEqual(list(seen.values["draft updated_at"]), ["after the open", "after the comment", "after the push"])
        self.assertEqual(seen.values["release comment"], {"author": BOT, "author_association": "NONE"})
        self.assertEqual(seen.values["labels after the release"], [labels.TODO])
        self.assertIn("C6: known", out)
        self.assertTrue(site.cleaned)

    def test_c6_fails_when_the_delete_leaves_the_draft_open(self):
        """The harness's model closes the open pull request whose head a
        delete removes, as the documentation says of a rename and the web
        UI's page of a delete; a GitHub that left it open would fail the
        check and the assumption with it."""
        site = FakeSite(runs=[{"event": "pull_request", "head_branch": "claude/101", "conclusion": "success"}])
        site.reactions += [release(101, "sha-002"), delete_second_push(101, closes=False)]
        seen, _ = run(site, "C6", known_of(site))
        self.assertEqual(seen.status, "failed", seen.as_dict())
        self.assertEqual(missed(seen), ["the delete closed the draft opened from the second push"])

    def test_c6_fails_when_the_board_s_own_push_started_a_run(self):
        runs = [{"event": "pull_request", "head_branch": "claude/101", "conclusion": "success"},
                {"event": "push", "head_branch": "board", "conclusion": "success"}]
        site = FakeSite(runs=runs)
        site.reactions += [release(101, "sha-002"), delete_second_push(101)]
        seen, _ = run(site, "C6", known_of(site))
        self.assertEqual(seen.status, "failed", seen.as_dict())
        self.assertEqual(missed(seen), ["the board's own pushes started no board run"])
        self.assertEqual(seen.values["board runs by event"]["push on board"], {"success": 1})

    def test_c6_fails_when_the_comment_moves_nothing(self):
        site = FakeSite(runs=[])
        comment = site.comment

        def comment_that_moves_nothing(number, text, author="app[bot]", association="NONE"):
            before = site.pulls[number]["updated_at"] if number in site.pulls else None
            comment(number, text, author, association)
            if before is not None:
                site.pulls[number]["updated_at"] = before
        site.comment = comment_that_moves_nothing
        site.reactions += [release(101, "sha-002"), delete_second_push(101)]
        seen, _ = run(site, "C6", known_of(site))
        self.assertEqual(seen.status, "failed", seen.as_dict())
        self.assertEqual(missed(seen), ["the comment moved the draft's updated_at"])

    def test_c6_names_a_refused_rename_and_asks_nothing_that_depends_on_it(self):
        site = FakeSite()
        site.reactions.append(release(101, "sha-002", refuse_rename=True))
        seen, _ = run(site, "C6", known_of(site))
        self.assertEqual(seen.status, "failed")
        self.assertEqual(missed(seen)[0], "the branch is renamed to released/101-sha-002 and the claim's name is free")
        asked = [e["expectation"] for e in seen.expectations]
        for depends in ("the rename closed the draft", "the board says once on the issue where the branch went",
                        "the freed name takes a push at the released head"):
            self.assertNotIn(depends, asked)
        self.assertNotIn("second push at the released head", seen.values)
        said = [note for note in seen.notes if note.startswith("the rename did not come")]
        self.assertEqual(len(said), 1)
        self.assertIn("the board says GitHub refused the rename", said[0])
        self.assertIn(canary.RULESET, said[0])
        site = FakeSite()
        number = site.throwaway("C6")
        self.assertIn("the board said nothing of a refusal", canary.why_not_renamed(site, number, "released/1-abc1234"))

    def test_c6_records_the_activity_around_the_release_and_judges_none_of_it(self):
        """Once the rename is seen, C6 records the newest entries of both
        names' activity, then the freed name's after its second push and
        after the wait for the board's delete of it, each entry with only
        the fields the model reasons about and the actor by login; these
        are observations, so no expectation reads them, and a note says
        what each settles. The fake carries a rename's entries over to the
        new name, as the model assumes."""
        site = FakeSite(runs=[])
        site.reactions += [release(101, "sha-002"), delete_second_push(101)]
        seen, _ = run(site, "C6", known_of(site))
        self.assertEqual(seen.status, "known", seen.as_dict())
        self.assertEqual(missed(seen), [])
        self.assertEqual([e["expectation"] for e in seen.expectations if "activity" in e["expectation"]], [])
        at = "2026-10-14T04:43:00Z"

        def entry(kind, before, after, ref="refs/heads/claude/101"):
            return {"activity_type": kind, "timestamp": at, "before": before, "after": after, "ref": ref,
                    "actor": "app[bot]"}
        renamed = "refs/heads/released/101-sha-002"
        self.assertEqual(seen.values["activity after the rename"], {
            "read at": at,
            renamed: [entry("push", "sha-001", "sha-002", renamed),
                      entry("branch_creation", NO_HEAD, "sha-001", renamed)],
            "refs/heads/claude/101": []})
        self.assertEqual(seen.values["activity after the second push"], {
            "read at": at, "refs/heads/claude/101": [entry("branch_creation", NO_HEAD, "sha-002")]})
        self.assertEqual(seen.values["activity after the wait for the delete"], {
            "read at": at, "refs/heads/claude/101": [entry("branch_deletion", "sha-002", NO_HEAD),
                                                     entry("branch_creation", NO_HEAD, "sha-002")]})
        recorded = [note for note in seen.notes if note.startswith("the activity after")]
        self.assertEqual(len(recorded), 3, seen.notes)
        for note, record in zip(recorded, canary.ACTIVITY_SETTLES):
            self.assertTrue(note.startswith(f"the {record} is recorded, an observation and no expectation"), note)
            self.assertIn(canary.ACTIVITY_SETTLES[record], note)
        json.dumps(seen.as_dict())

    def test_c6_notes_a_refused_read_of_the_activity_and_fails_nothing(self):
        """A read of the activity GitHub refuses is a note and fails
        nothing, the record holding what GitHub said in place of the
        entries; the flow goes on to the delete as before, and only what
        was read says what it settles. A 401 stops the check, as every
        wait does, since the token itself is refused."""
        site = FakeSite(runs=[])
        site.reactions += [release(101, "sha-002"), delete_second_push(101)]
        activity = site.activity

        def refused_for_the_claim(ref, kind="force_push", newest=None):
            if ref == "refs/heads/claude/101":
                raise canary.GitHubError("GET /repos/o/r/activity: HTTP 403", 403)
            return activity(ref, kind, newest)
        site.activity = refused_for_the_claim
        seen, _ = run(site, "C6", known_of(site))
        self.assertEqual(seen.status, "known", seen.as_dict())
        self.assertEqual(missed(seen), [])
        self.assertIn("the delete closed the draft opened from the second push",
                      [e["expectation"] for e in seen.expectations if e["holds"]])
        refused = "not read: GET /repos/o/r/activity: HTTP 403"
        self.assertEqual(seen.values["activity after the rename"]["refs/heads/claude/101"], refused)
        self.assertEqual(len(seen.values["activity after the rename"]["refs/heads/released/101-sha-002"]), 2)
        self.assertEqual(seen.values["activity after the second push"]["refs/heads/claude/101"], refused)
        self.assertEqual(seen.values["activity after the wait for the delete"]["refs/heads/claude/101"], refused)
        not_read = [note for note in seen.notes if " was not read for refs/heads/claude/101: " in note]
        self.assertEqual(len(not_read), 3, seen.notes)
        self.assertTrue(all(note.endswith("it is an observation, so nothing failed") for note in not_read))
        recorded = [note for note in seen.notes if " is recorded, an observation" in note]
        self.assertEqual(len(recorded), 1, "only the read GitHub answered says what it settles")
        self.assertTrue(recorded[0].startswith("the activity after the rename"))

        site = FakeSite(runs=[])
        site.reactions += [release(101, "sha-002"), delete_second_push(101)]
        site.activity = lambda ref, kind="force_push", newest=None: (_ for _ in ()).throw(
            canary.GitHubError("GET /repos/o/r/activity: HTTP 401", 401))
        seen, _ = run(site, "C6", known_of(site))
        self.assertEqual(seen.status, "failed")
        self.assertIn("stopped: GET /repos/o/r/activity: HTTP 401", seen.notes)
        self.assertNotIn("second push at the released head", seen.values)

    def test_the_activity_s_record_keeps_the_model_s_fields_of_the_newest_entries(self):
        many = [{"id": n, "activity_type": "push", "timestamp": f"t{n}", "before": "a", "after": "b",
                 "ref": "refs/heads/claude/5", "actor": {"login": "w", "id": 1}, "node_id": "x"} for n in range(9)]
        kept = canary.activity_entries(["no entry"] + many)
        self.assertEqual([e["timestamp"] for e in kept], [f"t{n}" for n in range(canary.ACTIVITY_SEEN)])
        self.assertEqual(sorted(kept[0]), ["activity_type", "actor", "after", "before", "ref", "timestamp"])
        self.assertEqual(kept[0]["actor"], "w")
        self.assertEqual(canary.activity_entries([{"activity_type": "push", "actor": None}])[0]["actor"], None)
        self.assertEqual(canary.activity_entries({"message": "Not Found"}), [])
        self.assertEqual(canary.activity_entries(None), [])

    def first_f8_run(self, check="C6", **kwargs):
        """The first run of the check on a copy with F8's fix, with the
        board's release of the claim it holds queued for a later run; the
        fake, its record and the run's output."""
        site = FakeSite(vendoring=EVERY_FIX, **kwargs)
        self.assertTrue(site.carries_f8)
        self.assertEqual(known_of(site), frozenset())
        if check == "C6":
            site.reactions += [release(101), delete_second_push(101)]
        else:
            site.reactions += [release(101, refuse_rename=True), rename(101)]
        seen, out = run(site, check, known_of(site))
        return site, seen, out

    def test_c6_on_a_copy_with_f8_holds_the_backdated_claim_and_a_run_a_day_on_releases_it(self):
        """On a copy with F8's fix the board dates the backdated push by
        when GitHub received it, so the first run sees it not released,
        F8's own check, and holds the claim in review; a run a month on,
        as the monthly schedule makes it, takes the label off and follows
        the release by idleness end to end, and holds its own fresh claim
        in its place."""
        runs = [{"event": "issues", "head_branch": "main", "conclusion": "success"}]
        site, seen, out = self.first_f8_run(runs=runs)
        self.assertEqual(seen.status, "passed", seen.as_dict())
        held = [e["expectation"] for e in seen.expectations if e["holds"]]
        self.assertIn(NOT_AT_ONCE, held)
        self.assertIn(RUN_SEEN, held)
        self.assertNotIn("the coordinator posts the release comment, with its marker first",
                         [e["expectation"] for e in seen.expectations], "no claim an earlier run held stands")
        self.assertFalse(seen.values["released at once"])
        self.assertEqual(seen.values["held for a later run"], 101)
        self.assertTrue(any(note.startswith("no claim an earlier run of C6 held stands") for note in seen.notes),
                        seen.notes)
        self.assertEqual(site.held, [101])
        self.assertEqual((site.issues[101]["state"], site.pulls[102]["state"]), ("open", "open"),
                         "the clean-up leaves the held claim and its draft")
        self.assertIn(labels.IN_REVIEW, site.issues[101]["labels"])
        self.assertEqual(site.issues[103]["state"], "closed", "the mark closes with its run")
        self.assertEqual(site.branches["claude/101"], "sha-002")

        site.clock += timedelta(days=30)
        seen, out = run(site, "C6", known_of(site))
        self.assertEqual(seen.status, "passed", seen.as_dict())
        self.assertEqual(seen.values["held claim"], {"issue": 101, "made": "2026-10-14T04:43:00Z", "due": True})
        held = [e["expectation"] for e in seen.expectations if e["holds"]]
        for expectation in (WAITED, NOT_AT_ONCE, "the coordinator posts the release comment, with its marker first",
                            "the labels claimed and the worker's are off the issue",
                            "the branch is renamed to released/101-sha-002 and the claim's name is free",
                            "the rename closed the draft", "the board says once on the issue where the branch went",
                            "the issue is back in the Queue and the claim is off the board",
                            "the board's own pushes started no board run",
                            "the freed name takes a push at the released head",
                            "the next run deletes the second push, since the released branch keeps its head",
                            "the delete closed the draft opened from the second push"):
            self.assertIn(expectation, held)
        self.assertEqual(site.runs_asked_since, site.now() - canary.RUNS_MARGIN,
                         "the runs are read from just before the label came off the held claim")
        self.assertEqual(seen.values["held for a later run"], 104)
        self.assertEqual(site.held, [101, 104])
        self.assertEqual(site.issues[101]["state"], "closed", "the claim followed closes with the run that followed it")
        self.assertEqual(site.issues[104]["state"], "open")
        self.assertIn(labels.IN_REVIEW, site.issues[104]["labels"])
        self.assertEqual(site.branches.get("released/101-sha-002"), "sha-002")

    def test_c6_leaves_a_held_claim_not_a_day_old_and_holds_no_other(self):
        """A run sooner than HELD_FOR after the one that held a claim, as a
        run by hand the same day is, leaves that claim standing, follows
        no release, and closes its own fresh claim with the run, so one
        claim at most is held."""
        site, _, _ = self.first_f8_run()
        site.clock += timedelta(hours=3)
        seen, _ = run(site, "C6", known_of(site))
        self.assertEqual(seen.status, "passed", seen.as_dict())
        self.assertFalse(seen.values["held claim"]["due"])
        self.assertTrue(any("less than 25 hours ago" in note for note in seen.notes), seen.notes)
        self.assertNotIn("held for a later run", seen.values)
        self.assertEqual(site.held, [101])
        self.assertEqual((site.issues[101]["state"], site.issues[104]["state"]), ("open", "closed"))
        self.assertIn(labels.IN_REVIEW, site.issues[101]["labels"])
        self.assertNotIn("the coordinator posts the release comment, with its marker first",
                         [e["expectation"] for e in seen.expectations])

    def test_c6_fails_when_the_claim_held_in_review_was_released_while_it_waited(self):
        """A finished claim waits however long it takes: one the board
        released while it was held fails the check, closes with the run,
        and the run holds its fresh claim in its place."""
        site, _, _ = self.first_f8_run()
        site.reactions.clear()
        site.issues[101]["labels"].discard(labels.CLAIMED)
        site.comment(101, claims.RELEASE_MARKER + "\nreleased: ...", BOT, "NONE")
        site.clock += timedelta(days=30)
        seen, _ = run(site, "C6", known_of(site))
        self.assertEqual(seen.status, "failed", seen.as_dict())
        self.assertEqual(missed(seen), [WAITED])
        self.assertEqual(site.issues[101]["state"], "closed")
        self.assertEqual(site.held, [101, 104])

    def test_c6_whose_clone_of_the_held_claim_fails_leaves_that_claim_and_holds_no_other(self):
        """The clone that pushes the held claim's head again is made before
        the fresh claim is held: one that fails once the fresh claim was
        seen not released stops the check with the held claim still held
        and the fresh one closed with the run, so the next run follows the
        held claim and one claim is held after each run, never two."""
        site, _, _ = self.first_f8_run()
        site.clock += timedelta(days=30)
        clone = site.clone
        mark = canary.throwaway_title("C6 mark")

        def clone_that_fails_once_the_fresh_claim_is_judged():
            if any(i["title"] == mark and i["state"] == "open" for i in site.issues.values()):
                raise canary.CanaryError("git clone: the remote end hung up unexpectedly")
            return clone()
        site.clone = clone_that_fails_once_the_fresh_claim_is_judged
        seen, _ = run(site, "C6", known_of(site))
        self.assertEqual(seen.status, "failed", seen.as_dict())
        self.assertEqual(seen.notes[-1], "stopped: git clone: the remote end hung up unexpectedly")
        self.assertIn(NOT_AT_ONCE, [e["expectation"] for e in seen.expectations if e["holds"]],
                      "the clone failed after the fresh claim was seen not released")
        self.assertNotIn("held for a later run", seen.values)
        self.assertEqual(site.held, [101])
        self.assertEqual(held_open(site), [101])
        self.assertEqual((site.issues[104]["state"], site.pulls[105]["state"]), ("closed", "closed"),
                         "the fresh claim and its draft close with the run")
        self.assertNotIn(labels.IN_REVIEW, site.issues[104]["labels"])

        site.clone = clone
        site.clock += timedelta(days=30)
        seen, _ = run(site, "C6", known_of(site))
        self.assertEqual(seen.status, "passed", seen.as_dict())
        self.assertEqual(seen.values["held claim"]["issue"], 101)
        self.assertIn("the delete closed the draft opened from the second push",
                      [e["expectation"] for e in seen.expectations if e["holds"]])
        self.assertEqual(site.issues[101]["state"], "closed")
        self.assertEqual(held_open(site), [107])

    def test_c6_on_a_copy_with_f8_fails_when_the_backdated_push_is_released_at_once(self):
        """With the fix, a release at once says the board read the claim by
        its committer date, as where the activity had not listed the push
        by the second read: not known on such a copy, so the check fails,
        and the release is still followed end to end; nothing is held."""
        site = FakeSite(vendoring=EVERY_FIX, runs=[])
        site.reactions += [release(101, by_committer_date=True), delete_second_push(101)]
        seen, _ = run(site, "C6", known_of(site))
        self.assertEqual(seen.status, "failed", seen.as_dict())
        self.assertEqual(missed(seen), [NOT_AT_ONCE])
        self.assertTrue(seen.values["released at once"])
        self.assertIn("the delete closed the draft opened from the second push",
                      [e["expectation"] for e in seen.expectations if e["holds"]])
        self.assertEqual(site.held, [])

    def test_c6_judges_nothing_and_holds_nothing_when_no_board_run_is_seen(self):
        """Not released is judged only once a board run that read the
        repository after the push is seen; a board that never lists the
        mark misses that expectation alone, and the claim is not held."""
        site = FakeSite(vendoring=EVERY_FIX)
        site.board = lambda: page_text(site).split(board.QUEUE_HEADING)[0]
        seen, _ = run(site, "C6", known_of(site))
        self.assertEqual(seen.status, "failed", seen.as_dict())
        self.assertEqual(missed(seen), [RUN_SEEN])
        self.assertNotIn(NOT_AT_ONCE, [e["expectation"] for e in seen.expectations])
        self.assertIsNone(seen.values["released at once"])
        self.assertEqual(site.held, [])
        self.assertEqual(site.issues[101]["state"], "closed")

    def test_c7_is_skipped_without_an_administrator(self):
        seen, _ = run(FakeSite(), "C7")
        self.assertEqual(seen.status, "skipped")
        self.assertIn("CANARY_ADMIN_TOKEN", seen.notes[0])

    def test_c7_sees_the_lease_and_the_rename_refused_then_the_rename(self):
        """On a copy before F8's fix the backdated push is released at once,
        F8's known miss, and the rename refused under the ruleset is that of
        the fresh claim, in the same run."""
        site = FakeSite(admin=True)
        site.reactions += [release(101, "sha-003", refuse_rename=True), rename(101, "sha-003")]
        seen, _ = run(site, "C7", known_of(site))
        self.assertEqual(seen.status, "known", seen.as_dict())
        self.assertEqual(missed(seen), [])
        self.assertEqual(site.held, [])
        self.assertFalse(seen.values["lease push under the ruleset"]["ok"])
        self.assertEqual(site.rulesets_made, {})
        self.assertIn(("add", 101, (labels.BLOCKED,)), site.label_calls)
        self.assertIn(("remove", 101, labels.BLOCKED), site.label_calls)

    def test_c7_on_a_copy_with_f8_holds_its_claim_and_a_later_run_sees_the_rename_refused(self):
        """With F8's fix the backdated push is not released, so the first
        run checks the lease push under the ruleset and holds its claim;
        the next run by hand a day on makes the ruleset again, takes the
        label off the held claim, sees its release, the rename refused and
        said once, and the rename once the ruleset is gone."""
        site, seen, _ = self.first_f8_run("C7", admin=True)
        self.assertEqual(seen.status, "passed", seen.as_dict())
        self.assertFalse(seen.values["lease push under the ruleset"]["ok"])
        self.assertIn(NOT_AT_ONCE, [e["expectation"] for e in seen.expectations if e["holds"]])
        self.assertNotIn("the coordinator releases the claim under the ruleset",
                         [e["expectation"] for e in seen.expectations])
        self.assertEqual(site.held, [101])
        self.assertEqual(site.rulesets_made, {})
        self.assertNotIn(("add", 101, (labels.BLOCKED,)), site.label_calls)

        site.clock += timedelta(hours=26)
        seen, _ = run(site, "C7", known_of(site))
        self.assertEqual(seen.status, "passed", seen.as_dict())
        held = [e["expectation"] for e in seen.expectations if e["holds"]]
        for expectation in (WAITED, "the ruleset refuses a lease push onto the claim", NOT_AT_ONCE,
                            "the coordinator releases the claim under the ruleset",
                            "the board says once that GitHub refused the rename", "the saying names the ruleset",
                            "the claim's name is still taken",
                            "once the ruleset is gone, the next run renames the branch"):
            self.assertIn(expectation, held)
        self.assertEqual(seen.values["held claim"]["issue"], 101)
        self.assertEqual(site.held, [101, 104])
        self.assertEqual(site.rulesets_made, {})
        self.assertIn(("add", 101, (labels.BLOCKED,)), site.label_calls)
        self.assertEqual(site.branches.get("released/101-sha-003"), "sha-003")
        self.assertEqual(site.issues[101]["state"], "closed")

    def test_a_run_that_finds_two_held_claims_follows_the_oldest_and_closes_the_other(self):
        """A run stopped between holding its fresh claim and taking
        in-review off the claim it follows, as a cancelled job is, leaves
        two claims of the check held, in C6 and C7 alike; the next run
        follows the oldest, closes the other with the run, issue and draft,
        and holds its own fresh claim, so one is held again, where
        following the oldest alone kept two for good."""
        for check, later, options in (("C6", timedelta(days=30), {}), ("C7", timedelta(hours=26), {"admin": True})):
            with self.subTest(check=check):
                site, _, _ = self.first_f8_run(check, **options)
                site.clock += later
                take_over = site.take_over

                def stopped(number, pull):
                    raise JobStopped(f"the job was cancelled before #{number} was taken over")
                site.take_over = stopped
                with self.assertRaises(JobStopped):
                    run(site, check, known_of(site))
                site.take_over = take_over
                self.assertEqual(site.held, [101, 104])
                self.assertEqual(held_open(site), [101, 104])
                self.assertIn(labels.IN_REVIEW, site.issues[101]["labels"], "the label never came off")

                site.clock += later
                seen, _ = run(site, check, known_of(site))
                self.assertEqual(seen.status, "passed", seen.as_dict())
                self.assertEqual(seen.values["held claim"]["issue"], 101)
                closed = [note for note in seen.notes if note.startswith("more than one claim")]
                self.assertEqual(len(closed), 1, seen.notes)
                self.assertTrue(closed[0].endswith("the others close with this run: #104"), closed[0])
                self.assertEqual((site.issues[104]["state"], site.pulls[105]["state"]), ("closed", "closed"),
                                 "the extra held claim and its draft close with the run")
                self.assertEqual(site.issues[101]["state"], "closed", "the claim followed closes with the run")
                self.assertEqual(site.held, [101, 104, 107])
                self.assertEqual(held_open(site), [107])

    def test_c0_reads_a_draft_and_opens_one_when_none_is_open(self):
        site = FakeSite()
        seen, _ = run(site, "C0")
        self.assertEqual(seen.status, "passed", seen.as_dict())
        self.assertEqual(seen.values["draft"]["mergeable_state"], "draft")
        self.assertIn("no open draft, so one was opened", seen.notes[0])
        self.assertEqual([p["state"] for p in site.pulls.values()], ["closed"], "the run closed the draft it opened")
        draft = site.open_pull("claude/9", "Someone's draft", "Closes #9", True)
        seen, _ = run(site, "C0")
        self.assertIn(f"read the open draft #{draft}", seen.notes[0])

    def test_a_check_that_stops_is_recorded_failed_and_the_rest_still_run(self):
        site = FakeSite()
        site.throwaway = lambda check: (_ for _ in ()).throw(canary.CanaryError("no issue"))
        out = io.StringIO()
        records = canary.run_checks(site, ["C1", "C3"], frozenset(), out)
        self.assertEqual([r.status for r in records], ["failed", "failed"])
        self.assertEqual(records[0].notes, ["stopped: no issue"])
        self.assertTrue(site.cleaned)

    def test_a_refused_token_stops_the_checks_that_follow(self):
        """A 401 is the token itself refused: every later call would be
        refused too, so the checks after it are not run, and nothing reads
        as the library failing."""
        site = FakeSite()
        site.throwaway = lambda check: (_ for _ in ()).throw(canary.GitHubError("POST /x: HTTP 401", 401))
        records = canary.run_checks(site, ["C1", "C3"], frozenset(), io.StringIO())
        self.assertEqual([r.status for r in records], ["failed", "skipped"])
        self.assertIn("GitHub refused the token itself (401)", records[0].notes[1])
        self.assertEqual(records[1].notes, ["not run: GitHub refused the token in an earlier check (401)"])
        self.assertTrue(site.cleaned)

    def test_the_token_s_hour_stops_the_checks_and_leaves_the_clean_up(self):
        site = FakeSite()
        site.expire_at_tick = 0
        records = canary.run_checks(site, ["C1", "C3"], frozenset(), io.StringIO())
        self.assertEqual([r.status for r in records], ["failed", "skipped"])
        self.assertEqual(records[0].notes, ["stopped: the run reached its token's hour"])
        self.assertEqual(records[1].notes, ["not run: an earlier check reached the token's hour"])
        self.assertTrue(site.cleaned)
        site = FakeSite()
        site.expired = True
        records = canary.run_checks(site, ["C1"], frozenset(), io.StringIO())
        self.assertEqual(records[0].status, "skipped")
        self.assertEqual(records[0].notes, ["not run: the run reached its token's hour before this check started"])


class TheRecords(unittest.TestCase):
    def records(self):
        site = FakeSite(runs=[{"event": "push", "head_branch": "main", "conclusion": "success"}])
        site.reactions.append(main_moved)
        out = io.StringIO()
        return canary.run_checks(site, ["C1", "C2"], frozenset(), out)

    def test_the_summary_names_each_check_and_what_did_not_hold(self):
        records = self.records()
        records[1].expect("something more", False, "F99")
        text = canary.summary(records, "sdlc-protocol 0.4.1 438844f", "o/r", frozenset({"F12", "F2", "F9"}))
        self.assertIn("## Canary", text)
        self.assertIn("vendoring sdlc-protocol 0.4.1 438844f. A failed expectation is known for: F2 (fixed after "
                      "0.4.1); F9 (fixed after 0.4.1); F12 (fixed after 0.4.1).", text)
        self.assertIn("- C1: passed, 3 of 3 expectations held", text)
        self.assertIn("- C2: failed, 5 of 6 expectations held", text)
        self.assertIn("- C2: did not hold: something more", text)
        self.assertIn("known for: none.", canary.summary(records, "v", "o/r"))

    def test_a_missed_expectation_is_marked_known_by_itself(self):
        """A check can miss a known expectation and a new one: the known
        one says so on its line, the new one does not, and the check is
        failed; a check that missed only known ones is known."""
        seen = canary.Seen("C2", frozenset({"F12"}))
        seen.expect("the word", False, "F12")
        seen.expect("the run", False)
        seen.expect("held", True, "F12")
        self.assertEqual(seen.status, "failed")
        self.assertEqual([e["known"] for e in seen.expectations], [True, False, False])
        text = canary.summary([seen], "v", "o/r", frozenset({"F12"}))
        self.assertIn("- C2: did not hold: the word (known, F12)\n", text)
        self.assertIn("- C2: did not hold: the run\n", text)
        with tempfile.TemporaryDirectory() as scratch:
            data = canary.write_results(Path(scratch) / "c.json", [seen], "v", "o/r", ["C2"], frozenset({"F12"}))
        issue = canary.issue_text(data, None)
        self.assertIn("- did not hold: the word (known, F12)\n", issue)
        self.assertIn("- did not hold: the run\n", issue)
        only_known = canary.Seen("C2", frozenset({"F12"}))
        only_known.expect("the word", False, "F12")
        self.assertEqual(only_known.status, "known")

    def test_a_failed_expectation_is_known_for_what_the_vendored_release_still_has(self):
        """The canary runs the release the canary repository vendors, not
        main: a finding fixed on main after that version is known there
        until a release carries the fix (known.json, fixed_after)."""
        first_line = f"sdlc-protocol 0.4.1 {COMMIT}"
        self.assertEqual(canary.vendored_version(first_line), "0.4.1")
        self.assertEqual(canary.vendored_version("sdlc-protocol 0.5.0 unknown"), "0.5.0")
        self.assertIsNone(canary.vendored_version("unreadable"))
        self.assertIsNone(canary.vendored_version(None))
        self.assertEqual(canary.known_for(first_line),
                         canary.known.AS_BUILT | {"F2", "F4", "F7", "F8", "F9", "F12", "F13", "F14", "F15", "F17",
                                                  "F18", "F19", "F20", "F21", "F25", "F26"})
        self.assertEqual(canary.known.AS_BUILT, frozenset(), "no finding is read as built since F2's fix")
        self.assertEqual(canary.known_for(f"sdlc-protocol 0.5.0 {COMMIT}"), canary.known.AS_BUILT | {"F21", "F25", "F26"})
        self.assertEqual(canary.known_for(f"sdlc-protocol {EVERY_FIX} {COMMIT}"), canary.known.AS_BUILT)
        self.assertEqual(canary.known_for("unreadable"), canary.known.AS_BUILT)
        self.assertEqual(canary.known_words(frozenset({"F12", "F2", "F13", "F99"})),
                         "F2 (fixed after 0.4.1); F12 (fixed after 0.4.1); F13 (fixed after 0.4.1); "
                         "F99 (fixed after an unrecorded version)")
        self.assertEqual(canary.known_words(frozenset({"F12", "F2"}) | canary.known.AS_BUILT),
                         "F2 (fixed after 0.4.1); F12 (fixed after 0.4.1)")
        out = io.StringIO()
        with tempfile.TemporaryDirectory() as scratch:
            path = Path(scratch) / "c.json"
            self.assertEqual(canary.run_site(FakeSite(), ["C2"], path, None, out), 0)
            data = json.loads(path.read_text())
            self.assertEqual(data["known_for"],
                             ["F2", "F4", "F7", "F8", "F9", "F12", "F13", "F14", "F15", "F17", "F18", "F19",
                              "F20", "F21", "F25", "F26"])
            self.assertEqual(data["results"][0]["status"], "known")
            self.assertIn("F12 (fixed after 0.4.1)", out.getvalue())
            self.assertEqual(canary.run_site(FakeSite(vendoring="0.5.0"), ["C2"], path, None, out), 1)
            data = json.loads(path.read_text())
            self.assertEqual(data["known_for"], ["F21", "F25", "F26"], "0.5.0 carries every fix up to F20's")
            self.assertEqual(data["results"][0]["status"], "failed")
            self.assertEqual(canary.run_site(FakeSite(vendoring=EVERY_FIX), ["C2"], path, None, out), 1)
            data = json.loads(path.read_text())
            self.assertEqual(data["known_for"], [], "the release after 0.5.0 carries every fix, F26's the last")
            self.assertEqual(data["results"][0]["status"], "failed")
            text = canary.issue_text(data, None)
            self.assertNotIn("This run reads a missed expectation as known", text)

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

    def test_a_first_read_refused_is_not_ready(self):
        """A token that does not reach the repository is the run's set-up,
        said in the summary with exit 2, not a check failing."""
        site = FakeSite()
        site.main_file = lambda path: (_ for _ in ()).throw(canary.GitHubError("GET /x: HTTP 403", 403))
        out = io.StringIO()
        with tempfile.TemporaryDirectory() as scratch:
            summary = Path(scratch) / "summary.md"
            self.assertEqual(canary.run_site(site, ["C1"], Path(scratch) / "c.json", summary, out), 2)
            self.assertIn("Not ready: GitHub refused the first read", summary.read_text())
        self.assertFalse(site.swept, "no check ran")

    def test_the_summary_takes_a_character_outside_ascii(self):
        with tempfile.TemporaryDirectory() as scratch:
            path = Path(scratch) / "summary.md"
            canary.append(path, "git said claude/caf\u00e9\n")
            self.assertEqual(path.read_bytes(), b"git said claude/caf\\xe9\n")

    def test_run_site_exits_1_on_a_failed_check_and_0_on_a_known_one(self):
        out = io.StringIO()
        with tempfile.TemporaryDirectory() as scratch:
            path = Path(scratch) / "c.json"
            self.assertEqual(canary.run_site(FakeSite(), ["C2"], path, None, out), 0)
            self.assertEqual(canary.run_site(FakeSite(vendoring="0.5.0"), ["C2"], path, None, out), 1)
            self.assertEqual(canary.run_site(FakeSite(take_every_push=True), ["C1"], path, None, out), 1)

    def test_the_script_lists_its_checks_and_needs_a_token(self):
        out = io.StringIO()
        self.assertEqual(canary.main(["list"], out), 0)
        self.assertIn("C6: a release end to end", out.getvalue())
        self.assertIn("(monthly)", out.getvalue())
        env = {name: value for name, value in os.environ.items() if name not in ("CANARY_TOKEN", "CANARY_REPO")}
        done = subprocess.run([sys.executable, str(ROOT / "scripts" / "canary.py"), "run"], capture_output=True,
                              text=True, env=env)
        self.assertEqual(done.returncode, 2)
        self.assertIn("CANARY_TOKEN is not set", done.stdout)

    def test_the_script_runs_on_the_canary_alone_unless_told(self):
        """The checks merge into the default branch and close every issue
        titled "canary ", so another repository takes --any-repository."""
        out = io.StringIO()
        self.assertEqual(canary.main(["run", "--repo", "o/r"], out), 2)
        self.assertIn("--any-repository", out.getvalue())
        env = {name: value for name, value in os.environ.items() if name not in ("CANARY_TOKEN", "CANARY_REPO")}
        done = subprocess.run([sys.executable, str(ROOT / "scripts" / "canary.py"), "run", "--repo", "o/r",
                               "--any-repository"], capture_output=True, text=True, env=env)
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
        # git 2.43 prints a UTC date as +00:00 and git 2.55 as Z; what the
        # spec reads idleness by is the instant, which the library's own
        # reader gives for either spelling.
        self.assertEqual(claims.parse_time(done.stdout.strip()), when)
        for name in canary.ODD_NAMES:
            clone.branch(name)
            clone.commit({"docs/canary/odd.md": canary.FILE_TEXT}, "odd")
            self.assertTrue(clone.push(name).ok, name)
            self.assertIsNotNone(self.remote_sha(name))


REPO = "o/r"
TOKEN = "secret-token"


def key(path, **params):
    """The selector a GET's answer is filed under in the FakeOpener: the
    path and the query as the adapter encodes it."""
    return path + ("?" + urllib.parse.urlencode(params) if params else "")


def listed(path, **params):
    """A list endpoint's first page, which the adapter asks for with the
    page size last."""
    return key(path, **params, per_page=github.PER_PAGE)


def encoded(text):
    """A file as the contents API gives it."""
    return FakeAnswer({"encoding": "base64", "content": base64.b64encode(text.encode()).decode()})


class TheSite(unittest.TestCase):
    """canary.Site's REST side over the library's FakeOpener: each method
    asks the path the live API has, reads the answer as GitHub shapes it,
    and sends the token in the header alone."""

    def setUp(self):
        self.scratch = Path(tempfile.mkdtemp(prefix="canary-site-"))

    def tearDown(self):
        shutil.rmtree(self.scratch, ignore_errors=True)

    def site(self, answers, admin=False, runs_allowed=False):
        opener = FakeOpener(answers)
        site = canary.Site(TOKEN, REPO, admin_token="admin-token" if admin else None, runs_allowed=runs_allowed,
                           workdir=self.scratch, sleep=lambda seconds: None, opener=opener)
        return site, opener

    def paths(self, opener, method):
        return [c.selector for c in opener.calls if c.get_method() == method]

    def test_it_reads_files_the_board_and_branches_as_the_api_gives_them(self):
        site, opener = self.site({
            key("/repos/o/r/contents/tools/VENDORED", ref="main"): encoded(f"sdlc-protocol 0.4.1 {COMMIT}\nx  y\n"),
            key("/repos/o/r/contents/STATE.md", ref="board"): encoded("# Board\n"),
            key("/repos/o/r/contents/big.md", ref="main"): FakeAnswer({"encoding": "none", "content": ""}),
            "/repos/o/r/git/ref/heads/claude/caf%C3%A9": FakeAnswer({"object": {"sha": "abc"}}),
            "/repos/o/r/git/ref/heads/main": FakeAnswer({"object": {"sha": "m1"}}),
            "DELETE /repos/o/r/git/refs/heads/claude/9": FakeAnswer(None),
        })
        self.assertEqual(site.main_file("tools/VENDORED"), f"sdlc-protocol 0.4.1 {COMMIT}\nx  y\n")
        self.assertEqual(canary.adopted(site), f"sdlc-protocol 0.4.1 {COMMIT}")
        self.assertEqual(site.board(), "# Board\n")
        self.assertIsNone(site.main_file("big.md"), "a file GitHub gives no text of is no file")
        self.assertIsNone(site.main_file("missing.md"))
        self.assertEqual(site.branch_sha("claude/caf\u00e9"), "abc")
        self.assertIsNone(site.branch_sha("released/5-abc1234"))
        self.assertEqual(site.main_sha(), "m1")
        site.delete_branch("claude/9")
        self.assertEqual(self.paths(opener, "DELETE"), ["/repos/o/r/git/refs/heads/claude/9"])
        for call in opener.calls:
            self.assertNotIn(TOKEN, call.full_url)
            self.assertEqual(call.get_header("Authorization"), f"Bearer {TOKEN}")

    def test_it_reads_check_runs_the_activity_and_the_board_s_runs(self):
        since = datetime(2026, 10, 14, 4, 0, tzinfo=timezone.utc)
        runs_path = "/repos/o/r/actions/workflows/board.yml/runs"
        answers = {
            listed("/repos/o/r/commits/abc/check-runs"): FakeAnswer({"check_runs": [{"name": "check"}]}),
            listed("/repos/o/r/activity", ref="refs/heads/claude/5", activity_type="force_push"):
                FakeAnswer([{"activity_type": "force_push", "before": "a", "after": "b"}]),
            listed(runs_path, created=">=2026-10-14T04:00:00Z"): FakeAnswer({"workflow_runs": [{"event": "push"}]}),
        }
        site, opener = self.site(answers)
        self.assertEqual(site.check_runs("abc"), [{"name": "check"}])
        self.assertEqual(site.activity("refs/heads/claude/5"), [{"activity_type": "force_push", "before": "a", "after": "b"}])
        self.assertIsNone(site.runs_since(since), "without Actions read the runs are not asked for")
        self.assertNotIn(listed(runs_path, created=">=2026-10-14T04:00:00Z"), self.paths(opener, "GET"))
        site, _ = self.site(answers, runs_allowed=True)
        self.assertEqual(site.runs_since(since), [{"event": "push"}])
        site, _ = self.site({listed(runs_path, created=">=2026-10-14T04:00:00Z"): Refusal(403)}, runs_allowed=True)
        self.assertIsNone(site.runs_since(since), "a 403 is the permission missing, not a failure")
        for status in (404, 500):
            site, _ = self.site({listed(runs_path, created=">=2026-10-14T04:00:00Z"): Refusal(status)}, runs_allowed=True)
            with self.assertRaises(canary.GitHubError, msg=f"a {status} is the copy's or GitHub's, not the token's"):
                site.runs_since(since)

    def test_it_reads_a_ref_s_newest_activity_of_every_kind_in_one_call(self):
        """C6's record of the activity asks one page, newest first and of
        every kind, where C5's reader asks every page of one kind; an empty
        answer is no entry, and a refusal is GitHub's, for the record to
        note."""
        newest = key("/repos/o/r/activity", ref="refs/heads/claude/5", direction="desc", per_page=canary.ACTIVITY_SEEN)
        site, opener = self.site({newest: FakeAnswer([{"activity_type": "branch_deletion", "before": "b"}])})
        self.assertEqual(site.activity("refs/heads/claude/5", kind=None, newest=canary.ACTIVITY_SEEN),
                         [{"activity_type": "branch_deletion", "before": "b"}])
        self.assertEqual(self.paths(opener, "GET"), [newest])
        site, _ = self.site({newest: FakeAnswer(None)})
        self.assertEqual(site.activity("refs/heads/claude/5", kind=None, newest=canary.ACTIVITY_SEEN), [])
        site, _ = self.site({newest: Refusal(500)})
        with self.assertRaises(canary.GitHubError):
            site.activity("refs/heads/claude/5", kind=None, newest=canary.ACTIVITY_SEEN)

    def test_it_opens_labels_comments_pokes_and_closes_issues(self):
        site, opener = self.site({
            "POST /repos/o/r/issues": FakeAnswer({"number": 7}),
            "/repos/o/r/issues/7": FakeAnswer({"number": 7, "labels": [{"name": "todo"}, {"name": "claimed"}]}),
            "POST /repos/o/r/labels": Refusal(422, {"message": "Validation Failed"}),
            "POST /repos/o/r/issues/7/labels": FakeAnswer([]),
            "POST /repos/o/r/issues/7/comments": FakeAnswer({"id": 1}),
            listed("/repos/o/r/issues/7/comments"): FakeAnswer([{"body": "hi"}]),
            "PATCH /repos/o/r/issues/7": FakeAnswer({}),
            listed("/repos/o/r/issues", state="open"): FakeAnswer([{"number": 1}, {"number": 2, "pull_request": {}}]),
        })
        self.assertEqual(site.throwaway("C1"), 7)
        self.assertEqual(site.issues, [7])
        made = opener.sent("POST")[0]
        self.assertEqual((made["title"], made["labels"]), ("canary C1: throwaway", [labels.TODO]))
        self.assertEqual(site.labels_of(7), ["claimed", "todo"])
        site.add_labels(7, [labels.CLAIMED, "worker:canary"])
        self.assertIn({"name": "worker:canary", "color": labels.WORKER_COLOUR}, opener.sent("POST"),
                      "the worker's label is made first; a 422 is the label being there")
        self.assertIn({"labels": [labels.CLAIMED, "worker:canary"]}, opener.sent("POST"))
        site.remove_label(7, labels.CLAIMED)
        self.assertEqual(self.paths(opener, "DELETE"), ["/repos/o/r/issues/7/labels/claimed"])
        site.comment(7, "hi")
        self.assertIn({"body": "hi"}, opener.sent("POST"))
        self.assertEqual(site.comments(7), [{"body": "hi"}])
        site.close_issue(7)
        site.poke(7)
        patched = opener.sent("PATCH")
        self.assertEqual(patched[0], {"state": "closed", "state_reason": "not_planned"})
        self.assertTrue(patched[1]["body"].startswith("Poked at "))
        self.assertEqual(site.open_issues(), [{"number": 1}], "a pull request in the issues list is no issue")

    def test_it_opens_reads_merges_and_closes_pull_requests(self):
        site, opener = self.site({
            "POST /repos/o/r/pulls": FakeAnswer({"number": 8}),
            "/repos/o/r/pulls/8": FakeAnswer({"number": 8, "state": "open", "mergeable_state": "draft"}),
            listed("/repos/o/r/pulls", state="open"): FakeAnswer([{"number": 8}]),
            "PATCH /repos/o/r/pulls/8": FakeAnswer({}),
            "PUT /repos/o/r/pulls/8/merge": FakeAnswer({"merged": True, "message": "Pull Request successfully merged"}),
            "PUT /repos/o/r/pulls/9/merge": Refusal(405, {"message": "Pull Request is not mergeable"}),
        })
        self.assertEqual(site.open_pull("claude/5", "canary C0: a draft", "Closes #5", True), 8)
        self.assertEqual(site.pulls, [8])
        self.assertEqual(opener.sent("POST")[0], {"title": "canary C0: a draft", "head": "claude/5", "base": "main",
                                                  "body": "Closes #5", "draft": True})
        self.assertEqual(site.pull(8)["mergeable_state"], "draft")
        self.assertEqual(site.open_pulls(), [{"number": 8}])
        site.close_pull(8)
        self.assertEqual(opener.sent("PATCH"), [{"state": "closed"}])
        merged = site.merge(8)
        self.assertEqual((merged.ok, merged.words), (True, "Pull Request successfully merged"))
        refused = site.merge(9)
        self.assertFalse(refused.ok)
        self.assertIn("HTTP 405", refused.words)
        self.assertIn("not mergeable", refused.words)
        self.assertNotIn(TOKEN, refused.words)

    def test_rulesets_take_the_administrator_s_token(self):
        site, opener = self.site({
            "POST /repos/o/r/rulesets": FakeAnswer({"id": 11}),
            listed("/repos/o/r/rulesets"): FakeAnswer([{"id": 11, "name": canary.RULESET}]),
            "DELETE /repos/o/r/rulesets/11": FakeAnswer(None),
        }, admin=True)
        self.assertEqual(site.ruleset_add(canary.RULESET, "refs/heads/claude/**", ["non_fast_forward"]), 11)
        made = opener.sent("POST")[0]
        self.assertEqual(made["conditions"], {"ref_name": {"include": ["refs/heads/claude/**"], "exclude": []}})
        self.assertEqual((made["rules"], made["enforcement"]), ([{"type": "non_fast_forward"}], "active"))
        self.assertEqual(site.rulesets(), [{"id": 11, "name": canary.RULESET}])
        site.ruleset_delete(11)
        self.assertEqual(self.paths(opener, "DELETE"), ["/repos/o/r/rulesets/11"])
        for call in opener.calls:
            self.assertEqual(call.get_header("Authorization"), "Bearer admin-token")
        site, _ = self.site({})
        self.assertIsNone(site.admin)

    def test_the_sweep_closes_what_an_earlier_run_left_and_stops_on_a_refusal(self):
        odd = canary.ODD_NAMES[0]
        in_review = [{"name": labels.TODO}, {"name": labels.IN_REVIEW}]
        site, opener = self.site({
            listed("/repos/o/r/pulls", state="open"): FakeAnswer([
                {"number": 3, "title": "canary C2: merged, #1", "head": {"ref": "claude/1"}},
                {"number": 4, "title": "Someone's own", "head": {"ref": "claude/2"}},
                {"number": 8, "title": "canary C6: a draft for #7", "head": {"ref": "claude/7"}}]),
            listed("/repos/o/r/issues", state="open"): FakeAnswer([
                {"number": 5, "title": "canary C6: throwaway"},
                {"number": 6, "title": "A real issue", "labels": in_review},
                {"number": 7, "title": "canary C6: throwaway", "labels": in_review}]),
            f"/repos/o/r/git/ref/heads/{canary.quote(odd)}": FakeAnswer({"object": {"sha": "odd"}}),
            "PATCH /repos/o/r/pulls/3": FakeAnswer({}),
            "PATCH /repos/o/r/issues/5": FakeAnswer({}),
            f"DELETE /repos/o/r/git/refs/heads/{canary.quote(odd)}": FakeAnswer(None),
            listed("/repos/o/r/rulesets"): FakeAnswer([{"id": 11, "name": canary.RULESET}, {"id": 12, "name": "other"}]),
            "DELETE /repos/o/r/rulesets/11": FakeAnswer(None),
        }, admin=True)
        said = site.sweep()
        self.assertEqual(said, ["closed the pull request #3 an earlier run left", "closed the issue #5 an earlier run left",
                                f"deleted the branch {canary.ascii_name(odd)} an earlier run left",
                                f"deleted the ruleset {canary.RULESET} an earlier run left"])
        self.assertEqual(self.paths(opener, "PATCH"), ["/repos/o/r/pulls/3", "/repos/o/r/issues/5"],
                         "the claim C6 held, #7, stands with its draft #8 for a later run")
        self.assertEqual(self.paths(opener, "DELETE"),
                         [f"/repos/o/r/git/refs/heads/{canary.quote(odd)}", "/repos/o/r/rulesets/11"])
        self.assertEqual(self.site({listed("/repos/o/r/issues", state="open"): Refusal(500)})[0].sweep(),
                         ["the sweep stopped: GET /repos/o/r/issues: HTTP 500"])

    def test_a_held_claim_leaves_the_clean_up_and_a_claim_taken_over_joins_it(self):
        site, opener = self.site({
            "POST /repos/o/r/labels": Refusal(422, {"message": "Validation Failed"}),
            "POST /repos/o/r/issues/7/labels": FakeAnswer([]),
        })
        site.issues, site.pulls = [5, 7], [6, 8]
        site.hold(7, 8)
        self.assertEqual(opener.sent("POST"), [{"labels": [labels.IN_REVIEW]}])
        self.assertEqual((site.issues, site.pulls), ([5], [6]))
        site.take_over(3, 4)
        site.take_over(3, None)
        self.assertEqual((site.issues, site.pulls), ([5, 3], [6, 4]))
        site, _ = self.site({"POST /repos/o/r/issues/7/labels": Refusal(500)})
        site.issues, site.pulls = [7], [8]
        with self.assertRaises(canary.GitHubError):
            site.hold(7, 8)
        self.assertEqual((site.issues, site.pulls), ([7], [8]), "a claim GitHub would not label closes with the run")

    def test_the_clean_up_closes_the_run_s_own_and_a_refusal_is_a_line(self):
        site, opener = self.site({
            "/repos/o/r/pulls/8": FakeAnswer({"state": "open"}),
            "/repos/o/r/pulls/9": FakeAnswer({"state": "closed"}),
            "PATCH /repos/o/r/pulls/8": FakeAnswer({}),
            "PATCH /repos/o/r/issues/7": Refusal(500, {"message": "boom"}),
            "DELETE /repos/o/r/git/refs/heads/claude/caf%C3%A9": FakeAnswer(None),
        })
        site.pulls, site.issues, site.extra_branches = [8, 9], [7], ["claude/caf\u00e9"]
        said = site.clean_up()
        self.assertEqual(said, ["issue #7 not closed: PATCH /repos/o/r/issues/7: HTTP 500"])
        self.assertEqual(self.paths(opener, "PATCH"), ["/repos/o/r/pulls/8", "/repos/o/r/issues/7"])
        self.assertEqual(self.paths(opener, "DELETE"), ["/repos/o/r/git/refs/heads/claude/caf%C3%A9"])

    def test_wait_gives_up_at_once_on_a_403_and_retries_other_refusals(self):
        waits = []
        site, opener = self.site({"/repos/o/r/forbidden": Refusal(403), "/repos/o/r/flaky": Refusal(502)})
        site.sleep = waits.append
        value, held = site.wait(lambda: site.api.get("/repos/o/r/forbidden"), lambda v: True)
        self.assertEqual((value, held, waits), ("not read: GET /repos/o/r/forbidden: HTTP 403", False, []))
        value, held = site.wait(lambda: site.api.get("/repos/o/r/flaky"), lambda v: True, budget=20, every=10)
        self.assertEqual((value, held, waits), ("not read: GET /repos/o/r/flaky: HTTP 502", False, [10, 10]))
        self.assertEqual(len(self.paths(opener, "GET")), 4)

    def test_wait_stops_the_check_on_a_refused_token_and_at_the_token_s_hour(self):
        site, _ = self.site({"/repos/o/r/expired": Refusal(401)})
        with self.assertRaises(canary.GitHubError) as raised:
            site.wait(lambda: site.api.get("/repos/o/r/expired"), lambda v: True)
        self.assertEqual(raised.exception.status, 401)
        self.assertFalse(site.out_of_time())
        site.deadline = site.now()
        self.assertTrue(site.out_of_time())
        reads = []
        with self.assertRaises(canary.OutOfTime):
            site.wait(lambda: reads.append(1), lambda v: True)
        self.assertEqual(reads, [], "nothing is read once the hour is up")
        self.assertLess(canary.TOKEN_LIFE, timedelta(hours=1))


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
        self.assertIn("timeout-minutes: 75", text)
        self.assertIn("SDLC_APP_PRIVATE_KEY", text)
        self.assertIn("issues: write", text)
        self.assertEqual(text.count("CANARY_RUNS:"), 1)
        for check in canary.MONTHLY:
            self.assertIn(check, text)

    def test_a_held_claim_is_due_a_day_and_the_token_s_hour_after_its_issue(self):
        """The push of a held claim came within the run's TOKEN_LIFE after
        its issue was made, so HELD_FOR after the issue it has surely gone
        more than the spec's day without a push; the five minutes are the
        clocks' skew."""
        self.assertEqual(canary.HELD_FOR, claims.IDLE_AFTER + canary.TOKEN_LIFE + timedelta(minutes=5))
        self.assertEqual(canary.HELD_FOR, timedelta(hours=25))
        self.assertGreater(canary.BACKDATED, claims.IDLE_AFTER)
        in_review = [{"name": labels.IN_REVIEW}]
        self.assertTrue(canary.is_held({"title": "canary C6: throwaway", "labels": in_review}))
        self.assertFalse(canary.is_held({"title": "canary C6: throwaway", "labels": [{"name": labels.TODO}]}))
        self.assertFalse(canary.is_held({"title": "Someone's issue", "labels": in_review}))
        self.assertFalse(canary.is_held(None))
        self.assertIsNone(canary.made_at({"created_at": "not a time"}))
        self.assertIsNone(canary.made_at({}))
        self.assertEqual(canary.made_at({"created_at": "2026-10-14T04:43:00Z"}),
                         datetime(2026, 10, 14, 4, 43, tzinfo=timezone.utc))

    def test_the_board_reads_the_open_issues_before_the_branches(self):
        """C6's and C7's judgment of the backdated push rests on this order
        (released_at_once): a page that lists the mark, an issue opened
        after the push, then read the claim's branch after the push."""
        asked = []

        class Api:
            repo = "o/r"

            def get_all(self, path, params=None):
                asked.append(path)
                return []

            def pages(self, path, params=None):
                asked.append(path)
                return []

            def get(self, path, params=None, missing_ok=False):
                asked.append(path)
                return None
        github.collect(Api(), "sha", "main", now=datetime(2026, 10, 14, 4, 43, tzinfo=timezone.utc))
        self.assertLess(asked.index("/repos/o/r/issues"), asked.index("/repos/o/r/branches"), asked)

    def test_the_claim_file_follows_the_template(self):
        text = canary.claim_text("o/r", 12, "canary", "plan")
        self.assertTrue(text.startswith("# Claim: #12\n\n- Issue: https://github.com/o/r/issues/12\n- Worker: canary\n"
                                        "- Branch: claude/12\n\n## Plan\n\nplan\n\n## Files to touch\n\n- docs/canary/12.md\n"))
        self.assertEqual(claims.claim_worker(text), "canary")


if __name__ == "__main__":
    unittest.main()
