# The validation prototype

This is the prototype that [../DESIGN.md](../DESIGN.md) measured: a model of the state GitHub holds for the claim protocol, a fake REST opener that the library's real GitHub adapter runs against, a breadth-first checker over the protocol's verbs, a checker over the merge gate's MemoryWorld, the push guard's grammar, rule coverage, hand probes of the findings, the critique's measurements and mutants, and properties for CrossHair. It is kept so every number in DESIGN.md can be reproduced, and nothing more:
- It is not part of the library. scripts/vendor.py copies only `sdlc_protocol/`, so nothing here is ever vendored into a consumer, and a commit that changes only this folder brings no sync.
- The test suite does not run it: `python3 -m unittest discover -s tests` reads only `tests/`, and no file here is a test.
- It is to be replaced by the harness DESIGN.md describes, which builds each piece again under the corrections the critique made (a sound state key, waits of 24 and 25 hours, atomic verbs, an oracle from the spec's text, no pruning at a violation, a fake faithful to http.client). Where a script here does what DESIGN.md says not to do, DESIGN.md says so and why.

## Running

From the repository's root, with Python 3.12 or later and git, and nothing to install:

```
python3 validation/prototype/<file> [arguments]
```

Each script finds the library at the repository's root from its own path, so it measures the library as the checkout holds it, and finds the prototype's other modules beside it. The scripts that make git repositories make them in a temporary directory, remove it at the end, and do not read the host's git configuration. Only the three property files need something more: CrossHair, from a scratch virtual environment (below).

The outputs below were printed on 2026-10-13, from a checkout at cf3bdb0 (the package at a996ec9), on 4 CPUs shared with other work, Python 3.12.3 and git 2.43.0. State counts, traces and verdicts reproduce; times move with the load, and CrossHair can report another counterexample of the same kind from one run to the next. Characters outside ASCII that a script prints are written here as `\u` escapes; unicode_classes.py prints its names through `ascii()`, so its output is shown as it prints.

## How these copies differ from the ones measured

The scripts were written in a session's scratch space and measured there; the copies here differ only so they run from this repository:
- Every absolute path became one found from the file's own place: the library at `Path(__file__).resolve().parents[2]`, the prototype's modules at `Path(__file__).resolve().parent`. measure.py runs explore.py by its path beside it.
- probe_names.py's two non-ASCII branch names are written as `\u` escapes, which Python reads as the same strings.
- probe_comment_failure.py writes STATE.md into a temporary directory that it now removes.
- gitprobe.py and gitflags.py are new files: the measured probes left no script, only the git repositories they made (a bare remote and its clones), from which these two were written and run again.
- guard_git_oracle.py and unicode_classes.py are new, written on this branch to measure what DESIGN.md sections 8.2 and 9.2 propose.

Line numbers DESIGN.md gives for these files are their line numbers here.

## The model and the checker

**model.py** is a module the others import; run alone it prints nothing. It holds the model (refs, content-addressed commits, issues with labels and comments, pull requests, the clock in hours, every commit a claim branch ever pointed at, the label events), `FakeGitHub`, an opener that answers the adapter's calls from the model and changes it on writes, and the board run in `cli.board_live_run`'s order on the model's clock, whole or in two halves.

**explore.py** is the breadth-first checker: every interleaving of the verbs for the workers given, up to a depth, each state checked against invariants written from the spec, a violation printed with the shortest trace that reaches it. Its options are `--issues N`, `--workers a,b`, `--depth D`, `--all` (go on after the first violation), `--careless` (a worker may push `claude/0<n>`) and `--split` (other verbs may happen inside a board run). Its key is the lossy one DESIGN.md section 6.3 describes.

```
$ python3 validation/prototype/explore.py --depth 5 --all
states 1599, transitions 3482, 1.6 s, violations 2
- L1 a release no rule completes: #[1]
  trace: claim/a/1 -> announce/a/1 -> wait -> board_fault -> board

$ python3 validation/prototype/explore.py --depth 5 --all --careless
states 1913, transitions 5080, 1.9 s, violations 966
- L1 a release no rule completes: #[1]
  trace: claim/a/1 -> announce/a/1 -> wait -> board_fault -> board
- S1 two branches claim #1: ['claude/1', 'claude/01']
  trace: claim/a/1 -> claim_padded/a/1
- T1 released a claim that was not due: [1]
  trace: claim_padded/a/1 -> wait -> board

$ python3 validation/prototype/explore.py --issues 2 --depth 6 --all
states 41215, transitions 93204, 43.4 s, violations 288
- L1 a release no rule completes: #[1]
  trace: claim/a/1 -> announce/a/1 -> wait -> board_fault -> board
- S8 #1's work was told again of [2] (on #1)
  trace: claim/a/1 -> claim/a/2 -> board -> draft/a/2 -> board
- S8 #1's work was told again of [2] (on #100)
  trace: claim/a/1 -> claim/a/2 -> board -> draft/a/1 -> board
- S8 #1's work was told again of [2] (on #101)
  trace: claim/a/1 -> claim/a/2 -> board -> draft/a/2 -> draft/a/1 -> board
- S8 #2's work was told again of [1] (on #100)
  trace: claim/a/1 -> claim/a/2 -> board -> draft/a/2 -> board
- S8 #2's work was told again of [1] (on #101)
  trace: claim/a/1 -> draft/a/1 -> claim/a/2 -> board -> draft/a/2 -> board
- S8 #2's work was told again of [1] (on #2)
  trace: claim/a/1 -> claim/a/2 -> board -> draft/a/1 -> board

$ python3 validation/prototype/explore.py --depth 7 --all --split
states 35682, transitions 76362, 34.3 s, violations 122
- L1 a release no rule completes: #[1]
  trace: claim/a/1 -> announce/a/1 -> wait -> board_fault -> board
```

L1 is F1, S1 and T1 under `--careless` are F5, and S8 is F2. DESIGN.md section 7 says what each check's name stands for.

**measure.py** runs explore.py with `--all` and the arguments it is given, and adds the peak memory of that run:

```
$ python3 validation/prototype/measure.py --depth 5
--depth 5 | states 1599, transitions 3482, 1.8 s, violations 2 | max rss 29 MB
$ python3 validation/prototype/measure.py --workers a,b,c --depth 6
--workers a,b,c --depth 6 | states 10144, transitions 23793, 9.7 s, violations 30 | max rss 62 MB
$ python3 validation/prototype/measure.py --depth 7
--depth 7 | states 26295, transitions 57054, 23.4 s, violations 122 | max rss 129 MB
$ python3 validation/prototype/measure.py --issues 2 --depth 6
--issues 2 --depth 6 | states 41215, transitions 93204, 41.8 s, violations 288 | max rss 219 MB
$ python3 validation/prototype/measure.py --depth 8
--depth 8 | states 106267, transitions 231220, 105.7 s, violations 596 | max rss 470 MB
```

**explore_variants.py** runs the same search under five variants, one line each: pruning at a violation or not; the key with absolute times or with every gap capped at 25 hours; with or without the history the invariants read (the commits ever at a claim branch's head, the label events); and with worker symmetry (the least key over every permutation of the workers' names). Its key always holds whole comment bodies, the pull requests' comments included. It takes `--issues`, `--workers`, `--depth`, `--split` and `--careless`.

```
$ python3 validation/prototype/explore_variants.py --depth 5
prune=True  capped=False history=False symmetry=False: states    1973  transitions     3952  violating      2 ['L1']     1.6 s
prune=False capped=False history=False symmetry=False: states    1973  transitions     3952  violating      2 ['L1']     1.9 s
prune=False capped=False history=True  symmetry=False: states    1979  transitions     3952  violating      2 ['L1']     1.7 s
prune=False capped=True  history=True  symmetry=False: states    1628  transitions     3482  violating      2 ['L1']     1.6 s
prune=False capped=True  history=True  symmetry=True : states     830  transitions     1798  violating      1 ['L1']     0.9 s

$ python3 validation/prototype/explore_variants.py --depth 6
prune=True  capped=False history=False symmetry=False: states    8887  transitions    17674  violating     24 ['L1']     8.4 s
prune=False capped=False history=False symmetry=False: states    8887  transitions    17674  violating     24 ['L1']     8.1 s
prune=False capped=False history=True  symmetry=False: states    8985  transitions    17724  violating     24 ['L1']     8.6 s
prune=False capped=True  history=True  symmetry=False: states    6935  transitions    14646  violating     22 ['L1']     6.8 s
prune=False capped=True  history=True  symmetry=True : states    3494  transitions     7419  violating     11 ['L1']     4.8 s

$ python3 validation/prototype/explore_variants.py --depth 7
prune=True  capped=False history=False symmetry=False: states   40123  transitions    79466  violating    182 ['L1', 'T3']    37.8 s
prune=False capped=False history=False symmetry=False: states   40123  transitions    79466  violating    182 ['L1', 'T3']    41.3 s
prune=False capped=False history=True  symmetry=False: states   41147  transitions    80298  violating    184 ['L1', 'T3']    46.9 s
prune=False capped=True  history=True  symmetry=False: states   29753  transitions    62080  violating    154 ['L1', 'T3']    32.9 s
prune=False capped=True  history=True  symmetry=True : states   14920  transitions    31199  violating     77 ['L1', 'T3']    21.1 s

$ python3 validation/prototype/explore_variants.py --depth 7 --split
prune=True  capped=False history=False symmetry=False: states   54814  transitions   106680  violating    182 ['L1', 'T3']    58.0 s
prune=False capped=False history=False symmetry=False: states   54814  transitions   106680  violating    182 ['L1', 'T3']    55.2 s
prune=False capped=False history=True  symmetry=False: states   56290  transitions   107870  violating    184 ['L1', 'T3']    55.2 s
prune=False capped=True  history=True  symmetry=False: states   41694  transitions    84605  violating    154 ['L1', 'T3']    44.9 s
prune=False capped=True  history=True  symmetry=True : states   20917  transitions    42536  violating     77 ['L1', 'T3']    27.6 s
```

The third line of each run is the sound key; the fifth adds the reductions. T3 at depth 7 is F10, which this key reaches because it keeps the pull requests' comments.

**scopes.py** `<issues> <workers> <depth>` runs two of DESIGN.md's budgeted scopes with the sound key, without and with the reductions:

```
$ python3 validation/prototype/scopes.py 1 a,b,c 6
issues=1 workers=3 depth=6 reductions=False: states 13960 transitions 29799 violating 36 ['L1'] 14.9 s
issues=1 workers=3 depth=6 reductions=True: states 3494 transitions 8208 violating 11 ['L1'] 6.2 s
$ python3 validation/prototype/scopes.py 2 a,b 6
issues=2 workers=2 depth=6 reductions=False: states 54343 transitions 111004 violating 400 ['L1', 'S8'] 57.3 s
issues=2 workers=2 depth=6 reductions=True: states 23353 transitions 49826 violating 198 ['L1', 'S8'] 30.4 s
```

**ext_verbs.py** adds four verbs the prototype lacks (main brought in by a merge, a stranger's marker comment, a refused comment POST, the issue closed without a merge) and counts how the states grow, and the board runs that raised (F11):

```
$ python3 validation/prototype/ext_verbs.py
  board runs that raised: 0, e.g. []
depth 4 capped+symmetry=False: states     859 transitions     1885 violating      0 []    0.7 s
  board runs that raised: 0, e.g. []
depth 4 capped+symmetry=True: states     431 transitions     1038 violating      0 []    0.4 s
  board runs that raised: 8, e.g. ['GitHubError: POST /repos/o/r/issues/100/comments: HTTP 500']
depth 5 capped+symmetry=False: states    4358 transitions     9593 violating      2 ['L1']    4.3 s
  board runs that raised: 4, e.g. ['GitHubError: POST /repos/o/r/issues/100/comments: HTTP 500']
depth 5 capped+symmetry=True: states    1975 transitions     4688 violating      1 ['L1']    2.5 s
  board runs that raised: 116, e.g. ['GitHubError: POST /repos/o/r/issues/100/comments: HTTP 500']
depth 6 capped+symmetry=False: states   22821 transitions    49486 violating     32 ['L1']   23.5 s
  board runs that raised: 54, e.g. ['GitHubError: POST /repos/o/r/issues/100/comments: HTTP 500']
depth 6 capped+symmetry=True: states    9583 transitions    22136 violating     15 ['L1']   12.8 s
```

**find_t3.py** searches with the sound key for the shortest trace on which the board and the oracle disagree, and prints it with the state it reaches. It is F10:

```
$ python3 validation/prototype/find_t3.py
trace: claim/a/1 -> wait -> announce/a/1 -> draft/a/1 -> finished/a/1 -> release_by_hand/1 -> board
problems: ["T3 the board's claims ['claude/1'] are not the oracle's []", "T3 the board's queue [] is not the oracle's [1]"]
note:
issue labels: ['todo']
  comment a COLLABORATOR 25 Plan for #1
  comment owner OWNER 25 <!-- sdlc-coordinator: claim-released -->
refs: {'main': 'b6c181b', 'claude/1': '0b7c9f2'}
commit times: {'main': 0, 'claude/1': 0}
pulls: [(100, 'claude/1', True, 'open')]
now: 25
```

and then the whole board, whose Claims section still lists `| #1 | claude/1 | a | #100 | docs/agents/claims/1.md |` and whose Queue says `(none)`.

**count_calls.py** counts the REST calls one board run makes over the fake, for two claims, a draft and a commit, and again when two releases are due:

```
$ python3 validation/prototype/count_calls.py
fresh state: 14 calls, 1.9 ms
    1 GET /branches
    2 GET /contents
    2 GET /git
    3 GET /issues
    4 GET /pulls
    2 POST /issues
after 25 h (two releases due): 29 calls, 2.6 ms
['#1: claim released, idle since 2026-10-01T00:00:00Z', '#2: claim released, idle since 2026-10-01T00:00:00Z', '#1: claim released; claude/1 renamed to released/1-0b7c9f2', '#2: claim released; claude/2 renamed to released/2-c2cc09e']
```

## The mutants

**mutants.py** `<mutant> [issues] [depth]` changes one rule of the library in its own process, never in the files, and runs the sound-key search; a mutant is caught when the search reports a kind of violation the unchanged library (`none`) does not. **mutants_wait24.py** `<mutant> [depth]` does the same with a wait of exactly 24 hours beside the 25-hour one.

```
$ python3 validation/prototype/mutants.py <mutant> 1 6
none                             issues=1 depth=6: violating     24 kinds ['L1']  (8985 states, 7.9 s)
m_idle_ge                        issues=1 depth=6: violating     24 kinds ['L1']  (8985 states, 7.9 s)
m_keep_worker_label              issues=1 depth=6: violating     24 kinds ['L1']  (8853 states, 7.8 s)
m_ignore_claimed_in_release      issues=1 depth=6: violating      0 kinds []  (9039 states, 8.7 s)
m_release_ready_prs              issues=1 depth=6: violating     24 kinds ['L1']  (8985 states, 9.3 s)
m_idle_comment_to_released       issues=1 depth=6: violating     24 kinds ['L1']  (8985 states, 8.2 s)
m_any_later_comment_releases     issues=1 depth=6: violating     24 kinds ['L1']  (8985 states, 8.1 s)
m_idle_after_12h                 issues=1 depth=6: violating     24 kinds ['L1']  (8985 states, 7.8 s)
$ python3 validation/prototype/mutants.py <mutant> 2 5
none                             issues=2 depth=5: violating     20 kinds ['L1', 'S8']  (8883 states, 8.2 s)
m_tell_overlaps_every_run        issues=2 depth=5: violating    492 kinds ['L1', 'S8', 'T4']  (8963 states, 8.3 s)
$ python3 validation/prototype/mutants_wait24.py <mutant> 6
none                             +wait24 depth=6: violating     38 kinds ['L1']  (25975 states, 20.6 s)
m_idle_ge                        +wait24 depth=6: violating   1258 kinds ['L1', 'T1', 'T2']  (28771 states, 22.5 s)
m_idle_after_12h                 +wait24 depth=6: violating     46 kinds ['L1', 'T2']  (25967 states, 21.1 s)
m_any_later_comment_releases     +wait24 depth=6: violating     46 kinds ['L1', 'T2']  (25975 states, 21.4 s)
```

One of eight caught, `m_tell_overlaps_every_run` (T4 is new); with the 24-hour wait, three more (T1 and T2 are new). DESIGN.md section 7 says why each of the others slipped.

## The merge gate

**gate_explore.py** `[depth]` runs every interleaving of two agents' claims, pushes, pull requests, marks and checks over the library's MemoryWorld, under the account and team presets, coordbench's arm policy and a gated check, and checks the gate's guarantees on every state:

```
$ python3 validation/prototype/gate_explore.py 7
account      depth 7: states 23712, transitions 256592, 20.2 s, violations 0
team         depth 7: states 23712, transitions 256592, 20.7 s, violations 0
arm          depth 7: states 24920, transitions 261580, 20.8 s, violations 0
gated-check  depth 7: states 21848, transitions 243020, 18.6 s, violations 0
```

## The push guard

**guard_grammar.py** generates 10,800 push commands from prefixes, wrappers, git's global options, push options and refspecs, judges each by the guard and by a hand-written oracle, and counts the disagreements by cause (F3):

```
$ python3 validation/prototype/guard_grammar.py
10800 commands; 7734 disagreements in 138 classes
  192  let through: another worker's claim [protocol]   e.g. git push origin claude/8
 1404  let through: another worker's claim [protocol] behind a prefix or bash -c   e.g. bash -c 'git push origin claude/8'
 3276  let through: default or board [guard] behind a prefix or bash -c   e.g. bash -c 'git push origin main'
  132  let through: forced push [guard]   e.g. git push origin +claude/7
 2496  let through: forced push [guard] behind a prefix or bash -c   e.g. bash -c 'git push origin +claude/7'
   78  let through: lease outside claude/ [guard] behind a prefix or bash -c   e.g. bash -c 'git push --force-with-lease origin feature'
  156  let through: wholesale [guard] behind a prefix or bash -c   e.g. bash -c 'git push --all origin'
```

**probe_guard.py** prints the guard's verdict on 22 hand-picked pushes, with the current branch `claude/7` and the default branch `main`:

```
$ python3 validation/prototype/probe_guard.py
REFUSE  git push origin main
REFUSE  git push origin HEAD:main
REFUSE  git push origin claude/7:refs/heads/main
pass    FOO=1 git push origin main
pass    GIT_TRACE=1 git push origin HEAD:main
pass    env git push origin main
pass    command git push origin main
pass    nice git push origin main
pass    bash -c 'git push origin main'
pass    sh -c "git push --force origin claude/7"
REFUSE  git push --force origin claude/7
pass    git push --force-with-lease origin claude/8
pass    git push origin claude/8
pass    git push origin --delete claude/8
pass    git push -d origin claude/8
pass    git push origin :claude/8
pass    git push origin +claude/7:claude/8
REFUSE  git push origin claude/7:board
pass    git -c push.default=upstream push
REFUSE  git push --mirror origin
pass    time git push origin main
pass    xargs git push origin < refs.txt
```

**gitflags.py** runs four commands git really performs that the guard passes, each in a fresh bare repository, from a clone on `claude/7` whose `claude/7` has diverged from the remote's and whose `main` is a commit ahead, and prints the remote's branches before and after:

```
$ python3 validation/prototype/gitflags.py
git version 2.43.0
git push -fu origin claude/7
  guard passes; git exit 0: + 8589fae...3cd281c claude/7 -> claude/7 (forced update)
  remote before: claude/7=8589fae main=828559c
  remote after:  claude/7=3cd281c main=828559c
git push --al origin
  guard passes; git exit 1: 5d7509f..0c64228  main -> main | ! [rejected]        claude/7 -> claude/7 (non-fast-forward)
  remote before: claude/7=8f35f14 main=5d7509f
  remote after:  claude/7=8f35f14 main=0c64228
git push --mirr origin
  guard passes; git exit 0: + 8f35f14...ecf6638 claude/7 -> claude/7 (forced update) | 5d7509f..0c64228  main -> main | * [new reference]   origin/claude/7 -> origin/claude/7 | * [new reference]   origin/main -> origin/main
  remote before: claude/7=8f35f14 main=5d7509f
  remote after:  claude/7=ecf6638 main=0c64228
git push origin heads/main
  guard passes; git exit 0: 5d7509f..0c64228  main -> main
  remote before: claude/7=8f35f14 main=5d7509f
  remote after:  claude/7=8f35f14 main=0c64228
```

The commit ids change from run to run, since the commits carry the time they were made.

**guard_git_oracle.py** tries git as the guard's oracle (DESIGN.md section 8.2) on ten shapes: each runs in a fresh world and in a stale one, whose remote's pre-receive hook records what it is offered and refuses it, and the board rules are read off what git offered:

```
$ python3 validation/prototype/guard_git_oracle.py
git version 2.43.0
git push origin claude/7
  git offers (fresh world): nothing; in the stale world: nothing
  the rules: allowed; the guard passes
git push --force-with-lease origin claude/7
  git offers (fresh world): heads/claude/7 forced; in the stale world: nothing
  the rules: allowed; the guard passes
git push -f origin claude/7
  git offers (fresh world): heads/claude/7 forced; in the stale world: heads/claude/7
  the rules: a plain forced update: git offered it where a lease would have been refused; the guard refuses
git push -fu origin claude/7
  git offers (fresh world): heads/claude/7 forced; in the stale world: heads/claude/7
  the rules: a plain forced update: git offered it where a lease would have been refused; the guard passes
git push origin +claude/7
  git offers (fresh world): heads/claude/7 forced; in the stale world: heads/claude/7
  the rules: a plain forced update: git offered it where a lease would have been refused; the guard passes
git push --al origin
  git offers (fresh world): heads/main fast-forward; in the stale world: heads/main
  the rules: updates the default or the board branch; the guard passes
git push --mirr origin
  git offers (fresh world): heads/claude/7 forced, heads/main fast-forward, remotes/origin/claude/7 create, remotes/origin/main create; in the stale world: heads/claude/7, heads/main, remotes/origin/claude/7, remotes/origin/main
  the rules: updates the default or the board branch; the guard passes
git push origin heads/main
  git offers (fresh world): heads/main fast-forward; in the stale world: heads/main
  the rules: updates the default or the board branch; the guard passes
git push origin HEAD:main
  git offers (fresh world): heads/main fast-forward; in the stale world: heads/main
  the rules: updates the default or the board branch; the guard refuses
git push origin claude/7:feature
  git offers (fresh world): heads/feature create; in the stale world: heads/feature
  the rules: allowed; the guard passes
20 pushes in 0.72 s, 36 ms each
```

## Rule coverage

**rule_coverage.py** is a module: it records the lines of the package that run, with `sys.monitoring`, and reports, per module, the lines reached and the functions never entered. **run_coverage.py** runs it over a 2-issue, depth-5 exploration with the board split:

```
$ python3 validation/prototype/run_coverage.py
states 9478, transitions 20414, 9.1 s
claims.py               100/ 118 lines ( 84%)  never entered: issue_files, said_finished.<locals>.<lambda>
board.py                329/ 389 lines ( 84%)  never entered: DueComment.kind, main_moved_text, pr_state, pulls_behind_main, rename_refused_text, write_if_changed
coordinator.py          116/ 127 lines ( 91%)  never entered: -
labels.py                48/  55 lines ( 87%)  never entered: account_label, agent_label, holder_labels, holders
adapters/github.py      282/ 444 lines ( 63%)  never entered: GitHub.add_sub_issue, GitHub.branch_names, ...
gate.py                 176/ 430 lines ( 40%)  never entered: Issue.label, MainMoved.__init__, ...
guard.py                  0/ 151 lines (  0%)  never entered: branches, command_of, git_line, ...
team.py                  57/ 200 lines ( 28%)  never entered: Snapshot.__init__, ...
protocol_check.py         0/  36 lines (  0%)  never entered: claim_number, closes_its_issue, ...
```

The last five lines are cut here; the script prints every function. guard.py, protocol_check.py and most of gate.py and team.py belong to other lanes than this exploration.

## Probes of the findings

**probe_coordinator.py** (F1, F2): a release whose label removal failed, read on the next run and thirty days on; and an overlap told again once a claim opens its pull request.

```
$ python3 validation/prototype/probe_coordinator.py
half-released claim, next run: releases_due = [] | released_keys = set()
thirty days on: releases_due = [] | released_keys = set()
run 1 tells: [(7, '<!-- sdlc-coordinator: overlap 12 -->'), (12, '<!-- sdlc-coordinator: overlap 7 -->')]
run 2 tells: [(12, '<!-- sdlc-coordinator: overlap 13 -->'), (13, '<!-- sdlc-coordinator: overlap 12 -->')]
```

**probe_deal.py** (F4): the clerk's answers to three workers of one run, when a peer's ready pull request falls behind main between their questions.

```
$ python3 validation/prototype/probe_deal.py
b asks while a's pull request is clean: take 6
c asks after main moved past it:       take 6
a asks after main moved past it:       carry 4
```

**probe_names.py** (F5, F6): how the claim rules read branch names that are not canonical, the board two such names render, and the render of `claude/\u00b2`.

```
$ python3 validation/prototype/probe_names.py
'claude/3' number_of -> 3 | issue_order -> (0, 3, '')
'claude/03' number_of -> 3 | issue_order -> (0, 3, '')
'claude/\u0663' number_of -> 3 | issue_order -> (0, 3, '')
'claude/\u00b2' number_of -> None | issue_order -> raises ValueError: invalid literal for int() with base 10: '\u00b2'
'claude/3 ' number_of -> None | issue_order -> (1, 0, '3 ')
'claude/+3' number_of -> None | issue_order -> (1, 0, '+3')
```

and then the board's Claims section, with the two rows `| #3 | claude/3 | alice | (no pull request yet) | - |` and `| #03 | claude/03 | bob | (no pull request yet) | - |`, and:

```
claimed numbers: {3}
claude/3 protocol problems: []
claude/03 protocol problems: []
render with claude/superscript-two raises ValueError invalid literal for int() with base 10: '\u00b2'
```

**gitprobe.py** (the lock, F7): two workers push `claude/5` into one bare repository.

```
$ python3 validation/prototype/gitprobe.py
git version 2.43.0
a claims #5:
  git push origin claude/5: taken; * [new branch]      claude/5 -> claude/5
b claims #5 from the clone it made before a pushed:
  git push origin claude/5: refused; ! [rejected]        claude/5 -> claude/5 (fetch first)
  git push --force-with-lease origin claude/5: refused; ! [rejected]        claude/5 -> claude/5 (stale info)
b fetches, then leases again:
  git push --force-with-lease origin claude/5: taken; + 5a258a8...11aca44 claude/5 -> claude/5 (forced update)
  guard passes it
the remote's claude/5 claim file now says: - Worker: b
```

**probe_finished_by_hand.py** (F10): a person releases a finished draft by hand; the oracle counts it released, the board never does.

```
$ python3 validation/prototype/probe_finished_by_hand.py
labels: ['todo'] | refs: ['claude/1', 'main']
board claims: ['claude/1'] | board queue: []
oracle: released = True | expected queue: [1]
30 days later -> board claims: ['claude/1'] | queue: [] | refs: ['claude/1', 'main']
```

**probe_comment_failure.py** (F11): a refused idle comment ends the whole board run with exit 1, after STATE.md was written locally.

```
$ python3 validation/prototype/probe_comment_failure.py
<a temporary directory>/STATE.md: rewritten
board: POST /repos/o/r/issues/100/comments: HTTP 500
exit code: 1 | STATE.md written locally: True
```

## Properties

**unicode_classes.py** enumerates the Unicode classes Python's own `str` methods define, whole, and tries them as names after `claude/` against the four properties CrossHair broke (DESIGN.md section 9.2):

```
$ python3 validation/prototype/unicode_classes.py
Unicode 15.0.0: 128 code points isdigit but not isdecimal, 670 isdecimal outside ASCII
  ordering claims never raises: broken by '\xb2'
  the board renders any branch: broken by '\xb2'
  a + refspec is refused: broken by '\xb2'
  a claim branch names one issue: broken by '\u0660'
classes built in 666 ms, 2403 names tried in 130 ms
```

**properties.py**, **properties_two.py** and **props_more.py** are properties in the shape CrossHair's `asserts` analysis reads: the leading asserts are preconditions and the later ones the property. Run with plain `python3`, each only defines its functions and prints nothing. CrossHair is no dependency of this repository; it was run from a scratch virtual environment, `<venv>` below, outside the checkout, with crosshair-tool 0.0.111, at the bounds the design used, every verdict reported, and props_more.py's functions one at a time by the line of each `def` (9, 14, 21, 28, 37):

```
$ python3 -m venv <venv>
$ <venv>/bin/pip install crosshair-tool==0.0.111
$ <venv>/bin/crosshair check --analysis_kind=asserts --report_all --per_condition_timeout=20 validation/prototype/properties.py
validation/prototype/properties.py:22: error: AssertionError:  when calling a_claim_branch_names_one_issue('00')
validation/prototype/properties.py:28: error: ValueError: invalid literal for int() with base 10: '\u00b2' when calling ordering_claims_never_raises('\u00b2')
validation/prototype/properties.py:38: error: TypeError: unsupported operand type(s) for divmod(): 'datetime.timedelta' and 'timedelta' when calling a_release_is_not_due_twice(24, 47, 1000)
validation/prototype/properties.py:43: info: Confirmed over all paths.
validation/prototype/properties.py:51: info: Not confirmed.
validation/prototype/properties.py:64: error: AssertionError:  when calling a_forced_refspec_is_refused('\x00')
validation/prototype/properties.py:67: info: Not confirmed.
$ <venv>/bin/crosshair check --analysis_kind=asserts --per_condition_timeout=120 validation/prototype/properties_two.py
validation/prototype/properties_two.py:18: error: TypeError: unsupported operand type(s) for divmod(): 'datetime.timedelta' and 'timedelta' when calling a_release_is_not_due_twice(24, 743, 1000)
validation/prototype/properties_two.py:26: error: ValueError: invalid literal for int() with base 10: '\u27800' when calling the_board_renders_any_branch('\u27800')
$ <venv>/bin/crosshair check --analysis_kind=asserts --report_all --per_condition_timeout=20 validation/prototype/props_more.py:9
validation/prototype/props_more.py:9: info: Confirmed over all paths.
```

and `Not confirmed.` for each of props_more.py's lines 14, 21, 28 and 37, about 21 s each. properties.py took 42 s and properties_two.py 47 s. The TypeError is a fault of CrossHair's own datetime emulation, which mixes its `timedelta` with the module's: run in plain Python, the same inputs raise nothing. The other errors are real counterexamples, and each fails the same way in plain Python.
