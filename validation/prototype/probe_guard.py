"""Probe which pushes the guard's refusal rule lets through, current branch claude/7, default main."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # the repository's root: the library
from sdlc_protocol import guard

commands = [
    "git push origin main",
    "git push origin HEAD:main",
    "git push origin claude/7:refs/heads/main",
    "FOO=1 git push origin main",
    "GIT_TRACE=1 git push origin HEAD:main",
    "env git push origin main",
    "command git push origin main",
    "nice git push origin main",
    "bash -c 'git push origin main'",
    "sh -c \"git push --force origin claude/7\"",
    "git push --force origin claude/7",
    "git push --force-with-lease origin claude/8",
    "git push origin claude/8",
    "git push origin --delete claude/8",
    "git push -d origin claude/8",
    "git push origin :claude/8",
    "git push origin +claude/7:claude/8",
    "git push origin claude/7:board",
    "git -c push.default=upstream push",
    "git push --mirror origin",
    "time git push origin main",
    "xargs git push origin < refs.txt",
]
for command in commands:
    args_list = [a for argv in guard.simple_commands(command) if (a := guard.push_arguments(argv)) is not None]
    reasons = [guard.refusal(a, "claude/7", "main") for a in args_list]
    verdict = next((r for r in reasons if r), None)
    print(f"{'REFUSE' if verdict else 'pass  '}  {command}")
