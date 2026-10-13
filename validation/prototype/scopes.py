"""Two scopes the design budgets: three workers on one issue, and two workers
on two issues, with the sound key, without and with the reductions."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))  # the prototype's own modules
import explore_variants
issues, workers, depth = int(sys.argv[1]), tuple(sys.argv[2].split(",")), int(sys.argv[3])
for capped, symmetry in ((False, False), (True, True)):
    s, t, v, kinds, sec = explore_variants.run(tuple(range(1, issues + 1)), workers, depth, False, capped, True, symmetry)
    print(f"issues={issues} workers={len(workers)} depth={depth} reductions={capped}: states {s} transitions {t} "
          f"violating {v} {kinds} {sec:.1f} s", flush=True)
