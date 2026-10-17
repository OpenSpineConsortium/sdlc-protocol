"""The lock rules: which keys meet, which entries conflict, how a table
takes, gives up and sweeps locks, how a change maps to the keys it
touches, and what a Python symbol's code depends on."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sdlc_protocol import locks  # noqa: E402
from sdlc_protocol.locks import READ, TOP, WRITE, Entry  # noqa: E402

MODULE = '''import os

from pkg.util import helper


def f(x):
    return x + 1


@decorated
def g(y):
    return f(y) * helper(y)


class Box:
    def put(self, item):
        return self.check(item)

    def check(self, item):
        return item is not None
'''

DOC = '''# Title

Intro.

## Alpha

Alpha text.

### Alpha detail

Detail.

```
## not a heading
```

## Beta

Beta text.
'''


class Keys(unittest.TestCase):
    def test_a_whole_file_meets_every_part_of_it(self):
        self.assertTrue(locks.keys_meet("a.py", "a.py#f"))
        self.assertTrue(locks.keys_meet("a.py#<top>", "a.py"))

    def test_two_symbols_of_one_file_do_not_meet(self):
        self.assertFalse(locks.keys_meet("a.py#f", "a.py#g"))
        self.assertFalse(locks.keys_meet("a.py#f", "a.py#<top>"))

    def test_a_class_meets_its_methods_and_a_section_its_subsections(self):
        self.assertTrue(locks.keys_meet("a.py#Box", "a.py#Box.put"))
        self.assertTrue(locks.keys_meet("d.md#Alpha > Alpha detail", "d.md#Alpha"))
        self.assertFalse(locks.keys_meet("a.py#Box", "a.py#Boxer"))

    def test_different_files_never_meet(self):
        self.assertFalse(locks.keys_meet("a.py", "b.py"))

    def test_a_named_resource_is_always_whole(self):
        self.assertEqual(locks.split_key("doc:a1cb9900#section"), ("doc:a1cb9900", None))
        self.assertTrue(locks.keys_meet("doc:a1cb9900#one", "doc:a1cb9900#two"))

    def test_a_grant_covers_what_it_holds_and_nothing_wider(self):
        self.assertTrue(locks.covers("a.py", "a.py#f"))
        self.assertTrue(locks.covers("a.py#Box", "a.py#Box.put"))
        self.assertFalse(locks.covers("a.py#Box.put", "a.py#Box"))
        self.assertFalse(locks.covers("a.py#f", "a.py"))


class Table(unittest.TestCase):
    def test_reads_share_and_a_write_excludes(self):
        held = [Entry("a.py#f", 1, READ)]
        self.assertEqual(locks.acquire(held, [Entry("a.py#f", 2, READ)])[-1], Entry("a.py#f", 2, READ))
        with self.assertRaises(locks.Held) as refused:
            locks.acquire(held, [Entry("a.py#f", 2, WRITE)])
        self.assertIn("held by #1", str(refused.exception))

    def test_a_refusal_grants_nothing_of_what_was_asked(self):
        held = [Entry("b.py", 1)]
        with self.assertRaises(locks.Held):
            locks.acquire(held, [Entry("a.py", 2), Entry("b.py#f", 2)])

    def test_a_claim_never_conflicts_with_itself_and_a_write_replaces_its_read(self):
        table = locks.acquire([Entry("a.py#f", 1, READ)], [Entry("a.py#f", 1, WRITE)])
        self.assertEqual(table, [Entry("a.py#f", 1, WRITE)])

    def test_a_dead_entry_is_swept_and_stands_in_no_one_s_way(self):
        held = [Entry("a.py", 1), Entry("b.py", 3)]
        table = locks.acquire(held, [Entry("a.py", 2)], live=lambda entry: entry.claim != 1)
        self.assertEqual(table, [Entry("a.py", 2), Entry("b.py", 3)])

    def test_release_drops_only_that_claim(self):
        self.assertEqual(locks.release([Entry("a.py", 1), Entry("b.py", 2)], 1), [Entry("b.py", 2)])

    def test_the_table_reads_back_what_was_written(self):
        entries = [Entry("b.py", 2, READ, "w2"), Entry("a.py#f", 1, WRITE, "w1")]
        self.assertEqual(locks.read_table(locks.table_text(entries)), sorted(entries))
        self.assertEqual(locks.read_table(locks.table_text([])), [])

    def test_a_table_of_another_version_or_mode_is_refused(self):
        with self.assertRaises(ValueError):
            locks.read_table('{"version": 2, "entries": []}')
        with self.assertRaises(ValueError):
            locks.read_table('{"version": 1, "entries": [{"key": "a", "claim": 1, "mode": "own"}]}')

    def test_the_message_says_who_took_and_who_gave_up_what(self):
        message = locks.change_message([Entry("a.py", 1, WRITE, "w1")], [Entry("b.py", 2, READ, "w2")])
        self.assertTrue(message.startswith("Locks: #1, #2\n\n"))
        self.assertIn("#2 (w2) takes b.py read", message)
        self.assertIn("#1 gives up a.py write", message)

    def test_uncovered_names_what_the_claim_changed_without_a_write(self):
        held = [Entry("a.py#f", 1), Entry("a.py#g", 1, READ), Entry("b.py", 2)]
        self.assertEqual(locks.uncovered(["a.py#f", "a.py#g", "b.py"], held, 1), ["a.py#g", "b.py"])


class Switch(unittest.TestCase):
    def test_off_unless_a_word_it_knows_says_otherwise(self):
        self.assertEqual(locks.mode_of({}, None), locks.OFF)
        self.assertEqual(locks.mode_of({"SYMPHONY_LOCKS": "enforced"}, None), locks.OFF)
        self.assertEqual(locks.mode_of({}, "advise\n"), locks.ADVISE)
        self.assertEqual(locks.mode_of({"SYMPHONY_LOCKS": "Enforce"}, "off"), locks.ENFORCE)


class Symbols(unittest.TestCase):
    def test_python_symbols_take_decorators_and_methods(self):
        found = {symbol.name: (symbol.first, symbol.last) for symbol in locks.python_symbols(MODULE)}
        self.assertEqual(found["f"], (6, 7))
        self.assertEqual(found["g"], (10, 12))
        self.assertEqual(found["Box.put"], (16, 17))
        self.assertEqual(found["Box"], (15, 20))

    def test_a_file_that_does_not_parse_is_locked_whole(self):
        self.assertIsNone(locks.python_symbols("def broken(:\n"))
        self.assertEqual(locks.touched_keys("a.py", "def broken(:\n", [(1, 1)]), ["a.py"])

    def test_markdown_sections_nest_and_ignore_fences(self):
        found = {symbol.name: (symbol.first, symbol.last) for symbol in locks.markdown_symbols(DOC)}
        self.assertEqual(found["Title > Alpha"], (5, 16))
        self.assertEqual(found["Title > Alpha > Alpha detail"], (9, 16))
        self.assertEqual(found["Title > Beta"], (17, 19))
        self.assertNotIn("Title > not a heading", found)

    def test_other_files_are_whole(self):
        self.assertEqual(locks.touched_keys("data.json", "{}\n", [(1, 1)]), ["data.json"])


class Touched(unittest.TestCase):
    def test_a_change_inside_a_function_touches_that_function(self):
        self.assertEqual(locks.touched_keys("m.py", MODULE, [(7, 1)]), ["m.py#f"])

    def test_a_method_change_touches_the_method(self):
        self.assertEqual(locks.touched_keys("m.py", MODULE, [(20, 1)]), ["m.py#Box.check"])

    def test_imports_and_blank_lines_are_top(self):
        self.assertEqual(locks.touched_keys("m.py", MODULE, [(1, 1)]), ["m.py#<top>"])

    def test_an_insertion_inside_a_body_touches_only_that_symbol(self):
        self.assertEqual(locks.touched_keys("m.py", MODULE, [(6, 0)]), ["m.py#f"])

    def test_an_insertion_at_an_edge_touches_the_symbol_and_top(self):
        self.assertEqual(locks.touched_keys("m.py", MODULE, [(7, 0)]), ["m.py#<top>", "m.py#f"])
        self.assertEqual(locks.touched_keys("m.py", MODULE, [(9, 0)]), ["m.py#<top>", "m.py#g"])

    def test_a_new_or_gone_file_is_touched_whole(self):
        self.assertEqual(locks.touched_keys("new.py", None, [(0, 0)]), ["new.py"])

    def test_hunks_are_read_from_a_zero_context_diff(self):
        diff = "diff --git a/m.py b/m.py\n@@ -7 +7 @@ x\n-a\n+b\n@@ -12,0 +13,2 @@\n+c\n+d\n@@ -3,2 +4,0 @@\n"
        self.assertEqual(locks.hunks_of(diff), [(7, 1), (12, 0), (3, 2)])


class Dependencies(unittest.TestCase):
    def test_a_function_reads_what_it_calls_here_and_what_it_imports_from_the_repository(self):
        reads = locks.python_reads("m.py", MODULE, "g", ["m.py", "pkg/util.py"])
        self.assertEqual(reads, ["m.py#f", "pkg/util.py#helper"])

    def test_an_import_from_outside_the_repository_is_no_lock(self):
        self.assertEqual(locks.python_reads("m.py", MODULE, "g", ["m.py"]), ["m.py#f"])

    def test_a_method_reads_its_class_s_methods_through_self(self):
        self.assertEqual(locks.python_reads("m.py", MODULE, "Box.put", []), ["m.py#Box.check"])

    def test_relative_imports_resolve_within_the_package(self):
        text = "from .util import helper\n\n\ndef h():\n    return helper()\n"
        self.assertEqual(locks.python_reads("pkg/m.py", text, "h", ["pkg/util.py"]), ["pkg/util.py#helper"])

    def test_the_plan_writes_the_targets_and_reads_the_rest(self):
        plan = locks.plan(["m.py#g", "m.py#f"], {"m.py": MODULE}.get, ["m.py", "pkg/util.py"], claim=4, worker="w")
        self.assertEqual(plan, [Entry("m.py#f", 4, WRITE, "w"), Entry("m.py#g", 4, WRITE, "w"),
                                Entry("pkg/util.py#helper", 4, READ, "w")])

    def test_two_claims_on_different_functions_run_at_once_and_a_dependency_waits(self):
        first = locks.plan(["m.py#g"], {"m.py": MODULE}.get, ["m.py"], claim=1)
        table = locks.acquire([], first)
        self.assertTrue(locks.acquire(table, locks.plan(["m.py#Box.check"], {"m.py": MODULE}.get, [], claim=2)))
        with self.assertRaises(locks.Held):
            locks.acquire(table, locks.plan(["m.py#f"], {"m.py": MODULE}.get, [], claim=2))


class ReviewedEdges(unittest.TestCase):
    """The cases an adversarial review of the first version found."""

    def test_a_hash_in_a_path_keeps_the_file_whole(self):
        self.assertEqual(locks.split_key("notes#1.md#Intro"), ("notes#1.md", "Intro"))
        self.assertEqual(locks.split_key("notes#1.txt"), ("notes#1.txt", None))
        self.assertTrue(locks.keys_meet("notes#1.md", "notes#1.md#Intro"))

    def test_a_markdown_heading_with_a_dot_holds_nothing_by_it(self):
        self.assertFalse(locks.keys_meet("d.md#1", "d.md#1. Setup"))
        self.assertFalse(locks.keys_meet("d.md#v1", "d.md#v1.2"))
        self.assertTrue(locks.keys_meet("a.py#Box", "a.py#Box.put"))

    def test_headings_as_commonmark_reads_them(self):
        text = "# C#\n\n   ## Indented\n\n````\n```\n## inside\n````\n\n## After\n"
        names = [symbol.name for symbol in locks.markdown_symbols(text)]
        self.assertEqual(names, ["C#", "C# > Indented", "C# > After"])

    def test_markdown_lines_are_counted_as_git_counts_them(self):
        text = "# T\n\x0c\n## A\n\n## B\nbody\n"
        found = {symbol.name: (symbol.first, symbol.last) for symbol in locks.markdown_symbols(text)}
        self.assertEqual(found["T > B"], (5, 6))

    def test_a_change_with_no_lines_to_read_touches_the_whole_file(self):
        self.assertEqual(locks.touched_keys("m.py", MODULE, []), ["m.py"])

    def test_from_a_package_import_a_module_reads_the_module(self):
        text = "from . import util\n\n\ndef h():\n    return util.helper()\n"
        self.assertEqual(locks.python_reads("pkg/m.py", text, "h", ["pkg/util.py"]), ["pkg/util.py"])


    def test_a_lone_carriage_return_does_not_move_python_s_lines(self):
        text = "x = 1  # a\rb\ndef f():\n    return 1\n"
        self.assertEqual([(symbol.name, symbol.first) for symbol in locks.python_symbols(text)], [("f", 2)])


if __name__ == "__main__":
    unittest.main()
