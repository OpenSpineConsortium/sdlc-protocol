"""The harness's oracle and validation/GUARANTEES.md held to the spec's own
text (DESIGN.md, 2.6): every word the oracle spells and every sentence it
or GUARANTEES.md quotes is in spec/PROTOCOL.md as written, the oracle
imports nothing from the package under test, and GUARANTEES.md has one row
for each guarantee the invariants know, with a status that says what is
checked. A spec edit that rewords one of those sentences fails here until
the oracle and GUARANTEES.md follow."""

import ast
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sdlc_protocol import board, claims, coordinator, labels  # noqa: E402
from sdlc_protocol.adapters import github  # noqa: E402
from validation import invariants, oracle  # noqa: E402

SPEC = (ROOT / "spec" / "PROTOCOL.md").read_text(encoding="utf-8")
GUARANTEES = (ROOT / "validation" / "GUARANTEES.md").read_text(encoding="utf-8")
ROW = re.compile(r"^\| (G\w+) \| (.+?) \| \"(.+?)\" \| (.+?) \|$")
STATUSES = ("checked", "known (", "partly checked", "not checked yet")


def rows():
    return {m.group(1): m for m in (ROW.match(line) for line in GUARANTEES.splitlines()) if m}


class TheOracleSpeaksTheSpec(unittest.TestCase):
    def test_every_sentence_it_quotes_is_in_the_spec(self):
        for name, sentence in oracle.SPEC.items():
            with self.subTest(name=name):
                self.assertIn(sentence, SPEC)

    def test_every_word_it_spells_is_in_the_spec(self):
        words = [oracle.CLAIM_PREFIX, oracle.CLAIM_DIR, oracle.FILES_HEADING, oracle.TODO, oracle.CLAIMED,
                 oracle.IN_REVIEW, oracle.BLOCKER, *oracle.WORKER_SPELLINGS, oracle.FINISHED, oracle.RELEASE_MARKER,
                 oracle.RELEASED_PREFIX, oracle.BOT, "<!-- sdlc-coordinator: overlap <numbers> -->", "24 hours"]
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
        self.assertEqual(oracle.REFUSED_FOR_GOOD, frozenset(github.REFUSED_FOR_GOOD))

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

    def test_each_known_finding_is_one_the_oracle_reads_as_built_or_a_careless_worker_shows(self):
        named = {f for row in rows().values() if row.group(4).startswith("known (")
                 for f in re.findall(r"F\d+", row.group(4).split(":")[0])}
        self.assertEqual(named - oracle.AS_BUILT, {"F7"})


if __name__ == "__main__":
    unittest.main()
