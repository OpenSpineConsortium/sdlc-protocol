# The run of record of the deep lane (2026-10-14)

`python3.12 scripts/validate.py deep --jobs 4 --out validation-out` on the cloud session's container (4 processors, git 2.43.0), at commit acc83fb of the branch claude/validation-harness-step-6-mm6b8t, the pull request's code when the run began: the review's fixes after the first full run (0c17ac6, 72d86a0), the runner's fixes of the third review (74d1857), the fourth review's 32 fixes (74aa6fe), the fifth to eighth reviews' fixes (61d6a89, 16ff569, f83398d, 0421b16), the ninth review's fixes with the search E-activity (acc83fb), and main merged in with step 7's canary and the fixes of F4, F14, F9 and F13 (942ab8f, c6f8c63, d56d47c, 2250bc7, 2511b6a). It ran from 19:34 UTC to its end, green, in 9,558.6 s: deep.json as the runner wrote it, its logs, and no trace files, since nothing was red. Its checkpoints are left out, as before: they are pickles of a few hundred megabytes that only a resume of that same run could read.

| Part | Measured |
| --- | --- |
| E-deep | depth 8, 16,706,180 states, 26,712,494 transitions, 7,563 s, 0 violations |
| E-two | depth 7, 18,787,930 states, 32,987,214 transitions, 9,494 s, 0 violations |
| E-faults | depth 4, 41,415 states, 108,546 transitions, 92 s, 0 violations |
| E-activity | depth 5, 1,035,026 states, 2,026,256 transitions, 1,040 s, 0 violations |
| Soak | 1,000 walks of 200 steps, 582 s of walking, 0 violations |
| Gate in memory | four policies to depth 8, 51,424 to 63,780 states each, 0 violations |
| Gate over git | S05g 10 acts with 0 mismatches; 40 sequences, 18 merges, 5 main moved, 71 refusals, 0 violations |
| Git world | 11 scenarios and 14 traces, matching known.json |
| Guard | 699 argument lists recorded again on git version 2.43.0, 0 differing, 0 missing |
| Conformance | 200 paths and 0 counterexamples in both worlds, 0 disagreements, 204 s |

This is the run the docs' measurements come from (validation/README.md, step 6; DESIGN.md, section 14, step 6, and the table of section 6.8). The two earlier runs beside it, 2026-10-14-partial and 2026-10-14-before-review, are kept for what they showed on the way: the soak's three leads, judged faults of the harness's model, and the lane's size and time before the review's fixes.
