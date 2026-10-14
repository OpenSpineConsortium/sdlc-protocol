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
what the two see differently; workflow_deals is the workers' workflow's
one look for a whole run, since F4's fix.
"""

from sdlc_protocol import claims, team
from sdlc_protocol.adapters import github

from . import fake_github
from .model import OWNER, REPO, Issue, moment

FIRST_ISSUE_ID = 1000
TOKEN = "fake-token-for-the-clerk"


def workflow_deals(clients, workers, looks, areas=None):
    """The moves of the workers of one run of the workers' workflow, as it
    is built since F4's fix: the run's first job looks once and deals
    (team.deals), and each leg reads its move from that look, so the
    moments the legs would have looked at, looks, by worker, decide
    nothing but when the run looked, no later than the earliest of them.
    Until the fix each leg asked from its own look (team.next_for at
    clients[look]), which the mutant "each leg dealt from its own look"
    puts back."""
    return team.deals(clients[min(looks)], list(workers), areas)


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

    def claim_text(self, branch):
        """The claim file on the branch at its head, as GitHub's contents
        API gives its text, or None where there is none."""
        number, head = claims.number_of(branch), self.model.refs.get(branch)
        if number is None or head is None:
            return None
        return self.model.tree(head).get(claims.claim_path(number))

    def released(self, branch):
        """Whether a release comment counts for the claim on the branch, as
        the adapter reads it (github.claim_released) from the fake GitHub
        at the model's now, so the clerk's reading of a release is the
        library's own."""
        api = github.GitHub(TOKEN, REPO, opener=self.fake, sleep=lambda seconds: None)
        return github.claim_released(api, branch, now=moment(self.model.now))

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
