# The first full run of the deep lane, stopped part way (2026-10-14)

`python3 scripts/validate.py deep --jobs 4` on the cloud session's container (4 processors, git 2.43.0), at commit 0c4d495 of the branch claude/validation-harness-step-6-mm6b8t. A restart of the container killed it after about 2,670 seconds, so this is what it had measured by then: deep.json as the runner last wrote it, the trace files it wrote, and its logs. The runner then predated the guard's "missing" list, so deep.json has no such key.

What finished:

- E-faults to depth 4: 43,603 states, 104,940 transitions, 74 s; F7, the careless lease, met as known.json records, nothing else.
- The gate: four policies to depth 8 over MemoryWorld, 51,424 to 63,780 states each, no violation; S05g over BareGitWorld as the design says; 40 sequences over git, 20 merges, 3 main moved, 78 refusals, no violation.
- The git world: all 11 scenarios and all 14 traces over git, matching known.json.
- The guard: 699 argument lists recorded again on git 2.43.0, none answered differently.
- The soak: 950 of 1,000 walks of 200 steps (the chunk of seeds 950 to 999 had not finished), with 7 violations, below.

What did not finish: E-deep (depth 8) had reached 6.9 million states and E-two (depth 7) 6.2 million, at about 2,650 s each, still growing; conformance had not started.

The soak's violations are not F7: the soak has no careless worker and no lease. They are leads for the next session, each to be replayed (`python3 scripts/validate.py replay <file>`, in memory and with `--world git`), shortened further and judged a fault of the harness's model or a finding of the library:

- G1 on seed 585, steps 161 to 166: "the board names e for claude/5, whose holder is b", after a close, a reopen, a rename back and a merge (traces/G1-soak-585-*.json, 113 steps).
- G1 on seed 959, steps 184 to 189 (traces/G1-soak-959-*.json).
- G9 on seed 427, step 159: "a second run with nothing new wrote: POST /issues/103/comments", after a released head was pushed again and renamed back (traces/G9-soak-427-159.json, 125 steps).

The shortening left the soak's traces at over a hundred steps: shrink.py removes one step at a time under a 600 s budget, which a 160-step walk outlasts. That is to fix too, before these traces join validation/traces/.
