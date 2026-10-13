"""F11 reproduction: one refused idle comment ends the whole board run with
exit 1 (cli.run_board), after STATE.md is written locally but before the
workflow's next step would push it to the branch board; the coordinator and
the renames swallow their failures, post_comments does not."""
import sys, tempfile, os
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))  # the prototype's own modules
import explore
from explore import apply, seeded
import model
from sdlc_protocol import cli
from sdlc_protocol.adapters import github

m = seeded((1,), ("a", "b"))
for act in [("claim", "a", 1), ("announce", "a", 1), ("draft", "a", 1), ("ready", "a", 1), ("wait",)]:
    m, _ = apply(m, act)
# a ready pull request idle for 25 hours is owed one idle comment; its POST fails
m.fail_next = "POST /issues/100/comments"
real = github.GitHub
github.GitHub = lambda token, repo, opener=None, sleep=None: real(token, model.REPO, opener=model.FakeGitHub(m), sleep=lambda s: None)
with tempfile.TemporaryDirectory() as scratch:   # removed again once the run is over
    out = os.path.join(scratch, "STATE.md")
    code = cli.run_board(["--out", out, "--sha", m.refs["main"]],
                         {"GITHUB_TOKEN": "t", "GITHUB_REPOSITORY": model.REPO, "GITHUB_EVENT_NAME": "schedule"})
    print("exit code:", code, "| STATE.md written locally:", os.path.exists(out))
