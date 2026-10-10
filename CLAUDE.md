# sdlc-protocol

This repository is the claim protocol for pull-request-gated Claude agents, as the Python package `sdlc_protocol`: the labels, the claim, the board, the protocol check, team mode's clerk, the push guard and the merge gate, with adapters for GitHub, a bare git repository and memory. README.md says what each module holds and who uses it; spec/PROTOCOL.md is the protocol in prose.

Three other repositories run this code, so a change here is a change there: the template vendors it under tools/ with scripts/vendor.sh, coordbench's sdlc arm imports the gate and the bare-git world, and the GitHub App imports the gate and the GitHub world. A rendered string (the board, the guard's refusals, the gate's answers) is part of the interface: change one only on purpose, and say so in the pull request.

## Layout

- sdlc_protocol/: the package. The rules take what they need as arguments, the clock included; a rule that needs the outside world asks a World or a Client for it. The two exceptions stay small and where they are: guard.branches asks the local git which branch is checked out, and board.write_if_changed writes STATE.md.
- sdlc_protocol/adapters/: the worlds and the REST client, the only modules that call GitHub or change a git repository. cli.py is the shell around them: it reads the environment, the event, stdin and the files it is named.
- spec/PROTOCOL.md: the protocol in prose, with no placeholders; it ships in the wheel as sdlc_protocol/spec/PROTOCOL.md.
- tests/: one test module per package module, test_<module>.py, with fixtures under tests/fixtures/.
- scripts/check.sh: the checks; scripts/vendor.sh: the copy into another repository.

## Checks

Run `bash scripts/check.sh` before opening or updating a pull request: it holds every file to ASCII with LF endings and a final newline, then runs the whole suite. CI runs the same script on Python 3.12 as the job named "check". One module's tests run alone with `python3.12 -m unittest tests.test_<module> -v`. The version is `__version__` in sdlc_protocol/__init__.py and `version` in pyproject.toml, changed together; the tests fail when they differ.

## Writing

- Plain prose that says why, in docstrings, comments, commit messages, pull requests and documents alike. What the code does is read from the code.
- Short functions; names that read as English.
- No emoji in code, output or documents. ASCII and LF in every file, each ending with a newline: the package is vendored into repositories whose checks take nothing else.
- No new dependencies: Python 3.12 or later with its standard library, plain bash, and GitHub's own actions, pinned to a major tag. A proposal to add one is an issue first.
- Nothing refers to a file that does not exist.
- In conversation, put a fact worth keeping on its own line tagged `Decision:` or `Constraint:`, so a checkpoint keeps it word for word; the rest of a session is inferred and may be lost.
