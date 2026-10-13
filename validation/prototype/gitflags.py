"""Four push commands the board rules forbid that the push guard passes and
git really performs (part of finding F3): bundled short options with a
force in them (-fu), abbreviations git accepts for --all and --mirror
(--al, --mirr), and a destination written heads/main. Each runs in a fresh
bare repository of its own, from a clone checked out on claude/7, with the
remote's branches printed before and after, so what git did is read from
the remote and not from the command. Everything happens in a temporary
directory that is removed at the end; the host's git configuration is not
read."""

import os
import shlex
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
COMMANDS = (
    "git push -fu origin claude/7",
    "git push --al origin",
    "git push --mirr origin",
    "git push origin heads/main",
)


def git(cwd, *args, check=True):
    done = subprocess.run(["git", *IDENTITY, *args], cwd=cwd, capture_output=True, text=True, env=ENV, timeout=60)
    if check and done.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} failed: {done.stderr.strip()}")
    return done


def commit(clone, name):
    (clone / "a").write_text(name + "\n", encoding="ascii")
    git(clone, "add", "a")
    git(clone, "commit", "-q", "-m", name)


def remote_heads(remote):
    listed = git(remote, "for-each-ref", "--format=%(refname:short)=%(objectname:short)", "refs/heads").stdout
    return " ".join(listed.split()) or "(none)"


def world(root):
    """A bare remote holding main and claude/7, and a clone on claude/7
    whose claude/7 has diverged from the remote's (a rebase's shape) and
    whose main is one commit ahead of the remote's."""
    remote = root / "remote.git"
    git(root, "init", "-q", "--bare", str(remote))
    clone = root / "w"
    git(root, "clone", "-q", str(remote), str(clone))
    commit(clone, "a")
    git(clone, "push", "-q", "origin", "main")
    git(clone, "switch", "-q", "-c", "claude/7")
    commit(clone, "b")
    git(clone, "push", "-q", "-u", "origin", "claude/7")
    git(clone, "reset", "-q", "--hard", "main")
    commit(clone, "c")
    git(clone, "switch", "-q", "main")
    commit(clone, "e")
    git(clone, "switch", "-q", "claude/7")
    return remote, clone


def guard_verdict(command):
    reason = guard.refusal(guard.push_arguments(shlex.split(command)), "claude/7", "main")
    return f"refuses: {reason}" if reason else "passes"


def probe(command):
    root = Path(tempfile.mkdtemp(prefix="gitflags-"))
    try:
        remote, clone = world(root)
        before = remote_heads(remote)
        done = git(clone, *shlex.split(command)[1:], check=False)
        said = " | ".join(line.strip() for line in (done.stdout + done.stderr).splitlines() if "->" in line)
        print(f"{command}\n  guard {guard_verdict(command)}; git exit {done.returncode}: {said or done.stderr.strip()}")
        print(f"  remote before: {before}\n  remote after:  {remote_heads(remote)}")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def main():
    print(git(".", "--version").stdout.strip())
    for command in COMMANDS:
        probe(command)


if __name__ == "__main__":
    main()
