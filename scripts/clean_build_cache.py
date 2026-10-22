"""List inactive generated native bundles; --apply explicitly deletes the listed copies."""
import argparse
import shutil
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / "src"))
from visor.build_storage import cleanup_candidates

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--apply", action="store_true")
parser.add_argument("--keep", type=int, default=2)
args = parser.parse_args()
if args.keep < 2:
    parser.error("Keep at least two recent build copies")
for path in cleanup_candidates(root, args.keep):
    print(("Removing " if args.apply else "Would remove ") + str(path.relative_to(root)))
    if args.apply:
        # Recheck process usage immediately before each removal.
        if path not in cleanup_candidates(root, args.keep):
            raise SystemExit("Build usage changed; rerun the cleanup preview")
        shutil.rmtree(path)
