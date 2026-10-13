"""A prototype of the validation harness's repository model: the state GitHub
holds for the claim protocol (refs, commits, issues with labels and comments,
pull requests, the clock), a fake REST opener that answers the real
sdlc_protocol.adapters.github code from it, and the protocol's verbs as
actions. Scratch only: it measures what the design document proposes."""

import base64
import copy
import hashlib
import io
import json
import sys
import urllib.error
import urllib.parse
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # the repository's root: the library

from sdlc_protocol import board, claims, labels  # noqa: E402
from sdlc_protocol.adapters import github  # noqa: E402

T0 = datetime(2026, 10, 1, tzinfo=timezone.utc)
BOT = "github-actions[bot]"
OWNER = "owner"
REPO = "o/r"


def iso(hour):
    return (T0 + timedelta(hours=hour)).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class Commit:
    parents: tuple
    author: str
    at: int
    files: tuple  # sorted (path, content) pairs: the whole tree
    message: str


@dataclass
class Comment:
    author: str
    association: str
    at: int
    body: str


@dataclass
class Issue:
    number: int
    title: str
    state: str = "open"
    labels: frozenset = frozenset({"todo"})
    comments: tuple = ()


@dataclass
class Pull:
    number: int
    head: str
    author: str
    opened: int
    draft: bool = True
    state: str = "open"
    merged_at: int = -1
    body: str = ""
    comments: tuple = ()


@dataclass
class Model:
    now: int = 0
    commits: dict = field(default_factory=dict)
    refs: dict = field(default_factory=dict)
    issues: dict = field(default_factory=dict)
    pulls: dict = field(default_factory=dict)
    next_number: int = 100
    ever_heads: frozenset = frozenset()   # every commit a claude/ branch ever pointed at
    label_events: tuple = ()              # (hour, issue, who, "+"/"-", label): the timeline GitHub keeps
    board_text: str = ""
    fail_next: str = ""                   # "METHOD path-prefix" of the next call to refuse, for fault runs
    writers: frozenset = frozenset({"a", "b", "c", OWNER})
    pending: object = None

    # --- commits and refs ---------------------------------------------------------

    def commit(self, parents, author, files, message):
        record = Commit(tuple(parents), author, self.now, tuple(sorted(files.items())), message)
        sha = hashlib.sha1(repr(record).encode()).hexdigest()
        self.commits[sha] = record
        return sha

    def tree(self, sha):
        return dict(self.commits[sha].files) if sha else {}

    def ancestors(self, sha):
        seen, todo = set(), [sha]
        while todo:
            one = todo.pop()
            if one and one not in seen:
                seen.add(one)
                todo.extend(self.commits[one].parents)
        return seen

    def is_ancestor(self, older, newer):
        return older in self.ancestors(newer)

    def set_ref(self, name, sha):
        self.refs[name] = sha
        if claims.is_claim_branch(name):
            self.ever_heads = self.ever_heads | {sha}

    def reachable(self):
        found = set()
        for sha in self.refs.values():
            found |= self.ancestors(sha)
        return found

    # --- reading the state the way the rules need it ------------------------------

    def claim_holder(self, ref):
        """The worker the claim file on this branch names, or None."""
        text = self.tree(self.refs.get(ref, "")).get(claims.claim_path(claims.number_of(ref)))
        return claims.claim_worker(text) if text else None

    def open_pull_from(self, ref):
        return next((p for p in self.pulls.values() if p.head == ref and p.state == "open"), None)

    def files_changed(self, ref):
        """The paths a branch changes beside its merge base with main, as a
        pull request's files are."""
        head_sha = self.refs[ref]
        base = self.merge_base(head_sha, self.refs["main"])
        head, old = self.tree(head_sha), self.tree(base)
        return sorted(p for p in set(head) | set(old) if head.get(p) != old.get(p))

    def merge_base(self, one, two):
        common = self.ancestors(one) & self.ancestors(two)
        best = [c for c in common if not any(c != d and c in self.ancestors(d) for d in common)]
        return min(best) if best else ""

    def key(self):
        """A canonical, hashable form of the state for deduplication. Times are
        kept absolute; the design's gap capping is not applied here."""
        return (
            self.now,
            tuple(sorted(self.refs.items())),
            tuple(sorted((n, i.state, tuple(sorted(i.labels)), tuple((c.author, c.at, c.body[:60]) for c in i.comments))
                         for n, i in self.issues.items())),
            tuple(sorted((n, p.head, p.author, p.draft, p.state) for n, p in self.pulls.items())),
            self.fail_next,
            self.pending is not None,
        )

    def clone(self):
        return copy.deepcopy(self)


# --- the fake REST API over the model -------------------------------------------------

class Answer:
    def __init__(self, body):
        self.raw = b"" if body is None else json.dumps(body).encode()
        self.headers = {}

    def read(self):
        return self.raw

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


def refusal(url, code, message=""):
    body = json.dumps({"message": message}).encode() if message else b""
    return urllib.error.HTTPError(url, code, "refused", {}, io.BytesIO(body))


class FakeGitHub:
    """Answers the calls the real adapter makes, from the model, and changes
    the model for the calls that write. Unknown calls are a 404, as GitHub
    answers a path it has no resource for."""

    def __init__(self, model):
        self.model = model
        self.calls = []

    def open(self, call, timeout=None):
        url = call.full_url
        parts = urllib.parse.urlsplit(url)
        path = parts.path.removeprefix(f"/repos/{REPO}")
        query = dict(urllib.parse.parse_qsl(parts.query))
        method = call.get_method()
        body = json.loads(call.data) if call.data else None
        self.calls.append(f"{method} {path}")
        if self.model.fail_next and f"{method} {path}".startswith(self.model.fail_next):
            self.model.fail_next = ""
            raise refusal(url, 500, "injected failure")
        found = self.answer(method, path, query, body)
        if isinstance(found, int):
            raise refusal(url, found)
        return Answer(found)

    def answer(self, method, path, query, body):
        m = self.model
        segments = [urllib.parse.unquote(s) for s in path.strip("/").split("/")]
        if method == "GET" and segments == ["issues"]:
            items = [self.issue_json(i) for i in m.issues.values() if i.state == "open"]
            items += [{"number": p.number, "pull_request": {}, "labels": []} for p in m.pulls.values() if p.state == "open"]
            return items
        if method == "GET" and segments[:1] == ["issues"] and len(segments) == 2:
            issue = m.issues.get(int(segments[1])) if segments[1].isdecimal() else None
            return self.issue_json(issue) if issue else 404
        if segments[:1] == ["issues"] and len(segments) == 3 and segments[2] == "comments":
            number = int(segments[1])
            if method == "GET":
                return [self.comment_json(c) for c in self.comments_of(number)]
            comment = Comment(BOT, "NONE", m.now, body["body"])
            self.add_comment(number, comment)
            return {"created_at": iso(m.now)}
        if method == "DELETE" and segments[:1] == ["issues"] and len(segments) == 4 and segments[2] == "labels":
            issue = m.issues[int(segments[1])]
            name = segments[3]
            if name in issue.labels:
                issue.labels = issue.labels - {name}
                m.label_events += ((m.now, issue.number, BOT, "-", name),)
            return []
        if method == "GET" and segments == ["pulls"]:
            state = query.get("state")
            if state == "closed":
                return [{"number": p.number, "merged_at": iso(p.merged_at) if p.merged_at >= 0 else None,
                         "title": f"PR {p.number}", "head": {"ref": p.head}} for p in m.pulls.values() if p.state != "open"]
            return [{"number": p.number} for p in m.pulls.values() if p.state == "open"]
        if method == "GET" and segments[:1] == ["pulls"] and len(segments) == 2:
            return self.pull_json(m.pulls[int(segments[1])])
        if method == "GET" and segments[:1] == ["pulls"] and len(segments) == 3 and segments[2] == "files":
            pull = m.pulls[int(segments[1])]
            return [{"filename": f} for f in m.files_changed(pull.head)] if pull.head in m.refs else []
        if method == "GET" and segments == ["branches"]:
            return [{"name": name, "commit": {"sha": sha}} for name, sha in sorted(m.refs.items())]
        if method == "GET" and segments[:1] == ["contents"]:
            ref = query.get("ref", "main")
            text = m.tree(m.refs.get(ref, "")).get("/".join(segments[1:]))
            return {"content": base64.b64encode(text.encode()).decode()} if text is not None else 404
        if method == "GET" and segments[:2] == ["git", "commits"]:
            commit = m.commits.get(segments[2])
            return {"committer": {"date": iso(commit.at)}} if commit else 404
        if method == "GET" and segments[:1] == ["collaborators"]:
            return {"permission": "write"} if segments[1] in m.writers else 404
        if method == "GET" and segments[:3] == ["git", "ref", "heads"]:
            name = "/".join(segments[3:])
            return {"object": {"sha": m.refs[name]}} if name in m.refs else 404
        if method == "POST" and segments[:1] == ["branches"] and segments[-1] == "rename":
            old, new = "/".join(segments[1:-1]), body["new_name"]
            if old not in m.refs:
                return 404
            if new in m.refs:
                return 422
            sha = m.refs.pop(old)
            m.refs[new] = sha
            for pull in m.pulls.values():   # spec/PROTOCOL.md: GitHub closes the branch's pull request
                if pull.head == old and pull.state == "open":
                    pull.state = "closed"
            return {}
        if method == "DELETE" and segments[:3] == ["git", "refs", "heads"]:
            name = "/".join(segments[3:])
            if name not in m.refs:
                return 422
            del m.refs[name]
            return None
        return 404

    def comments_of(self, number):
        if number in self.model.issues:
            return self.model.issues[number].comments
        pull = self.model.pulls.get(number)
        return getattr(pull, "comments", ())

    def add_comment(self, number, comment):
        target = self.model.issues.get(number) or self.model.pulls[number]
        target.comments = tuple(getattr(target, "comments", ())) + (comment,)

    def issue_json(self, issue):
        return {"number": issue.number, "title": issue.title, "state": issue.state,
                "labels": [{"name": n} for n in sorted(issue.labels)], "updated_at": iso(0), "body": ""}

    def comment_json(self, c):
        return {"body": c.body, "created_at": iso(c.at), "user": {"login": c.author}, "author_association": c.association}

    def pull_json(self, pull):
        m = self.model
        sha = m.refs.get(pull.head, "")
        if pull.draft:
            state = "draft"
        elif sha and m.is_ancestor(m.refs["main"], sha):
            state = "clean"
        else:
            state = "behind"
        updated = max([pull.opened] + ([m.commits[sha].at] if sha else []))
        return {"number": pull.number, "title": f"PR {pull.number}", "draft": pull.draft,
                "head": {"ref": pull.head, "sha": sha}, "base": {"ref": "main"}, "mergeable": True,
                "mergeable_state": state, "updated_at": iso(updated), "user": {"login": pull.author}}


# --- the board's run, in cli.board_live_run's order, on the model's clock ------------

def board_first_half(model):
    """The board's run up to the rename: collect, coordinate, render. The
    snapshot waits on the model for the second half, so other verbs can
    happen between, as they do during a workflow run."""
    api = github.GitHub("t", REPO, opener=FakeGitHub(model), sleep=lambda s: None)
    now = T0 + timedelta(hours=model.now)
    snap = github.snapshot(api, model.refs["main"], "main", now)
    github.coordinate(api, snap, now)
    model.board_text = board.render(snap)
    model.pending = (snap, now)


def board_second_half(model, event="schedule", ref=""):
    api = github.GitHub("t", REPO, opener=FakeGitHub(model), sleep=lambda s: None)
    snap, now = model.pending
    model.pending = None
    lines = github.release_claims(api, snap)
    return lines + github.post_comments(api, board.comments_due(snap, event, ref, now))


def board_run(model, event="schedule", ref=""):
    api = github.GitHub("t", REPO, opener=FakeGitHub(model), sleep=lambda s: None)
    now = T0 + timedelta(hours=model.now)
    snap = github.snapshot(api, model.refs["main"], "main", now)
    lines = list(github.coordinate(api, snap, now))
    model.board_text = board.render(snap)
    lines += github.release_claims(api, snap)
    lines += github.post_comments(api, board.comments_due(snap, event, ref, now))
    return lines


# --- setting a world up -----------------------------------------------------------

def seeded(issues=(1,), workers=("a", "b")):
    m = Model()
    m.writers = frozenset(workers) | {OWNER}
    base = m.commit((), OWNER, {"README.md": "base\n"}, "base")
    m.refs["main"] = base
    for n in issues:
        m.issues[n] = Issue(n, f"Issue {n}")
    board_run(m)
    return m


def claim_text(n, worker):
    return (f"# Claim: #{n}\n\n- Issue: https://github.com/{REPO}/issues/{n}\n- Worker: {worker}\n"
            f"- Branch: claude/{n}\n\n## Plan\n\nDo issue {n}.\n\n## Files to touch\n\n- src/{n}.py\n- src/shared.py\n")
