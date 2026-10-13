"""Probe the clerk's dealing when a peer's ready pull request falls behind
main between two workers' snapshots of the same run."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # the repository's root: the library
from sdlc_protocol import team


class FakeClient:
    def __init__(self, mergeable_state):
        self.state = mergeable_state

    def open_issues(self):
        return [
            {"number": 4, "title": "a's task", "labels": [{"name": "claimed"}, {"name": "worker:a"}], "body": ""},
            {"number": 5, "title": "free", "labels": [{"name": "todo"}], "body": ""},
            {"number": 6, "title": "free", "labels": [{"name": "todo"}], "body": ""},
        ]

    def branch_names(self):
        return ["main", "claude/4"]

    def open_pull(self, branch):
        if branch == "claude/4":
            return {"number": 40, "draft": False, "mergeable_state": self.state, "head": {"sha": "f" * 40}}
        return None

    def check_runs(self, sha):
        return []


workers = ["a", "b", "c"]
print("b asks while a's pull request is clean:", team.next_for(FakeClient("clean"), "b", workers))
print("c asks after main moved past it:      ", team.next_for(FakeClient("behind"), "c", workers))
print("a asks after main moved past it:      ", team.next_for(FakeClient("behind"), "a", workers))
