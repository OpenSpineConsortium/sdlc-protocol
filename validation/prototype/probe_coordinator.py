"""Probe two coordinator paths: a release whose label removal failed, and an
overlap told again when a claim opens its pull request."""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # the repository's root: the library
from sdlc_protocol import board, coordinator

T0 = datetime(2026, 10, 1, tzinfo=timezone.utc)
iso = lambda t: t.strftime("%Y-%m-%dT%H:%M:%SZ")
commit = T0
released_at = T0 + timedelta(hours=30)   # the coordinator's comment, posted at hour 30
now = T0 + timedelta(hours=31)           # the next board run

# The comment was posted, but taking the labels off failed: claimed and worker:alice stay.
snap = board.Snapshot.from_dict({
    "sha": "m" * 40, "branches": ["claude/5"],
    "issues": [{"number": 5, "title": "t", "labels": ["todo", "claimed", "worker:alice"]}],
    "claims": {"5": "- Worker: alice\n"},
    "claim_issues": {"5": {"state": "open", "labels": ["claimed", "todo", "worker:alice"]}},
    "releases": {"5": {"released_at": iso(released_at), "sha": "c" * 40, "committed_at": iso(commit)}},
})
print("half-released claim, next run: releases_due =", coordinator.releases_due(snap, now),
      "| released_keys =", board.released_keys(snap))
later = now + timedelta(days=30)
print("thirty days on: releases_due =", coordinator.releases_due(snap, later), "| released_keys =", board.released_keys(snap))

# Overlap: claim #7 (no pull request) and PR #12 meet in a.py; then #7 opens PR #13.
first = {"sha": "m" * 40, "branches": ["claude/7", "claude/9"],
         "claims": {"7": "- Worker: a\n\n## Files to touch\n\n- a.py\n", "9": "- Worker: b\n"},
         "claim_issues": {"7": {"state": "open", "labels": []}, "9": {"state": "open", "labels": []}},
         "pulls": [{"number": 12, "head": {"ref": "claude/9"}, "files": ["a.py"], "draft": True}]}
due1 = coordinator.overlap_comments_due(first, {})
print("run 1 tells:", [(d.number, d.marker) for d in due1])
told = {d.number: {int(n) for n in d.marker.split("overlap ")[1].split(" -->")[0].split()} for d in due1}
second = dict(first, pulls=first["pulls"] + [{"number": 13, "head": {"ref": "claude/7"}, "files": ["a.py"], "draft": True}])
due2 = coordinator.overlap_comments_due(second, told)
print("run 2 tells:", [(d.number, d.marker) for d in due2])
