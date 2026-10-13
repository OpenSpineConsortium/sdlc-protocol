import resource, sys, subprocess
from pathlib import Path
args = sys.argv[1:]
out = subprocess.run([sys.executable, "-B", str(Path(__file__).resolve().parent / "explore.py"), "--all", *args], capture_output=True, text=True)
first = out.stdout.splitlines()[0] if out.stdout else out.stderr[-500:]
rss = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
print(" ".join(args), "|", first, f"| max rss {rss // 1024} MB")
