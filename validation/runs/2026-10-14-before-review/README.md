# The first complete run of the deep lane, before the review's fixes (2026-10-14)

`python3.12 scripts/validate.py deep --jobs 4` on the cloud session's container (4 processors, git 2.43.0), at commit f71fce3 of the branch claude/validation-harness-step-6-mm6b8t, which checkpoints the searches but predates the adversarial review's fixes (0c17ac6 and 72d86a0). It ran from 08:08 UTC to its end, green, in 7,688.8 s: deep.json as the runner wrote it, its logs, and no trace files, since nothing was red. Its checkpoints are left out: they are pickles of a few hundred megabytes and only a resume of that same run could read them.

| Part | Measured |
| --- | --- |
| E-deep | depth 8, 16,727,208 states, 26,745,598 transitions, 5,994 s, 0 violations |
| E-two | depth 7, 18,801,050 states, 33,008,162 transitions, 7,618 s, 0 violations |
| E-faults | depth 4, 41,409 states, 101,769 transitions, 61 s, 0 violations |
| Soak | 1,000 walks of 200 steps, 398 s of walking, 0 violations |
| Gate in memory | four policies to depth 8, 51,424 to 63,780 states each, 0 violations |
| Gate over git | S05g 10 acts with 0 mismatches; 40 sequences, 20 merges, 3 main moved, 78 refusals |
| Git world | 11 scenarios and 14 traces, matching known.json |
| Guard | 699 argument lists recorded again on git 2.43.0, none differing, none missing |
| Conformance | 200 paths in both worlds, 0 disagreements, 172 s |

It is kept as the measure of the lane's size and time, not as the run of record: the review's fixes changed what the gate checks (parts 3, 5 and 7 of G13), what G1 holds the Worker cell to and how the memory world rebases, so the run of record is validation/runs/2026-10-14/, made at the pull request's code before main's F15, F16 and F17 came in, with validation/runs/2026-10-14-merged/ beside it for the final code.
