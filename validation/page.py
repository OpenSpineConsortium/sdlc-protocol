"""Reading STATE.md as a worker reads it: the rows of each section.

The invariants compare what the board rendered with what the oracle says,
and the scripted workers decide from the page alone, so both read it here.
Sections are found by board.py's own heading constants, never by their
wording, so a heading reworded on purpose changes nothing here; a cell is
read by its column.
"""

from sdlc_protocol import board


def rows(page, heading):
    """The cells of each row of the section under heading, "(none)" left out."""
    found, inside = [], False
    for line in page.splitlines():
        if line.startswith("## "):
            inside = line == heading
            continue
        if not inside or not line.startswith("| "):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split(" | ")]
        if line.startswith("| ---") or cells == list(columns_of(heading)) or cells[0] == "(none)":
            continue
        found.append(cells)
    return found


def columns_of(heading):
    return {
        board.CLAIM_HEADING: board.CLAIM_COLUMNS,
        board.PULL_HEADING: board.PULL_COLUMNS,
        board.OVERLAP_HEADING: board.OVERLAP_COLUMNS,
        board.QUEUE_HEADING: board.ISSUE_COLUMNS,
        board.GIVEN_HEADING: board.GIVEN_COLUMNS,
        board.BLOCKER_HEADING: board.ISSUE_COLUMNS,
        board.MERGED_HEADING: board.MERGED_COLUMNS,
    }[heading]


def number(cell):
    """#12 as 12; None for any other cell, "(not a claim)" among them."""
    text = cell.lstrip("#")
    return int(text) if cell.startswith("#") and text.isdigit() and text.isascii() else None


def queue(page):
    return sorted(number(r[0]) for r in rows(page, board.QUEUE_HEADING))


def given(page):
    return {number(r[0]): [w.strip() for w in r[2].split(",")] for r in rows(page, board.GIVEN_HEADING)}


def claims(page):
    """The Claims section's rows as (issue cell, branch, worker, pull cell)."""
    return [(r[0], r[1], r[2], r[3]) for r in rows(page, board.CLAIM_HEADING)]


def claim_branches(page):
    return sorted(r[1] for r in rows(page, board.CLAIM_HEADING))


def overlaps(page):
    return {r[0]: [number(n.strip()) for n in r[1].split(",")] for r in rows(page, board.OVERLAP_HEADING)}


def pulls(page):
    return sorted(number(r[0]) for r in rows(page, board.PULL_HEADING))


def merged(page):
    """The Recently merged section's pull request numbers, newest first, as
    the page lists them."""
    return [number(r[0]) for r in rows(page, board.MERGED_HEADING)]


def not_claims(page):
    """The branches the Claims section lists as no claim (board.NOT_A_CLAIM
    in the issue cell): a name that reads as a claim and claims nothing."""
    return sorted(r[1] for r in rows(page, board.CLAIM_HEADING) if r[0] == board.NOT_A_CLAIM)


def claim_workers(page):
    """The Claims section as a map from branch to the worker shown."""
    return {r[1]: r[2] for r in rows(page, board.CLAIM_HEADING)}
