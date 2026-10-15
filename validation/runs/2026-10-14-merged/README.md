# The deep lane's parts but the searches, at the final code (2026-10-15)

`python3.12 scripts/validate.py deep --only soak,gate,git,guard,conformance --jobs 3 --out validation-out` on the cloud session's container (4 processors, git 2.43.0), at commit 01afc41 of the branch claude/validation-harness-step-6-mm6b8t, the pull request's final code: the run of record's code (acc83fb) with main's F15, F16 and F17 (#28 to #31) merged in (0a99040), the review of that merge answered (b388b0c) and the second review, of those answers, answered too. It ran on 2026-10-15 from 01:59 UTC to its end, green, in 442.8 s: deep.json as the runner wrote it, its logs, and no trace files, since nothing was red.

| Part | Measured |
| --- | --- |
| Soak | 1,000 walks of 200 steps, 491 s of walking, 0 violations |
| Gate in memory | four policies to depth 8, 51,424 to 63,780 states each, 0 violations |
| Gate over git | S05g 10 acts with 0 mismatches; 40 sequences, 18 merges, 5 main moved, 71 refusals, 0 violations |
| Git world | 11 scenarios and 17 traces, matching known.json |
| Guard | 699 argument lists recorded again on git version 2.43.0, 0 differing, 0 missing |
| Conformance | 200 paths and 0 counterexamples in both worlds, 0 disagreements, 179 s |

The run of record beside it, validation/runs/2026-10-14/, ran every part at acc83fb, before main's F15, F16 and F17 came in, and so measured the library without them; this run measures every part but the four searches on the library with them. The searches take hours, and validate.yml's weekly deep job runs them on main. The git world plays seventeen traces here, F15's, F16's and F17's among them, where the run of record played fourteen.
