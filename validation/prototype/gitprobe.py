"""The claim lock in real git: two workers push claude/5 from main into one
bare repository, a first and then b, each with its own claim file. b's plain
push is refused ("fetch first"), b's --force-with-lease with no
remote-tracking ref is refused ("stale info"), and the same lease after a
fetch is taken, so the claim file on the remote then names b (finding F7).
The push guard's verdict on b's lease push is printed beside it. Everything
happens in a temporary directory that is removed at the end; the host's git
configuration is not read."""

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # the repository's root: the library

from sdlc_protocol import guard  # noqa: E402

ENV = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C", "GIT_CONFIG_GLOBAL": os.devnull,
       "GIT_CONFIG_NOSYSTEM": "1"}
IDENTITY = ["-c", "user.name=probe", "-c", "user.email=probe@example.invalid", "-c", "commit.gpgsign=false",
            "-c", "init.defaultBranch=main"]
CLAIM = "docs/agents/claims/5.md"


def git(cwd, *args, check=False):
    done = subprocess.run(["git", *IDENTITY, *args], cwd=cwd, capture_output=True, text=True, env=ENV, timeout=60)
    if check and done.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} failed: {done.stderr.strip()}")
    return done


def ref_lines(done):
    """git's own words about the pushed ref, the lines that name it."""
    return [line.strip() for line in (done.stdout + done.stderr).splitlines() if "claude/5 ->" in line]


def write_claim(clone, worker):
    path = clone / CLAIM
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"- Worker: {worker}\n", encoding="ascii")
    git(clone, "switch", "-q", "-c", "claude/5", "origin/main", check=True)
    git(clone, "add", CLAIM, check=True)
    git(clone, "commit", "-q", "-m", "Claim #5", check=True)


def seed(root):
    remote = root / "remote.git"
    git(root, "init", "-q", "--bare", str(remote), check=True)
    first = root / "seed"
    git(root, "clone", "-q", str(remote), str(first), check=True)
    (first / "README").write_text("base\n", encoding="ascii")
    git(first, "add", "README", check=True)
    git(first, "commit", "-q", "-m", "base", check=True)
    git(first, "push", "-q", "origin", "main", check=True)
    return remote


def push(clone, *args):
    done = git(clone, "push", *args)
    verdict = "taken" if done.returncode == 0 else "refused"
    print(f"  git push {' '.join(args)}: {verdict}; {' | '.join(ref_lines(done)) or done.stderr.strip()}")
    return done


def guard_verdict(command):
    reason = guard.refusal(guard.push_arguments(command.split()), "claude/5", "main")
    return f"guard refuses: {reason}" if reason else "guard passes it"


def main():
    print(git(".", "--version").stdout.strip())
    root = Path(tempfile.mkdtemp(prefix="gitprobe-"))
    try:
        remote = seed(root)
        a, b = root / "a", root / "b"
        git(root, "clone", "-q", str(remote), str(a), check=True)
        git(root, "clone", "-q", str(remote), str(b), check=True)
        print("a claims #5:")
        write_claim(a, "a")
        push(a, "origin", "claude/5")
        print("b claims #5 from the clone it made before a pushed:")
        write_claim(b, "b")
        push(b, "origin", "claude/5")
        push(b, "--force-with-lease", "origin", "claude/5")
        print("b fetches, then leases again:")
        git(b, "fetch", "-q", "origin", check=True)
        push(b, "--force-with-lease", "origin", "claude/5")
        print(f"  {guard_verdict('git push --force-with-lease origin claude/5')}")
        shown = git(root, "--git-dir", str(remote), "show", f"claude/5:{CLAIM}", check=True).stdout.strip()
        print(f"the remote's claude/5 claim file now says: {shown}")
    finally:
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    main()
