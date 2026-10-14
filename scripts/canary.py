#!/usr/bin/env python3
"""The live canary: the few things only GitHub itself shows (validation/DESIGN.md, section 11).

Every other lane of the harness runs the library against a model, a fake or
a local git repository. This one runs a scripted worker against the live
service, on the canary repository OpenSpineConsortium/symphony-protocol-canary,
an internal repository made from scratch for it on 2026-10-13 and brought to
the current template by an adoption through the front door, so that its own
board workflow runs the library's copy at each release. The driver pushes
throwaway claims there and reads what GitHub and the board's own run did
with them; it never dispatches a workflow, since that takes Actions write,
and starts every board run with an event the token can make. It writes
canary.json, one record per check with the values it saw and the library
version the canary vendors, and a job summary; .github/workflows/canary.yml
runs it monthly and by hand and opens, or updates, one issue here when a
check fails.

    python3 scripts/canary.py run                      the monthly checks (C1, C2, C3, C5 and C6)
    python3 scripts/canary.py run --checks C0 C7       chosen checks; C7 needs CANARY_ADMIN_TOKEN
    python3 scripts/canary.py issue canary.json        open or update "Canary failed: ..." in GITHUB_REPOSITORY
    python3 scripts/canary.py list                     the checks and what each settles

The environment: CANARY_TOKEN, an installation token of SymphonyBoard for
the canary repository alone, with contents, issues and pull requests write;
an installation token lives an hour, so a run stops its checks at
TOKEN_LIFE after it starts and cleans up while the token still works;
CANARY_REPO, the repository as OWNER/NAME (the default is the canary, and
any other is refused unless --any-repository says so on purpose, since a
run merges into that repository's default branch and closes its issues
titled "canary ");
CANARY_RUNS, "true" when that token may list workflow runs, which C2 and C6
need for their run counts and which the App has only once the owner adds
Actions read to it; CANARY_ADMIN_TOKEN, a token with Administration on the
repository, which C7 alone needs, for its ruleset; GITHUB_TOKEN and
GITHUB_REPOSITORY for the issue command. No token is ever printed: git is
given it through a credential helper that reads the environment, and the
library's errors name a method, a path and a status.

A check's expectation is a sentence the spec makes; one that fails for a
finding validation/known.json still lists as built, or lists as fixed on
main after the version the canary repository vendors, which no release
it can vendor yet carries, is recorded as known, not failed, so the
canary goes red on the day something new breaks and not every month
until the owner's decision lands or the release reaches the canary.
"""

import argparse
import base64
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sdlc_protocol import board, claims, gate, labels, vendoring  # noqa: E402
from sdlc_protocol.adapters.github import GitHub, GitHubError, check_word  # noqa: E402
from validation import known, page  # noqa: E402

CANARY_REPO = "OpenSpineConsortium/symphony-protocol-canary"
WORKER = "canary"
# How the driver waits for the board: STATE.md every 10 seconds, for at
# most 5 minutes per expectation (DESIGN.md, section 11); the required
# check on the pull request C2 merges takes longer, since it runs the
# template's whole suite.
POLL_EVERY = 10
POLL_BUDGET = 300
CHECK_BUDGET = 900
# The board runs a merge fires wait their turn in the copy's concurrency
# group (queue: max), each a board run of about a minute, so the run the
# push to main started may finish some minutes after the merge.
RUNS_BUDGET = 600
# The runs are asked for by their creation time on GitHub's clock, to
# the second, from an instant on the runner's: a minute earlier keeps a
# runner a few seconds ahead from leaving the merge's runs out, and the
# runs are then told apart by event and branch, which nothing else in
# that minute shares (no other step pushes to main or to board).
RUNS_MARGIN = timedelta(minutes=1)
# An installation token lives 60 minutes from its mint; the run stops its
# checks five minutes short of that, from when the site is made, so the
# clean-up still has a token that works. A token minted by hand some
# time before the run lives less than this says.
TOKEN_LIFE = timedelta(minutes=55)
# GitHub writes a pull request's updated_at in whole seconds, so C6 waits
# this long between reading it and commenting, or the comment could land
# in the same second and look as if it moved nothing.
UPDATED_AT_TICK = 2
# What C6 and C7 push: a commit whose committer date is a day and an hour
# back, past the spec's 24 hours, so the next board run releases the claim.
BACKDATED = timedelta(hours=25)
MONTHLY = ("C1", "C2", "C3", "C5", "C6")
# The names C3 pushes, as \u escapes so this file stays ASCII: a superscript
# two, which reads as a digit and is none, and an e with an acute accent.
ODD_NAMES = ("claude/\u00b2", "claude/caf\u00e9")
RULESET = "canary C7"
ISSUE_TITLE = "Canary failed: "
ISSUE_LABEL = "canary"
SUMMARY_HEADING = "## Canary"
# What the canary's files in the canary repository say.
FILE_TEXT = "A throwaway file of the validation harness's canary (symphony-protocol, scripts/canary.py).\n"


class CanaryError(Exception):
    """A check that could not go on: what was asked and what GitHub said."""


class OutOfTime(CanaryError):
    """The run reached TOKEN_LIFE: the token is about to expire, and every
    call after that would be refused, which would read as the library
    failing."""


# --- what a check records ---------------------------------------------------

class Seen:
    """One check's record: its expectations, each a sentence with whether
    it held and whether a miss of it is known, the values it saw, and its
    status, which is failed when an expectation that is not known does not
    hold, and known when every expectation that does not hold fails for a
    finding the run reads as known (known_for: as built on main, or fixed
    on main after the version the canary vendors)."""

    def __init__(self, check, known_findings=frozenset()):
        self.check = check
        self.known_findings = known_findings
        self.status = "passed"
        self.expectations = []
        self.values = {}
        self.notes = []

    def expect(self, sentence, holds, finding=None):
        holds = bool(holds)
        known_miss = not holds and bool(finding) and finding in self.known_findings
        self.expectations.append({"expectation": sentence, "holds": holds, "finding": finding, "known": known_miss})
        if not holds and self.status != "failed":
            self.status = "known" if known_miss else "failed"
        return holds

    def note(self, sentence):
        self.notes.append(sentence)

    def skip(self, why):
        self.status = "skipped"
        self.notes.append(why)

    def fail(self, why):
        self.status = "failed"
        self.notes.append(why)

    def as_dict(self):
        return {"check": self.check, "status": self.status, "expectations": self.expectations,
                "values": self.values, "notes": self.notes}


class Pushed:
    """What git answered a push: whether it was taken, and its words with
    nothing of the remote's address in them."""

    def __init__(self, ok, words):
        self.ok = ok
        self.words = words

    def as_dict(self):
        return {"ok": self.ok, "words": self.words}


# --- git, with the token in the environment alone --------------------------

def git_env(token, base=None):
    """The environment git runs in: the token reaches it through a
    credential helper read from the environment, so it is in no argument
    and no file, and the author is the canary. Automatic maintenance is
    off, since a detached gc races the clean-up of a scratch clone."""
    env = dict(os.environ if base is None else base)
    settings = [
        ("credential.helper", ""),
        ("credential.helper", "!f() { echo username=x-access-token; echo \"password=$CANARY_GIT_TOKEN\"; }; f"),
        ("gc.auto", "0"),
        ("gc.autoDetach", "false"),
        ("maintenance.auto", "false"),
        ("advice.detachedHead", "false"),
    ]
    env["GIT_CONFIG_COUNT"] = str(len(settings))
    for index, (key, value) in enumerate(settings):
        env[f"GIT_CONFIG_KEY_{index}"] = key
        env[f"GIT_CONFIG_VALUE_{index}"] = value
    env["CANARY_GIT_TOKEN"] = token or ""
    env["GIT_TERMINAL_PROMPT"] = "0"
    for name in ("GIT_AUTHOR_NAME", "GIT_COMMITTER_NAME"):
        env[name] = "symphony canary"
    for name in ("GIT_AUTHOR_EMAIL", "GIT_COMMITTER_EMAIL"):
        env[name] = "canary@users.noreply.github.com"
    return env


def push_words(output, remote):
    """Git's answer to a push, one line, with the remote's address left
    out: the lines that say what was refused or rejected, else the last."""
    lines = [line.replace(remote, "<remote>").strip() for line in output.splitlines() if line.strip()]
    said = [line for line in lines if "rejected" in line or "refused" in line or "error" in line.lower()]
    return " | ".join(said or lines[-1:])


class Clone:
    """One clone of the canary repository, where a scripted worker commits
    and pushes. Each check clones afresh, so two clones racing each other
    are as two workers' machines."""

    def __init__(self, path, remote, env, default_branch="main"):
        self.path = Path(path)
        self.remote = remote
        self.env = env
        self.default_branch = default_branch

    def git(self, *args, check=True, env=None):
        done = subprocess.run(["git", "-C", str(self.path), *args], capture_output=True, text=True,
                              env=self.env if env is None else env)
        if check and done.returncode != 0:
            raise CanaryError(f"git {' '.join(args[:2])}: {push_words(done.stderr or done.stdout, self.remote)}")
        return done

    @classmethod
    def make(cls, path, remote, env, default_branch="main"):
        done = subprocess.run(["git", "clone", "--quiet", "--branch", default_branch, remote, str(path)],
                              capture_output=True, text=True, env=env)
        if done.returncode != 0:
            raise CanaryError(f"git clone: {push_words(done.stderr, remote)}")
        return cls(path, remote, env, default_branch)

    def head(self):
        return self.git("rev-parse", "HEAD").stdout.strip()

    def branch(self, name, start=None):
        """A new branch from the default branch's tip as the clone has it,
        or from start; the clone is left on it."""
        self.git("checkout", "--quiet", "-B", name, start or f"origin/{self.default_branch}")

    def commit(self, files, message, committer_date=None):
        """One commit: each path to its text, or None to delete it; the
        committer date, when given, is what the spec reads idleness by."""
        for rel, text in files.items():
            target = self.path / rel
            if text is None:
                self.git("rm", "--quiet", rel)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="ascii")
            self.git("add", rel)
        env = dict(self.env)
        if committer_date is not None:
            env["GIT_COMMITTER_DATE"] = committer_date.strftime("%Y-%m-%dT%H:%M:%S%z")
        self.git("commit", "--quiet", "--allow-empty", "-m", message, env=env)
        return self.head()

    def push(self, branch, lease=None, delete=False):
        """Push the clone's branch under that name; with lease, the tip the
        clone last saw there, as --force-with-lease=branch:lease, the one
        forced push the spec allows; with delete, delete the remote name."""
        args = ["push", "--quiet", "origin"]
        if delete:
            args += ["--delete", branch]
        elif lease:
            args += [f"--force-with-lease={branch}:{lease}", f"HEAD:refs/heads/{branch}"]
        else:
            args += [f"HEAD:refs/heads/{branch}"]
        done = self.git(*args, check=False)
        return Pushed(done.returncode == 0, push_words(done.stderr or done.stdout or "pushed", self.remote))


def race(actions):
    """Run each action in its own thread, all released at the same instant,
    and give their results in order; a thread's exception is its result."""
    results = [None] * len(actions)
    gate = threading.Barrier(len(actions))

    def go(index, action):
        gate.wait()
        try:
            results[index] = action()
        except Exception as problem:  # noqa: BLE001 - a thread's failure is a result
            results[index] = problem
    threads = [threading.Thread(target=go, args=(i, a)) for i, a in enumerate(actions)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return results


# --- the live site ------------------------------------------------------------

class Site:
    """GitHub and git over the canary repository. Every method a check calls
    is here, so tests/test_canary.py drives the checks with a fake that has
    the same methods and the live site is the one piece they do not run."""

    def __init__(self, token, repo=CANARY_REPO, admin_token=None, runs_allowed=False, workdir=None,
                 sleep=time.sleep, opener=None):
        self.repo = repo
        self.api = GitHub(token, repo, opener=opener)
        self.admin = GitHub(admin_token, repo, opener=opener) if admin_token else None
        self.runs_allowed = runs_allowed
        self.sleep = sleep
        self.workdir = Path(workdir or tempfile.mkdtemp(prefix="canary-"))
        self.env = git_env(token)
        self.remote = f"https://github.com/{repo}.git"
        self.default_branch = "main"
        self.issues = []
        self.pulls = []
        self.extra_branches = []
        self.clones = 0
        self.deadline = self.now() + TOKEN_LIFE

    # -- waiting --------------------------------------------------------------

    def now(self):
        return datetime.now(timezone.utc)

    def out_of_time(self):
        return self.now() >= self.deadline

    def mind_the_clock(self):
        if self.out_of_time():
            raise OutOfTime(f"the run reached its token's hour (TOKEN_LIFE, {int(TOKEN_LIFE.total_seconds() // 60)} "
                            f"minutes after it started), so it stopped before the token expired")

    def wait(self, read, holds, budget=POLL_BUDGET, every=POLL_EVERY):
        """Read until holds(value), at most budget seconds: the value and
        whether it held. A read GitHub refuses for the moment is tried
        again at the next tick; a 403 is the token's permission and ends
        the wait at once; a 401 is the token itself refused and ends the
        check, as reaching the token's hour does (OutOfTime), since every
        later call would be refused too."""
        waited = 0
        while True:
            self.mind_the_clock()
            try:
                value = read()
            except GitHubError as problem:
                if problem.status == 401:
                    raise
                value = f"not read: {problem}"
                if problem.status == 403:
                    return value, False
            else:
                if holds(value):
                    return value, True
            if waited >= budget:
                return value, False
            self.sleep(every)
            waited += every

    # -- git ------------------------------------------------------------------

    def clone(self):
        self.clones += 1
        return Clone.make(self.workdir / f"clone-{self.clones}", self.remote, self.env, self.default_branch)

    def race(self, actions):
        return race(actions)

    # -- reading the repository -----------------------------------------------

    def contents(self, path, ref):
        found = self.api.get(f"/repos/{self.repo}/contents/{path}", {"ref": ref}, missing_ok=True)
        if not found or found.get("encoding") != "base64":
            return None
        return base64.b64decode(found.get("content") or "").decode("utf-8", "replace")

    def main_file(self, path):
        return self.contents(path, self.default_branch)

    def board(self):
        """STATE.md on the branch board, or None before the first board."""
        return self.contents(board.STATE_PATH.name, "board")

    def branch_sha(self, name):
        found = self.api.get(f"/repos/{self.repo}/git/ref/heads/{quote(name)}", missing_ok=True)
        return (found or {}).get("object", {}).get("sha")

    def main_sha(self):
        return self.branch_sha(self.default_branch)

    def delete_branch(self, name):
        self.api.delete(f"/repos/{self.repo}/git/refs/heads/{quote(name)}", missing_ok=True)

    def check_runs(self, sha):
        return self.api.check_runs(sha)

    def activity(self, ref):
        return self.api.get_all(f"/repos/{self.repo}/activity", {"ref": ref, "activity_type": "force_push"})

    def runs_since(self, when):
        """The board workflow's runs created since when, or None when the
        token may not list them: it was minted without Actions read, which
        the App lacks until the owner adds it (DESIGN.md, section 11), or
        GitHub refused the list (403). A 404 is no workflow board.yml in
        the copy, which is the copy's fault, not the token's, and stops the
        check with its path."""
        if not self.runs_allowed:
            return None
        stamp = when.strftime("%Y-%m-%dT%H:%M:%SZ")
        try:
            pages = self.api.pages(f"/repos/{self.repo}/actions/workflows/board.yml/runs", {"created": f">={stamp}"})
            return [run for found in pages for run in (found or {}).get("workflow_runs", [])]
        except GitHubError as problem:
            if problem.status == 403:
                return None
            raise

    # -- issues and pull requests ---------------------------------------------

    def throwaway(self, check):
        """A throwaway issue for one check, labelled todo so a released
        claim's issue shows in the Queue; closed when the run ends."""
        made = self.api.create_issue(f"canary {check}: throwaway", f"Made by the validation harness's canary "
                                     f"(symphony-protocol, scripts/canary.py) for check {check}; closed when "
                                     f"the run ends.", [labels.TODO])
        self.issues.append(made["number"])
        return made["number"]

    def issue(self, number):
        return self.api.issue(number)

    def labels_of(self, number):
        return sorted(label["name"] for label in self.issue(number).get("labels", []))

    def add_labels(self, number, names):
        for name in names:
            if name.startswith(labels.WORKER_PREFIX):
                self.api.create_label(name, labels.WORKER_COLOUR)
        self.api.post(f"/repos/{self.repo}/issues/{number}/labels", {"labels": list(names)})

    def remove_label(self, number, name):
        self.api.remove_label(number, name)

    def comment(self, number, text):
        return self.api.post(f"/repos/{self.repo}/issues/{number}/comments", {"body": text})

    def comments(self, number):
        return self.api.get_all(f"/repos/{self.repo}/issues/{number}/comments")

    def close_issue(self, number):
        self.api.patch(f"/repos/{self.repo}/issues/{number}", {"state": "closed", "state_reason": "not_planned"})

    def poke(self, number):
        """One more event on the issue, issues edited, whose board run is
        then the newest in the group: a run that reads after a push can be
        the one a later event replaced, and this one nothing replaces."""
        self.api.patch(f"/repos/{self.repo}/issues/{number}", {"body": f"Poked at {self.now().isoformat()}."})

    def open_issues(self):
        return [i for i in self.api.get_all(f"/repos/{self.repo}/issues", {"state": "open"}) if "pull_request" not in i]

    def open_pull(self, branch, title, body, draft):
        made = self.api.post(f"/repos/{self.repo}/pulls", {"title": title, "head": branch, "base": self.default_branch,
                                                            "body": body, "draft": draft})
        self.pulls.append(made["number"])
        return made["number"]

    def pull(self, number):
        return self.api.get(f"/repos/{self.repo}/pulls/{number}")

    def open_pulls(self):
        return self.api.get_all(f"/repos/{self.repo}/pulls", {"state": "open"})

    def close_pull(self, number):
        self.api.patch(f"/repos/{self.repo}/pulls/{number}", {"state": "closed"})

    def merge(self, number):
        try:
            answer = self.api.put(f"/repos/{self.repo}/pulls/{number}/merge", {"merge_method": "merge"})
            return Pushed(bool((answer or {}).get("merged")), (answer or {}).get("message", "merged"))
        except GitHubError as problem:
            return Pushed(False, f"{problem} {problem.detail}".strip())

    # -- rulesets, with the administrator's token -------------------------------

    def ruleset_add(self, name, pattern, rule_types):
        made = self.admin.post(f"/repos/{self.repo}/rulesets", {
            "name": name, "target": "branch", "enforcement": "active",
            "conditions": {"ref_name": {"include": [pattern], "exclude": []}},
            "rules": [{"type": kind} for kind in rule_types], "bypass_actors": []})
        return made["id"]

    def ruleset_delete(self, ruleset_id):
        self.admin.delete(f"/repos/{self.repo}/rulesets/{ruleset_id}", missing_ok=True)

    def rulesets(self):
        return self.admin.get_all(f"/repos/{self.repo}/rulesets")

    # -- the start and the end of a run ---------------------------------------

    def sweep(self):
        """What an earlier run that died left behind: its pull requests
        closed, then its issues, whose closing starts the template's tidy
        workflow, which deletes their branches, then the names no issue
        owns; with an administrator, the ruleset C7 makes. Each refusal is
        a line, never a failure."""
        said = []
        try:
            for pull in self.open_pulls():
                if pull.get("title", "").startswith("canary "):
                    self.close_pull(pull["number"])
                    said.append(f"closed the pull request #{pull['number']} an earlier run left")
            for issue in self.open_issues():
                if issue.get("title", "").startswith("canary "):
                    self.close_issue(issue["number"])
                    said.append(f"closed the issue #{issue['number']} an earlier run left")
            for name in ODD_NAMES:
                if self.branch_sha(name):
                    self.delete_branch(name)
                    said.append(f"deleted the branch {ascii_name(name)} an earlier run left")
            if self.admin:
                for ruleset in self.rulesets():
                    if ruleset.get("name") == RULESET:
                        self.ruleset_delete(ruleset["id"])
                        said.append(f"deleted the ruleset {RULESET} an earlier run left")
        except GitHubError as problem:
            said.append(f"the sweep stopped: {problem}")
        return said

    # -- the end of a run -----------------------------------------------------

    def clean_up(self):
        """Close what the run opened: the pull requests first, then the
        issues, whose closing starts the template's tidy workflow, which
        deletes each issue's claim branches; the branches no issue owns are
        deleted here. What GitHub refuses is a line, never a failure."""
        said = []
        for number in self.pulls:
            try:
                if self.pull(number).get("state") == "open":
                    self.close_pull(number)
            except GitHubError as problem:
                said.append(f"pull request #{number} not closed: {problem}")
        for number in self.issues:
            try:
                self.close_issue(number)
            except GitHubError as problem:
                said.append(f"issue #{number} not closed: {problem}")
        for name in self.extra_branches:
            try:
                self.delete_branch(name)
            except GitHubError as problem:
                said.append(f"branch {name} not deleted: {problem}")
        return said


def quote(name):
    """A ref's name in a path, slashes kept, as the library's adapter has
    it: GitHub reads a ref's slashes as part of its name, so released/N-sha
    is looked up under heads/released/N-sha, never heads/released%2FN-sha."""
    return urllib.parse.quote(name, safe="/")


def ascii_name(name):
    """A branch name as a key in the records: ASCII, every other character
    as the \\u escape the design writes it with."""
    return "".join(c if ord(c) < 128 else f"\\u{ord(c):04x}" for c in name)


# --- the steps the checks share ------------------------------------------------

def claim_text(repo, number, worker, plan):
    """A claim file on the template docs/agents/claims/README.md gives."""
    return "\n".join([
        f"# Claim: #{number}", "",
        f"- Issue: https://github.com/{repo}/issues/{number}",
        f"- Worker: {worker}",
        f"- Branch: {claims.branch_of(number)}", "",
        "## Plan", "", plan, "",
        "## Files to touch", "", f"- {canary_file(number)}", "",
    ])


def canary_file(number):
    return f"docs/canary/{number}.md"


def claim(site, clone, number, worker=WORKER, plan="A throwaway claim of the validation harness's canary."):
    """The claim as the spec has it: a new branch claude/N from the default
    branch whose first commit adds the claim file and nothing else, pushed
    under its name. The push's answer, and the clone on the branch."""
    branch = claims.branch_of(number)
    clone.branch(branch)
    clone.commit({claims.claim_path(number): claim_text(site.repo, number, worker, plan)}, f"Claim #{number}")
    return clone.push(branch)


def announce(site, number, worker=WORKER):
    site.add_labels(number, [labels.CLAIMED, labels.WORKER_PREFIX + worker])
    site.comment(number, f"Plan: a throwaway claim of the validation harness's canary, held by {worker}.")


def has_marker(comments, marker):
    return [c for c in comments if (c.get("body") or "").startswith(marker)]


def claim_row(page_text, number):
    """The Claims row for an issue, or None."""
    for row in page.claims(page_text or ""):
        if row[0] == f"#{number}":
            return row
    return None


def by_event(runs):
    """How many runs each event started, with how they ended."""
    counted = {}
    for run in runs or []:
        key = f"{run.get('event')} on {run.get('head_branch')}"
        counted.setdefault(key, {})
        ended = run.get("conclusion") or run.get("status") or "unknown"
        counted[key][ended] = counted[key].get(ended, 0) + 1
    return counted


RUNS_NOT_READ = ("the board's runs were not read: the token may not list workflow runs, since it was minted without "
                 "Actions read (the App lacks it until the owner adds it to SymphonyBoard, or the mint that asks for "
                 "it failed this run, which its step in the run's log shows) or GitHub refused the list")


def check_finished(runs):
    return any(r.get("name") == "check" and r.get("status") == "completed" for r in runs)


def check_passed(runs):
    """Whether the required check, named check, passed on the commit: the
    gate's own reading of the runs, red winning over pending."""
    return check_word(runs, "check") == gate.GREEN


def push_to_main_done(runs, default_branch):
    """Whether a run the push to the default branch started has finished,
    cancelled included, so its conclusion can be judged."""
    return any(r.get("event") == "push" and r.get("head_branch") == default_branch and r.get("status") == "completed"
               for r in runs or [])


def wait_for_board(site, seen, sentence, holds, finding=None, budget=POLL_BUDGET):
    page_text, ok = site.wait(site.board, lambda text: text is not None and holds(text), budget=budget)
    seen.expect(sentence, ok, finding)
    return page_text


# --- the checks ----------------------------------------------------------------

def check_c0(site, seen):
    """One REST GET on an open draft: the mergeable and mergeable_state
    GitHub reports for a draft (F9)."""
    drafts = [p for p in site.open_pulls() if p.get("draft")]
    if drafts:
        number = drafts[0]["number"]
        seen.note(f"read the open draft #{number}")
    else:
        number = site.throwaway("C0")
        claim(site, site.clone(), number)
        number = site.open_pull(claims.branch_of(number), f"canary C0: a draft for #{number}", f"Closes #{number}", True)
        seen.note(f"no open draft, so one was opened, #{number}")
    detail, computed = site.wait(lambda: site.pull(number), mergeability_computed, budget=60, every=3)
    detail = detail if isinstance(detail, dict) else {}
    seen.values["draft"] = {key: detail.get(key) for key in ("number", "draft", "mergeable", "mergeable_state")}
    seen.expect("GitHub computed the draft's mergeability, so its mergeable_state is a word", computed)


def mergeability_computed(detail):
    """GitHub answers mergeable null and mergeable_state "unknown" until a
    background check has run; only a computed answer settles F9."""
    return detail.get("mergeable") is not None and detail.get("mergeable_state") not in (None, "unknown")


def check_c1(site, seen):
    """Two clones push claude/N from main at the same instant: exactly one
    is taken, and the board lists that one claim."""
    number = site.throwaway("C1")
    branch = claims.branch_of(number)
    clones = [(site.clone(), f"{WORKER}-a"), (site.clone(), f"{WORKER}-b")]
    for clone, worker in clones:
        clone.branch(branch)
        clone.commit({claims.claim_path(number): claim_text(site.repo, number, worker, f"The claim of {worker}.")},
                     f"Claim #{number}")
    results = site.race([lambda c=clone: c.push(branch) for clone, _ in clones])
    pushes = []
    for (clone, worker), result in zip(clones, results):
        pushed = result if isinstance(result, Pushed) else Pushed(False, str(result))
        pushes.append({"worker": worker, "sha": clone.head(), **pushed.as_dict()})
    seen.values["pushes"] = pushes
    taken = [p for p in pushes if p["ok"]]
    seen.expect("exactly one of two simultaneous pushes of the claim branch is taken", len(taken) == 1)
    if len(taken) == 1:
        on_github = site.branch_sha(branch)
        seen.values["branch"] = on_github
        seen.expect("the branch holds the taken push", on_github == taken[0]["sha"])
        wait_for_board(site, seen, "the board lists the claim, held by the worker whose push was taken",
                       lambda text: (claim_row(text, number) or [None] * 3)[2] == taken[0]["worker"])


def check_c2(site, seen):
    """A merge fires four events at once, beside a worker's push: whether
    the pull request behind main hears "main moved" for main's new commit,
    judged by the state, the marker on the pull request within the budget
    whichever run posts it, as the library tells it since F12's fix, and
    read as known (F12) while the canary vendors a release without that
    fix; whether the push to main's run survives the concurrency group,
    which the copy's board.yml keeps with queue: max, F12's other half,
    which the template brings and no release of the library changes, so
    it is never read as known; and what each event's run did."""
    behind, merged = site.throwaway("C2 behind"), site.throwaway("C2 merged")
    clone_behind, clone_merged = site.clone(), site.clone()
    for clone, number in ((clone_behind, behind), (clone_merged, merged)):
        pushed = claim(site, clone, number)
        if not pushed.ok:
            raise CanaryError(f"the claim of #{number} was not taken: {pushed.words}")
        clone.commit({claims.claim_path(number): None, canary_file(number): FILE_TEXT}, f"Finish #{number}")
        pushed = clone.push(claims.branch_of(number))
        if not pushed.ok:
            raise CanaryError(f"the second push of #{number} was not taken: {pushed.words}")
    pull_behind = site.open_pull(claims.branch_of(behind), f"canary C2: stays behind, #{behind}", f"Closes #{behind}", False)
    pull_merged = site.open_pull(claims.branch_of(merged), f"canary C2: merged, #{merged}", f"Closes #{merged}", False)
    # Both pull requests have their check done before the merge: the merge
    # needs it on one, and the other must have passed it, since GitHub
    # reads a pull request whose required check failed or is pending as
    # blocked, which hides how it stands to main, and the board tells
    # "main moved" only to one it reads as behind or conflicting.
    for which, clone, ready in (("merged", clone_merged, check_finished), ("behind", clone_behind, check_passed)):
        head = clone.head()
        runs, done = site.wait(lambda: site.check_runs(head), ready, budget=CHECK_BUDGET)
        checks = {r.get("name"): r.get("conclusion") or r.get("status") for r in (runs if isinstance(runs, list) else [])}
        if not isinstance(runs, list):
            seen.note(str(runs))
        seen.values[f"checks on the {which} head"] = checks
        if which == "merged" and not done:
            seen.note("no check named check finished on the merged head within the budget; going on anyway")
        if which == "behind":
            # A miss here is the copy's or the canary's, never F12's, so the
            # word is not judged on top of it, which would read as F12.
            judge_the_word = seen.expect("the check passed on the pull request left behind, so GitHub can say how it "
                                         "stands to main", done)
            if not done:
                seen.note(f"the main-moved expectation was not judged: the check on the pull request left behind "
                          f"stood at {checks.get('check', 'no run named check')} at the end of the budget, and GitHub "
                          f"reads a pull request whose required check failed or is pending as blocked")
    # The worker's push beside the merge goes to a claim with no pull
    # request, so the merge's four events meet one skipped run and nothing
    # changes the pull request left behind while it waits for the word.
    beside = site.throwaway("C2 beside")
    clone_beside = site.clone()
    pushed = claim(site, clone_beside, beside)
    if not pushed.ok:
        raise CanaryError(f"the claim of #{beside} was not taken: {pushed.words}")
    clone_beside.commit({f"docs/canary/{beside}-beside.md": FILE_TEXT}, "A worker's push beside the merge")
    since = site.now() - RUNS_MARGIN
    merge, pushed = site.race([lambda: site.merge(pull_merged), lambda: clone_beside.push(claims.branch_of(beside))])
    merge = merge if isinstance(merge, Pushed) else Pushed(False, str(merge))
    pushed = pushed if isinstance(pushed, Pushed) else Pushed(False, str(pushed))
    seen.values["merge"] = merge.as_dict()
    seen.values["push beside it"] = pushed.as_dict()
    if not seen.expect("the pull request merged", merge.ok):
        return
    main_sha = site.main_sha() or ""
    seen.values["main"] = main_sha
    queued = "queue: max" in (site.main_file(".github/workflows/board.yml") or "")
    seen.values["board.yml queues its runs"] = queued
    if not queued:
        seen.note("the copy's board.yml does not queue its runs (queue: max), F12's half that the template brings and "
                  "no release of the library changes: adopt the copy from the current template")
    if judge_the_word:
        marker = board.MAIN_MOVED_MARKER.format(sha=board.short_sha(main_sha))
        comments, heard = site.wait(lambda: site.comments(pull_behind), lambda cs: bool(has_marker(cs, marker)))
        seen.expect("the pull request behind main hears main moved for main's new commit", heard, finding="F12")
    # How GitHub stands the pull request left behind to main, so a missed
    # word names its cause: behind with no word is the library's; blocked
    # is the check; clean is a ruleset that does not ask for an up-to-date
    # branch.
    detail, _ = site.wait(lambda: site.pull(pull_behind), mergeability_computed, budget=60, every=3)
    seen.values["the pull request left behind after the merge"] = (detail if isinstance(detail, dict) else {}).get(
        "mergeable_state")
    runs = site.runs_since(since)
    if runs is None:
        seen.note(RUNS_NOT_READ)
        return
    runs, _ = site.wait(lambda: site.runs_since(since), lambda found: push_to_main_done(found, site.default_branch),
                        budget=RUNS_BUDGET)
    runs = runs if isinstance(runs, list) else []
    seen.values["board runs by event"] = by_event(runs)
    to_main = [r for r in runs if r.get("event") == "push" and r.get("head_branch") == site.default_branch]
    if not push_to_main_done(runs, site.default_branch):
        seen.note("no board run the push to main started had finished within the budget")
    seen.expect("the push to main started a board run that was not cancelled",
                any(r.get("status") == "completed" and r.get("conclusion") not in ("cancelled", "skipped")
                    for r in to_main))


def check_c3(site, seen):
    """Branch names outside ASCII: whether GitHub takes them, and that the
    board's run neither fails nor reads them as claims (F6, live)."""
    number = site.throwaway("C3")
    clone = site.clone()
    pushes = {}
    for name in ODD_NAMES:
        clone.branch(name)
        clone.commit({claims.claim_path(number): claim_text(site.repo, number, WORKER, f"Pushed as {name!a}.")},
                     f"A branch named {name!a}")
        pushed = clone.push(name)
        pushes[ascii_name(name)] = pushed.as_dict()
        if pushed.ok:
            site.extra_branches.append(name)
    seen.values["pushes"] = pushes
    taken = [name for name in ODD_NAMES if pushes[ascii_name(name)]["ok"]]
    seen.note(f"GitHub took {len(taken)} of {len(ODD_NAMES)} names outside ASCII")
    if not taken:
        return
    # The board writes a branch outside ASCII escaped (board.escaped, each
    # such character as \uXXXX), so the names are looked for as it writes
    # them, never as the page's cell would fold them (claude/2).
    wait_for_board(site, seen, "the board was written after the push, listing each name as no claim",
                   lambda text: all(board.escaped(name) in page.not_claims(text) for name in taken))
    seen.expect("no name outside ASCII is read as the issue's claim",
                claim_row(site.board() or "", number) is None)


def check_c5(site, seen):
    """After a lease push onto a claim, the activity API lists the
    force_push with its actor, its time, before and after (F7 and F8)."""
    number = site.throwaway("C5")
    clone = site.clone()
    pushed = claim(site, clone, number)
    if not pushed.ok:
        raise CanaryError(f"the claim was not taken: {pushed.words}")
    first = clone.head()
    clone.branch(claims.branch_of(number))
    second = clone.commit({claims.claim_path(number): claim_text(site.repo, number, WORKER, "Rewritten by a lease push.")},
                          f"Claim #{number}, rewritten")
    leased = clone.push(claims.branch_of(number), lease=first)
    seen.values["lease push"] = leased.as_dict()
    if not seen.expect("the lease push onto the claim is taken", leased.ok):
        return
    ref = f"refs/heads/{claims.branch_of(number)}"
    events, listed = site.wait(lambda: site.activity(ref),
                               lambda found: any(e.get("before") == first and e.get("after") == second for e in found))
    found = [e for e in (events if isinstance(events, list) else []) if e.get("before") == first]
    seen.values["force_push"] = [{"keys": sorted(e.keys()), "activity_type": e.get("activity_type"),
                                  "actor": (e.get("actor") or {}).get("login"), "timestamp": e.get("timestamp"),
                                  "before": e.get("before"), "after": e.get("after")} for e in found]
    seen.expect("the activity API lists the lease push as a force_push with before and after", listed)
    if listed:
        seen.expect("the force_push names its actor and its time",
                    all(e.get("actor") and e.get("timestamp") for e in found))


def check_c6(site, seen):
    """A release end to end on a throwaway claim: the comment, the labels,
    the rename, the closed draft, the issue back in the Queue, and the
    second push of the freed name deleted."""
    number = site.throwaway("C6")
    branch = claims.branch_of(number)
    clone = site.clone()
    pushed = claim(site, clone, number)
    if not pushed.ok:
        raise CanaryError(f"the claim was not taken: {pushed.words}")
    announce(site, number)
    pull = site.open_pull(branch, f"canary C6: a draft for #{number}", f"Closes #{number}", True)
    updated = {"after the open": site.pull(pull).get("updated_at")}
    site.sleep(UPDATED_AT_TICK)
    site.comment(pull, "A comment on the draft, before the backdated push.")
    updated["after the comment"], by_comment = site.wait(
        lambda: site.pull(pull).get("updated_at"), moved_from(updated["after the open"]), budget=60)
    wait_for_board(site, seen, "the board lists the claim before the backdated push",
                   lambda text: claim_row(text, number) is not None)
    head = clone.commit({canary_file(number): FILE_TEXT}, "Work, dated 25 hours back", site.now() - BACKDATED)
    since = site.now() - RUNS_MARGIN
    pushed = clone.push(branch)
    if not pushed.ok:
        raise CanaryError(f"the backdated push was not taken: {pushed.words}")
    updated["after the push"], by_push = site.wait(
        lambda: site.pull(pull).get("updated_at"), moved_from(updated["after the comment"]), budget=60)
    seen.values["draft updated_at"] = updated
    # The push's runs may be the ones a burst replaces; one more event,
    # nothing newer behind it, makes sure a run reads after the commit.
    site.poke(number)
    seen.expect("the comment moved the draft's updated_at", by_comment)
    seen.expect("the push moved the draft's updated_at", by_push)
    comments, released = site.wait(lambda: site.comments(number),
                                   lambda cs: bool(has_marker(cs, claims.RELEASE_MARKER)))
    if not seen.expect("the coordinator posts the release comment, with its marker first", released):
        return
    release = has_marker(comments, claims.RELEASE_MARKER)[0]
    seen.values["release comment"] = {"author": (release.get("user") or {}).get("login"),
                                      "author_association": release.get("author_association")}
    seen.expect("the release comment is github-actions[bot]'s",
                (release.get("user") or {}).get("login") in claims.RELEASING_BOTS)
    names, off = site.wait(lambda: site.labels_of(number),
                           lambda found: labels.CLAIMED not in found and labels.WORKER_PREFIX + WORKER not in found)
    seen.values["labels after the release"] = names
    seen.expect("the labels claimed and the worker's are off the issue", off)
    renamed = claims.released_branch(number, head)
    _, kept = site.wait(lambda: (site.branch_sha(renamed), site.branch_sha(branch)),
                        lambda pair: pair[0] == head and pair[1] is None)
    seen.expect(f"the branch is renamed to {renamed} and the claim's name is free", kept)
    if kept:
        state, closed = site.wait(lambda: site.pull(pull).get("state"), lambda found: found == "closed")
        seen.expect("the rename closed the draft", closed)
        marker = board.RELEASED_MARKER.format(branch=renamed)
        comments, said = site.wait(lambda: site.comments(number), lambda cs: len(has_marker(cs, marker)) == 1)
        seen.expect("the board says once on the issue where the branch went", said)
    else:
        seen.note(why_not_renamed(site, number, renamed))
    wait_for_board(site, seen, "the issue is back in the Queue and the claim is off the board",
                   lambda text: number in page.queue(text) and claim_row(text, number) is None)
    runs = site.runs_since(since)
    if runs is not None:
        seen.values["board runs by event"] = by_event(runs)
        seen.expect("the board's own pushes started no board run",
                    not any(r.get("head_branch") == "board" for r in runs))
    else:
        seen.note(RUNS_NOT_READ)
    if not kept:
        return
    again = clone.push(branch)
    seen.values["second push at the released head"] = again.as_dict()
    if not seen.expect("the freed name takes a push at the released head", again.ok):
        return
    site.poke(number)
    _, deleted = site.wait(lambda: (site.branch_sha(branch), site.branch_sha(renamed)),
                           lambda pair: pair[0] is None and pair[1] == head)
    seen.expect("the next run deletes the second push, since the released branch keeps its head", deleted)


def moved_from(before):
    """A wait's test that a pull request's updated_at moved past before: a
    value read and different, never None."""
    return lambda found: bool(found) and found != before


def why_not_renamed(site, number, renamed):
    """The note when C6's rename did not come, naming the board's own word
    when it said GitHub refused the rename, as a ruleset over claude/**
    does (a C7 run killed before it removed its ruleset leaves one); the
    draft's closing, the word on where the branch went and the second push
    of the freed name depend on the rename, so they are not asked."""
    said = has_marker(site.comments(number), board.RENAME_REFUSED_MARKER.format(branch=renamed))
    cause = ("the board says GitHub refused the rename, as a ruleset over claude/** makes it do (one named "
             f"{RULESET!r} is a C7 run's that was killed before it removed it: delete it under the canary repository's "
             "Settings, Rules, Rulesets)") if said else "the board said nothing of a refusal"
    return f"the rename did not come within the budget: {cause}; what depends on it was not asked"


def check_c7(site, seen):
    """Under a ruleset over claude/** with only non_fast_forward: a lease
    push onto a claim is refused (G11b's second fence), the board's rename
    of a released claim is refused and said once, naming the ruleset, and
    the next run after the ruleset goes renames."""
    if site.admin is None:
        seen.skip("C7 needs CANARY_ADMIN_TOKEN, a token with Administration on the canary repository, for its ruleset")
        return
    number = site.throwaway("C7")
    branch = claims.branch_of(number)
    ruleset = site.ruleset_add(RULESET, f"refs/heads/{claims.PREFIX}**", ["non_fast_forward"])
    seen.values["ruleset"] = ruleset
    try:
        clone = site.clone()
        pushed = claim(site, clone, number)
        if not pushed.ok:
            raise CanaryError(f"the claim was not taken: {pushed.words}")
        first = clone.head()
        clone.branch(branch)
        clone.commit({claims.claim_path(number): claim_text(site.repo, number, WORKER, "Rewritten under the ruleset.")},
                     f"Claim #{number}, rewritten")
        leased = clone.push(branch, lease=first)
        seen.values["lease push under the ruleset"] = leased.as_dict()
        seen.expect("the ruleset refuses a lease push onto the claim", not leased.ok)
        clone.branch(branch, first)
        announce(site, number)
        pull = site.open_pull(branch, f"canary C7: a draft for #{number}", f"Closes #{number}", True)
        head = clone.commit({canary_file(number): FILE_TEXT}, "Work, dated 25 hours back", site.now() - BACKDATED)
        pushed = clone.push(branch)
        if not pushed.ok:
            raise CanaryError(f"the backdated push was not taken: {pushed.words}")
        site.poke(number)
        comments, released = site.wait(lambda: site.comments(number),
                                       lambda cs: bool(has_marker(cs, claims.RELEASE_MARKER)))
        if not seen.expect("the coordinator releases the claim under the ruleset", released):
            return
        # The marker names the branch the rename was to make (board.py).
        renamed = claims.released_branch(number, head)
        marker = board.RENAME_REFUSED_MARKER.format(branch=renamed)
        comments, refused = site.wait(lambda: site.comments(number), lambda cs: len(has_marker(cs, marker)) == 1)
        seen.expect("the board says once that GitHub refused the rename", refused)
        if refused:
            seen.expect("the saying names the ruleset", RULESET in has_marker(comments, marker)[0].get("body", ""))
        seen.expect("the claim's name is still taken", site.branch_sha(branch) == head)
        seen.values["draft"] = site.pull(pull).get("state")
    finally:
        try:
            site.ruleset_delete(ruleset)
        except GitHubError as problem:
            seen.fail(f"the ruleset {RULESET} ({ruleset}) is still on the repository, delete it by hand: {problem}")
    site.add_labels(number, [labels.BLOCKED])
    site.remove_label(number, labels.BLOCKED)
    renamed = claims.released_branch(number, head)
    _, kept = site.wait(lambda: (site.branch_sha(renamed), site.branch_sha(branch)),
                        lambda pair: pair[0] == head and pair[1] is None)
    seen.expect("once the ruleset is gone, the next run renames the branch", kept)


CHECKS = {
    "C0": (check_c0, "one GET on an open draft: the mergeable_state GitHub reports for a draft (F9)"),
    "C1": (check_c1, "the race: two pushes of one claim branch at the same instant, exactly one taken"),
    "C2": (check_c2, "the concurrency group: a merge's four events beside a worker's push, and main moved (F12)"),
    "C3": (check_c3, "names outside ASCII: what GitHub and the board's run do with them (F6, live)"),
    "C5": (check_c5, "the activity API: a lease push listed as a force_push with before and after (F7, F8)"),
    "C6": (check_c6, "a release end to end: comment, labels, rename, closed draft, the Queue, the freed name"),
    "C7": (check_c7, "rulesets: a lease push and the rename refused under non_fast_forward on claude/** (G11b)"),
}
NEEDS_ADMIN = {"C7"}


# --- running ----------------------------------------------------------------------

def adopted(site):
    """The library the canary vendors, the first line of its tools/VENDORED
    ("sdlc-protocol <version> <commit>"), or None when the repository is
    not adopted yet."""
    record = site.main_file("tools/VENDORED")
    if not record:
        return None
    first = record.splitlines()[0].split() if record.strip() else []
    return " ".join(first[:3]) if first else "unreadable"


def vendored_version(first_line):
    """The version in a record's first line, or None when the line is not
    one, as "unreadable" or an "unknown" commit's version are not: what
    known.json's fixed_after is read against."""
    found = vendoring.FIRST_LINE.fullmatch((first_line or "").strip())
    return found.group(1) if found else None


def known_for(first_line):
    """The findings a failed expectation is known for on this canary: as
    built on main, and fixed on main after the version the canary vendors
    (known.as_built_for)."""
    return known.as_built_for(vendored_version(first_line))


def known_words(known_findings):
    """Which findings a failed expectation is read as known for, and why
    each: as built, or fixed on main after the version named."""
    as_built = [f for f in by_number(known_findings) if f in known.AS_BUILT]
    fixed = [f for f in by_number(known_findings) if f not in known.AS_BUILT]
    parts = []
    if as_built:
        parts.append(f"{', '.join(as_built)} (as built)")
    parts += [f"{f} (fixed after {known.FIXED_AFTER.get(f, 'an unrecorded version')})" for f in fixed]
    return "; ".join(parts) or "none"


def by_number(findings):
    """Findings in the order of their numbers, F2 before F12."""
    return sorted(findings, key=lambda f: (len(f), f))


def run_checks(site, names, known_findings=None, out=sys.stdout):
    """The sweep of what an earlier run left, then every named check in
    order, each recorded whatever happens to it, then the clean-up."""
    if known_findings is None:
        known_findings = frozenset(known.AS_BUILT)
    records = []
    stopped = None
    try:
        for line in site.sweep():
            print(line, file=out, flush=True)
        for name in names:
            seen = Seen(name, known_findings)
            if stopped is None and site.out_of_time():
                stopped = "the run reached its token's hour before this check started"
            if stopped:
                seen.skip(f"not run: {stopped}")
                records.append(seen)
                print(f"{name}: {seen.status}", file=out, flush=True)
                continue
            check, _ = CHECKS[name]
            print(f"{name}: running", file=out, flush=True)
            started = time.monotonic()
            try:
                check(site, seen)
            except OutOfTime as problem:
                seen.fail(f"stopped: {problem}")
                stopped = "an earlier check reached the token's hour"
            except (CanaryError, GitHubError) as problem:
                seen.fail(f"stopped: {problem}")
                if getattr(problem, "status", None) == 401:
                    seen.note("GitHub refused the token itself (401): an installation token lives an hour, so the run "
                              "outlived it or the token was revoked; this says nothing of the library")
                    stopped = "GitHub refused the token in an earlier check (401)"
            except Exception as problem:  # noqa: BLE001 - a check's crash is a record, kept with the rest
                seen.fail(f"stopped: {type(problem).__name__}: {problem}")
            seen.values["seconds"] = round(time.monotonic() - started)
            print(f"{name}: {seen.status}", file=out, flush=True)
            records.append(seen)
    finally:
        for line in site.clean_up():
            print(line, file=out)
    return records


def summary(records, version, repo, known_findings=frozenset()):
    """The job summary: one line per check, then each expectation that did
    not hold and each note."""
    lines = [SUMMARY_HEADING, "", f"Repository {repo}, vendoring {version}. A failed expectation is known for: "
                                  f"{known_words(known_findings)}.", ""]
    for seen in records:
        held = sum(1 for e in seen.expectations if e["holds"])
        lines.append(f"- {seen.check}: {seen.status}, {held} of {len(seen.expectations)} expectations held"
                     f" in {seen.values.get('seconds', 0)} s")
    details = []
    for seen in records:
        for e in seen.expectations:
            if not e["holds"]:
                details.append(f"- {seen.check}: did not hold: {e['expectation']}{known_suffix(e)}")
        details += [f"- {seen.check}: {note}" for note in seen.notes]
    if details:
        lines += ["", *details]
    return "\n".join(lines) + "\n"


def known_suffix(expectation):
    """What follows a missed expectation's line when the miss is known."""
    return f" (known, {expectation['finding']})" if expectation.get("known") else ""


def write_results(path, records, version, repo, checks_asked, known_findings=frozenset()):
    data = {"repository": repo, "vendoring": version, "checks": checks_asked,
            "known_for": by_number(known_findings),
            "ran_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "results": [seen.as_dict() for seen in records]}
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="ascii")
    return data


def failed_checks(data):
    return [r["check"] for r in data.get("results", []) if r["status"] == "failed"]


def issue_text(data, run_url):
    """The issue's body: which checks failed, each expectation that did
    not hold, and where the run is."""
    lines = [f"The canary on {data.get('repository')} failed on {data.get('ran_at')}, vendoring "
             f"{data.get('vendoring')}. Run: {run_url or '(by hand)'}", ""]
    if data.get("known_for"):
        lines += [f"This run reads a missed expectation as known when it fails for {', '.join(data['known_for'])}, "
                  f"which the vendored release still has; such a line says (known), a check marked known missed "
                  f"nothing else, and every other line is new.", ""]
    for record in data.get("results", []):
        if record["status"] not in ("failed", "known"):
            continue
        lines.append(f"**{record['check']}: {record['status']}**")
        lines += [f"- did not hold: {e['expectation']}{known_suffix(e)}" for e in record["expectations"]
                  if not e["holds"]]
        lines += [f"- {note}" for note in record["notes"]]
        lines.append("")
    lines.append("The values each check saw are in the run's artifact canary.json. The checks are "
                 "validation/DESIGN.md, section 11; scripts/canary.py runs them.")
    return "\n".join(lines) + "\n"


def open_or_update_issue(api, data, run_url=None):
    """One issue, "Canary failed: <checks>", opened on the first failure and
    updated on later ones rather than opening another; the title carries
    the checks that failed this time. What was done, in words."""
    failed = failed_checks(data)
    if not failed:
        return "nothing failed; no issue"
    title = ISSUE_TITLE + ", ".join(failed)
    body = issue_text(data, run_url)
    api.create_label(ISSUE_LABEL, "d93f0b")
    found = [i for i in api.get_all(f"/repos/{api.repo}/issues", {"state": "open", "labels": ISSUE_LABEL})
             if i.get("title", "").startswith(ISSUE_TITLE) and "pull_request" not in i]
    if found:
        number = found[0]["number"]
        api.patch(f"/repos/{api.repo}/issues/{number}", {"title": title, "body": body})
        return f"updated issue #{number}: {title}"
    made = api.create_issue(title, body, [ISSUE_LABEL])
    return f"opened issue #{made['number']}: {title}"


def parse(argv):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="run checks on the canary repository")
    run.add_argument("--checks", nargs="+", default=list(MONTHLY), choices=sorted(CHECKS),
                     help="which checks (default: the monthly ones, %s)" % " ".join(MONTHLY))
    run.add_argument("--out", default="canary-out/canary.json", help="where the records go")
    run.add_argument("--summary", help="a file the job summary is appended to")
    run.add_argument("--repo", default=os.environ.get("CANARY_REPO") or CANARY_REPO)
    run.add_argument("--any-repository", action="store_true",
                     help="run on a repository other than the canary, on purpose: the checks merge into its default "
                          "branch, open issues labelled todo and close any issue or pull request titled 'canary '")
    issue = commands.add_parser("issue", help="open or update the failure issue from a run's records")
    issue.add_argument("records", help="the canary.json a run wrote")
    issue.add_argument("--run-url", default=os.environ.get("CANARY_RUN_URL"))
    commands.add_parser("list", help="the checks and what each settles")
    return parser.parse_args(argv)


def main(argv=None, out=sys.stdout):
    args = parse(argv)
    if args.command == "list":
        for name, (_, what) in CHECKS.items():
            print(f"{name}: {what}{' (monthly)' if name in MONTHLY else ''}", file=out)
        return 0
    if args.command == "issue":
        data = json.loads(Path(args.records).read_text(encoding="utf-8"))
        api = GitHub(os.environ.get("GITHUB_TOKEN"), os.environ["GITHUB_REPOSITORY"])
        print(open_or_update_issue(api, data, args.run_url), file=out)
        return 0
    if args.repo != CANARY_REPO and not args.any_repository:
        print(f"the canary runs on {CANARY_REPO} alone, and {args.repo} is another repository: its checks merge into "
              f"the default branch and close every issue and pull request titled 'canary ', so pass "
              f"--any-repository to run there on purpose", file=out)
        return 2
    token = os.environ.get("CANARY_TOKEN")
    if not token:
        print("CANARY_TOKEN is not set: an installation token for the canary repository is needed", file=out)
        return 2
    site = Site(token, args.repo, admin_token=os.environ.get("CANARY_ADMIN_TOKEN"),
                runs_allowed=os.environ.get("CANARY_RUNS", "").lower() == "true")
    return run_site(site, args.checks, args.out, args.summary, out)


def run_site(site, checks, out_path, summary_path, out=sys.stdout):
    """What main does once it has a site: the adoption first, then the
    checks, the records, the summary and the exit code: 0 when every check
    passed or is known, 1 when one failed, 2 when the canary is not ready."""
    try:
        version = adopted(site)
    except GitHubError as problem:
        text = (f"{SUMMARY_HEADING}\n\nNot ready: GitHub refused the first read of {site.repo} ({problem}), so the "
                f"token does not reach the repository as the run needs: check the mint's repositories and "
                f"permissions in .github/workflows/canary.yml, and that the App is installed there.\n")
        print(text, file=out)
        if summary_path:
            append(summary_path, text)
        return 2
    if version is None:
        text = (f"{SUMMARY_HEADING}\n\nNot ready: {site.repo} has no tools/VENDORED on its default branch, so it is "
                f"not adopted yet. Adopt it through the front door (OpenSpineConsortium/symphony-start, the form "
                f"its install opens) and run the canary again.\n")
        print(text, file=out)
        if summary_path:
            append(summary_path, text)
        return 2
    known_findings = known_for(version)
    records = run_checks(site, checks, known_findings, out=out)
    data = write_results(out_path, records, version, site.repo, checks, known_findings)
    text = summary(records, version, site.repo, known_findings)
    print(text, file=out)
    if summary_path:
        append(summary_path, text)
    return 1 if failed_checks(data) else 0


def append(path, text):
    """Add to the job summary in ASCII, a character outside it written as
    its escape, as ascii_name writes a branch name: a note can quote git's
    words on a name such as claude/caf\u00e9, and one such character must
    not lose the summary and the run's exit code."""
    with Path(path).open("a", encoding="ascii", errors="backslashreplace") as out:
        out.write(text)


if __name__ == "__main__":
    sys.exit(main())
