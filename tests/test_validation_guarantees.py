"""The harness's oracle and validation/GUARANTEES.md held to the spec's own
text (DESIGN.md, 2.6): every word the oracle spells and every sentence it
or GUARANTEES.md quotes is in spec/PROTOCOL.md as written, the oracle
imports nothing from the package under test, and GUARANTEES.md has one row
for each guarantee the invariants know, with a status that says what is
checked. A spec edit that rewords one of those sentences fails here until
the oracle and GUARANTEES.md follow."""

import ast
import importlib
import importlib.util
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sdlc_protocol import board, claims, coordinator, labels  # noqa: E402
from sdlc_protocol.adapters import github  # noqa: E402
from validation import cycle, invariants, model as M, oracle, verbs  # noqa: E402
from validation.verbs import Verb  # noqa: E402

SPEC = (ROOT / "spec" / "PROTOCOL.md").read_text(encoding="utf-8")
GUARANTEES = (ROOT / "validation" / "GUARANTEES.md").read_text(encoding="utf-8")
ROW = re.compile(r"^\| (G\w+) \| (.+?) \| \"(.+?)\" \| (.+?) \|$")
STATUSES = ("checked", "known (", "partly checked", "not checked yet")
# A rule a status cites by its module and name, as board.given_rows, or by
# a longer dotted path, as github.GitHubWorld.standing; a file is named by
# its path (validation/dealing.py), which the lookbehind leaves out, or by
# its name alone when it lives elsewhere (the template's workers.yml),
# which its suffix marks.
CITED = re.compile(r"(?<![\w/])([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+)")
FILE_SUFFIXES = {"py", "md", "yml", "json"}
PACKAGES = ("sdlc_protocol", "sdlc_protocol.adapters", "validation")
MISSING = object()


def rows():
    return {m.group(1): m for m in (ROW.match(line) for line in GUARANTEES.splitlines()) if m}


def module_named(name):
    """The module of the library or the harness a cited rule starts with,
    or None for a word that names no module of theirs."""
    for package in PACKAGES:
        if importlib.util.find_spec(f"{package}.{name}") is not None:
            return importlib.import_module(f"{package}.{name}")
    return None


def cited_rules():
    """Every dotted name the rows' statuses cite, but a file's."""
    return {m.group(1) for row in rows().values() for m in CITED.finditer(row.group(4))
            if m.group(1).rsplit(".", 1)[1] not in FILE_SUFFIXES}


def resolved(dotted):
    """What a cited name names: its first part a module of the library or
    the harness, or its first parts one by the package's own path, each
    further part an attribute of the one before; MISSING where any part
    names nothing."""
    parts = dotted.split(".")
    found, rest = module_named(parts[0]), parts[1:]
    if found is None and parts[0] in ("sdlc_protocol", "validation"):
        found, rest = longest_module(parts)
    if found is None:
        return MISSING
    for part in rest:
        found = attribute(found, part)
        if found is MISSING:
            return MISSING
    return found


def attribute(found, part):
    """One part of a cited name below the one before: an attribute, or a
    field of a dataclass, which the class holds no value of when the field
    has no default, as board.Pull.number; MISSING when there is neither."""
    fields = getattr(found, "__dataclass_fields__", None) if isinstance(found, type) else None
    if fields and part in fields:
        return fields[part]
    return getattr(found, part, MISSING)


def longest_module(parts):
    """The module the longest leading run of parts names, and the parts
    after it; (None, parts) when none does."""
    for cut in range(len(parts), 0, -1):
        try:
            return importlib.import_module(".".join(parts[:cut])), parts[cut:]
        except ImportError:
            continue
    return None, parts


class TheOracleSpeaksTheSpec(unittest.TestCase):
    def test_every_sentence_it_quotes_is_in_the_spec(self):
        for name, sentence in oracle.SPEC.items():
            with self.subTest(name=name):
                self.assertIn(sentence, SPEC)

    def test_every_word_it_spells_is_in_the_spec(self):
        words = [oracle.CLAIM_PREFIX, oracle.CLAIM_DIR, oracle.FILES_HEADING, oracle.TODO, oracle.CLAIMED,
                 oracle.IN_REVIEW, oracle.BLOCKER, oracle.EPIC, oracle.BLOCKED, *oracle.WORKER_SPELLINGS,
                 *oracle.WORKER_LINES, oracle.FINISHED, oracle.RELEASE_MARKER, oracle.RELEASED_PREFIX, oracle.BOT,
                 "<!-- sdlc-coordinator: overlap <numbers> -->", "24 hours"]
        for word in words:
            with self.subTest(word=word):
                self.assertIn(word, SPEC)

    def test_it_imports_nothing_from_the_package(self):
        tree = ast.parse((ROOT / "validation" / "oracle.py").read_text(encoding="utf-8"))
        imported = [node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
        imported += [alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names]
        self.assertFalse([name for name in imported if name.startswith("sdlc_protocol")])

    def test_where_the_spec_and_the_library_spell_one_word_they_agree(self):
        """So a difference between the oracle and the library is a
        difference of rule, never of spelling."""
        self.assertEqual(oracle.RELEASE_MARKER, claims.RELEASE_MARKER)
        self.assertEqual(oracle.FINISHED, claims.FINISHED_SIGNAL)
        self.assertEqual(oracle.WORKER_SPELLINGS, labels.HOLDER_PREFIXES)
        self.assertEqual(oracle.IDLE_MINUTES * 60, claims.IDLE_AFTER.total_seconds())
        self.assertTrue(oracle.OVERLAP_MARKER.fullmatch(coordinator.OVERLAP_MARKER.format(numbers="2 13")))
        self.assertEqual(oracle.EPIC, labels.EPIC)
        self.assertEqual(oracle.BLOCKED, labels.BLOCKED)
        for line in oracle.WORKER_LINES:
            self.assertEqual(claims.claim_worker(f"{line} a\n"), "a", line)
        self.assertEqual(oracle.REFUSED_FOR_GOOD, frozenset(github.REFUSED_FOR_GOOD))
        # The spec says "came after the run began"; the library allows a
        # runner's clock a few minutes ahead of GitHub's, and the oracle as
        # much (SPEC["moved off"], F8's second review).
        self.assertEqual(oracle.MOVED_SLACK_MINUTES * 60, github.PUSH_SINCE_SLACK.total_seconds())

    def test_the_run_s_own_words_in_its_log_are_github_py_s(self):
        """oracle.LOG_WORDS, which the spec does not spell, are the lines a
        board run writes for a claim no read dated by its push (F8's fix
        check): b's commit, dated two days back, pushed an hour after its
        claim, read from the floor, its claim's making, where both reads
        are a push behind, and from its committer date where GitHub
        refuses every read for good, its answer in parentheses."""
        model = M.seeded((1,), writers=("b",))
        verbs.apply(model, Verb("claim", "b", (1,)))
        model.now = M.HOUR
        verbs.apply(model, Verb("commit", "b", (1, -48 * M.HOUR)))
        self.assertEqual(oracle.LOG_WORDS["listed none"], github.NO_PUSH_LISTED)
        self.assertTrue(oracle.LOG_WORDS["committer date"].startswith(oracle.LOG_WORDS["read from"]))
        self.assertTrue(oracle.LOG_WORDS["floor"].startswith(oracle.LOG_WORDS["read from"]))

        def said(fault):
            record = cycle.board_run(model.clone(), faults=(verbs.FAULTS[fault],))
            return [line for line in record.lines if oracle.LOG_WORDS["read from"] in line]

        floor = oracle.LOG_WORDS["floor"].format(floor=M.iso(0))
        self.assertEqual(said("pushes-lagging-twice"), ["claude/1: " + oracle.LOG_WORDS["listed none"] + floor])
        refused = said("pushes-403")
        self.assertEqual(len(refused), 1)
        self.assertTrue(refused[0].startswith("claude/1: " + oracle.LOG_WORDS["refused"]), refused[0])
        self.assertTrue(refused[0].endswith("Resource not accessible by integration)"
                                            + oracle.LOG_WORDS["committer date"]), refused[0])

    def test_the_board_s_own_markers_are_board_py_s(self):
        self.assertEqual(oracle.BOARD_MARKERS, {
            "main-moved": board.MAIN_MOVED_MARKER, "idle": board.IDLE_MARKER,
            "released": board.RELEASED_MARKER, "rename-refused": board.RENAME_REFUSED_MARKER,
        })


class GuaranteesMd(unittest.TestCase):
    def test_one_row_for_each_guarantee_the_invariants_know(self):
        self.assertEqual(sorted(rows()), sorted(invariants.GUARANTEES))

    def test_every_quote_is_in_the_spec(self):
        for name, row in rows().items():
            with self.subTest(guarantee=name):
                self.assertIn(row.group(3), SPEC)

    def test_every_status_is_one_of_the_words_it_defines(self):
        for name, row in rows().items():
            with self.subTest(guarantee=name):
                self.assertTrue(row.group(4).startswith(STATUSES), row.group(4))

    def test_a_guarantee_the_invariants_check_is_not_marked_unchecked(self):
        for name, row in rows().items():
            with self.subTest(guarantee=name):
                self.assertEqual(name in invariants.CHECKED, not row.group(4).startswith("not checked"))

    def test_each_known_finding_is_one_the_oracle_reads_as_built_or_a_scope_shows(self):
        named = {f for row in rows().values() if row.group(4).startswith("known (")
                 for f in re.findall(r"F\d+", row.group(4).split(":")[0])}
        self.assertEqual(named - oracle.AS_BUILT, set(), "every known finding is one the oracle reads as built")

    def test_every_rule_a_row_cites_is_one_the_library_or_the_harness_has(self):
        """A row that names the rule behind its status, as G9's names
        board.given_rows and G10's github.standing_of, names one that
        exists: every dotted name in a status but a file's resolves, so a
        rename or a misspelling that left the document pointing at nothing
        fails here, a module's name as much as a rule's."""
        cited = cited_rules()
        self.assertTrue({"board.given_rows", "github.standing_of", "team.deals", "clerk.workflow_deals"} <= cited,
                        cited)
        for dotted in sorted(cited):
            with self.subTest(rule=dotted):
                self.assertIsNot(resolved(dotted), MISSING)

    def test_a_name_that_resolves_to_nothing_is_told_apart(self):
        """The resolver the citation check rests on: a class's method, a
        harness module's function, a path from the package, a constant and
        a dataclass's field, with a default or without one, resolve; a
        misspelled module, a rule that is not there and a part below one do
        not."""
        for dotted in ("github.GitHubWorld.standing", "dealing.run_lane", "validation.dealing.run_lane",
                       "sdlc_protocol.adapters.github.standing_of"):
            with self.subTest(dotted=dotted):
                self.assertTrue(callable(resolved(dotted)))
        self.assertEqual(resolved("github.OPAQUE_STATES"), github.OPAQUE_STATES)
        for dotted in ("board.Pull.number", "board.Pull.standing"):
            with self.subTest(dotted=dotted):
                self.assertIsNot(resolved(dotted), MISSING)
        for dotted in ("orakle.main_moved_effects", "board.no_such_rule", "board.Pull.no_such_field",
                       "validation.no_such_lane.run", "sdlc_protocol.boards.render"):
            with self.subTest(dotted=dotted):
                self.assertIs(resolved(dotted), MISSING)


if __name__ == "__main__":
    unittest.main()
