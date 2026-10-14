# Board

As of commit fedcba9 on trunk. Rewritten by .github/workflows/board.yml; do not edit by hand.

## Claims (open claude/<issue> branches and docs/agents/claims/<issue>.md)

| Issue | Branch | Worker | PR | Files it touches |
| --- | --- | --- | --- | --- |
| #5 | claude/5 | main | (no pull request yet) | docs/a.md, docs/b.md, tools/x.py |
| #6 | claude/6 | third | issue closed | delete the branch |
| #7 | claude/7 | main | issue closed | delete the branch |
| #8 | claude/8 | no claim file yet | issue closed | delete the branch |
| #22 | claude/22 | second | #61 | a.md, z.md |
| #30 | claude/30 | main | #60 | - |
| #31 | claude/31 | second | #63 | docs/b.md |
| #32 | claude/32 | no claim file yet | #64 | cafe.md |
| #33 | claude/33 | unnamed | #65 | - |
| #34 | claude/34 | wurth | #66 | a.md, b.md, c.md |
| (not a claim) | claude/x-notes | main | (no pull request yet) | notes.md |

## Open pull requests

| PR | Title | Branch | State (clean, behind, conflicting, blocked, unknown) | Last update |
| --- | --- | --- | --- | --- |
| #60 | Hooks only | claude/30 | clean | 2026-09-23T09:00:00Z |
| #61 | Conflicts with trunk | claude/22 | conflicting | 2026-09-22T09:00:00Z |
| #62 | Unstable checks | feature/x | clean | 2026-09-23T10:00:00Z |
| #63 | Blocked by review | claude/31 | blocked | 2026-09-21T10:00:00Z |
| #64 | Not computed yet | claude/32 | unknown | 2026-09-23T11:00:00Z |
| #65 | A draft | claude/33 | unknown | 2026-09-23T11:30:00Z |
| #66 | Behind trunk | claude/34 | behind | 2026-09-23T11:45:00Z |

## Overlaps (files that more than one piece of open work changes; land the work in this order: a blocker first, then work marked ready, then the oldest)

| File | Open work, in the order it should land |
| --- | --- |
| a.md | #61, #66 |
| docs/b.md | #63, #5 |

## Queue (open issues labelled todo, unclaimed and given to no worker)

| Issue | Title | Labels |
| --- | --- | --- |
| #25 | Blocks everyone \| twice over | blocker, todo |
| #100 | A hundred, to sort after nine | todo |

## Given to workers (open issues a worker's label names, not yet claimed)

| Issue | Title | Worker |
| --- | --- | --- |
| #9 | Nine  with a dash | main |
| #24 | A member's task | w1 |

## Blockers (open issues labelled blocker)

| Issue | Title | Labels |
| --- | --- | --- |
| #25 | Blocks everyone \| twice over | blocker, todo |
| #26 | A blocker someone holds | account:second, blocker, claimed |

## Recently merged (the last 5)

| PR | Title | Branch | Merged |
| --- | --- | --- | --- |
| #52 | Newest | claude/52 | 2026-09-22T10:00:00Z |
| #51 | Middle quoted | claude/51 | 2026-09-21T10:00:00Z |
| #50 | Older | claude/50 | 2026-09-20T10:00:00Z |
