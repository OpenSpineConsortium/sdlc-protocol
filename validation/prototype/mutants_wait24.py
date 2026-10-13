"""The same mutants with one more verb in scope, a wait of exactly 24 hours
beside the 25-hour one, so a gap can sit on the threshold. Scratch only."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))  # the prototype's own modules
import explore, explore_variants, mutants
base_enabled, base_apply = explore.enabled, explore.apply

def enabled(m, workers):
    acts = base_enabled(m, workers)
    return acts + ([("wait24",)] if m.pending is None else [])

def apply(m, act):
    if act[0] == "wait24":
        m = m.clone(); m.now += 24
        return m, ""
    return base_apply(m, act)

explore_variants.enabled, explore_variants.apply = enabled, apply
name = sys.argv[1]
depth = int(sys.argv[2]) if len(sys.argv) > 2 else 6
if name != "none":
    getattr(mutants, name)()
s, t, v, kinds, sec = explore_variants.run((1,), ("a", "b"), depth, False, False, True, False)
print(f"{name:32s} +wait24 depth={depth}: violating {v:6d} kinds {kinds}  ({s} states, {sec:.1f} s)", flush=True)
