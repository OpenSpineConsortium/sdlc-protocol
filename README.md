# sdlc-protocol

The claim protocol for pull-request-gated Claude agents, as one Python package with no dependencies. Accounts and agents share a repository by claiming an issue as a branch `claude/<issue>`, working in the open on a draft pull request, and landing only what merges cleanly with the default branch as it is now. This package holds those rules once, so every place that runs them gives the same answers: the board that shows who holds what, the check that holds a pull request to the protocol, team mode's clerk, the push guard, and the merge gate. The protocol in prose is [spec/PROTOCOL.md](spec/PROTOCOL.md). It needs Python 3.12 or later and its standard library, nothing else.

## What is in it

| Module | What it holds |
| --- | --- |
| `sdlc_protocol.labels` | The labels the protocol reads and writes, with their colours, and the prefixes `account:` and `agent:`. |
| `sdlc_protocol.claims` | The claim branch's name, the claim file `docs/agents/claims/<n>.md` and how to read it, and which claims are due for release. |
| `sdlc_protocol.board` | The board, STATE.md, rendered from a Snapshot of the repository, and the comments it is due to post ("main moved", idle). |
| `sdlc_protocol.protocol_check` | The problems a `claude/<issue>` pull request has against the protocol, for CI to fail on. |
| `sdlc_protocol.team` | Team mode's clerk: seeding an epic's tasks, routing the lead, telling each member what to do next. |
| `sdlc_protocol.guard` | The push guard's rules: which `git push` commands a session may not run. |
| `sdlc_protocol.gate` | The merge gate: the protocol's decisions as a Policy over a World. |
| `sdlc_protocol.adapters` | The worlds: `github` (the REST API), `bare_git` (a shared bare repository), `memory` (tests and simulations). |
| `sdlc_protocol.cli` | `python3 -m sdlc_protocol <board, check, team or guard>`, a thin shell over the modules above. |

The rules are plain functions and dataclasses over what they are handed, and the clock is an argument. Only `adapters/` calls GitHub or changes a git repository, and `cli.py` reads the environment and the event; the two other places that look outside are small and named: `guard.branches` asks the local git which branch is checked out, and `board.write_if_changed` writes STATE.md. That is what lets the same rules run in a GitHub workflow, in a benchmark's hub and in a test.

## The three consumers

The template repository, OpenSpineConsortium/sdlc-template, vendors the package under `tools/sdlc_protocol` with `scripts/vendor.sh`, so its workflows and its Claude Code hooks need nothing but `python3`. Its board workflow, its CI's protocol check, team mode's workflows and the PreToolUse hook on Bash run `python3 -m sdlc_protocol board`, `check`, `team` and `guard` with `PYTHONPATH=tools`, or the path form `python3 tools/sdlc_protocol <command>`, which needs no `PYTHONPATH`. `tools/VENDORED` says which version and commit the copy is.

Coordbench, the benchmark that compares ways of coordinating agents, imports `sdlc_protocol.gate` and `sdlc_protocol.adapters.bare_git` for its sdlc arm: the agents share a bare repository, and the arm's hub claims, opens and lands pull requests through the gate, under a policy in which the gate itself merges. An experimental way of gating is another Policy behind the same interface, kept in coordbench, not here.

The GitHub App, sdlc-app, imports the gate with `sdlc_protocol.adapters.github`: `GitHub(token, repo)` with the App's installation token, and `GitHubWorld(api, policy)` as the world the gate reads and merges through, so the App answers on its webhooks with the same decisions the board and the arm make.

## The policy

`gate.Policy` is how the protocol behaves where it runs, and nothing else changes from one place to another. `claim_prefix` is what a claim branch starts with (`claude/`). `required_check` is the check that must be green before the gate merges (`check`, or empty where merging cleanly is the whole check). `bring_main_in` is how a branch takes in a default branch that has moved: `rebase`, an account's way, with the one `--force-with-lease` the protocol allows, or `merge`, a team member's, so nobody force-pushes. `who_merges` is who moves the default branch: `owner` by hand, `gate` when a pull request is marked ready, or `auto-merge` once the required check is green. `idle_after` is how long a claim may go without a commit before it is released (24 hours). `remote` and `default_branch` are only the names the gate's answers use. `gate.ACCOUNT_POLICY` (rebase, the owner merges) and `gate.TEAM_POLICY` (merge, auto-merge) are the two ways of working the spec describes.

## The world

`gate.World` is where the branches live, as the gate needs to see them, and an adapter implements it. `main_sha()` and `branch_sha(branch)` give commits, or "" when there is none. `standing(branch)` says how a branch stands to the default branch: `clean`, `behind`, `conflicting` or `unknown`. `check(branch)` gives the required check's state on the branch's head: `green`, `red`, `pending` or `none`. `merge(branch)` lands the branch and returns a `MergeResult` with the default branch's new commit, or raises `MainMoved` (the default branch is left as it was), `MergeRefused`, `GateFailed` or, where the policy leaves merging to someone else, `NotAllowed`. `now()` is the world's clock. The GitHub world reads the standing from the pull request's mergeable state and the check from the head's check runs, and merges through the merge API only when `who_merges` is `gate`; the bare-git world asks git itself and names the conflicting files, runs no checks and merges with `git merge --no-ff`, pushed without force; the memory world takes standings and checks set by hand and a clock that moves only when told. The gate's refusals exit with codes a script can tell apart: 1 refused, 2 someone else's, 3 main moved, 4 gate failed.

## The command line

Each subcommand takes the arguments the template's own tool for the job took, so a workflow line changes only in what it runs. These are the template's workflow lines, rewritten:

```
python3 -m sdlc_protocol board --default-branch "$DEFAULT_BRANCH" --sha "$(git rev-parse HEAD)" --out "$RUNNER_TEMP/STATE.md"
python3 -m sdlc_protocol check
python3 -m sdlc_protocol team route --event "$EVENT" --issue "$ISSUE" --label "$LABEL"
python3 -m sdlc_protocol team seed --epic "$EPIC" --plan "$RUNNER_TEMP/plan.json"
python3 -m sdlc_protocol team next --agent "$AGENT" --label "$LABEL"
python3 -m sdlc_protocol guard < payload.json
```

`board` and `team` call GitHub with `GITHUB_TOKEN` on the repository in `GITHUB_REPOSITORY` (`team --repo OWNER/NAME` names another), and `board` also posts the comments that are due unless given `--render-only`; `board --dry-run tests/fixtures/board.json` renders a saved snapshot with no network at all. `check` reads the pull request from the event in `GITHUB_EVENT_PATH`. `team` takes the members from `CLAUDE_TEAM`, a JSON list such as `["w1","w2"]`. `guard` reads the hook's JSON on stdin and exits 2, with the reason on stderr, to refuse a push.

## Running the tests

From the repository's root, with Python 3.12, the oldest version the package supports:

```
python3.12 -m unittest discover -s tests -v     # the whole suite
python3.12 -m unittest tests.test_gate -v       # one module's tests
bash scripts/check.sh                           # what CI runs
```

`scripts/check.sh` first holds every file to ASCII with LF endings and a final newline, since the package is vendored into repositories whose own checks demand that, then runs the whole suite. CI (`.github/workflows/ci.yml`) runs it on Python 3.12 as the job named `check`.

## Vendoring

```
scripts/vendor.sh <target repository>
```

The script copies `sdlc_protocol/` to `<target>/tools/sdlc_protocol/`, replacing whatever was there and leaving Python's caches behind, writes `<target>/tools/VENDORED` with the line `sdlc-protocol <version> <commit>`, and prints what it did. The commit ends in `-dirty` when the package had changes not yet committed, and is `unknown` outside a git checkout, so the line never names a commit the copy does not match; check out a release tag before vendoring, and the line names the tag's commit. Nothing in the target is committed: review the change there and land it by that repository's own pull request. A target can test its copy by comparing the version in `tools/VENDORED` with `__version__` in `tools/sdlc_protocol/__init__.py`.

## License

MIT; see [LICENSE](LICENSE).
