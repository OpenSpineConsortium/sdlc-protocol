"""The clerk's client over the model: team.Client answered from a RepoModel.

team.py's clerk (next_for, seed, route) reads GitHub through the few calls
of team.Client. ModelClient answers them from a RepoModel, in the shapes
GitHub's REST API gives, the same shapes fake_github.FakeGitHub serves the
board's run, so the clerk is run over the harness's worlds with no HTTP
at all: the dealing lane (dealing.py) hands three workers snapshots of
one model, and the planning table (tests/test_validation_tables.py) seeds
an epic's tasks into one and reads the board that renders them.

A snapshot is a client over a clone of the model at the moment the worker
looked, so a change made after one worker's look and before another's is
what the two see differently.
"""

from . import fake_github
from .model import Issue, OWNER

FIRST_ISSUE_ID = 1000


class ModelClient:
    def __init__(self, model, check_runs=None):
        self.model = model
        self.fake = fake_github.FakeGitHub(model)
        self.runs = dict(check_runs or {})

    def snapshot(self):
        return ModelClient(self.model.clone(), self.runs)

    # --- reads --------------------------------------------------------------

    def issue(self, number):
        issue = self.model.issues[number]
        return {**self.fake.issue_json(issue), "id": FIRST_ISSUE_ID + number}

    def open_issues(self):
        return [self.issue(i.number) for i in self.model.issues.values() if i.state == "open"]

    def branch_names(self):
        return sorted(self.model.refs)

    def open_pull(self, branch):
        pull = self.model.open_pull_from(branch)
        return fake_github.pull_detail(self.model, pull) if pull else None

    def check_runs(self, sha):
        return list(self.runs.get(sha, ()))

    # --- writes, as seed and route make them --------------------------------

    def create_label(self, name, colour):
        pass

    def create_issue(self, title, body, labels):
        number = max(self.model.issues, default=0) + 1
        self.model.issues[number] = Issue(number, title, labels=frozenset(labels), body=body)
        return self.issue(number)

    def add_sub_issue(self, parent, child_id):
        pass

    def set_body(self, number, body):
        self.model.issues[number].body = body

    def remove_label(self, number, name):
        self.model.remove_label(number, OWNER, name)
