"""Git as the push guard's oracle, tried on a few shapes: each command runs
in two scratch worlds whose bare remote's pre-receive hook records every
ref update it is offered, with its kind (create, delete, fast-forward or
forced), and refuses them all, so nothing ever moves. In the fresh world
the clone's remote-tracking refs match the remote; in the stale world the
remote's claude/7 has moved behind the clone's back, where git refuses a
--force-with-lease itself ("stale info") but still offers a plain force.
The board rules are read off what git offered, not off the command's
words, and set beside the guard's verdict. The clone is checked out on
claude/7, which has diverged from the remote's as after a rebase, and its
main is a commit ahead. Everything happens in temporary directories that
are removed at the end; the host's git configuration is not read."""

import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # the repository's root: the library

from sdlc_protocol import guard  # noqa: E402

ENV = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C", "GIT_CONFIG_GLOBAL": os.devnull,
       "GIT_CONFIG_NOSYSTEM": "1"}
IDENTITY = ["-c", "user.name=probe", "-c", "user.email=probe@example.invalid", "-c", "commit.gpgsign=false",
            "-c", "init.defaultBranch=main"]
ZERO = "0" * 40
HOOK = f"""#!/bin/sh
while read old new ref; do
  if [ "$old" = {ZERO} ]; then kind=create
  elif [ "$new" = {ZERO} ]; then kind=delete
  elif git merge-base --is-ancestor "$old" "$new"; then kind=fast-forward
  else kind=forced; fi
  echo "$ref $kind" >> "$GIT_DIR/offered"
done
exit 1
"""
COMMANDS = (
    "git push origin claude/7",
    "git push --force-with-lease origin claude/7",
    "git push -f origin claude/7",
    "git push -fu origin claude/7",
    "git push origin +claude/7",
    "git push --al origin",
    "git push --mirr origin",
    "git push origin heads/main",
    "git push origin HEAD:main",
    "git push origin claude/7:feature",
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


def world(root, stale):
    remote, clone = root / "remote.git", root / "w"
    git(root, "init", "-q", "--bare", str(remote))
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
    if stale:
        other = root / "other"
        git(root, "clone", "-q", "-b", "claude/7", str(remote), str(other))
        commit(other, "x")
        git(other, "push", "-q", "origin", "claude/7")
    hook = remote / "hooks" / "pre-receive"
    hook.write_text(HOOK, encoding="ascii")
    hook.chmod(0o755)
    return remote, clone


def offered(remote, clone, command):
    """The (ref, kind) pairs git offered the remote for one command."""
    log = remote / "offered"
    log.write_text("", encoding="ascii")
    git(clone, *shlex.split(command)[1:], check=False)
    return {tuple(line.split()) for line in log.read_text(encoding="ascii").splitlines() if line.strip()}


def rules_verdict(fresh, stale):
    """What the board rules say of the effects git offered: a reason the
    push is forbidden, or None."""
    every = fresh | stale
    if any(ref in ("refs/heads/main", "refs/heads/board") for ref, _ in every):
        return "updates the default or the board branch"
    if any(kind == "forced" for _, kind in stale):
        return "a plain forced update: git offered it where a lease would have been refused"
    if any(kind == "forced" and not ref.startswith("refs/heads/claude/") for ref, kind in fresh):
        return "a lease outside claude/"
    if len({ref for ref, _ in every}) > 1:
        return "updates more than one ref"
    return None


def guard_verdict(command):
    reason = guard.refusal(guard.push_arguments(shlex.split(command)), "claude/7", "main")
    return "refuses" if reason else "passes"


def main():
    print(git(".", "--version").stdout.strip())
    roots = {stale: Path(tempfile.mkdtemp(prefix="guard-oracle-")) for stale in (False, True)}
    try:
        worlds = {stale: world(root, stale) for stale, root in roots.items()}
        began = time.perf_counter()
        for command in COMMANDS:
            fresh, stale = (offered(*worlds[s], command) for s in (False, True))
            forbidden = rules_verdict(fresh, stale)
            shown = ", ".join(f"{ref.removeprefix('refs/')} {kind}" for ref, kind in sorted(fresh)) or "nothing"
            print(f"{command}\n  git offers (fresh world): {shown}; in the stale world: "
                  f"{', '.join(sorted(ref.removeprefix('refs/') for ref, _ in stale)) or 'nothing'}\n"
                  f"  the rules: {forbidden or 'allowed'}; the guard {guard_verdict(command)}")
        spent = time.perf_counter() - began
        print(f"{2 * len(COMMANDS)} pushes in {spent:.2f} s, {1000 * spent / (2 * len(COMMANDS)):.0f} ms each")
    finally:
        for root in roots.values():
            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    main()
