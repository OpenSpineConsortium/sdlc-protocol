# Validating symphony-protocol: the harness design

This is the design of the harness that validates this library against its spec: scripted scenarios, an exhaustive small-scope checker, a mutation lane that tests the checker, standard-library property lanes, CrossHair run by hand, and a monthly canary on live GitHub. It is the one document a later session builds the harness from. It joins a first design and an adversarial critique of that design, both written on 2026-10-13 in a session's scratch space: the critique's corrections are applied in place, and the prototype both of them measured is kept in [prototype/](prototype/), whose README says how to run each script and what it printed.

- **Line numbers** in this repository's files refer to commit cf3bdb0, where the package (`sdlc_protocol/`, version 0.4.0) was last changed by a996ec9. They will move as the package changes; the function names beside them will not. Line numbers in `validation/prototype/` refer to its files as this branch adds them.
- **Other repositories' files** are named with the repository: symphony-template's `.github/workflows/board.yml` (read at a1d9695), coordbench's `PARITY.md` and the other coordbench files section 1 names (read at d6a3480; PARITY.md was last changed by e8e72f7), beethoven-qa's `src/beethoven_qa/gate.py` (read at 85f0e1c), symphony-app's `manifest.json` (read at 17cea0c), and GitHub's documentation by its path in the github/docs repository, read on 2026-10-13.
- **Bare module names.** A name such as claims.py or adapters/github.py is a file of the package, under `sdlc_protocol/`; a bare name that follows a path in the same list, as measure.py does in "validation/prototype/explore.py and measure.py", is a file in that path's folder. Paths inside a scenario's own repository (`src/1.py`, `docs/agents/claims/1.md`) and REST paths are not files of this one.
- **Planned files.** Build step 1 of section 14 is built: validation/model.py, fake_github.py, cycle.py, verbs.py, policies.py, oracle.py, invariants.py, explore.py and page.py, GUARANTEES.md, and their tests, tests/test_validation_*.py. The files the later steps will add, and the folder `validation-out/` their lanes write to, are listed in section 12 and are marked "planned" where they are named; every other path in this document names a file that exists at this commit.
- **The package does not change here.** F1, F3, F5, F6, F10 and F11 are fixed in 0.4.1 (pull request #11, released and synced on 2026-10-13); F2 and F12 are still open, each waiting on the owner's choice of fix; F4, F7, F8 and F9 are decisions for the owner (section 14); F13 and F14, found by build step 1 when its oracle was held to the spec's words, are two more readings where the library and the spec part, for the owner to settle on either side.
- **Where the numbers come from.** Every number was measured in one container: 4 CPUs shared with other sessions, Python 3.12.3, git 2.43.0. State counts are exact and were reproduced on this branch; times move with the load.

## Summary

The harness runs the library's own code, never a copy of it:
- **The board side.** `github.collect`, `coordinate`, `release_claims` and `post_comments` run against a fake of GitHub's REST API that keeps state. The fake plugs in through the `opener` argument the adapter already takes (adapters/github.py:112-118), and it is faithful to the transport under it: a request line that is not ASCII fails in the fake as `http.client` makes it fail live.
- **The merge gate** runs over the library's own MemoryWorld and BareGitWorld.
- **Workers are scripts, not Claude.** They act in an in-memory model and, for two scenarios and every counterexample, in a real bare git repository, where git itself refuses the second push. A clock that only the scenario moves drives both.
- **Judging.** An oracle written from the text of spec/PROTOCOL.md, importing nothing from the package, computes from the state before each board run what that run must do. The run's actual effects (comments by kind and target, labels taken off, renames, deletions, in their order) must equal the oracle's; the run must never raise over a refused write or a branch name; and a second run with nothing new must make no write call.

There are five lanes:
1. **Fast**, inside the required check `check`, adding at most 60 seconds: scenarios S01 to S05 in memory (S01 and S05 also in git), one regression trace per finding, three table-driven tests (the gate's presets, the protocol check, the clerk's planning), the checker at depth 5 to 6 plus searches started from scenario prefixes, the guard grammar judged by git's recorded answers, the dealing search, and a property lane over the Unicode classes Python's own `str` methods define.
2. **Mutation**, the harness's acceptance test: 10 to 20 mutants of the library's rules, each of which the fast lane's searches must catch.
3. **Deep**, weekly and by hand: two issues at depth 7, one issue at depth 8, a seeded soak, every counterexample and a sample of paths replayed in git, git's answers for the whole guard grammar, and the gate over BareGitWorld.
4. **CrossHair**, by hand from a scratch virtual environment, over the same assert-shaped properties, with no dependency in CI.
5. **Canary**, monthly and by hand, for the few things only live GitHub shows, on the internal test repository OpenSpineConsortium/sdlc-template-test.

The prototype and its critique found twelve things, and build step 1 two more. Section 13 has the evidence for each and how to reproduce it.

| | Finding | Status |
| --- | --- | --- |
| F1 | A release whose label removal failed is never completed: the claim stays held for good | fixed in 0.4.1 |
| F2 | Overlapping work is told again when a claim opens its pull request | open; the owner's choice of fix |
| F3 | The push guard passes forced pushes and pushes to main in plain shapes: a `+` refspec, bundled short options (`-fu`), abbreviations git accepts (`--al`, `--mirr`), a destination written `heads/main`, and any command behind `VAR=value`, `env`, `command`, `nice` or `time` | fixed in 0.4.1 |
| F4 | The clerk can deal one issue to two workers of the same run | decision for the owner |
| F5 | Two branch names can claim one issue (`claude/03` beside `claude/3`) | fixed in 0.4.1 |
| F6 | One hand-made branch name stops every board run: `claude/\u00b2` in the render, and any non-ASCII name, `claude/caf\u00e9` included, in a live run | fixed in 0.4.1 |
| F7 | A worker that has fetched can take someone else's claim with `--force-with-lease` | decision for the owner |
| F8 | Idleness is measured by the committer date, which the person pushing sets | decision for the owner |
| F9 | A draft pull request may never be told "main moved" | decision for the owner |
| F10 | Releasing a finished draft by hand never takes effect | fixed in 0.4.1 |
| F11 | One refused comment fails the whole board run, and the board branch is not written | fixed in 0.4.1 |
| F12 | "Main moved" is lost when the board's concurrency group replaces the run the push to main started | open; the owner's choice of fix |
| F13 | The Overlaps' landing order reads "the oldest" as the lowest number, an issue's and a pull request's alike, so a claim made later lands before an older draft | found by step 1; the library or the spec, the owner's choice |
| F14 | Given to workers needs the label todo and leaves epics out, which the spec's sentence does not say, so an issue labelled only worker:<name> is shown nowhere | found by step 1; the library or the spec, the owner's choice |

What held wherever the prototype looked, and how far that goes:
- Under honest workers, one issue had one holder, and a holder who came back between the release comment and the rename kept the claim.
- Every merge-gate invariant held over 21,848 to 24,920 states for each of four policies. Over MemoryWorld those invariants partly check MemoryWorld itself, so the ones that matter move to BareGitWorld (G13).
- "No work is ever lost" held only because the prototype had no verb that drops a commit. The spec allows a rebase pushed with a lease and the tidy workflow's deletions, so the guarantee is restated as the board's and the coordinator's alone (G4).
- The checker was not sensitive enough. With a sound state key it caught 1 of 8 hand-made mutants of the library, and 4 of 8 once a 24-hour wait was added (section 7). The corrections in sections 2 to 6 are what that measurement asked for.

## 1. What made the coordbench pilots convincing, and how each part carries over

| coordbench | Evidence in coordbench | The harness |
| --- | --- | --- |
| Every arm runs on the same task pair, side by side | bench/cooperbench/pilot.sh:1-11 and 35-40 run solo through sdlc on one task, then the report | The scenarios' verbs drive the memory and the git world alike, and the checker uses the same verbs. |
| The score comes from CooperBench's own code, not a rewrite of it | PARITY.md row 0 (87-97): coordbench/run.py calls CooperBench's own `test_merged` | The harness drives the library's real adapter, board and gate. Only GitHub, and in the memory world git, are stood in for. |
| Every difference is stated, with a status | PARITY.md:1-9, its status words at 28-35 and its summary table at 64-79 | validation/GUARANTEES.md has one row per guarantee sentence of the spec, with a status: held, held under an assumption, contradicted (naming the finding) or not checked. Section 3.6 is the same kind of table for the worlds. |
| One measure, computed two independent ways | PARITY.md row 1 (99-122) keeps the cascade's two verdicts apart | The oracle restates the spec from its text, and its answer is compared with what the board rendered and what the run did. |
| Nothing calls a model unless the owner runs it on purpose | PARITY.md, decision 4 (1796-1806) | Workers are scripts. No token, no Claude. |
| The report is pinned by tests on synthetic inputs | tests/test_experiment_report.py | tests/test_validation_report.py (planned) pins the Markdown on a synthetic results file. |
| The command behind each measure is named, with what it costs | PARITY.md, "Producing each measure" (2026-2061) | The lane table in section 12. |

coordbench's comparison page, one self-contained file that opens offline (PARITY.md, decision 11 at 1838-1842; coordbench/compare.py), is the model for an HTML report. Here that report comes last and is optional (section 10).

## 2. The guarantees

### 2.1 What a run records

A run is a list of steps. Each step records the time, the actor, the verb, its arguments and its outcome. After each step the harness keeps:
- **Refs and commits:** each ref's commit; for each commit its parents, author, committer date and tree; and every commit that was ever the head of a claim branch.
- **Issues:** state, labels, and comments with their whole bodies (author, author_association, created_at, body). It also keeps the label events GitHub keeps on an issue's timeline: who added or took off which label, and when.
- **Pull requests:** number, head branch, author, draft or ready, open, closed or merged, updated_at, and comments with their whole bodies.
- **Commands:** every git command a worker ran, the push guard's verdict on it (guard.run, guard.py:235-255) and, in the git world, what git did.
- **Board runs:** the event that queued each run, and whether it ran or was replaced while it waited. For a run that ran: the snapshot it collected, every write call it made in order (method, path, and for a comment its kind and target), the STATE.md text, whether it raised, its exit code, and whether the board branch was written.
- **The clock.**

### 2.2 Terms, from the spec's text

Each term restates a sentence of spec/PROTOCOL.md, cited by line, over the records above. None is defined through the function under test; section 2.6 says how the oracle keeps to that.

- **claude/\<n\>**, the claim branch of issue n, spelled exactly as the spec spells it (line 29: "Push a new branch `claude/<issue number>`"): n in ASCII decimal digits, as GitHub numbers issues, with no sign and no leading zero. Any other name under `claude/` is hand-made, and what the library makes of it is G1's question.
- **The holder of claude/\<n\>:** the worker whose push created the branch, as the step that made it records. That push's claim file names the same worker on its `- Worker:` line (line 31).
- **What the readers show** for a branch: its row on the board, issue and worker (board.claim_rows, board.py:526-536); the numbers board.claimed_numbers (323-326) and team.claim_branches (team.py:144-147) take it to claim; and, for a pull request from it, protocol_check.claim_number (protocol_check.py:33-36).
- **Idle at t:** more than 24 hours between the committer date of the branch's head and t (line 67: "no commit for more than 24 hours, by the committer date of its head"). F8 is the trust this places in whoever pushes.
- **Finished:** the issue is labelled in-review, or a pull request from the branch is marked ready, or a draft's author's latest comment on it starts with "finished; ready to mark" (line 67).
- **Release comment:** a comment on the issue whose first line is `<!-- sdlc-coordinator: claim-released -->` (line 67), by the repository's owner, a member of its organization, a collaborator, `github-actions[bot]`, or anyone else who may write to the repository (line 69).
- **due(n) at t:** claude/\<n\> exists, the issue is open, the claim is not finished, it is idle at t, and no release comment came more than 24 hours after the branch's last commit (line 67: the coordinator "skips a claim whose latest release comment came more than 24 hours after the branch's last commit").
- **released(n):** line 69's sentence. The issue is open, labelled neither claimed nor in-review; its pull request, if any, is still a draft; and its latest release comment came more than 24 hours after the branch's last commit, with no commit since. The library's own reading also excepts a finished draft (F10); the oracle follows the spec.
- **A piece of open work:** a claim, or an open pull request; a claim and the pull request from its branch are one piece. Line 73 has the coordinator tell "each piece of open work about the work it meets, once" (F2).

### 2.3 The guarantees

Each guarantee is one function in validation/invariants.py, run at one of five kinds of moment:
- **state:** after every step;
- **cycle:** on every board run, given the state before and after it and the run's write calls;
- **command:** on every command a worker runs;
- **gate:** on every step of the gate's world;
- **dealing:** on every set of answers the clerk gives the workers of one run.

The spec column gives line numbers in spec/PROTOCOL.md. A guarantee that names effects requires them to happen when they are owed, not only at most once: a coordinator that posted nothing passed the first design's "at most once" versions of G7 and G10.

| Id | Guarantee | Spec | Code under test | Kind |
| --- | --- | --- | --- | --- |
| G1 | **One name, one holder, by what every reader shows.** For every open issue n, the branches that any reader takes to name n are at most one, and it is claude/\<n\>. From the push that creates claude/\<n\> until a release or a merge frees the name, every reader names its holder and no other worker. Defining G1 through `claims.number_of` alone would let it pass vacuously if that function grew too strict, and a plain push that edits the claim file's Worker line changes whom the board names without changing who pushed first. | 3, 29, 104 | claims.key_of, number_of and claim_worker (claims.py:78-115); board.claim_rows (526-536); team.claim_branches (144-147); protocol_check.claim_number (33-36) | state |
| G2 | **A release comment only on a due claim.** Every release comment the coordinator posts is on an issue that was due when the run began. | 67 | coordinator.is_candidate and releases_due (41-63); claims.release_due (194-208) | cycle |
| G3 | **A due claim is released, in the spec's order.** In a run with no injected fault: claimed and every worker label taken off first; then one release comment by `github-actions[bot]`, the marker on its first line, and no release comment posted while a label it takes off is still on (the order of 0.4.1; cf3bdb0 posted the comment first, F1); then claude/\<n\> renamed to `released/<n>-<first seven of its head>`; the branch's open pull request closed by the rename; and n in the Queue. | 67, 69 | github.coordinate, release_one, release_claims and free_name (adapters/github.py:457-504, 536-586) | cycle |
| G3L | **No half release.** A claim whose release comment stands, more than 24 hours after its last commit and with no commit since, whose label claimed nobody took off and nobody put back (read from the label events), is completed by the next run with no fault. | 67, 69 | as G3 | cycle |
| G4 | **The board and the coordinator never make a commit unreachable.** Every commit reachable from some ref before a board run is reachable from some ref after it, and a run deletes claude/\<n\> only when `released/<n>-<sha7>` already holds its head. A rebase pushed with a lease (line 55) and the tidy workflow's deletions (line 59) may drop commits; they are flows the protocol allows, not the board's, so "no work is ever lost" as a whole was false and is not claimed. | 59, 69 | github.free_name (570-586) | cycle |
| G5 | **A release frees nothing it should not.** A marker comment by an untrusted author, or one made within a day of the last commit, or one followed by a commit, leaves claude/\<n\> named and n off the Queue. Putting claimed back undoes a release. | 69 | claims.is_release_comment and was_released (225-272); board.is_released (348-359) | cycle |
| G6 | **The coordinator never releases a finished claim.** While the claim is finished, however long it waits, `github-actions[bot]` posts no release comment on its issue, and no rename follows from such a comment. A person's release of a finished draft is not the coordinator's act, and G6 says nothing of it: line 69 counts it released, so G9 and the effects comparison (section 2.4) owe its rename, which the board on cf3bdb0 never makes (F10). | 67 | github.releases (370-398); coordinator.is_candidate (49-51); board.is_released (355-358) | cycle |
| G7 | **Overlaps are told when owed, once, in order.** Every file that more than one piece of open work changes is listed with those pieces in landing order: a blocker first, then work marked ready, then the oldest. In the run that first sees a piece meet work it has not been told of, that piece gets exactly one comment, whose marker names every piece it meets. A piece already told of every piece it meets gets none, and a claim and the pull request it opens count as one piece. | 73 | board.landing_key and overlaps (597-646); coordinator.overlap_comments_due (219-234); github.tell_overlaps (507-527) | cycle |
| G8 | **Labels name the holder.** After a release the issue carries neither claimed nor any worker label, in all three spellings. An issue a worker's label names stays off the Queue, under Given to workers, until it is claimed. The first design's "after Announce" half checked the scripted worker's own step, not the library, and is a scenario expectation instead. | 17, 67 | labels.holder_names and reserved (67-118); coordinator.labels_released (137-143); board.given_rows (565-580) | state |
| G9 | **The board renders the state, and a run with nothing new writes nothing.** The Claims rows are exactly the claim branches the oracle does not count released; the Queue and Given to workers are the oracle's; the page is ASCII; and one snapshot always renders the same bytes. A second board run with nothing new makes no write call. Two runs' pages need not be the same bytes: board.py:544 renders a pull request's updated_at, which a comment moves. | 3, 69 | board.render (660-683); board.py docstring 9-17 | cycle |
| G10 | **Each kind of comment is posted when owed, and once.** "Main moved": every open pull request but a released claim's that is behind or conflicting with main at commit M hears so once for M, even when the run the push of M started was replaced in the concurrency group (F12). "Idle": once per pull request with no change for more than a day, never to a released claim's. "Released": once per new branch name, after the rename. "Rename refused": once per target name, when GitHub refused the rename for good, while every later run tries the rename again. | 55, 69 | board.comments_due (456-476); github.comment_once (436-441), release_claim (547-567) and explain_refusal (589-608) | cycle |
| G11a | **The guard refuses every push it can read that the board rules forbid.** It can read a simple command whose program is git; whether it also reads past `VAR=value`, `env`, `command`, `nice` and `time` is part of F3's fix. Forbidden, by what git would do (section 8): an update of the default or the board branch, however the destination is spelled; a plain forced update, however the force is spelled; a lease outside claude/; a wholesale push. A refusal of a push git shows harmless fails the lane only for the shapes the rules name as allowed. | 104; symphony-template's .claude/rules/board.md | guard.push_arguments, refusal and run (guard.py:123-193, 235-255) | command |
| G11b | **What only the second fence enforces.** "Never push to another worker's branch" needs to know whose a branch is, which the command does not say, and the guard cannot read a command inside a string another program runs (`bash -c`, `sh -c`, `xargs`). guard.py:14-17 leaves what it cannot read to branch protection on GitHub. The harness records each such push as the second fence's, and the canary measures that fence (F7). | 104 | none: GitHub's rules | command |
| G12 | **The protocol check passes what the protocol prescribes and fails the rest:** the claim file there while the pull request is a draft and gone once it is ready, a body that closes its issue, and a branch in the repository itself. | 107 | protocol_check.problems_for (48-74) | state |
| G13 | **The merge gate behaves as specified, in eight parts.** (1) Each issue has one claimant and one pull request, opened by that claimant. (2) Only the author marks it ready, and marking it ready releases the claim. (3) The gate merges only where the policy says it merges. (4) It merges only on a green required check. (5) It merges only a branch that merges cleanly with main as main is now. (6) MainMoved leaves main as it was. (7) Main only moves forward. (8) Each refusal exits 1, 2, 3 or 4 by its kind. Parts 3 to 5 and 7 run over BareGitWorld in the deep lane: over MemoryWorld, parts 3 to 5 check MemoryWorld's own rule (adapters/memory.py:107-117) and standing words the prototype set by hand (validation/prototype/gate_explore.py:62-66), and the prototype never checked part 7 (validation/prototype/gate_explore.py:82-109). | 79-99 | gate.claim, open_pull, mark_ready, land and record_outcome (316-576); MemoryWorld.merge (adapters/memory.py:107-117); BareGitWorld.merge_commits (adapters/bare_git.py:259-278) | gate |
| G14 | **Workers of one run are never dealt the same issue.** | 77 | team.deal and next_for (248-282) | dealing |
| G15 | **A board run never raises over a refused write or a branch name.** With any one write refused (a comment's POST, a label's DELETE, a rename, a ref's DELETE; answered 403, 422, 500, or a secondary rate limit), a run whose reads succeeded still exits 0, its board reaches the branch board, and the refused write is owed again to a later run (F11). Whatever the branches under `claude/` are named, hand-made names included, a run whose reads succeeded exits 0 and its board reaches the branch board (F6); a read the adapter could not even send, for a path that is not ASCII, is the run's own failure, not a read that failed. | 3 | github.post_comments and comment_once (436-452); cli.board_live_run and run_board (cli.py:98-127); for names, claims.by_number (99-103), board.claimed_numbers, claim_rows and open_work (323-326, 526-536, 615-636), and github.claim_text and claim_issues (266-275, 292-306) | cycle |

### 2.4 The precise checks for the central guarantees

```
G1   for n in open issues: {r : some reader takes r to name n} is empty or is {claude/<n>}
     for r = claude/<n>, from its creation to its release or merge: every reader's worker for r == holder(r)
G2   for c in the release comments the run posted: oracle.due(before, issue of c)
G3   for n with oracle.due(before, n), in a fault-free run: effects(run, n) == oracle.release_effects(before, n),
     in order: the comment's POST, then the label DELETEs, then the rename; and n in the rendered Queue
G3L  no n in after with claude/<n> present, a release comment c more than 24 h after the head and no commit since,
     "claimed" on the issue, and no label event on "claimed" at or after c
G4   reachable(before) <= reachable(after), for every board run
G9   rendered Claims == {claude/<n> in after : not oracle.released(after, n)}
     rendered Queue == oracle.queue(after); rendered Given == oracle.given(after); the page is ASCII
     running the board again from after makes no write call
G15  exit code == 0 for every board run in which every read that reached GitHub succeeded,
     whatever one write answered and whatever the branches are named
all  effects(run) == oracle.effects(before, event), as (kind, target) pairs in the spec's order
```

### 2.5 Guarantees not yet covered

Each needs its precise reading from the spec before it becomes an invariant:
- **U1.** A second pull request, or a branch that is no claim, closing a claimed issue (line 103: "One issue, one branch `claude/<issue>`, one pull request").
- **U2.** The claim's first commit holds only the claim file (line 29).
- **U3.** Planning gives each task to "a registered worker whose area fits" (line 77), but team.owners_for (team.py:316-332) never checks areas.
- **U4.** Work a blocker stands in the way of waits until the blocker is done (line 63).

### 2.6 The oracle, from the spec's text

validation/oracle.py restates the terms of section 2.2 directly over the model: refs, trees, labels, comments and the label events. It never reads a `board.Snapshot`, and it imports nothing from `sdlc_protocol`, not even a constant. The marker, the branch names, the label names, "finished; ready to mark", the overlap marker and the 24 hours are spelled in the oracle as the spec spells them. tests/test_validation_guarantees.py checks that each one, and each spec sentence an oracle function quotes, appears in spec/PROTOCOL.md word for word. So a change to a library constant shows up as a disagreement, and a change to the spec breaks that test until the oracle and validation/GUARANTEES.md follow.

The prototype shows why. Its oracle builds the expected Queue with `claims.number_of` and `labels.reserved` (validation/prototype/explore.py:96-100), the functions under test, so a fault in either moves the oracle with it and is never seen; and its marker came from `claims.RELEASE_MARKER`.

The oracle also takes one switch per entry of validation/known.json (planned). A known finding is then reported once, as known, and the search goes on past it (section 6.1).

## 3. The worlds

### 3.1 The memory world

validation/model.py holds `RepoModel` and its records: `Commit`, `Issue`, `Comment`, `PullRequest` and `LabelEvent`.
- Its clock counts minutes from the start of the scenario.
- `commit()` is content-addressed, stamped with the clock.
- `push(worker, ref, sha, force=False, lease=None)` follows git's rule as validation/prototype/gitprobe.py measured it. A push is taken when it creates the ref or fast-forwards it. Otherwise it is refused, unless it is forced, or it is a lease that matches the worker's own remote-tracking ref. Refusals use git's own words ("fetch first", "stale info").
- Other methods: `ancestors`, `merge_base`, `tree`, `standing` (draft, clean, behind or dirty, where a conflict is any path both sides changed since the merge base), `canonical()`, `digest()` and `clone()`.

### 3.2 The fake GitHub

validation/fake_github.py holds `FakeGitHub`. It is an opener: `open(request, timeout)` returns an object with `read()` and `headers`, or raises `urllib.error.HTTPError`. tests/test_github.py already fakes this interface with fixed answers (its `FakeAnswer` and `FakeOpener`, lines 26-73); FakeGitHub answers from the model and changes it on writes. It serves exactly the endpoints the adapter calls:
- issues, open, and one by number;
- pulls, open and closed, one by number, and its files;
- branches;
- the contents of the claim file at a ref;
- `git/commits/{sha}`, `git/ref/heads/{b}`, and `git/refs/heads/{b}` to delete;
- issue comments, read and post;
- `collaborators/{login}/permission`;
- an issue's label, delete;
- `compare/{base}...{sha}`;
- `branches/{b}/rename`;
- `rules/branches/{b}` and `rulesets/{id}`.

It is faithful to the transport, which the prototype's fake was not:
- **The request line is ASCII.** Before it answers, the fake encodes the request line (method, `call.selector`, protocol) as ASCII and raises `UnicodeEncodeError` as `http.client` does before it connects. The adapter puts some branch names into paths raw (adapters/github.py:270, 303, 396), and `GitHub.request` turns that error into "the answer was not JSON" (150-151). The prototype's fake parsed `full_url` and unquoted it (validation/prototype/model.py, `FakeGitHub.open` and `answer`), so it could never show F6's live failure.
- **Pagination.** List endpoints answer 100 items a page with a `Link` header naming the next page, so `GitHub.pages` and `next_link` (77-84, 157-166) run.
- **Mergeability.** A pull request's `mergeable` is null on the first read of its detail and computed on the next, so `settled` (246-255) runs.

`Fault(method, path_prefix, status, times=1)` makes chosen calls fail, on every write endpoint and on the reads that matter: a comment's POST answering 500, or 403 and 429 with the message a secondary rate limit sends, with and without a `retry-after` header (github/docs content/rest/using-the-rest-api/rate-limits-for-the-rest-api.md:112), a label's DELETE answering 500, the rename answering 403 and 422, a ref's DELETE answering 422, compare answering 404, contents answering 404.

The fake encodes several things about GitHub. Each is documented, or is assumed until a canary check settles it:
- The issues list includes pull requests: documented (github/docs src/rest/data/fpt-2022-11-28/issues.json, "List repository issues": "GitHub's REST API considers every pull request an issue").
- A draft's mergeable_state is "draft": assumed. F9 asks what GitHub answers today, and C0 settles it.
- Renaming a branch closes the open pull request whose head it is: documented (github/docs, content/repositories/configuring-branches-and-merges-in-your-repository/managing-branches-in-your-repository/renaming-a-branch.md:19: "If the renamed branch is the head branch of an open pull request, this pull request is closed").
- A rename onto a name that already exists answers 422: assumed. The reference gives 422 only as "Validation failed, or the endpoint has been spammed" (github/docs src/rest/data/fpt-2022-11-28/branches.json, "Rename a branch"), and github.free_name relies on it (adapters/github.py:570-586); C6 settles it.
- The board's comments are posted as `github-actions[bot]`, with author_association NONE: assumed; C6 reads both off the bot's release comment.
- A pull request's updated_at moves with pushes and comments: assumed; C6 reads it before and after one of each.

### 3.3 The board's run, as the workflow makes it

**One sequence.** `cli.board_live_run` runs collect, coordinate, render, writes STATE.md, then renames and posts the comments, in that order, on the wall clock. The harness has to run the same order on its own clock. Build step 3 made the extraction that keeps behaviour: two functions in cli.py that take `now`, `cli.plan_board` (collect, coordinate, render; the snapshot and the page) and `cli.act_on_board` (renames and comments, from that snapshot). `board_live_run` calls both with `datetime.now(timezone.utc)` and writes STATE.md between them; validation/cycle.py calls them with the model's clock, as `cycle.start_run` and `cycle.finish_run`, and the checker puts other verbs between them, as a workflow run of half a minute allows, when the scope's `split_board` is on (section 6.2). The alternative, copying the lines into validation/cycle.py, would have let a change of order in cli.py past the harness unseen; tests/test_validation_cycle.py requires that the cycle runs the cli's own halves. Between the halves the clock stands and no other run starts, and the run's writes are judged by the states its halves read: its effects against the state it started from, less the rename of a branch that moved between the halves, which the second half rightly leaves alone (S09, spec: "a commit pushed to the branch before the rename, its holder coming back, keeps the claim"); its labels and its page against the state its first half left; a second run's quiet only when nothing changed between the halves. The mutation lane's twentieth mutant, `github.free_name` renaming without reading the branch again, is caught only by a search that splits the run.

**The concurrency group.** symphony-template's board.yml:83-85 puts every run in the group `board` with `cancel-in-progress: false` and no `queue`. GitHub's documentation (github/docs, data/reusables/actions/actions-group-concurrency.md) says that "by default, any existing `pending` job or workflow in the same concurrency group will be canceled and the new queued job or workflow will take its place". The model therefore queues a board run for each event, keeps at most one pending while one runs, and has a transition in which a newer event's run replaces the pending one. The push trigger at board.yml:62 has no branch filter, so a worker's push to a claude/ branch also queues a run, whose job board.yml:94 then skips. Whether such a skipped run still takes the pending slot is inferred, not measured (canary C2). As built (step 3): every verb says which of board.yml's events it fires, as GitHub names them (a push, and `create` when it made the branch; `pull_request` when the branch has an open pull request, when one is opened, reopened or merged; `issues` for a label put on or taken off and an issue closed; a merge fires `push` to main, `pull_request`, `issues` and `delete` at once; a comment fires none, and neither does marking a pull request ready, which board.yml does not take), and `RepoModel.fire` queues the runs of the events the scope lists: the newest act's replace whatever waited, a push to any branch but the default takes the slot and runs nothing, and of several fired at once every one is kept as a candidate, since which GitHub leaves is not known. The board verb starts the schedule's run, which replaces what waited, or one of the runs queued, whose fellows go on waiting (`RepoModel.starts`); the queue and the run between its halves are in the state key. The board's own writes, made with GITHUB_TOKEN, fire nothing (section 3.6). F12's mechanism is reached by `move-main`, a worker's push and a run: the push to main's run is cancelled, and reading the spec, the run that follows owes the pull request the word.

**The board branch** is written only when the run exits 0: the step that writes it (board.yml:132-134) runs only after the render step succeeded.

### 3.4 The git world

validation/git_store.py (planned) holds `GitStore`: a bare repository in a temporary directory with one clone per worker, built the way tests/test_bare_git.py (`SharedRemote`, 46-97) and tests/test_board.py (its git helpers and `ReleasedClaimReturns`, 640-765) already build them.
- Workers' verbs run real git: switch, commit, push, fetch, rebase and merge. GIT_AUTHOR_DATE and GIT_COMMITTER_DATE come from the scenario clock.
- FakeGitHub reads refs, trees, committer dates and standings from the bare repository (`for-each-ref`, `show`, `log -1 --format=%cI`, and `merge-tree --write-tree`, as bare_git.read_standing does at adapters/bare_git.py:280-295), and makes the rename with `git branch -m` there.
- Issues, labels, comments and pull requests stay in the model, since git has none.

So the git world differs from the memory world only where git decides: whether a push is taken and, at line level, whether two changes conflict. validation/prototype/gitprobe.py measured git 2.43 with worker b pushing `claude/5` after a had pushed it:
- b's plain push was refused: `! [rejected] claude/5 -> claude/5 (fetch first)`.
- b's `--force-with-lease` with no remote-tracking ref was refused: `(stale info)`.
- After `git fetch`, b's `--force-with-lease` was taken, and the remote's claim file then said `- Worker: b` (F7).

The git world is kept for S01, S05 and the conformance replays. A board run makes 14 to 29 API calls (validation/prototype/count_calls.py), about 30 to 70 ms over git, so all the scenarios in both worlds beside the checker would not fit the fast lane's budget.

### 3.5 The gate's worlds

MemoryWorld (adapters/memory.py) and BareGitWorld (adapters/bare_git.py) are used as they are. BareGitWorld's clock is the wall clock (adapters/bare_git.py:166-167), so the harness wraps it in `ClockedWorld(world, clock)`, which answers `now()` from the scenario. MemoryWorld.push overwrites a branch and never refuses (adapters/memory.py:60-64): it models what the gate sees, not the claim lock. That is why the claim lock is checked by G1 in the model and in git, and the gate's own lock by G13 in its Queue.

### 3.6 Where the worlds differ

This is the harness's own parity table.

| Behaviour | Memory | Git | GitHub |
| --- | --- | --- | --- |
| A second claim push from main | refused: not a fast-forward | refused by git ("fetch first"), measured | refused; a simultaneous race is canary C1 |
| A lease push after a fetch, onto someone else's claim | taken | taken, measured | taken unless a ruleset covers `claude/**` (canary C7) |
| A push that builds on someone else's branch | taken (fast-forward) | taken | taken |
| Conflicts | at file level, from the merge base | git's own, at line level | GitHub's mergeability |
| Rename | ref moved; the head's open pull request closed | `git branch -m` in the bare repository; the model closes the pull request | REST rename, which closes the pull request (renaming-a-branch.md:19) |
| Rename under a ruleset over `claude/**` | an injected 403 or 422 | the same | an administrator's only (renaming-a-branch.md:31); canary C7 |
| Committer date | the scenario clock | GIT_COMMITTER_DATE from the clock | whatever the person pushing sets (F8) |
| mergeable_state | computed: draft, clean, behind or dirty | from merge-tree | GitHub's, computed late; "draft" for drafts is assumed (F9) |
| A non-ASCII path | refused before any answer, as http.client refuses it | the same | canary C3 |
| Board runs | queued by event; at most one pending; a newer run replaces the pending one; can be split in two halves | the same | the `board` concurrency group (board.yml:83-85); canary C2 |
| A push made with GITHUB_TOKEN starts no workflow run | not modelled: board runs are queued only by the scenario's events | the same | canary C6, once |

The checker runs only in memory. Every counterexample, and a sample of explored paths, is replayed in git (section 6.6), so a fault in the memory model shows up as a disagreement with git.

## 4. Verbs and scripted workers

### 4.1 Atomic verbs

The verbs are GitHub's real operations, one each, never bundles. The prototype's `ready` verb deleted the claim file, pushed, marked the pull request ready and labelled the issue in-review in one step (validation/prototype/explore.py:204-211). The label then hid a mutant that released ready pull requests, since `may_be_due` checks in-review (adapters/github.py:316-322).

- **Branches:** push the claim commit; push a commit; push a commit that deletes the claim file; bring main in by a merge; bring main in by a rebase pushed with a lease; someone else moves main; after a release, push one's old head again as `claude/<n>`.
- **Labels:** put claimed on; put one's own worker label on; take another worker's label off; take claimed off; take a worker label off; label the issue in-review.
- **Comments:** the plan; "finished; ready to mark"; a release marker by the owner, by a collaborator, by a writer GitHub shows as a contributor, whom only the permission API vouches for, or by a stranger with no write access.
- **Pull requests:** open a draft; mark it ready; merge it (the owner); close the issue without a merge; the tidy workflow deletes the branch.
- **Undo a release:** put claimed back; rename `released/<n>-<sha7>` back; reopen the pull request.
- **Time:** wait 24 hours; wait 25 hours. With only 25, every gap was 0 or at least 25 hours and never sat on the threshold, so `>` against `>=` and a 12-hour idle time went unseen (validation/prototype/explore.py:152-153; section 7).
- **The board:** an event queues a run; the pending run starts; its first half; its second half; any of these with one injected fault.

### 4.2 Scripted workers

These live in validation/policies.py. A policy sees only what a worker sees: the last STATE.md, its own clone, and the comments on its own issues and pull requests. It never sees the model, so a worker that acts on a wrong board acts wrongly, and the scenario shows it. Each policy is `decide(view, rng) -> Verb | None`, deterministic, or seeded with `random.Random(seed)` in the soak runs. Before running any git command a policy issues, the harness passes it through `guard.run` (G11a), as the template's PreToolUse hook does.

| Policy | Behaviour |
| --- | --- |
| EagerClaimer | Takes a Given to workers row first, otherwise the first Queue row in its area. Claims, announces, opens a draft, commits every `pace` hours, and finishes after `k` commits: deletes the claim file, marks ready, labels in-review. |
| Racer | An EagerClaimer that acts on the same render as another worker in the same step, so both pick one issue before either pushes. |
| IdleHolder(returns_at=None) | Claims, announces, opens a draft and stops. With `returns_at`, it pushes one more commit at that time, without announcing again. |
| Abandoner | Claims and stops. One variant also opens a draft. |
| Finisher | Does the work but may not mark ready (a cloud session whose `gh pr` calls are refused). Comments `finished; ready to mark` on its draft and stops. |
| PersonByHand | At given times, posts the release marker and takes claimed and the worker labels off. Can also undo: puts claimed back, renames `released/<n>-<sha7>` back to `claude/<n>`, and reopens the pull request. |
| MainBringer(how) | On "main moved", or when its pull request is behind or conflicting: with `merge`, merges main in, resolves and pushes; with `rebase`, rebases, resolves and pushes with `--force-with-lease`. |
| Owner | Merges ready pull requests. For a Finisher, also completes the finish: deletes the claim file, marks ready, labels in-review. |
| Fault models | CarelessRebaser fetches, then pushes `--force-with-lease` onto a claim it does not hold. Stranger has no write access and posts the marker. ClockSkewed commits dated -48 h or +72 h. NonCanonical pushes `claude/0<n>`. HandMade pushes `claude/\u00b2` or `claude/caf\u00e9`. |

## 5. Scenarios and regression traces

### 5.1 How a scenario is written and run

Scenarios live in validation/scenarios.py (planned):

```
Scenario(name="S02", title="idle holder released, issue reclaimed", guarantees=("G2", "G3", "G4", "G6", "G9", "G10"),
         issues={1: ["todo"]}, workers={"a": IdleHolder(), "c": EagerClaimer()}, policy=gate.ACCOUNT_POLICY,
         steps=[Step(at="26h", actor="board", verb="board", args={"event": "schedule"},
                     expect=Expect(released=[1], queue=[1], comments={1: ["claim-released", "released"]}))],
         worlds=("memory",))
```

validation/run.py (planned) provides `run_scenario(scenario, world) -> RunRecord`. The record holds each step with its outcome, every invariant check with its count, the violations, the board renders and the timeline. tests/test_validation_scenarios.py (planned) has one test per scenario per world.

Expectations name structure: who holds what, which markers were posted, which rows the board lists. They never name whole sentences. The library's CLAUDE.md treats a rendered string as part of its interface, so when a wording changes on purpose, only the parsers follow, and they read board.py's own heading constants.

validation/known.json (planned) records each result that is red on purpose, with its finding, for example `{"scenario": "S05", "step": 4, "guarantee": "G7", "finding": "F2"}`. The suite passes when every result is what the file records. A fix that turns a result green removes its entry in the same pull request, and any new red result fails the suite. Nothing is skipped.

### 5.2 The five scenarios

Times are hours from 2026-10-01T00:00Z. "board" means one board run, made the way the workflow makes it (section 3.3). After every step the state guarantees run (G1, G8, G12); after every board run the cycle guarantees run (G2, G3, G3L, G4, G5, G6, G7, G9, G10, G15); G11a runs on every command. The Expected column is what the spec says; "on cf3bdb0" marks a step the code at that commit answers otherwise.

**S01 `race`: two workers race for one issue, and a worker's label names its issue.** Guarantees: G1, G8, G9, G11a. Workers: a (EagerClaimer), b (Racer, reading the same render as a), c (EagerClaimer). Issues: #1 todo, #2 todo, #3 todo plus worker:c. Memory and git.

| # | Time | Who | Does | Expected |
| --- | --- | --- | --- | --- |
| 1 | 0 | board | run (schedule) | Queue: #1, #2. Given to workers: #3 (c). Claims: none. |
| 2 | 0 | a, b | read the same render; both pick #1 | - |
| 3 | 0 | a | `git switch -c claude/1 origin/main`; commit `docs/agents/claims/1.md` (Worker: a); `git push origin claude/1` | The guard passes it. The push is taken. |
| 4 | 0 | b | the same, with Worker: b | Refused. Memory: not a fast-forward. Git: `[rejected] (fetch first)`. b deletes its local branch. |
| 5 | 0 | b | takes the next Queue row from the same render (#2) and claims it | taken |
| 6 | 0 | c | claims #3 from Given to workers, not from the Queue | taken |
| 7 | 0 | a, b, c | announce: claimed plus worker:\<self\>, and a plan comment | Each issue carries only its holder's worker label (the scripted workers' own step, checked here and not by an invariant). |
| 8 | 0 | board | run (create) | Claims: #1 a, #2 b, #3 c. Queue: none. Given: none. No comment due. |
| 9 | 0 | a | holds a stale render that shows #3 | The claim verb is not enabled for a on #3, which is another worker's (spec lines 17 and 25). |

Variant S01c, under the careless fault model: b runs `git fetch origin && git push --force-with-lease origin claude/1`. The guard passes it, because a lease on any `claude/` branch is allowed (guard.py:188-192), and git takes it (measured). The claim file now names b, so every reader names b and G1's second half breaks. known.json records it as F7.

**S02 `idle-holder`: an idle claim is released once after a day, goes back to the queue, its work is kept, and someone else claims it.** Guarantees: G2, G3, G4, G6, G9, G10. Workers: a (IdleHolder that never returns), c (EagerClaimer). Issue: #1. Memory.

| # | Time | Who | Does | Expected |
| --- | --- | --- | --- | --- |
| 1 | 0 | a | claims #1 (Files to touch: src/1.py), announces, opens draft #100 with "Closes #1" | Labels: claimed, todo, worker:a. Protocol check on #100: no problems. |
| 2 | 1 | a | commits src/1.py and pushes, then stops for good | taken |
| 3 | 1 | board | run (pull_request) | Claims: #1 a. Open pull requests: #100. Queue: none. |
| 4 | 24 | board | run | Nothing posted or renamed: idle for 23 h. |
| 5 | 25 | board | run | Still nothing. Exactly 24 h is not "more than 24 hours": claims.is_idle uses `>` (claims.py:187-191). |
| 6 | 26 | board | run | The release, in six parts. (1) One release comment on #1 by `github-actions[bot]`: the marker on the first line; it names a, the head's date 2026-10-01T01:00:00Z, "its pull request #100 is still a draft", and the plan (coordinator.release_text, 111-134). (2) claimed and worker:a taken off, after the comment. (3) `claude/1` renamed to `released/1-<sha7>`. (4) #100 closed by the rename. (5) One "released:" comment naming the new branch (github.release_claim, 547-567). (6) Queue: #1. #100 gets no idle comment, because a released claim's pull request hears nothing (board.py:463-467). |
| 7 | 27 | board | run | No write call (G9). |
| 8 | 27 | c | claims #1 afresh from main, cherry-picks a's work commit from `released/1-<sha7>` after its claim commit, announces | Push taken: the name is free. Labels: claimed, todo, worker:c. |
| 9 | 27 | board | run | Claims: #1 c. No release: the head is fresh. |

Expected outcome: exactly one release comment; a's commits reachable from `released/1-<sha7>` (G4); c holds #1.

**S03 `by-hand`: a person releases too soon, a stranger tries, the holder comes back, and the label undoes a release.** Guarantees: G2, G3, G5. Workers: a (IdleHolder), b (EagerClaimer that comes back at 58), owner (PersonByHand), s (Stranger). Memory.

| # | Time | Who | Does | Expected |
| --- | --- | --- | --- | --- |
| 1 | 0 | a | claims #1, announces, opens draft #100 | - |
| 2 | 10 | owner | posts the marker comment; takes claimed and worker:a off | - |
| 3 | 10 | board | run | Not released: the comment came 10 h after the last commit (claims.was_released, 262-272). Claims: #1 a. Queue: none. The labels are off, but the branch still holds the issue. |
| 4 | 30 | s | posts the marker comment (author_association NONE, no write access) | - |
| 5 | 30 | board | run | The coordinator posts its own release comment, because neither the owner's early comment nor the stranger's counts (claims.is_release_comment; github.may_write answers no for s, 332-350). Then the rename. Queue: #1. |
| 6 | 31 | b | claims #1 afresh, announces, opens draft #101 | taken |
| 7 | 58 | owner | releases by hand: marker comment, claimed and worker:b off; 27 h after b's last commit | - |
| 8 | 58 | b | comes back and pushes a commit before any board run | taken |
| 9 | 58 | board | run | Not released, since a commit came after the comment. `claude/1` keeps its name. Claims: #1 b. Queue: none. |
| 10 | 85 | board | run (b idle for 27 h) | The coordinator releases with one comment; there are no labels left to take off, since b did not announce again. Renamed to `released/1-<sha7>`; #101 closed; Queue: #1. |
| 11 | 85 | owner | undoes: puts claimed back, renames `released/1-<sha7>` back to `claude/1`, reopens #101 | - |
| 12 | 85 | board | run | Claims: #1 b. Queue: none. No new release comment. |
| 13 | 120 | board | run | Still no second release while b stays idle: this idle period has already been released once (claims.release_due with `released`, 194-208). |

Expected outcome: the release comments are the owner's (10 h, frees nothing), the stranger's (30 h, frees nothing), the bot's (30 h, releases a), the owner's (58 h, undone by b's commit) and the bot's (85 h, releases b). There are no others, and b holds #1 at the end.

**S04 `finished-draft`: a finished draft waits for review however long it takes; an abandoned draft is released.** Guarantees: G6, G2, G3, G10, G12. Workers: f (Finisher, on #1), d (Abandoner with a draft, on #2), owner. Memory.

| # | Time | Who | Does | Expected |
| --- | --- | --- | --- | --- |
| 1 | 0 | f, d | claim #1 and #2, announce, open drafts #100 (f) and #101 (d) | - |
| 2 | 1 | f | commits src/1.py, comments `finished; ready to mark` on #100, stops | Protocol check on #100: no problems, since a draft carries its claim file. |
| 3 | 26 | board | run | #2 is released: one comment, labels off, `released/2-<sha7>`, #101 closed, Queue: #2. #1 is untouched: no release comment and no rename, because a finished draft is skipped (github.releases at 394; finished_draft at 360-367). #100 gets one idle comment. |
| 4 | 50, 74, 98 | board | three runs | Nothing new: no release on #1, and no second idle comment on #100 (IDLE_MARKER at board.py:43; comment_once). |
| 5 | 99 | owner | completes the finish for f: deletes `docs/agents/claims/1.md` on `claude/1`, marks #100 ready, labels #1 in-review | Protocol check passes: ready, claim file gone. |
| 6 | 200 | board | run | Claims: #1, the worker read from the label worker:f (board.claim_row, 519-523). #100 ready and waiting. No release. |
| 7 | 201 | owner | merges #100 | #1 closed. `claude/1` deleted. |
| 8 | 201 | board | run | Claims: none. Recently merged: #100. #2 still in the Queue. |

**S05 `overlap-and-main`: overlapping work is told once and lands in order; "main moved" is told once; main is brought in by rebase or by merge.** Guarantees: G7, G10, G11a, G13. Workers: m (MainBringer `merge`), r (MainBringer `rebase`), owner. Both claim files list src/shared.py. Memory and git.

| # | Time | Who | Does | Expected |
| --- | --- | --- | --- | --- |
| 1 | 0 | m, r | claim #1 and #2, announce | - |
| 2 | 0 | board | run | Overlaps: src/shared.py with #1, #2, oldest first. The coordinator tells #1 of #2 and #2 of #1, one comment each (markers `overlap 2` and `overlap 1`). |
| 3 | 1 | m, r | open drafts #100 and #101; each changes only its claim file, so the files to compare come from the claim files (board.open_work, 615-636) | - |
| 4 | 1 | board | run | Overlaps: src/shared.py with #100, #101. Spec: no new overlap comment, since these are the same two pieces of work. On cf3bdb0: `overlap 101` is posted on #100 and `overlap 100` on #101 (F2, recorded red). |
| 5 | 2 | m, r | each commits a different change to src/shared.py, deletes its claim file, marks its pull request ready, labels in-review | - |
| 6 | 2 | board | run | Overlaps: src/shared.py with #100, #101 (both ready, so oldest first). |
| 7 | 3 | owner | merges #100 | main moves |
| 8 | 3 | board | run (push, ref main) | One "main moved" comment on #101, its marker carrying main's sha7, its text "main moved: this branch is conflicting with main at \<sha7\>; rebase or merge main before the next push." (board.py:435-440). |
| 9 | 3 | board | run (pull_request closed) | No second "main moved" (the marker; github.comment_once). Variant S05q: this run's event arrives while step 8's run is still pending and replaces it; spec: #101 still hears "main moved" once for this sha; on cf3bdb0 it never does (F12, recorded red). |
| 10 | 4 | r | S05a: `git rebase origin/main`, resolve, `git push --force-with-lease origin claude/2`. S05b, under TEAM_POLICY: `git merge origin/main`, resolve, `git push origin claude/2`. | The guard passes both: a lease on its own `claude/` branch, and a plain push. Git takes both. `git push --force origin claude/2` is refused by the guard (guard.py:165-168). On cf3bdb0, `git push origin +claude/2` is let through (F3, recorded red). |
| 11 | 4 | board | run (pull_request synchronize) | #101 clean. No "main moved". No overlaps. |
| 12 | 5 | owner | merges #101; then a board run | Recently merged: #101, #100. Claims: none. Queue: none. |

Variant S05g runs the gate over BareGitWorld under coordbench's arm policy, in which the gate merges, main is merged in, and no check is required:
- `mark_ready(#1)` merges, exit 0.
- `mark_ready(#2)` answers main moved, exit 3. Main is unchanged, and the conflicts are named: `("src/shared.py",)`.
- r merges `team/main` in and pushes; `mark_ready(#2)` then merges.
- Main has moved forward twice, and both issues are closed.

tests/test_bare_git.py:326-365, in `GateOverBareGitTest`, covers the same flow; here it runs under the scenario's guarantees.

### 5.3 One regression trace per finding

The first design listed thirteen more scenarios, S06 to S18. None stays a scenario of its own, and each lands in one of four places:
- **A replayable trace per finding,** in validation/traces/ (planned), each red in known.json until its fix lands (the table below), for the five that showed one: S12 (F4), S13 (F5 and F7), S14 (F6; its `claude/\u0663` is F5's, which the property lane's Unicode classes also cover, section 9.2), S15 (F1) and S16 (F8).
- **Three table-driven tests.** S06, the gate's presets: a ready pull request waits for the owner under ACCOUNT_POLICY and lands by auto-merge on green under TEAM_POLICY, red and pending checks refuse, exit codes are checked, and three agents race under the caller's lock as tests/test_gate.py:163-186 has them do. S11, the protocol check's cases: every pull request state crossed with the claim file present or not, the body's closing forms, and a head in a fork. S10, the clerk's planning, which no other lane runs: team.route's answer to each event (an epic labelled, before and after it is planned; a task labelled blocked; the last task closing, two closing in one run, and an integration already claimed; team.py:152-185), then team.seed over a team.Client that reads and writes the model (team.py:70-111), after which each task is given to a registered worker, or goes to the open queue when none is registered, and the board rendered from the model lists every given task under Given to workers and not in the Queue (spec lines 17 and 77; team.py:316-393). Section 10.1 puts the clerk's planning under the coverage floor.
- **The checker,** through verbs (section 4.1) and situations it must reach (section 6.9). S07, a released head pushed again: the old holder pushes its head again as `claude/<n>`, and the next run deletes the name, since `released/<n>-<sha7>` already keeps that head (github.free_name, adapters/github.py:570-586). S08, a rename refused: the rename faults of section 3.2, in E-faults, with G10's "rename refused" part. S09, the holder back during a run: the board run split in two halves (section 6.2), where a commit lands between the release comment and the rename, and free_name's second read keeps the claim. S18, a writer whose author_association does not vouch: a marker by a writer GitHub shows as a contributor, which counts through the permission API (github.may_write, adapters/github.py:332-350).
- **Variants and a check,** for S17, a worker's label in its three spellings: S01's variant S01l, in which #3 carries `account:c` and then `agent:c` in place of `worker:c` and is listed under Given to workers and kept off the Queue the same way; S02's variant S02l, in which #1 also carries `account:a` and `agent:a` when the release comes, so the release must take all three off (G8); and the property lane's label sets, which hold all three spellings (section 9.2).

| Finding | The trace | Breaks |
| --- | --- | --- |
| F1 | claim a 1, announce a 1, wait 25, board with the label DELETE answering 500, board | G3L |
| F2 | claim a 1, claim a 2, board, draft a 1, board | G7 |
| F3 | the guard on `git push origin +claude/7`, `git push -fu origin claude/7`, `git push --al origin`, `git push --mirr origin`, `git push origin heads/main` and `FOO=1 git push origin main`, judged by git | G11a |
| F4 | three workers ask the clerk; between b's question and c's, a's ready pull request falls behind main | G14 |
| F5 | claim a 1, then the same claim pushed as `claude/01` | G1 |
| F6 | a branch `claude/\u00b2` (the render raises), and a branch `claude/caf\u00e9` (the live run's contents call raises) | G9, G15 |
| F7 | in git: a claims 5; b fetches and pushes `--force-with-lease` onto `claude/5` | G1 |
| F8 | a commit dated +72 h, then one dated -48 h | none: recorded as the assumption under which G2 and G3 hold |
| F9 | a draft behind main, then a push to main | G10, once the owner decides whether drafts hear "main moved" |
| F10 | claim a 1, wait 25, announce a 1, draft a 1, finished a 1, release by hand, board | G9 |
| F11 | claim, announce, draft, ready, wait 25, board with the idle comment's POST answering 500 | G15 |
| F12 | a merge queues runs for its push to main and its other events at once; the push's run is replaced while it waits | G10 |

## 6. The exhaustive checker

### 6.1 Search

validation/explore.py provides `explore(start, scope, depth, known=oracle.AS_BUILT) -> Exploration`, the search of this step; the depth-first search of the deep scopes, chosen by an argument, is planned. The search starts from the state it is given, the seeded state after one board run in the tests, and applies, to a copy, every verb enabled for every worker and person, and every fault the scope names. It checks each new state's guarantees and deduplicates states by their key. Breadth first, the first trace that breaks a guarantee is a shortest one.

**No pruning at a violation.** The prototype stopped exploring at every violating state (validation/prototype/explore.py:353-357), so with entries in known.json nothing past a known finding would ever be explored. The harness records the violation and goes on; each known finding is a switch in the oracle (section 2.6), so a known red neither stops the search nor hides a second fault behind it.

**Deep scopes** search depth first and keep 16-byte digests rather than whole states. A plain seen-set is incomplete for a depth-bounded search, since a state first reached deep is then never expanded when it is reached again shallower; the set therefore keeps, for each digest, the shallowest depth at which it was reached, and expands a state again when it is reached shallower. validation/shrink.py (planned) shortens a failing depth-first trace by removing steps while it still fails (delta debugging).

### 6.2 Scope

```
Scope(workers=("a", "b"), issues=(1,), people=("owner",), faults=("label-delete-500",),
      careless=False, split_board=True, waits=(24, 25), events=("schedule", "push", "pull_request"))
```

Inside a scope, any worker may take any verb its role allows, in any state: the verbs of section 4.1, with the policies taken away.

### 6.3 The state key

The key holds everything any guarantee or the oracle reads: refs, trees, issue and pull request comments with their whole bodies, every commit that was ever a claim branch's head, the label events, the queued and pending board runs, and the faults still armed. The prototype's key (validation/prototype/model.py:145-156) keyed pull requests without their comments, so the "finished" verb was invisible and F10 never reached; it cut issue comment bodies at 60 characters; and it left out `ever_heads` and `label_events` (validation/prototype/model.py:81-82), which the work-lost and half-release checks read. Rule coverage showed the first of these: the ordering in `claims.said_finished` never ran in the prototype's runs, since every state after a "finished" comment was folded into the state before it (section 10.1). With a sound key the same scope reaches 1,979 states at depth 5 where the prototype's reached 1,599, and 41,147 at depth 7 where it reached 26,295.

### 6.4 Reductions, and why each is exact

- **Time.** Every clock rule in the library either orders instants (latest_release, said_finished, merged_rows) or compares the gap between two instants with the one constant IDLE_AFTER: claims.is_idle (claims.py:187-191), behind release_due and was_released, and board.pull_is_idle (board.py:428-432). The reduction sorts the state's instants (commit dates, comment times, pull request updates, now) and caps each gap between neighbours at 25 hours. Order is kept, so every ordering rule answers the same. Take a test of whether the gap from t_i to t_j is more than 24 hours: either no capped gap lies between them, and the difference is unchanged, or one does, and then both the old difference and the capped one exceed 24 hours. So the capped state answers every rule as the real state does, and waiting for ever reaches only finitely many states. The rendered text quotes times (release_text, the Last update column), so the key leaves rendered text out and the oracle compares structure.
- **Workers.** Workers with the same role are interchangeable, so the key is the least key over every permutation of the workers' names; "renamed in order of first appearance", the first design's reduction, is not well defined. That gives up to 2 times fewer states for 2 workers and up to 6 times fewer for 3.
- **Commits** are content-addressed over the capped times, so the same history gives the same key; `released/<n>-<sha7>` is keyed by the commit it names.
- **Board runs** run one at a time, as the concurrency group runs them; a run is split into its two halves only when the scope asks for it.

### 6.5 What is checked on each state and each run

State guarantees run on every reached state, and cycle guarantees on every board transition, given the state before and after. On every board transition the checker also:
- compares the run's effects with the oracle's effects, computed from the state before the run (the "all" line of section 2.4);
- requires that the run did not raise over a refused write or a branch name (G15);
- runs the board again from the state after a fault-free run and requires no write call (G9).

Liveness is checked as a response within one run: a due claim plus a fault-free run must give a released claim (G3), and a half-made release plus a fault-free run must give a completed one (G3L). That turns "eventually" into a safety check that needs no fairness assumption.

### 6.6 Counterexamples you can replay

A violation is written to `validation-out/traces/<guarantee>-<n>.json` (planned):

```
{"library": {"version": "0.4.0", "commit": "a996ec9"},
 "scope": {"workers": ["a", "b"], "issues": [1], "faults": ["label-delete-500"]},
 "violated": "G3L a release no rule completes: #1",
 "steps": [["claim", "a", 1], ["announce", "a", 1], ["wait", 25],
           ["board", {"fault": ["DELETE", "/issues/", 500]}], ["board"]]}
```

It is also printed as one line, with its replay command, `python3 scripts/validate.py replay <file> --world memory|git` (planned). The replay runs the steps again, checks every guarantee and draws the timeline. Every counterexample, and 200 sampled paths, are replayed in git in the deep lane; any difference in what git and the model observe (a push taken or refused, the refs afterwards, the board's rows, the markers posted) is a fault in the memory model and is reported as one.

### 6.7 Measured on the prototype

With a sound key (validation/prototype/explore_variants.py, scopes.py and ext_verbs.py; "reduced" means time capping plus symmetry), 2 workers unless stated:

| Scope | Depth | States | Time | Reduced states | Reduced time |
| --- | --- | --- | --- | --- | --- |
| 1 issue | 5 | 1,979 | 1.7 s | 830 | 1.0 s |
| 1 issue | 6 | 8,985 | 7.6 s | 3,494 | 3.9 s |
| 1 issue | 7 | 41,147 | 37.5 s | 14,920 | 19.5 s |
| 1 issue, board split in two halves | 7 | 56,290 | 58 s | 20,917 | 25 s |
| 3 workers, 1 issue | 6 | 13,960 | 13.6 s | 3,494 | 6.3 s |
| 2 issues | 6 | 54,343 | 56 s | 23,353 | 28.5 s |
| 1 issue, plus four verbs: main brought in by a merge, a stranger's marker, a refused comment POST, the issue closed without a merge | 6 | 22,821 | 21.6 s | 9,583 | 12.3 s |
| 1 issue, plus the 24-hour wait (mutants_wait24.py with no mutant) | 6 | 25,975 | 20 s | | |

- States grow about 4.5 times per level of depth, 5.1 times with the four extra verbs, and, by estimate, about 6 times with the full verb list and five fault kinds.
- With the four extra verbs, 116 of the board runs with a refused comment POST raised at depth 6, before the reductions (F11).
- The prototype's own lossy key, for comparison (validation/prototype/explore.py and measure.py): 1,599 states at depth 5 (2 workers, 1 issue), 26,295 at depth 7, 106,267 at depth 8, in under two minutes and about 470 MB; 10,144 for 3 workers at depth 6 in 62 MB; 41,215 for 2 issues at depth 6 in about 220 MB.
- The gate (validation/prototype/gate_explore.py, 2 agents, 2 issues, open queue, MemoryWorld, depth 7): 23,712 states and 256,592 transitions under the account and the team policies, 24,920 and 261,580 under the arm's, 21,848 and 243,020 under a gated check, about 20 s each, no violation.
- Costs: one board run over the fake makes 14 calls for two claims, a draft and a commit, and 29 when two releases are due, in about 2 ms (validation/prototype/count_calls.py); one model copy costs 0.13 ms; one git call in a bare repository costs 3.3 ms, so a board run over git costs 30 to 70 ms.

### 6.8 Scopes and budgets

| Scope | Lane | Expected time |
| --- | --- | --- |
| E-fast: 2 workers, 1 issue, the full atomic verb list, both waits, one fault kind at a time, reductions | fast | depth 5: a few seconds; depth 6: about 15 to 20 s (estimated from the growth above) |
| E-prefix: depth 3 to 4 from each scenario's interesting prefix (S02 at step 5, S03 at step 7, S04 at step 3, S05 at step 7) | fast | seconds each |
| E-three: 3 workers, 1 issue, depth 5 | fast | a few seconds |
| E-guard: the guard against git's recorded answers (section 8) | fast | under a second |
| E-deal: 3 workers asking the clerk (section 9.1) | fast | under a second |
| E-two: 2 workers, 2 issues, depth 7 | deep | minutes |
| E-deep: 2 workers, 1 issue, depth 8, depth first with the depth-aware digest set, and shrinking | deep | about 8 to 10 minutes |
| E-faults: every fault kind, careless workers, clock skew, hand-made names | deep | minutes |
| E-gate: four policies, depth 8, over MemoryWorld; parts 3 to 5 and 7 over BareGitWorld | deep | minutes in memory; BareGitWorld not yet measured |
| Soak: 1,000 seeds, 200 steps each, 5 workers, 6 issues, seeded random walks | deep | about 10 minutes (estimated) |
| Conformance: every counterexample plus 200 sampled paths, replayed in git | deep | minutes |
| Git's answers for the guard grammar | deep | about a minute (section 8) |

Depth 7 with the full verb list would be about 60,000 to 90,000 reduced states, minutes rather than seconds, so the fast lane stops at depth 5 or 6 and reaches deeper behaviour from scenario prefixes. Depth 10 would be about ten million states, hours, so the deep lane stops at depth 8.

### 6.9 Situations

validation/situations.py (planned) names the states that matter, and the report lists for each scope which it reached and which no scope reached:
- an idle draft; an idle claim with no pull request;
- a finished draft gone idle; a ready pull request gone idle; a finished draft released by hand (F10);
- released and renamed; a rename refused; the holder back before the rename; a release undone; an issue claimed again;
- two claims meeting in a file; a claim meeting a pull request;
- behind main; conflicting with main; a run replaced while pending (F12);
- a released head pushed again;
- an untrusted marker; an early marker; a marker by a writer only the permission API vouches for;
- a half release (F1); a refused comment (F11);
- a hand-made claim name (F5, F6).

## 7. The mutation lane

A guarantee the harness never sees break is not shown to hold, so the harness is tested the way it tests the library. A mutant is a function that changes one rule of the library inside the harness's own process, never in the files, the way a careless change could; validation/prototype/mutants.py holds eight, and validation/mutants.py the lane's nineteen. The mutation lane runs each mutant against the fast lane's searches and requires every one to be caught: to make the searches report something the unchanged library does not. A mutant no lane catches fails the lane, unless validation/equivalent.json (planned) names it with the reason it cannot change behaviour. The lane is the acceptance test of the harness's foundations (section 14, step 2). It needs 10 to 20 mutants, about 4 seconds each at depth 6 with reductions, and runs in validate.yml: weekly, by hand, and on every pull request that changes `validation/` or the library.

Measured on the prototype checker with a sound key, 2 workers, 1 issue, depth 6 (8,985 states) unless stated (validation/prototype/mutants.py and mutants_wait24.py). The letters are the prototype's own checks (validation/prototype/explore.py, `state_problems` and `transition_problems`): T1 a release that was not due, T2 a due claim not released, T3 the board against the oracle, T4 a second run that changed something, L1 a half release, S1 two branches for one issue, S2 work lost, S8 overlaps told again.

| Mutant | Caught | With the 24-hour wait | Why it slipped |
| --- | --- | --- | --- |
| idle at exactly 24 hours (`>=` for `>`) | no | yes (T1, T2) | waits of 25 hours never put a gap on the threshold |
| a 12-hour idle time | no | yes (T2) | the same |
| any comment after the last commit releases | no | yes (T2) | the same |
| the worker label left on at release | no | no | no check read the labels a release takes off, and the board-against-oracle check compared after-states only (validation/prototype/explore.py:322-326), on which any oracle agrees with the board: by spec line 17 an issue that keeps worker:a is a's alone and is left out of the Queue, whether or not the oracle's Queue calls `labels.reserved`. Only G8 and comparing the run's label DELETEs with the oracle's (section 6.5) see this mutant |
| a release ignores the label claimed | no | no | the board-against-oracle check compared after-states only, and a wrongly renamed claim is gone from both (validation/prototype/explore.py:322-326) |
| ready pull requests released | no | no | the bundled `ready` verb also labelled in-review, which `may_be_due` checks |
| an idle comment to a released claim's pull request | no | no | no check read the comments sent to a released claim's pull request |
| overlaps told on every run (2 issues, depth 5) | yes (T4) | | |

It caught 1 of 8, and 4 of 8 with the extra wait. The harness's answers are the corrections above: effects compared with the oracle's (section 6.5), both waits (section 4.1), atomic verbs (section 4.1), and G8 and G10 reading labels and comments after a release. More mutants to add, so that each guarantee has at least one: labels taken off before the comment is posted; an overlap marker read from any line; a release comment accepted from any author; `comment_once` without its marker check; "main moved" posted after any event; the rename skipped; a claim branch from a fork accepted by the protocol check; the clerk dealing without its taken set; the guard accepting `--force`. Build step 2 added the order of the labels and the comment, the release comment from any author, `comment_once` without its marker check (`has_marker`), "main moved" after any event and the rename skipped, with others (validation/README.md); the overlap marker read from any line, the fork's claim branch, the clerk and `--force` wait for worlds and verbs of later steps.

## 8. The guard grammar, with git as its oracle

### 8.1 What is generated

validation/guard_grammar.py (planned) generates push commands from parts: a prefix (none, `VAR=value`, `env`, `command`, `nice`, `time`); a wrapper (none, `bash -c '...'`, an and-list, a subshell, a command substitution); git's global options (none, `-C .`, `-c push.default=current`); push options, plain, bundled and abbreviated (`--force`, `-f`, `-fu`, `--force-with-lease`, `--delete`, `--all`, `--al`, `--branches`, `--mirror`, `--mirr`, `-u`, `--no-verify`); and refspecs, the current claim branch, another worker's, the default and the board branch under every spelling (`main`, `HEAD:main`, `refs/heads/main`, `heads/main`, `+HEAD:main`, `:main`), and a branch that is no claim. Each command is judged twice: by `guard.run`, and by what git would do with it.

### 8.2 Git as the oracle

The first design's oracle read the meaning of each part from a table its author wrote, so it knew only the meanings its author thought of, and never generated the shapes git really accepts in F3. Git is the oracle instead. Each distinct git argument list runs in two scratch worlds whose bare remote has a pre-receive hook that records every ref update it is offered, with its kind (create, delete, fast-forward or forced), and refuses them all, so nothing ever moves. In the fresh world the clone's remote-tracking refs match the remote; in the stale world the remote's claim branch has moved behind the clone's back, where git refuses a `--force-with-lease` itself ("stale info") but still offers a plain force. The board rules are read off what git offered:
- an update of `refs/heads/main` or `refs/heads/board` is a push to the default or the board branch;
- a forced update offered in the stale world is a plain force, since a lease would have been refused there;
- a forced update offered only in the fresh world, on a ref outside `refs/heads/claude/`, is a lease outside claude/;
- more than one ref offered is a wholesale push;
- a deletion is judged by whose branch it is, which only the second fence knows (G11b).

The prefix and the wrapper change whether the guard can read a command, never what git does, so git runs once per distinct argument list. A refusal of a push git shows harmless is listed by shape; it fails the lane only for the shapes the rules name as allowed (a plain push of one's own claim branch, its lease after a rebase, and a plain push of a branch that is no claim), since the rules forbid some shapes whatever their effect: after a rebase "your own claim branch takes `git push --force-with-lease`, and nothing else ever does" (symphony-template's .claude/rules/board.md).

validation/prototype/guard_git_oracle.py tries the method on ten shapes. In both worlds git offered `-f`, `-fu` and `+claude/7` as forced updates of `claude/7`, so each is a plain force, while it offered `--force-with-lease origin claude/7` in the fresh world alone; `--al` offered `main`, since git refused `claude/7` itself as not a fast-forward; `--mirr` offered both branches and the remote-tracking refs; and `heads/main` and `HEAD:main` offered `main`. The guard passed five of the forbidden shapes (`-fu`, `+claude/7`, `--al`, `--mirr`, `heads/main`). A push cost about 36 ms, so the whole grammar's distinct argument lists, twice each, take about a minute: the deep lane runs git and writes its answers, keyed by git's version, to validation/guard_git.json (planned), and the fast lane judges the guard against those answers in under a second, failing when the file lacks an answer for a command the grammar generates.

### 8.3 Measured, and the causes

validation/prototype/guard_grammar.py, with the hand-written oracle, judged 10,800 commands: 8,550 forbidden, 7,734 of those let through, and no false refusal, in 0.6 s. That count is four root causes multiplied by the grammar's fan-out (6 prefixes, 5 wrappers, 3 global options), and 1,596 of them are another worker's claim, which the guard cannot know by design; so the report gives causes, not counts. validation/prototype/gitflags.py showed that git 2.43 really performs four plain shapes the hand-written grammar never generated, and that the guard passes each: `git push -fu origin claude/7` made a forced update, `--al` and `--mirr` were taken as `--all` and `--mirror`, and `git push origin heads/main` pushed `main -> main`.

| Cause | Where | Shapes | Can the command show it |
| --- | --- | --- | --- |
| A `+` refspec is not counted as a force | guard.py:171 takes the `+` off and goes on | `git push origin +claude/7` | yes |
| A command is read as git only when its first word is git | guard.py:125 | `FOO=1 git push origin main`, and `env`, `command`, `nice`, `time` | yes |
| `-f`, `--force`, `--all`, `--branches` and `--mirror` are matched exactly, and every other word that starts with a dash is skipped | guard.py:150-151, 154 and 158-159 | `git push -fu origin claude/7`, `git push --al origin`, `git push --mirr origin` | yes |
| Only `refs/heads/` is taken off a destination | guard.py:174 | `git push origin heads/main` | yes |
| The command is inside a string another program runs | guard.py:14-17 leaves what it cannot read to the second fence | `bash -c '...'`, `sh -c "..."`, `xargs git push` | no: G11b |
| The destination is another worker's claim | whose a branch is, is not in the command | `git push origin claude/8` | no: G11b and F7 |

guard.py:14-17 does not name `bash -c`; it says that anything the module cannot read passes, with branch protection on GitHub as the second fence.

## 9. Dealing, and the property lanes

### 9.1 Dealing

validation/dealing.py (planned) has three workers each take their own snapshot at any interleaving of the state changes that matter (a peer's pull request falls behind, a peer claims), passes each snapshot through `team.next_for`, and checks the deals with G14. validation/prototype/probe_deal.py shows F4 at the first interleaving it tries.

### 9.2 The standard-library property lane, always on

The package takes no dependency, so its properties are plain functions in validation/properties.py (planned), outside the package: the leading asserts are the preconditions and the later asserts are the property, the shape CrossHair's `--analysis_kind=asserts` reads, and plain unittest calls the same functions over enumerated inputs. Under unittest a failing precondition assert would look like a property failure, so the lane filters the inputs by the preconditions first, and fails when the filter leaves nothing to check.

validation/domains.py (planned) builds the inputs:
- every string of up to 3 characters over an alphabet of ASCII digits, `0`, `/`, space, `+`, `#`, `-` and NUL, together with the classes Python's own `str` methods define: in Python 3.12.3 (Unicode 15.0.0) there are 128 code points for which `isdigit()` holds and `isdecimal()` does not, and 670 non-ASCII code points for which `isdecimal()` holds (`[c for c in map(chr, range(sys.maxunicode + 1)) if c.isdigit() and not c.isdecimal()]`, and the same with `c.isdecimal() and not c.isascii()`), enumerated whole rather than sampled;
- whole hours around the thresholds: 0, 1, 23, 24, 25, 48, 49;
- label sets made from the label names the spec gives (lines 13 to 19), in all three spellings of a worker's label;
- the worker names of a three-worker scope.

Enumerating those classes whole, with `0`, `1` and `+`, as one- and two-character names breaks each of the four properties CrossHair broke (section 9.3) in well under a second (validation/prototype/unicode_classes.py: 2,403 names tried in about 130 ms, after the classes take a fraction of a second to build). tests/test_validation_properties.py (planned) runs every property over this domain, deterministically, in about a second.

validation/unproven.json (planned) lists the functions no property may call because they are not pure, each with its reason: `adapters/`, `cli.py`, `guard.branches`, `guard.git_line` and `board.write_if_changed`. The lane prints the list on every run and refuses a stale entry, as beethoven-qa's gate does with its unproven table (beethoven-qa's src/beethoven_qa/gate.py, lines 29-40).

### 9.3 Which functions get properties

Line numbers are in the module the first column names.

| Function | Property | Measured |
| --- | --- | --- |
| claims.number_of (85-91) | A claim branch names one issue: the number is None, or `branch_of(number) == branch`. | CrossHair: `'00'`; `claude/00` names #0 but spells back as `claude/0`. |
| claims.by_number and issue_order (99-103, 211-214) | Never raise; numbers sort before names. | CrossHair: `'\u00b2'` raises ValueError. |
| board.render (660-683) | Never raises for any branch name; the page is ASCII. | Not confirmed at 20 s on 3-character names; at a 120 s bound on 2-character names, `'\u27800'`. |
| claims.release_due, was_released and is_idle (187-272) | Due exactly when idle and not released this period; never due twice; "more than" 24 hours. | Over whole hours, "an idle unreleased claim is due" was confirmed over all paths. "Never due twice" hit a TypeError inside CrossHair's datetime emulation, which replays cleanly in plain Python: a tool fault, so these time rules run in the enumerated lane. |
| claims.is_release_comment and starts_with_release_marker (217-241) | The marker counts only on the first line; an untrusted author never releases. | Not confirmed at 20 s. |
| claims.said_finished (244-252) | Only the author's latest comment decides. | not run yet |
| claims.released_branch (275-279) | Starts with `released/`; two heads with different short shas give two names. | not run yet |
| claims.claim_worker, claim_files, issue_files and claim_plan (106-169) | The worker line round-trips through the claim template; file lists are sorted and unique. | not run yet |
| labels.holder_names, reserved and worker_of (67-118) | Each name once, in order; any worker label reserves the issue. | holder_names: not confirmed at 20 s. |
| board.state_word and cell (293-302, 481-485) | Always one of the five words; a cell is one ASCII line with every pipe escaped. | state_word: confirmed over all paths, at once (it is a lookup). |
| board.overlaps and landing_key (597-646) | Each listed group has 2 or more pieces, sorted by landing_key, and every piece touches the file. | not run yet |
| board.released_keys and is_released (329-359) | A subset of the claim keys; released implies not claimed, not in-review, and no ready pull request. | not run yet |
| board.comments_due (456-476) | Never addressed to a released claim's pull request. | not run yet |
| coordinator.release_text and labels_released (111-143) | The first line is the marker, so starts_with_release_marker accepts it; only claimed and worker labels come off. | not run yet |
| coordinator.told_numbers and overlap_comments_due (180-234) | Never tells again a subset of what was told. | the checker found F2 here |
| guard.refusal (141-193) | Refuses a `+` refspec, `-f`, `--force` and bundled short options that hold `f`, the default and board branches however spelled, wholesale pushes and their abbreviations, and a lease outside `claude/`. | CrossHair: `'\x00'`; `+\x00` passes, and so does `+claude/7`. |
| protocol_check.problems_for (48-74) | The four rules, exactly. | "A ready pull request that keeps its claim file is refused": not confirmed at 20 s. |
| team.deal (248-262) | Each issue to at most one worker; every dealt issue fits its worker's area; greedy-maximal. | Not confirmed at 20 s; no counterexample. |
| workers.pattern_regex, in_area and fits (205-243) | `fits` implies every file is in the area. | not run yet |
| gate.issue_number and main_moved_instruction (300-307, 597-609) | ASCII digits only, raising nothing but Refused; the instruction names `--force-with-lease` exactly when the policy rebases. | issue_number: not confirmed at 20 s. |

### 9.4 CrossHair, by hand

CrossHair is run by a person, from a scratch virtual environment `<venv>` outside the checkout, over the same functions; it is no dependency of this repository, needs no issue under the no-dependency rule, and no workflow runs it:

```
python3 -m venv <venv>
<venv>/bin/pip install crosshair-tool==0.0.111
<venv>/bin/crosshair check --analysis_kind=asserts --report_all --per_condition_timeout=20 validation/prototype/properties.py
```

Without `--report_all` CrossHair prints only counterexamples and errors: run on this branch, it printed four lines for the seven properties in validation/prototype/properties.py and left out the one confirmed over all paths and the two not confirmed, so a bound that proved nothing would pass in silence.

Every counterexample is replayed in plain Python before it is believed; a counterexample that does not fail on replay is a tool fault.

What the prototype's properties gave (validation/prototype/properties.py, properties_two.py and props_more.py):

| Result | How many | Which |
| --- | --- | --- |
| Confirmed over all paths | 2 | `state_word`, at once, a lookup; the idle rule over whole hours, which restates `is_idle` |
| Not confirmed at 20 s | 6 | `holder_names`, `is_release_comment`, `gate.issue_number`, `problems_for`, `deal`, `render` |
| Counterexample | 3 | `number_of` (`'00'`), `by_number` (`'\u00b2'`), the guard (`+\x00`); render's `'\u27800'` came only at a 120 s bound |
| Tool fault | 1 | the datetime release rule |

What that shows and does not:
- It finds real counterexamples in pure rules in seconds, but every one it found is a short string the enumerated Unicode classes find in milliseconds. Only finite functions, such as `landing_key` and `main_moved_instruction`, can be expected to confirm; anything that takes strings, lists or mappings comes back "not confirmed", which is a bounded search and proves nothing. beethoven-qa measured 107 of 117 conditions not confirmed at 20 s on its own package (beethoven-qa's CLAUDE.md, "Every verdict, every run").
- It says nothing about interleavings (the checker's job), git, GitHub, the wall clock or the I/O modules.
- Its datetime emulation cannot mix with the module's own `timedelta`, made at import time as IDLE_AFTER, so the time rules belong in the enumerated lane.

beethoven-qa's gate is not used as it stands: it always passes `--analysis_kind=icontract` and fails any public function that carries no icontract contracts (beethoven-qa's src/beethoven_qa/gate.py, lines 1-17 and 184-205), so pointed at this package it would fail on every function, and passing it would take icontract inside the package, against the no-dependency rule. What carries over is its discipline: every verdict printed and counted, a precondition no input can meet failing the run, the unproven list, and every counterexample replayed concretely before it is reported.

## 10. Rule coverage and the report

### 10.1 Rule coverage

validation/coverage.py (planned) records which lines of the package each lane executed, with `sys.monitoring`, which is in Python 3.12's standard library; validation/prototype/rule_coverage.py shows the method, and validation/prototype/run_coverage.py runs it over a 2-issue, depth-5 exploration with the board split (9,478 states). That reached 84% of claims, 84% of board, 91% of coordinator, 87% of labels and 63% of the adapter (the board's half of it; GitHubWorld and the clerk's calls are other lanes'), and named what the scope was missing:
- `board.pulls_behind_main` and `main_moved_text`: the scope moved main, but every board run in it got the event `schedule`, the default of `board_run` and `board_second_half` in validation/prototype/model.py, so no run had a push event to the default branch and the main-moved rule (board.py:469) never ran;
- `rename_refused_text` and `explain_refusal`: it had no rename fault;
- `may_write`'s inner `ask`: it had no untrusted author;
- the ordering in `claims.said_finished`: the lossy key folded every state after a "finished" comment into the state before it (section 6.3);
- `labels.holder_labels`, `holders`, `account_label` and `agent_label`: no label of any spelling could reach them, since no board path calls them. `holder_labels` and `account_label` are called only by tests/test_labels.py; `agent_label` is only re-exported by team.py, as `agent_of` (team.py:41), and called nowhere; and `holders` is the clerk's (team.py:123 and 242). The board reads labels through `worker_of`, `reserved`, `holder_names` and `HOLDER_PREFIXES` (board.py:522, 559 and 575; coordinator.py:85 and 143). So `holders` is the dealing lane's to enter (section 9.1), and the other three go into validation/unreached.json (planned) with that reason.

Coverage is how scopes get chosen, and it has a floor: when no lane enters some function of claims, board, coordinator, labels, gate, the board's half of the adapter, or the clerk's dealing and planning in team.py (next_for and deal; route, integration_due, owners_for and seed), the fast lane fails, unless validation/unreached.json (planned) names the function with a reason. A check that passes because it never looked is the failure to fear.

### 10.2 The report

validation/report.py (planned) writes the report as Markdown, from `python3 scripts/validate.py report validation-out/*.json --md validation-report.md` (planned). It is built last, once the lanes work, and it has these sections:
1. **Header:** the library's version and the last commit to the package, read the way scripts/vendor.py reads them (scripts/vendor.py:63-82); Python's and git's versions; the date; one line of verdicts.
2. **Guarantees:** G1 to G15, each with its status and the evidence behind it (scenarios passed, states checked, mutants caught, properties, canary checks). A contradicted guarantee names its finding.
3. **Scenarios by world:** a matrix of passed, known red (with the finding) and red.
4. **Each scenario:** its steps (time, who, verb, outcome, guarantees checked, violations), then its timeline, then the board after each run as a diff from the one before. The timeline has one lane per issue, with bars per holder and marks for release, rename, merge, overlap, "main moved", idle and finished:

   ```
   S02 idle-holder (memory)   0    6    12   18   24   30
   #1  a  claim==draft #100==================| released 26, released/1-3fc99e4
       c                                       claim 27 ====>
   ```

5. **Exhaustive search:** for each scope, states, transitions, depth, time, reductions, the violations with their shortest traces and replay commands, and the situations reached and not reached.
6. **Mutation lane:** each mutant, caught by what, or slipped.
7. **Properties:** inputs tried and failures for each property; the unproven list; and CrossHair's verdicts when a person ran it.
8. **Rule coverage:** for each module, lines reached against executable lines, and the functions no lane entered.
9. **Canary:** the checks from its last run.
10. **What it does not show:** Claude's own behaviour, since no model runs; GitHub beyond the canary's checks (the delay before mergeability is computed, a secondary rate limit, which no check trips, and outages); rulesets other than the one C7 makes; workers' own REST calls outside board runs, which are modelled as atomic verbs; forks; the template's skills and hooks other than the guard.

tests/test_validation_report.py (planned) pins the Markdown on a synthetic results file. An HTML page with the same content (inline SVG timelines, no script, no CDN, as coordbench's comparison page) is optional and comes after everything else.

## 11. The live canary

These are what no local world can show, and only these run live; facts GitHub's documentation settles are taken from it once:
- renaming a branch closes the open pull request whose head it is (github/docs renaming-a-branch.md:19);
- a branch covered by a branch protection or a repository-level ruleset can be renamed only by an administrator (renaming-a-branch.md:31);
- at most one run is pending in a concurrency group, and a newer one replaces it, unless the group sets `queue: max` (github/docs data/reusables/actions/actions-group-concurrency.md), which github.com offers (github/docs data/features/actions-nga.yml);
- a secondary rate limit answers 403 or 429 with a message that says so, and a `retry-after` header when it sends one (github/docs content/rest/using-the-rest-api/rate-limits-for-the-rest-api.md:112). F11's fix is tested against the fake's injected answers of that shape (section 3.2), and no check trips the limit on purpose: requests made while limited "may result in the banning of your integration" (the same page, line 114).

**Where it runs.** The internal test repository OpenSpineConsortium/sdlc-template-test, which this environment reaches and which deletes a head branch on merge. A new public repository made from the template would put the template's files in public, which the template being internal rules out. Its main predated the vendored library when this was written (76853d7: the old tools/board.py and no tools/sdlc_protocol), so it is first brought to the current template by an adoption through the front door. It then takes each release of the library through its own protocol.yml, so the canary after a release checks the release on the live service.

**The driver.** `.github/workflows/canary.yml` and `scripts/canary.py` (both planned), in this repository, use an App token minted for that one repository with the sync's `SDLC_APP_ID` and `SDLC_APP_PRIVATE_KEY` (`actions/create-github-app-token@v1` with `repositories: sdlc-template-test`, as .github/workflows/ci.yml:100-108 mints one for a consumer), with contents, issues and pull requests write, which SymphonyBoard's manifest grants (symphony-app's manifest.json). C2 also needs actions read, to list the runs (github/docs src/rest/data/fpt-2022-11-28/actions.json, "List workflow runs for a repository"), and that manifest grants no Actions permission: the owner adds Actions read to the App, or C2 lists the runs with the owner's own `gh` sign-in. No check dispatches a workflow, which takes Actions write ("Create a workflow dispatch event", in the same file); each starts its board run with an event the token can make. A push made with that token starts the test repository's own workflows, as a real worker's push does (README.md, "How a change reaches the other repositories", step 3). The driver polls the branch board's STATE.md every 10 seconds, for at most 5 minutes per expectation.

| Check | What the driver does | What it settles |
| --- | --- | --- |
| C0, once, now | one REST GET on any open draft pull request | the `mergeable` and `mergeable_state` GitHub reports for a draft (F9); GraphQL marks `MergeStateStatus.DRAFT` deprecated (github/docs src/graphql/data/fpt/schema.docs.graphql:26531-26536), so the REST answer is not known |
| C1 the race | two clones push `claude/N` from main at the same instant | that exactly one push is taken; the first design's check pushed one after the other, which a local bare repository already shows |
| C2 the concurrency group | merges a pull request, which fires pull_request closed, a push to main, issues closed and a branch deletion at once, beside a worker's push to a claude/ branch; counts the runs by event, the cancelled ones included | whether the push to main's run survives and "main moved" is posted (F12), and whether a skipped run takes the pending slot |
| C3 a non-ASCII name | pushes `claude/\u00b2` and `claude/caf\u00e9` on a throwaway issue | whether GitHub takes the names, and what the board run does with them (F6, live) |
| C5 the activity API | after a lease push onto a throwaway claim, reads `GET /repos/{owner}/{repo}/activity` | whether it lists the `force_push` with its `pusher`, `pushed_at`, `before` and `after`, as GitHub's REST description says (the options for F7 and F8) |
| C6 a release end to end, once | on a throwaway issue N: pushes a claim, announces it, opens a draft and comments on it, then pushes one more commit whose committer date is 25 hours back (GIT_COMMITTER_DATE), whose pull_request synchronize event (board.yml:66) starts the board run; once the claim is released, pushes `claude/N` again at the released head | a release comment by `github-actions[bot]` with the marker first, and the author_association GitHub gives it (the fake gives NONE); the labels off; the rename to `released/N-<sha7>`; the draft closed; one "released:" comment; N back in the Queue; that the board's own pushes start no board run; that the draft's updated_at moved with the comment and with the push; and that the run the second push of `claude/N` starts deletes it, its rename answering 422 |
| C7 rulesets, by hand | adds a repository ruleset on `refs/heads/claude/**` with only non_fast_forward | a lease push onto a claim is refused; the board's rename of a released claim is refused, 403 or 422; one "rename refused" comment names the ruleset; after the ruleset is removed the next run renames (F7's trade-off) |

C6's backdated commit comes last on purpose: the claim's own push fires `create` (board.yml:63), whose run would release a claim already 25 hours idle before the draft it is to close was open.

C4 is not used. A burst of comments that trips the limit would learn only what the documentation says, would spend the installation token of SymphonyBoard, on which the sync and the front door depend, and nothing shows it would test the board run, which posts with the test repository's own GITHUB_TOKEN (symphony-template's board.yml:127-128), not with that token. The other numbers stay as the findings cite them.

C7 needs Administration on the test repository, so it runs with the owner's own `gh` sign-in, or with Symphony Setup installed on that repository alone; never with SymphonyBoard, which by decision holds Administration on no repository.

**Output.** The driver writes `canary.json`, with each check, the values it saw and the library version the test repository vendors (its tools/VENDORED), and a job summary. On a failure it opens one issue in this repository, "Canary failed: \<check\>", and updates that issue on later failures rather than opening another.

**How often.** Monthly and by hand; C0 and C6 once, and again only when the owner asks.

## 12. Where it lives, how long it runs, and how it guards every change

The files the harness adds; those step 1 built (section 14) are on main, the rest planned:

```
symphony-protocol/
  sdlc_protocol/          unchanged, but for the board run's two halves in cli.py (section 3.3), built by step 3
  validation/             never vendored: scripts/vendor.py copies only sdlc_protocol/ (scripts/vendor.py:151-156)
    README.md  DESIGN.md  prototype/            on this branch
    GUARANTEES.md  known.json  unproven.json  unreached.json  equivalent.json  guard_git.json  traces/
    __init__.py  model.py  fake_github.py  git_store.py  cycle.py  verbs.py  policies.py  page.py
    oracle.py  invariants.py  situations.py  scenarios.py  run.py
    explore.py  shrink.py  mutants.py  guard_grammar.py  dealing.py
    properties.py  domains.py  coverage.py  report.py
  scripts/validate.py     fast | deep | mutants | scenarios | explore | guard | deal | properties | replay | report
  scripts/canary.py
  tests/test_validation_<module>.py, tests/test_validate.py, tests/test_canary.py
  .github/workflows/validate.yml, canary.yml
  validation-out/         what the lanes write (traces, results, the report): uploaded as an artifact, added to .gitignore
```

Every file is ASCII with LF endings and a final newline; a hostile character appears only as a `\u` escape, because scripts/check.sh rejects anything else (scripts/check.sh:13-35). ECOSYSTEM.md is unaffected, because the ecosystem scripts read only the package (scripts/ecosystem.py:25).

| Lane | Command | Where | When | Budget |
| --- | --- | --- | --- | --- |
| fast | `python3 -m unittest discover -s tests`, which picks up tests/test_validation_*.py | the job named "check" (.github/workflows/ci.yml:40-50) | every pull request and every push to main | at most 60 s added to the suite, which ran 661 tests in 35 s on this branch, and took 35 to 44 s in three measurements on 2026-10-13 |
| mutation | `python3 -m validation.mutants` | validate.yml | weekly, by hand, and on every pull request that changes `validation/`, the library or the harness's tests | about 90 seconds |
| deep | `python3 scripts/validate.py deep --out validation-out` | validate.yml | weekly, by hand, and on a pull request labelled `deep validation` | 20 to 40 minutes |
| CrossHair | `crosshair check --analysis_kind=asserts --report_all ...` (section 9.4) | a person's own scratch environment | by hand, after a rule changes | 1 to 5 minutes |
| canary | `python3 scripts/canary.py` | canary.yml | monthly, and by hand | 10 to 15 minutes |
| report | `python3 scripts/validate.py report ...` | each lane above, uploaded with `actions/upload-artifact@v4` | with each lane | seconds |

How it guards every future change:
1. **The fast lane is part of the required check.** A pull request to the library runs it inside "check", so a rule change that breaks a guarantee goes red with the shortest trace in the log.
2. **Exceptions stay visible.** A fix removes its entry from known.json in the same pull request. A spec edit that drops or rewords a guarantee breaks tests/test_validation_guarantees.py until the oracle and validation/GUARANTEES.md follow.
3. **Coverage has a floor** (section 10.1), and **the mutation lane keeps the harness honest**: a change to the harness that stops it catching a mutant fails that lane.
4. **The harness grows without sync noise.** A commit that changes only `validation/` changes no consumer's record, since scripts/vendor.py records the last commit that changed `sdlc_protocol/` (scripts/vendor.py:71-82), so no sync pull request follows.

No step is added to the downstream jobs. A partial copy is already covered by scripts/ecosystem.py's `missing_imports` and `copy_problems`, by `vendoring.py verify`, and by each consumer's own check, which those jobs run.

## 13. Findings

Every reproduction runs from the repository's root, on the library as the checkout holds it; the outputs quoted are from cf3bdb0.

**F1. A release whose label removal failed is never completed.** Status: fixed in 0.4.1 (#11).
- **What happens.** github.release_one posts the comment first (adapters/github.py:494), then takes the labels off (497-502). When the removal raises, it returns a line and never calls after_release (503).
- **Why it never recovers.** On every later run coordinator.releases_due skips the claim as already released, since claims.release_due counts a trusted comment that came more than a day after the last commit as a release (claims.py:194-208, 262-272). Meanwhile board.is_released refuses the claim, because claimed is still on (board.py:355).
- **Evidence.** `python3 validation/prototype/probe_coordinator.py` prints `releases_due = [] | released_keys = set()` on the next run and again thirty days on. The checker's shortest trace (`python3 validation/prototype/explore.py --depth 5 --all`) is claim a 1, announce a 1, wait, board with the label DELETE answering 500, board.
- **Effect.** The issue says the claim was released, while the board shows it held and the Queue leaves it out. Only a person taking the label off, or the holder committing and going idle for another day, moves it. GitHub's issue timeline would tell a failed removal from an undo (an undo shows a person's "labeled claimed" event after the comment), but the board does not read the timeline.

**F2. Overlapping work is told again when a claim opens its pull request.** Status: open, left so by 0.4.1; the owner's choice of fix (section 14).
- **What happens.** tell_overlaps reads what has been told from the comments on the work's own number (adapters/github.py:513-517). A claim's work is numbered by its issue until it opens a pull request, and by the pull request after that (board.open_work, board.py:624-635), and the pull request has no markers yet. The marker also names the other piece by the number it had at the time (coordinator.py:229-233).
- **Evidence.** `python3 validation/prototype/probe_coordinator.py`: run 1 tells #7 and #12 of each other; once #7's claim opens #13, run 2 tells #12 and #13 of each other, the same two pieces of work. The checker's shortest trace (`python3 validation/prototype/explore.py --issues 2 --depth 6 --all`) is claim a 1, claim a 2, board, draft a 1, board.
- **Against the spec.** Line 73 says the coordinator "tells it again only when work it has not been told of starts to meet it". This is noise, not a safety problem.

**F3. The push guard passes forced pushes and pushes to main in plain shapes.** Status: fixed in 0.4.1 (#11).
- **What happens.** guard.refusal takes a leading `+` off a refspec (guard.py:171) but does not count it as a force; it counts only `-f` and `--force`, matched exactly (150-151), matches `--all`, `--branches` and `--mirror` exactly (154), and skips every other word that starts with a dash (158-159), so bundled short options (`-fu`) and the abbreviations git accepts (`--al`, `--mirr`) pass. It takes only `refs/heads/` off a destination (174), so `heads/main` passes. push_arguments reads a command as a push only when its first word is git (125), so `FOO=1 git push origin main`, and the same behind `env`, `command`, `nice` and `time`, pass.
- **Evidence.** `python3 validation/prototype/probe_guard.py` lists the shapes the guard passes. `python3 validation/prototype/guard_grammar.py` judged 10,800 commands with a hand-written oracle and found 7,734 of the 8,550 forbidden ones let through, and no false refusal; by cause, 132 are a plain `+` refspec, 1,596 another worker's claim, and the rest the same causes behind a prefix or inside `bash -c`. `python3 validation/prototype/gitflags.py` shows git 2.43 performing `-fu` as a forced update, `--al` as `--all`, `--mirr` as `--mirror`, and `heads/main` as a push of `main`, each passed by the guard. `python3 validation/prototype/guard_git_oracle.py` judges the same shapes by what git offers. CrossHair found `+\x00` on its own (validation/prototype/properties.py, `a_forced_refspec_is_refused`).
- **Which can be fixed.** Every cause the command shows (section 8.3's first four rows) can; a command inside a string another program runs, and whose a claim branch is, cannot be read from the command alone (G11b).

**F4. The clerk can deal one issue to two workers of the same run.** Status: decision for the owner.
- **What happens.** Each worker in symphony-template's workers.yml asks at its own job's start (workers.yml:117) and gets its answer from its own snapshot (team.py:265-282), from which its idle peers are worked out (team.py:280).
- **Evidence.** `python3 validation/prototype/probe_deal.py`: b asks while a's ready pull request is clean and is told `take 6`; c asks after main has moved, so a's claim needs work again, and is also told `take 6`.
- **Effect.** The lock still holds, so one push of `claude/6` wins and the other run is wasted. Spec line 77 says the work is "dealt ... so no two race for one claim".
- **The critique's view.** It reproduced F4 and proposed no change of its own. The choice stays the first design's: one job deals the whole run and passes the deals to the matrix, or the occasional wasted run is accepted, since the lock holds.

**F5. Two branch names can claim one issue.** Status: fixed in 0.4.1 (#11).
- **What happens.** claims.number_of accepts any decimal digits after `claude/` (claims.py:85-91), so `claude/03` and `claude/\u0663` (Arabic-Indic three) both name #3, while GitHub's first-push lock works per name.
- **Evidence.** `python3 validation/prototype/probe_names.py`: the board lists two rows, `#3 alice` and `#03 bob`, and the protocol check passes both drafts. The checker's careless scope (`python3 validation/prototype/explore.py --depth 5 --all --careless`) finds it in two steps, and also a release posted for `claude/01` that the oracle never counted due. CrossHair found `'00'`.
- **Contrast.** The gate already takes issue numbers in ASCII digits only (gate.py:74-79).

**F6. A single hand-made branch name stops the board.** Status: fixed in 0.4.1 (#11).
- **In the render.** claims.by_number tests with `isdigit` and then calls `int` (claims.py:103), which is exactly what its neighbour warns against: "a superscript passes isdigit() but not int(), and a branch name must never raise" (claims.py:86-89). board.claim_rows sorts by it (board.py:531), so render raises ValueError for `claude/\u00b2` (validation/prototype/probe_names.py) or `claude/\u27800` (CrossHair on validation/prototype/properties_two.py). board.claimed_numbers and open_work make the same mistake (board.py:326, 632-635).
- **In a live run, earlier and wider.** http.client refuses any non-ASCII request path before it connects, with UnicodeEncodeError, which is a ValueError, and GitHub.request turns it into the GitHubError "the answer was not JSON" (adapters/github.py:150-151), so cli.run_board exits 1 before STATE.md is written (cli.py:121-127). claim_text builds `/contents/docs/agents/claims/{key}.md` with the raw key, and collect calls it for every `claude/` branch (adapters/github.py:269-272, 418), so a branch such as `claude/caf\u00e9`, with no digit at all, stops every live run although the render handles it; claim_issues puts a key that passes `isdigit` into `/issues/{key}` (adapters/github.py:301-303). The prototype's fake parsed and unquoted the URL, so it could never show this; the harness's fake encodes the request line as http.client does (section 3.2).
- **Effect.** Until that branch is deleted, every board run in the repository fails, the coordinator's releases included.

**F7. A worker that has fetched can take someone else's claim.** Status: decision for the owner.
- **What happens.** With git 2.43, a lease push after a fetch overwrites another worker's claim (`python3 validation/prototype/gitprobe.py`: the remote's claim file then says `- Worker: b`), and the guard passes it (guard.py:188-192).
- **Why GitHub does not stop it.** A ruleset covering `claude/**` would refuse the push, but under a repository-level branch ruleset only an administrator may rename a branch (renaming-a-branch.md:31), and the board renames every released claim. That is why the spec tells rulesets to leave `claude/**` out (spec line 69). So under the setup the spec recommends, nothing on GitHub enforces "never push to another worker's branch". Canary C7 measures the trade-off.
- **The critique's view.** A ruleset blocking forced pushes on `claude/**`, the first design's proposal, defeats itself: it refuses every `--force-with-lease`, the session's prescribed way to bring main in (spec line 55; ACCOUNT_POLICY, gate.py:121), and makes every rename an administrator's. Two better options: the guard, before a lease push, reads the claim file on `origin/claude/N` locally and refuses when its Worker line is not this worker; or, on GitHub's side, `GET /repos/{owner}/{repo}/activity` (GitHub's REST description) lists each `force_push` with its `pusher`, `pushed_at`, `before` and `after`, which the board could read (canary C5).

**F8. Idleness is the pusher's clock.** Status: decision for the owner.
- **What happens.** A release reads the head's committer date (spec line 67; github.committed_at, adapters/github.py:353-357), which the person pushing sets. A commit dated in the future holds a claim past its idle day; a commit dated in the past is released at once. The canary uses the second on purpose (C6).
- **The critique's view.** The first design asked whether the spec should say that idleness is measured by committer date; it already does (line 67: "by the committer date of its head"), so that question is moot. The real choice is whether to read the server's `pushed_at` from the activity API instead, which would also answer F8 (canary C5).

**F9. A draft may never be told "main moved".** Status: decision for the owner.
- **What happens.** board.state_word maps GitHub's mergeable_state "draft" to unknown (board.py:293-302); tests/test_board.py:333 pins that mapping, and tests/fixtures/board-wide.json:21 uses it. The "main moved" rule speaks only to pull requests behind or conflicting (board.py:417-420).
- **Effect.** If GitHub reports "draft" for every draft, as that fixture and the adapter's OPAQUE_STATES (adapters/github.py:50-53) assume, a claim's draft never hears "main moved", and a draft is the protocol's working state (spec line 55). The gate's world, by contrast, reads such a draft through mergeable and behind_by (adapters/github.py:657-670).
- **The critique's view.** One REST GET on any open draft settles what GitHub answers, with no canary repository (C0). GraphQL marks `MergeStateStatus.DRAFT` deprecated (github/docs src/graphql/data/fpt/schema.docs.graphql:26531-26536), so the REST answer today is not known. Then: should drafts hear "main moved", read the way GitHubWorld reads them?

**F10. Releasing a finished draft by hand never takes effect.** Status: fixed in 0.4.1 (#11).
- **What happens.** github.releases skips a finished draft before it reads any release comment (adapters/github.py:394), so board.is_released never sees the person's release. The spec counts it as released: line 69 excepts only in-review and a pull request marked ready, and line 67 says a person "may release by the same steps". The labels come off, but the board keeps showing the claim held, and the issue stays out of the Queue.
- **Evidence.** `python3 validation/prototype/probe_finished_by_hand.py`: the oracle says released, with the Queue `[1]`; the board shows `claude/1` held, and still does thirty days later. `python3 validation/prototype/find_t3.py` finds the trace: claim, wait, announce, draft, finished, release by hand, board.
- **The fix's choice.** Either the release takes effect, or a by-hand release of a finished draft is refused as a whole rather than half applied.

**F11. One refused comment fails the whole board run, and the board branch is not written.** Status: fixed in 0.4.1 (#11).
- **What happens.** post_comments and comment_once (adapters/github.py:436-452) let GitHubError escape, while coordinate (463-465) and release_claim (548-554) deliberately swallow theirs. board_live_run has already written STATE.md locally (cli.py:110) and then raises at 116, and run_board exits 1 (121-127). symphony-template's step that writes the branch board (board.yml:132-134) runs only after success, so it is skipped.
- **Evidence.** `python3 validation/prototype/probe_comment_failure.py` prints `board: POST /repos/o/r/issues/100/comments: HTTP 500` and exit code 1, with STATE.md written locally. With a refused comment POST in scope, 116 board runs raised at depth 6, before the reductions (`python3 validation/prototype/ext_verbs.py`).
- **Effect.** A secondary rate limit does the same. A comment GitHub refuses for good would freeze the board branch: the marker is never written, so every run tries the comment again and fails again.
- **The fix, and the harness.** Catch the error per comment, as release_claim does; the harness adds G15 and faults on every write endpoint.

**F12. "Main moved" is lost under the board's concurrency group.** Status: open, left so by 0.4.1; the owner's choice of fix (section 14).
- **What happens.** "Main moved" is the only rule keyed on the event that started the run (board.py:469). symphony-template's board.yml:83-85 sets `group: board` with no `queue`, and by default a newer run replaces the one pending in the group (github/docs data/reusables/actions/actions-group-concurrency.md).
- **Why it is common.** A merge fires pull_request closed, a push to main, issues closed and delete at nearly the same moment, and the push trigger at board.yml:62 has no branch filter, so every worker's push to a `claude/` branch also queues a run, whose job board.yml:94 then skips; that such a run still takes the pending slot is inferred, not measured. So the push to main's run is often replaced, and that commit's "main moved" comments are never posted.
- **The fix's options.** Make "main moved" depend on state: on any run, tell each behind or conflicting pull request that has no marker for main's current sha. Or add `queue: max` to the group, which github.com offers (github/docs data/features/actions-nga.yml). The model has the transition "a pending run is replaced" (section 3.3), and canary C2 measures the drops.

**F13. The landing order's "oldest" is the lowest number.** Status: found by build step 1; a fix in the library or a word in the spec, the owner's choice.
- **What happens.** board.landing_key orders the open work that meets in a file by blocker, then ready, then the number the work is shown by (board.py, landing_key), where spec line 73 says "then the oldest". A claim with no pull request is shown by its issue's number and one with a pull request by the pull request's, so the two kinds are compared by numbers from two sequences, and a claim made later lands before an older draft whenever its issue's number is the lower.
- **Evidence.** tests/test_validation_invariants.py, `test_f13_the_landing_order_s_oldest_is_the_lowest_number`: a claims #1 and opens its draft (#100); five hours later b claims #2, which lists the same file; the board's Overlaps row reads #2, #100, where the oracle, reading "the oldest" as the work that began first, reads #100, #2. The oracle reads the rule as built with F13 in oracle.AS_BUILT.
- **The choice.** Order by when the work began, which the board can read from the claim branch's first commit or the pull request's opening; or say in the spec that the order is by number, which is what a reader of the page can check.

**F14. Given to workers needs the label todo.** Status: found by build step 1; a fix in the library or a word in the spec, the owner's choice.
- **What happens.** board.given_rows lists an issue under Given to workers only when it carries todo and not epic (board.py, given_rows), where spec line 17 says "An issue a worker's label names is that worker's alone: it is left out of the board's Queue and shown under Given to workers until the worker claims it", and the coordinator gives a task by "labelling it `worker:<name>`" (line 77). An open issue labelled worker:a with no todo is shown in neither section.
- **Evidence.** tests/test_validation_invariants.py, `test_f14_given_to_workers_needs_the_label_todo`: one issue labelled worker:a alone; the page's Given to workers is empty, the oracle's names #1 for a. The oracle reads the rule as built with F14 in oracle.AS_BUILT.
- **The choice.** Show every open, unclaimed issue a worker's label names; or say in the spec that Given to workers lists the queue's issues, labelled todo, that a worker's label names, and that an epic is the coordinator's.

## 14. Build order and the owner's decisions

Pull requests, in order:
1. **Foundations, in memory:** the sound state key, waits of 24 and 25 hours and the atomic verbs; no pruning at a violation; the effect-equivalence oracle built from the spec's text, and G15; validation/model.py, fake_github.py, cycle.py, verbs.py, policies.py, oracle.py and invariants.py, GUARANTEES.md and its test. Built, with explore.py, page.py and the fake's transport (an ASCII request line, Link pagination, `mergeable` null on the first read of a run) and the board-branch write that happens only when a run exits 0, which were step 3's: validation/README.md says what each file holds and what it measured. Until known.json (step 5), the open findings are switches in the oracle, `oracle.AS_BUILT`, and F7 shows only under the careless scope.
2. **The mutation lane,** standard library, 10 to 20 mutants, as the acceptance test of step 1: no step after this one starts until every mutant is caught. Built: validation/mutants.py, 19 mutants, every one caught (validation/README.md says by which search, and what the lane made the harness add); validate.yml runs the lane, and step 6 adds the deep lane to it.
3. **The run as the workflow makes it:** the concurrency group, with its pending run replaced; and the board run in two halves in cli.py. Built: `cli.plan_board` and `cli.act_on_board`, which the next release carries to the consumers, with `board_live_run` unchanged in what it does; `cycle.start_run` and `cycle.finish_run`, the scope's `split_board`, and the checks judging a split run by the states its halves read (section 3.3); the events each verb fires and the queue in the model (`RepoModel.fire` and `starts`), the scope's `events` now naming which the model hears (section 3.3); S09 and F12's mechanism as tests; the twentieth mutant and the lane's fifth search, the run split (section 7). Measured: two workers on one issue to depth 4 reach 4,359 states hearing every event and 4,102 with the run split, in about 3 seconds each, with no violation on 0.4.1; the lane takes about 105 seconds.
4. **The fast lane, under 60 seconds:** S01 to S05 and the three table-driven tests (section 5.3); the checker at depth 5 plus searches from scenario prefixes; the guard grammar against git's answers, which this step records first, into validation/guard_git.json (planned; section 8.2), by the command the deep lane later runs again; dealing; the Unicode-class property lane.
5. **Regression traces for F1 to F12,** each red in known.json until its fix lands, which then removes the entry; F1, F3, F5, F6, F10 and F11, fixed in 0.4.1, are recorded green from the start.
6. **The deep lane:** two issues at depth 7, one issue at depth 8 with the depth-aware digest set, the soak, conformance against git, git's answers for the guard grammar recorded again on every deep run, and the gate over BareGitWorld; validate.yml.
7. **One GET now for F9 (canary C0).** Then the monthly canary on sdlc-template-test, once it carries the current template: canary.yml and scripts/canary.py, both planned.
8. **The Markdown report** last; the HTML page optional.

The owner's decisions:
- **F2.** Which identity for a piece of open work in the overlap marker: the issue for as long as the claim lives, so opening the pull request tells nothing again, or the pull request once there is one, with the issue's markers read as told too?
- **F12.** Make "main moved" depend on state, telling on any run each behind or conflicting pull request with no marker for main's current sha; or add `queue: max` to the board's concurrency group in the copies' board.yml.
- **F4.** Should one job deal the whole run and pass the deals to the matrix, or is the occasional wasted run accepted, since the lock still holds?
- **F7.** Which second fence, if any: the guard reading the claim file on `origin/claude/N` before a lease push, the board reading the activity API's `force_push` events, or neither? A ruleset blocking forced pushes on `claude/**` is ruled out: it refuses the prescribed lease and makes renames an administrator's.
- **F8.** Keep the committer date, as the spec says, and state the trust it places in the pusher; or read the server's `pushed_at` from the activity API, once canary C5 shows it behaves as documented.
- **F9.** After C0's one GET: should drafts hear "main moved", read the way GitHubWorld reads them?
- **F13.** Order the overlaps by when the work began, or write the number order into the spec?
- **F14.** Show every open, unclaimed issue a worker's label names under Given to workers, or write todo, and the epics' exclusion, into the spec?
- **The canary.** Bringing sdlc-template-test to the current template; for C2, Actions read added to SymphonyBoard or the owner's own sign-in; and, for C7, the owner's own sign-in or Symphony Setup installed on that repository for the run.
