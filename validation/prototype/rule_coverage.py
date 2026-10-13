"""Prototype of the harness's rule coverage: which lines of sdlc_protocol an
exploration executed, with sys.monitoring (Python 3.12's standard library),
per module, and the functions never entered. Scratch only."""

import sys
import types
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[2] / "sdlc_protocol"
TOOL = sys.monitoring.COVERAGE_ID
reached = set()


def on_line(code, line):
    reached.add((code.co_filename, line))
    return sys.monitoring.DISABLE   # each line is recorded once; disabling keeps the run fast


def start():
    sys.monitoring.use_tool_id(TOOL, "validation-coverage")
    sys.monitoring.register_callback(TOOL, sys.monitoring.events.LINE, on_line)
    sys.monitoring.set_events(TOOL, sys.monitoring.events.LINE)


def stop():
    sys.monitoring.set_events(TOOL, 0)
    sys.monitoring.free_tool_id(TOOL)


def code_objects(code):
    yield code
    for const in code.co_consts:
        if isinstance(const, types.CodeType):
            yield from code_objects(const)


def report(modules):
    """Per module: lines reached, executable lines, and the functions whose
    body never ran."""
    rows = []
    for name in modules:
        path = PACKAGE / name
        top = compile(path.read_text(encoding="utf-8"), str(path), "exec")
        hit = {line for file, line in reached if file == str(path)}
        lines, never = set(), []
        for code in code_objects(top):
            own = {line for _, _, line in code.co_lines() if line is not None}
            lines |= own
            if code.co_name != "<module>":
                body = own - {code.co_firstlineno} or own
                if not body & hit:
                    never.append(code.co_qualname)
        rows.append((name, len(hit & lines), len(lines), sorted(never)))
    return rows
