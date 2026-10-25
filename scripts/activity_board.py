"""Open a local activity-board preview; this does not connect to live VISOR sessions."""
import argparse
import json
import os
import sys
from dataclasses import fields
from pathlib import Path

root = Path(__file__).resolve().parents[1]
# Avoid inheriting private Qt/C++ paths from a packaged VISOR terminal.
env = os.environ.copy()
for key in ("LD_LIBRARY_PATH", "LD_LIBRARY_PATH_ORIG", "QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH"):
    if key in env:
        entries = [p for p in env[key].split(os.pathsep)
                   if p and not Path(p).resolve().is_relative_to((root / "dist").resolve())]
        if entries:
            env[key] = os.pathsep.join(entries)
        else:
            env.pop(key)
if env != dict(os.environ):
    os.execve(sys.executable, [sys.executable, *sys.argv], env)
sys.path.insert(0, str(root / "src"))
from visor.activity_document import ActivityPolicy, list_replies, read_document

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--document", type=Path, action="append", help="Local topic JSON; repeat for multiple windows")
parser.add_argument("--state-dir", type=Path, required=True, help="Private layouts and local reply files")
parser.add_argument("--policy", type=Path, help="Operator policy JSON overriding ActivityPolicy fields")
parser.add_argument("--responses", action="store_true", help="Read saved local responses as JSON; no delivery claim")
parser.add_argument("--board-id", help="Filter responses by board ID")
parser.add_argument("--inspect", action="store_true", help="Validate input and print document metadata without a window")
parser.add_argument("--screenshot", type=Path, help="Render first window, save a PNG, then exit")
args = parser.parse_args()
try:
    policy_values = json.loads(args.policy.read_text()) if args.policy else {}
    if not isinstance(policy_values, dict) or policy_values.keys() - {f.name for f in fields(ActivityPolicy)}:
        raise ValueError("Policy must contain only ActivityPolicy fields")
    policy = ActivityPolicy(**policy_values)
    if args.responses:
        print(json.dumps(list_replies(args.state_dir, args.board_id), indent=2))
    elif not args.document:
        parser.error("Provide --document or --responses")
    elif args.inspect:
        docs = [read_document(p, policy) for p in args.document]
        print(json.dumps([{k: d[k] for k in ("id", "title", "revision")} | {"cards": len(d["cards"])} for d in docs], indent=2))
    else:
        from visor.activity import launch_activity
        raise SystemExit(launch_activity(args.document, args.state_dir, policy, args.screenshot))
except (OSError, ValueError, TypeError) as exc:
    raise SystemExit("Activity preview: " + str(exc)) from None
