"""Count the REST calls one board run makes over the prototype's fake, for a
small state: two claims, one draft, one idle claim due for release."""
import sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))  # the prototype's own modules
import model
from model import seeded, board_run, FakeGitHub
import explore

m = seeded((1, 2), ("a", "b"))
for act in [("claim", "a", 1), ("announce", "a", 1), ("claim", "b", 2), ("announce", "b", 2), ("draft", "a", 1), ("work", "b", 2)]:
    m, _ = explore.apply(m, act)
calls = []
orig = FakeGitHub.open
def counting(self, call, timeout=None):
    calls.append(f"{call.get_method()} {call.full_url.split('/repos/o/r')[-1].split('?')[0]}")
    return orig(self, call, timeout)
FakeGitHub.open = counting
t = time.perf_counter(); board_run(m); t1 = time.perf_counter() - t
print("fresh state:", len(calls), "calls,", f"{t1*1000:.1f} ms")
from collections import Counter
for k, v in sorted(Counter(c.split()[0] + " " + "/".join(c.split()[1].split("/")[:2]) for c in calls).items()):
    print(f"  {v:3d} {k}")
calls.clear()
m, _ = explore.apply(m, ("wait",))
t = time.perf_counter(); lines = board_run(m); t2 = time.perf_counter() - t
print("after 25 h (two releases due):", len(calls), "calls,", f"{t2*1000:.1f} ms")
print(lines)
