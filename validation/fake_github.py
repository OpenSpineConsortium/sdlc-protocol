"""A fake of GitHub's REST API that answers from a RepoModel (DESIGN.md, 3.2).

FakeGitHub is an opener: the library's GitHub adapter takes it through the
opener argument it already has (adapters/github.py, GitHub.__init__), and
every call the adapter makes is answered from the model, a write changing
the model as GitHub would. It serves exactly the endpoints the adapter's
board run calls (the snapshot, the coordinator, the renames and the
comments); the clerk's and the gate's come with the steps that drive them
(DESIGN.md, 14). A call to any other endpoint raises
Unserved, which no part of the adapter catches, so a call the harness
does not model stops the run loudly rather than reading as a 404.

It is faithful to the transport under the adapter, where the prototype's
fake was not:
- The request line is ASCII. Before it answers, the fake encodes the
  method, the selector and the protocol as http.client does, which raises
  UnicodeEncodeError for any other character before anything is sent.
- List endpoints answer 100 items a page, with a Link header naming the
  next page.
- A pull request's mergeable is null on the first read of its detail in a
  run, and computed on the next, as GitHub computes it late.

Fault makes chosen calls fail: an HTTP status, GitHub's message and, for a
rate limit, a retry-after header, or the token's hour spent and ending a
given number of seconds on by the client's clock, which the fake keeps as
the model's minute (FakeGitHub.clock). A fault of a rate limit's, a 429 or
a 403 that says so, answers one try of the call, which the library's
client makes again after the wait GitHub asks for
(adapters.github.rate_wait), so a fault refuses a call for the run only
with times enough for every try. The client's sleep is the fake's
(FakeGitHub.sleep), which takes no time and keeps each wait the client
makes between two tries of one call on the try it makes next
(Call.waited), so the harness judges a call by its last try and holds
each wait to the one GitHub asked for (invariants.answered,
waits_as_asked), while a call the library makes again of its own, a read
of a push after head_since's pause, stands for itself. With applied, the
write is made and its answer lost, as a 502 after GitHub wrote the
comment; with answer, a read of the activity is answered with a list
other than the model's, one that lags a push or lists one the run did not
see (F8).

What the fake assumes of GitHub, each settled by the documentation or by a
canary check (DESIGN.md, 3.2 and 11) unless said: the issues list holds
the pull requests; a draft's mergeable_state is the word its branch's
standing gives, as any pull request's (C0, read on 2026-10-14 on live
drafts: "clean" for a clean one, "dirty" for a conflicting one, never
"draft"), and "behind" for a branch that is behind, assumed, under a rule
that requires the branch to be up to date before merging, as the
template's main ruleset does (C0 read no branch behind); with the model's
opaque_pulls on, the word is "blocked" wherever the branch is not in
conflict, with mergeable true, as a rule that holds the pull request
gives, a required check pending or red or a review not yet given, and the
comparison carries behind_by (F9); with the model's lenient_rules on,
the word for a branch that is behind is "clean", as GitHub gives under a
rule that does not require an up-to-date branch or under none (F16), and
with both on the word is "blocked", since a rule that holds the pull
request holds it whatever its branch; a rename closes the pull request
whose head the branch is, and so does a delete (the web UI's page says
so; canary C6 settles the REST delete), never one from a fork, whose
branch of the same name stands in the fork; a pull request from a fork
names its author's fork in its head and this repository in its base, in
its detail and in the list's entries, and the list's head filter,
owner:branch, matches the repository the branch stands in (F17); a pull
request names its author and the author's author_association, in its
detail and in the list's entries, and the permission API answers admin
for the owner, write for the model's other writers, read for anyone else
and 403 for a login the model says GitHub will not answer for (F19); a
rename onto a
name that stands answers 422; the board posts as github-actions[bot]
with the author_association NONE; a pull request's updated_at moves
with pushes and comments; and the repository's activity lists the push
that made each branch at GitHub's own time, newest first (F13), with the
head that push set, and the push that last set each branch's head, with
the head it set and the head it moved the branch from (F8; canary C5
read a forced push's before and after on 2026-10-15), with the push
before it, or, for a branch a push made again, the delete before that
push, at that delete's own time with the head it took away as the head
before, assumed, as GitHub's branch_deletion entry (F8's second review), a
renamed branch keeping them, which the model's rename does and canary C6
now records (DESIGN.md section 11, What C6 records of the activity); a
call GitHub refuses for a rate limit is one it did not act on, so such a
fault's refusal writes nothing, assumed, which the client's making a
write again rests on, and which C4 would settle and is not run (DESIGN.md,
3.2 and 11); and a 403 that names a wait in retry-after is a rate limit's
refusal whatever its words, assumed as the library assumes it
(adapters.github.rate_limited), since GitHub's documentation names
retry-after for a secondary limit's refusal and never says that no other
refusal sends it (DESIGN.md, 3.2).
"""

import base64
import fnmatch
import hashlib
import io
import json
import sys
import urllib.error
import urllib.parse
from typing import NamedTuple

from sdlc_protocol.adapters import github

from .model import BOT, MAIN, OWNER, REPO, Comment, iso, moment

PER_PAGE = 100
# GitHub's contents API gives the text of a file of up to 1 MB and answers
# a bigger one with an empty content and the encoding "none".
CONTENTS_LIMIT = 1024 * 1024
BASE = f"/repos/{REPO}"


class Unserved(Exception):
    """A call the fake does not model."""


class Fault(NamedTuple):
    """Make the next times calls whose "METHOD path" (the path after
    /repos/OWNER/NAME, unquoted) matches "METHOD path", a glob in which *
    stands for any run of characters, fail with status. applied makes the
    write and loses its answer. query, pairs of a parameter and its value,
    narrows the calls to those whose query carries each, a value of None
    to those whose query lacks the parameter, so one read of the activity
    is told from another (F8). answer, for a read of the activity, has the
    call answered with status, 200, and a list the model's own answer is
    changed into, in place of a refusal (FakeGitHub.activity): LAGGING, the
    activity as it stood before each branch's last push, as a read GitHub
    answers before it lists that push, the push before it newest (F8's
    second review); LAGGING_BY_TWO, as it stood before the last two, as an
    activity catching up answers a first read, the second a push behind
    (F8's third review); MOVED, the activity with a push after the run read
    the branch, from the head the run read to one it never saw, at the
    run's minute, which answers every later read of that branch in the
    run as GitHub would, listing that push still (FakeGitHub.moved_since),
    and which the model does not take, so the next run reads the branch as
    the model holds it; MOVED_TWICE, as MOVED with a second push after it
    in the same minute, so the newest entry names the head the run read on
    neither side (F8's fix check); DELETED, the activity with the branch's
    delete after the run read it, at the run's minute, naming the head the
    run read as the head it took away, listed at every later read of the
    branch in the run as MOVED is and not taken by the model either (F8's
    third review). then is the fault that takes the calls this one matches
    once its times are spent, and fires on no call before that, whatever
    it matches (FakeGitHub.fault_for; until F8's third review a link fired
    on the first call it matched, which held only while it matched the
    same calls as its head), so one fault can refuse a read and answer the
    next in place of the model, or the other way about (F8's fix check: a
    read refused and the next listing a push after the run read the
    branch, and a read a push behind and the next refused). reset_after,
    for a rate limit's refusal, says the token's hour is spent and ends
    that many seconds on by the client's clock (FakeGitHub.clock), in the
    two headers of the hour the client reads, x-ratelimit-remaining 0 and
    x-ratelimit-reset, so the wait until the hour's end is the fault's own
    whatever the model's minute."""

    method: str
    path: str
    status: int = 500
    times: int = 1
    message: str = "injected"
    retry_after: str | None = None
    applied: bool = False
    query: tuple = ()
    answer: str = ""
    then: object = None
    reset_after: int | None = None

    def chain(self):
        """This fault and each that takes its calls after it (then), in
        the order they fire."""
        fault = self
        while fault is not None:
            yield fault
            fault = fault.then

    def matches(self, call, query):
        """Whether the call, "METHOD path", and its query are this fault's."""
        if not fnmatch.fnmatchcase(call, f"{self.method} {self.path}"):
            return False
        return all(query.get(name) == value for name, value in self.query)


# How a fault answers a read of the activity in place of the model (Fault.answer).
LAGGING, LAGGING_BY_TWO, MOVED, MOVED_TWICE, DELETED = "lagging", "lagging by two", "moved", "moved twice", "deleted"
# The answers that list an entry after the run read the branch, a push
# (MOVED, MOVED_TWICE) or the branch's delete (DELETED), which GitHub goes
# on listing at every later read of the branch in the run.
PUSHED_SINCE = (MOVED, MOVED_TWICE, DELETED)
# The sha GitHub's activity names as the head before the push that made a branch.
NO_SHA = "0" * 40


class Call(NamedTuple):
    """One call as the fake saw it: what was asked, the body, the status it
    answered, and whether a write it made took effect."""

    method: str
    path: str
    query: tuple
    body: object
    status: int
    applied: bool
    # Whether a Fault made the answer, as against a refusal the model gives.
    injected: bool = False
    # The Fault that made the answer, None where the model gave it, so a
    # check can read what the fault said: GitHub's message for a refusal,
    # and how a fault answered in place of the model (Fault.answer).
    fault: object = None
    # The list a read of the repository's activity was answered with, as
    # its reader got it, so a check can read what a run was told of each
    # push (invariants.push_dates); None for every other call.
    answered: object = None
    # The seconds the library's client waited before this call, the try it
    # made after its try before was refused (adapters.github.GitHub.request,
    # FakeGitHub.sleep); None for a call made at once, or after a pause of
    # the library's own, which is no try of the call before it.
    waited: object = None


class Answer:
    """What urllib's opener returns: read(), headers, a context manager."""

    def __init__(self, body, headers=None):
        self.raw = b"" if body is None else json.dumps(body).encode("utf-8")
        self.headers = headers or {}

    def read(self):
        return self.raw

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


def refusal(url, status, message="", headers=None):
    body = json.dumps({"message": message}).encode("utf-8") if message else b""
    return urllib.error.HTTPError(url, status, message or "refused", headers or {}, io.BytesIO(body))


class Refused(Exception):
    """A status the fake answers in place of a body."""

    def __init__(self, status):
        super().__init__(status)
        self.status = status


def label_event_json(entry):
    """One entry of the model's label history as GitHub's issue events
    give it."""
    return {"event": "labeled" if entry.added else "unlabeled", "label": {"name": entry.label},
            "created_at": iso(entry.at)}


class FakeGitHub:
    def __init__(self, model, faults=()):
        self.model = model
        # Each link of each fault's chain with the times it has left and the
        # index of the link before it in its chain, None for a chain's head,
        # whose times must be spent before this link fires (fault_for).
        self.faults = []
        for fault in faults:
            head = None
            for link in fault.chain():
                self.faults.append([link, link.times, head])
                head = len(self.faults) - 1
        self.calls = []
        # The seconds of the wait the client made since its last try, for
        # the try it makes next (sleep, record); None while it made none.
        self.waiting = None
        self.computed = set()
        # The fault listing a push after the run read the branch (MOVED or
        # MOVED_TWICE) each branch's activity met in the run, by the ref
        # its read named (moved_since).
        self.moved = {}

    # --- the client's sleep and clock ---------------------------------------

    def sleep(self, seconds):
        """The client's sleep, which takes no time here. The client's wait
        between two tries of one call, the sleep GitHub.request makes, is
        kept for the try it makes next (record), so a check tells the
        client's tries of one call from a call the library makes again of
        its own after a pause, head_since's or settled's, which sleeps from
        elsewhere and is kept as no wait. The two share the client's one
        sleep, so the caller is read from the frame that called this one,
        which is why the client is given this method itself and no wrapper
        around it (cycle.api_for)."""
        if sys._getframe(1).f_code is github.GitHub.request.__code__:
            self.waiting = seconds

    def clock(self):
        """The client's clock: the model's minute in seconds since the
        epoch, which x-ratelimit-reset is written in (Fault.reset_after)."""
        return moment(self.model.now).timestamp()

    def record(self, call):
        """Keep a call as the fake answered it, with the wait the client
        made before it where it is the try after a refused one."""
        self.calls.append(call._replace(waited=self.waiting))
        self.waiting = None

    # --- the opener ---------------------------------------------------------

    def open(self, request, timeout=None):
        method = request.get_method()
        f"{method} {request.selector} HTTP/1.1".encode("ascii")
        parts = urllib.parse.urlsplit(request.full_url)
        if not parts.path.startswith(BASE):
            raise Unserved(f"{method} {parts.path}")
        segments = [urllib.parse.unquote(s) for s in parts.path[len(BASE):].strip("/").split("/")]
        query = dict(urllib.parse.parse_qsl(parts.query))
        body = json.loads(request.data) if request.data else None
        path = "/" + "/".join(segments)
        fault = self.fault_for(f"{method} {path}", query) or self.moved_since(method, path, query)
        if fault is not None and fault.answer in PUSHED_SINCE:
            self.moved.setdefault(query.get("ref", ""), fault)
        asked = tuple(sorted(query.items()))
        if fault is not None and fault.answer:
            found, headers = self.answer(method, segments, query, body, request.full_url, fault.answer)
            self.record(Call(method, path, asked, body, fault.status, False, True, fault, found))
            return Answer(found, headers)
        if fault is not None and not fault.applied:
            self.record(Call(method, path, asked, body, fault.status, False, True, fault))
            raise self.fault_answer(request.full_url, fault)
        try:
            found, headers = self.answer(method, segments, query, body, request.full_url)
        except Refused as refused:
            self.record(Call(method, path, asked, body, refused.status, False))
            raise refusal(request.full_url, refused.status) from None
        self.record(Call(method, path, asked, body, fault.status if fault else 200, method != "GET",
                         fault is not None, fault, found if path == "/activity" else None))
        if fault is not None:
            raise self.fault_answer(request.full_url, fault)
        return Answer(found, headers)

    def fault_for(self, call, query=None):
        """The first fault armed for the call, which spends one of its times:
        a link of a chain only once the link before it has spent its own
        (Fault.then), so a chain fires in its order whatever each link
        matches."""
        for entry in self.faults:
            fault, left, head = entry
            if left > 0 and (head is None or self.faults[head][1] == 0) and fault.matches(call, query or {}):
                entry[1] = left - 1
                return fault
        return None

    def moved_since(self, method, path, query):
        """The fault that answered a read of this branch's activity with an
        entry after the run read the branch, a push (MOVED, MOVED_TWICE) or
        its delete (DELETED), for every later read of that branch in the
        run, which GitHub would answer listing that entry still; None for
        any other call, a read of another kind among them."""
        fault = self.moved.get(query.get("ref", "")) if path == "/activity" else None
        return fault if fault is not None and fault.matches(f"{method} {path}", query) else None

    def fault_answer(self, url, fault):
        headers = {"retry-after": fault.retry_after} if fault.retry_after else {}
        if fault.reset_after is not None:
            reset = int(self.clock()) + fault.reset_after
            headers.update({"x-ratelimit-remaining": "0", "x-ratelimit-reset": str(reset)})
        return refusal(url, fault.status, fault.message, headers)

    # --- dispatch -----------------------------------------------------------

    def answer(self, method, s, query, body, url, as_fault=""):
        """The body and the headers GitHub would answer, or Refused; as_fault
        is how a fault changes a read of the activity (Fault.answer), the
        one read whose answer a fault changes."""
        route = (method, s[0] if s else "")
        if route == ("GET", "issues") and len(s) == 1:
            return self.page(self.open_issue_list(), query, url)
        if s[:1] == ["issues"] and len(s) >= 2:
            return self.issue_route(method, s, query, body, url)
        if s[:1] == ["pulls"]:
            return self.pull_route(method, s, query, url)
        if route == ("GET", "branches") and len(s) == 1:
            listed = [{"name": name, "commit": {"sha": sha}} for name, sha in sorted(self.model.refs.items())]
            return self.page(listed, query, url)
        if route == ("POST", "branches") and s[-1] == "rename":
            return self.rename("/".join(s[1:-1]), body["new_name"]), {}
        if route == ("GET", "contents"):
            return self.contents("/".join(s[1:]), query.get("ref", MAIN)), {}
        if s[:1] == ["git"]:
            return self.git_route(method, s), {}
        if route == ("GET", "collaborators") and s[-1] == "permission":
            if s[1] in self.model.unanswered:
                raise Refused(403)
            word = "admin" if s[1] == OWNER else "write" if s[1] in self.model.writers else "read"
            return {"permission": word, "role_name": word}, {}
        if route == ("GET", "compare"):
            base, _, sha = s[1].partition("...")
            return self.compare(base, sha), {}
        if route == ("GET", "rules"):
            return self.page([], query, url)
        if route == ("GET", "rulesets"):
            raise Refused(404)
        if route == ("GET", "commits") and s[-1] == "check-runs":
            return {"total_count": 0, "check_runs": []}, {}
        if route == ("GET", "activity") and len(s) == 1:
            return self.page(self.activity(query, as_fault), query, url)
        raise Unserved(f"{method} /{'/'.join(s)}")

    def page(self, items, query, url):
        """One page of a list endpoint, with a Link header to the next."""
        number = int(query.get("page", "1"))
        size = int(query.get("per_page", PER_PAGE))
        chunk = items[(number - 1) * size:number * size]
        headers = {}
        if number * size < len(items):
            parts = urllib.parse.urlsplit(url)
            asked = dict(urllib.parse.parse_qsl(parts.query))
            asked["page"] = str(number + 1)
            headers["Link"] = f'<{parts._replace(query=urllib.parse.urlencode(asked)).geturl()}>; rel="next"'
        return chunk, headers

    # --- issues and comments ------------------------------------------------

    def open_issue_list(self):
        found = [self.issue_json(i) for i in self.model.issues.values() if i.state == "open"]
        found += [self.pull_as_issue(p) for p in self.model.pulls.values() if p.state == "open"]
        return sorted(found, key=lambda item: -item["number"])

    def issue_route(self, method, s, query, body, url):
        number = int(s[1]) if s[1].isdigit() and s[1].isascii() else None
        target = self.model.target(number) if number is not None else None
        if target is None:
            raise Refused(404)
        if len(s) == 2 and method == "GET":
            return (self.issue_json(target) if number in self.model.issues else self.pull_as_issue(target)), {}
        if len(s) == 2:
            raise Unserved(f"{method} /{'/'.join(s)}")
        if s[2] == "comments" and len(s) == 3:
            if method == "GET":
                return self.page([self.comment_json(number, i, c) for i, c in enumerate(target.comments)], query, url)
            if method == "POST":
                self.model.add_comment(number, Comment(BOT, "NONE", self.model.now, body["body"]))
                at = len(self.model.comments_on(number)) - 1
                return self.comment_json(number, at, self.model.comments_on(number)[at]), {}
        if s[2] == "events" and len(s) == 3 and method == "GET" and number in self.model.issues:
            return self.page(self.events_json(self.model, self.model.issues[number]), query, url)
        if s[2] == "labels" and number in self.model.issues:
            if method == "POST" and len(s) == 3:
                for name in body["labels"]:
                    self.model.add_label(number, BOT, name)
                return [{"name": n} for n in sorted(self.model.issues[number].labels)], {}
            if method == "DELETE" and len(s) == 4:
                if not self.model.remove_label(number, BOT, s[3]):
                    raise Refused(404)
                return [{"name": n} for n in sorted(self.model.issues[number].labels)], {}
        raise Unserved(f"{method} /{'/'.join(s)}")

    def issue_json(self, issue):
        """An issue as GitHub gives it, with its state_reason: reopened while
        an issue closed and opened again stays open (F22), completed once
        it is closed, and none for one never closed."""
        reason = "reopened" if issue.reopened else "completed" if issue.state == "closed" else None
        return {"number": issue.number, "title": issue.title, "state": issue.state, "body": issue.body,
                "labels": [{"name": n} for n in sorted(issue.labels)], "updated_at": iso(0), "state_reason": reason}

    @staticmethod
    def events_json(model, issue):
        """An issue's events as GitHub lists them, oldest first: its label
        history and its latest close among it, the events the coordinator
        reads them for (F22's reviews)."""
        mine = [(at, e) for at, e in enumerate(model.label_events) if e.issue == issue.number]
        labelled = [label_event_json(e) for at, e in mine if at < issue.closed_after]
        later = [label_event_json(e) for at, e in mine if at >= issue.closed_after]
        closed = [] if issue.closed_at is None else [{"event": "closed", "created_at": iso(issue.closed_at)}]
        return labelled + closed + later

    def pull_as_issue(self, pull):
        return {"number": pull.number, "title": pull.title, "state": "open" if pull.state == "open" else "closed",
                "body": pull.body, "labels": [], "pull_request": {"url": f"{BASE}/pulls/{pull.number}"},
                "updated_at": iso(pull.updated_at)}

    @staticmethod
    def comment_json(number, index, comment):
        return {"id": number * 1000 + index, "body": comment.body, "created_at": iso(comment.at),
                "user": {"login": comment.author}, "author_association": comment.association}

    # --- pull requests ------------------------------------------------------

    def pull_route(self, method, s, query, url):
        if method != "GET":
            raise Unserved(f"{method} /{'/'.join(s)}")
        pulls = self.model.pulls
        if len(s) == 1:
            state = query.get("state", "open")
            chosen = [p for p in pulls.values() if (p.state == "open") == (state == "open")]
            if "head" in query:
                chosen = [p for p in chosen if f"{head_owner(p)}:{p.head}" == query["head"]]
            if state == "closed":
                chosen.sort(key=lambda p: (-p.updated_at, -p.number))
                return self.page([self.closed_json(p) for p in chosen], query, url)
            chosen.sort(key=lambda p: -p.number)
            return self.page([{"number": p.number, "head": {"ref": p.head, "repo": head_repo(p)},
                               "base": {"ref": MAIN, "repo": {"full_name": REPO}}, "user": {"login": p.author},
                               "author_association": p.association} for p in chosen], query, url)
        number = int(s[1]) if s[1].isdigit() and s[1].isascii() else None
        pull = pulls.get(number)
        if pull is None:
            raise Refused(404)
        if len(s) == 2:
            return self.pull_json(pull), {}
        if s[2:] == ["files"]:
            return self.page([{"filename": f} for f in self.model.pull_files(pull)], query, url)
        raise Unserved(f"{method} /{'/'.join(s)}")

    def closed_json(self, pull):
        return {"number": pull.number, "title": pull.title, "head": {"ref": pull.head},
                "merged_at": iso(pull.merged_at) if pull.merged_at is not None else None,
                "updated_at": iso(pull.updated_at)}

    def pull_json(self, pull):
        """The detail of one pull request; mergeable is null on its first
        read in a run for each head and main it stands on."""
        model = self.model
        sha = model.pull_head(pull) if pull.state == "open" else ""
        standing = model.pull_standing(pull) if sha else "clean"
        seen = (pull.number, sha, model.refs[MAIN])
        if seen in self.computed:
            mergeable = standing != "dirty"
            state = "blocked" if model.opaque_pulls and mergeable else standing
            if state == "behind" and model.lenient_rules:
                state = "clean"
        else:
            self.computed.add(seen)
            mergeable, state = None, "unknown"
        return {"number": pull.number, "title": pull.title, "body": pull.body, "draft": pull.draft,
                "state": "open" if pull.state == "open" else "closed", "user": {"login": pull.author},
                "author_association": pull.association,
                "head": {"ref": pull.head, "sha": sha, "repo": head_repo(pull)},
                "base": {"ref": MAIN, "repo": {"full_name": REPO}},
                "mergeable": mergeable, "mergeable_state": state, "created_at": iso(pull.opened_at),
                "updated_at": iso(pull.updated_at),
                "merged_at": iso(pull.merged_at) if pull.merged_at is not None else None}

    # --- branches, contents and commits -------------------------------------

    def activity(self, query, as_fault=""):
        """The repository's activity as far as the model records it, for
        each branch that stands (branch_entries) and each claim branch
        deleted since it was made (gone_entries), newest first unless asked
        for the oldest first (direction asc), filtered as asked by ref
        (refs/heads/<name> or the name) and by type; a filter the model
        records nothing of, any other type, answers an empty list, as
        GitHub does for activity that never happened. as_fault changes each
        branch's entries as a fault answers (Fault.answer)."""
        model = self.model
        ref = query.get("ref", "")
        name = ref[len("refs/heads/"):] if ref.startswith("refs/heads/") else ref
        kind = query.get("activity_type")
        entries = []
        for branch in model.created:
            if not ref or branch == name:
                entries += self.branch_entries(branch, as_fault)
        for branch in model.gone_made:
            if branch not in model.refs and (not ref or branch == name):
                entries += self.gone_entries(branch)
        newest_first = query.get("direction", "desc") != "asc"
        listed = [entry for _, entry in sorted(entries, key=lambda e: e[0], reverse=newest_first)]
        return [entry for entry in listed if kind is None or entry["activity_type"] == kind]

    def branch_entries(self, branch, as_fault=""):
        """One branch's entries, oldest first, each keyed by its place in
        the order: the delete that took it away before a push made it
        again, naming the head it had as the head before (model.earlier);
        the push that made it, naming the head that push set
        (model.made_heads); the push before the last, when that was a later
        one (model.earlier); and the push that last set its head, when that
        was a later one, naming the head it set and the head it moved the
        branch from (model.pushed, F8), a minute's entries in that order.
        With as_fault LAGGING the newest is left out, as an activity that
        does not list the latest push yet answers, so the entry before it
        is newest, as GitHub's would be a push behind (F8's second
        review), and with LAGGING_BY_TWO the newest two, as one two pushes
        behind (F8's third review); with MOVED one more comes after them, a
        push at the clock's minute from the branch's head to a commit
        nobody made, as a push after the run read the branch; with
        MOVED_TWICE that push and a second in the same minute from its
        commit to another nobody made, as after two pushes since the run
        read the branch, so the newest names the head on neither side
        (F8's fix check); with DELETED the branch's delete at the clock's
        minute, naming its head as the head it took away and no head after,
        as a delete after the run read the branch (F8's third review). A
        branch a test made by hand, with no making's head recorded, names
        its head now for the head its making set."""
        model = self.model
        made = model.created[branch]
        head = model.refs.get(branch, "")
        found = [((made, 1), self.entry(branch, made, "branch_creation", model.made_heads.get(branch, head)))]
        earlier = model.earlier.get(branch)
        if earlier is not None:
            at, kind, after, before = earlier
            place = 0 if kind == "branch_deletion" else 2
            found.append(((at, place), self.entry(branch, at, kind, after or NO_SHA, before or NO_SHA)))
        last = model.pushed.get(branch)
        if last is not None and last[2] != "branch_creation":
            found.append(((last[0], 3), self.entry(branch, last[0], last[2], last[1], last[3] or NO_SHA)))
        found.sort(key=lambda item: item[0])
        if as_fault == LAGGING:
            return found[:-1]
        if as_fault == LAGGING_BY_TWO:
            return found[:-2]
        if as_fault == DELETED:
            found.append(((model.now, 4), self.entry(branch, model.now, "branch_deletion", NO_SHA, head)))
        if as_fault in (MOVED, MOVED_TWICE):
            unseen = hashlib.sha1(f"pushed after the run read {branch} at {model.now}".encode("ascii")).hexdigest()
            found.append(((model.now, 4), self.entry(branch, model.now, "push", unseen, head)))
        if as_fault == MOVED_TWICE:
            again = hashlib.sha1(f"pushed again after the run read {branch} at {model.now}".encode("ascii")).hexdigest()
            found.append(((model.now, 5), self.entry(branch, model.now, "push", again, unseen)))
        return found

    def gone_entries(self, branch):
        """A deleted claim branch's entries, each keyed by its place in the
        order: the push that made it and the delete that took it away
        (model.gone_made, model.deleted), as GitHub's activity lists a
        branch that no longer stands (F22's third review). The model keeps
        only the head the delete took away, which both name, and not who
        made it, so the entries name nobody; no reader of the library's
        takes either from a deleted branch's entries."""
        model = self.model
        at, head = model.deleted[branch]
        made = model.gone_made[branch]
        return [((made, 1), self.entry(branch, made, "branch_creation", head)),
                ((at, 4), self.entry(branch, at, "branch_deletion", NO_SHA, head))]

    def entry(self, branch, at, kind, after, before=NO_SHA):
        return {"ref": f"refs/heads/{branch}", "timestamp": iso(at), "activity_type": kind,
                "actor": {"login": self.model.creators.get(branch, "")}, "before": before, "after": after}

    def rename(self, old, new):
        if old not in self.model.refs:
            raise Refused(404)
        if new in self.model.refs:
            raise Refused(422)
        self.model.rename_ref(old, new)
        return {"name": new}

    def contents(self, path, ref):
        sha = self.model.refs.get(ref)
        text = self.model.tree(sha).get(path) if sha else None
        if text is None:
            raise Refused(404)
        data = text.encode("utf-8")
        if len(data) > CONTENTS_LIMIT:
            return {"type": "file", "path": path, "encoding": "none", "content": ""}
        return {"type": "file", "path": path, "encoding": "base64", "content": base64.b64encode(data).decode("ascii")}

    def git_route(self, method, s):
        model = self.model
        if method == "GET" and s[1] == "commits":
            commit = model.commits.get(s[2])
            if commit is None:
                raise Refused(404)
            return {"sha": s[2], "committer": {"date": iso(commit.committed_at)}}
        if method == "GET" and s[1:3] == ["ref", "heads"]:
            name = "/".join(s[3:])
            if name not in model.refs:
                raise Refused(404)
            return {"ref": f"refs/heads/{name}", "object": {"sha": model.refs[name]}}
        if method == "DELETE" and s[1:3] == ["refs", "heads"]:
            name = "/".join(s[3:])
            if name not in model.refs:
                raise Refused(422)
            model.delete_ref(name)
            return None
        raise Unserved(f"{method} /{'/'.join(s)}")

    def compare(self, base, sha):
        model = self.model
        base_sha = model.refs.get(base)
        if base_sha is None or sha not in model.commits:
            raise Refused(404)
        ahead = model.ancestors(sha) - model.ancestors(base_sha)
        behind = model.ancestors(base_sha) - model.ancestors(sha)
        order = model.in_order(ahead)
        return {"ahead_by": len(ahead), "behind_by": len(behind),
                "commits": [{"sha": c, "commit": {"message": model.commits[c].message}} for c in order],
                "files": [{"filename": f} for f in model.changed(model.merge_base(sha, base_sha), sha)]}


def head_owner(pull):
    """The owner of the repository a pull request's branch stands in: the
    author's fork for one from a fork, this repository otherwise (F17)."""
    return pull.author if pull.fork else REPO.split("/")[0]


def head_repo(pull):
    """The repository a pull request's branch stands in, as GitHub names it
    in the head of the pull request's detail and of the list's entries."""
    return {"full_name": f"{head_owner(pull)}/{REPO.split('/')[1]}"}


def pull_detail(model, pull):
    """A pull request's settled detail, as the protocol check reads one."""
    fake = FakeGitHub(model)
    fake.pull_json(pull)
    return fake.pull_json(pull)

