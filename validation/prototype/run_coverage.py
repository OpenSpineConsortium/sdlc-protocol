import sys
import rule_coverage
rule_coverage.start()
import explore
explore.SPLIT = True
found, states, transitions, seconds = explore.explore((1, 2), ("a", "b"), 5, stop_at_first=False)
rule_coverage.stop()
print(f"states {states}, transitions {transitions}, {seconds:.1f} s")
for name, hit, total, never in rule_coverage.report(["claims.py", "board.py", "coordinator.py", "labels.py", "adapters/github.py", "gate.py", "guard.py", "team.py", "protocol_check.py"]):
    shown = ", ".join(n for n in never if not n.endswith(("to_dict", "from_json", "to_json")))
    print(f"{name:22s} {hit:4d}/{total:4d} lines ({100 * hit // max(total, 1):3d}%)  never entered: {shown or '-'}")
