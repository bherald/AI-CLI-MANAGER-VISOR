"""Prepare a source handoff without Git history, credentials, data, caches or binaries."""
import argparse
import hashlib
import json
import subprocess
import sys
import tarfile
from pathlib import Path

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / "src"))
from visor.bundles import public_tar_metadata

parser = argparse.ArgumentParser()
parser.add_argument("--public", action="store_true", help="Exclude private planning history and concept art")
args = parser.parse_args()
private_files = {"docs/PLANNING_HISTORY.md", "docs/IMPLEMENTATION_PLAN.md",
                 "docs/PUBLICATION_CHECKLIST.md", "ui-concept-v2.png"}

subprocess.run([sys.executable, str(root / "scripts/audit_public_repo.py")], cwd=root, check=True)
files = subprocess.check_output(["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
                                cwd=root).decode().split("\0")
files = sorted(set(filter(None, files)) - (private_files if args.public else set()))
output = root / "dist" / ("visor-source-1.2.3" + ("-public" if args.public else "") + ".tar.gz")
output.parent.mkdir(exist_ok=True)
with tarfile.open(output, "w:gz") as bundle:
    for name in files:
        path = root / name
        if not path.resolve().is_relative_to(root.resolve()):
            raise ValueError("Source candidate resolves outside repository")
        if path.is_file():
            bundle.add(path, arcname="visor-source/" + name, recursive=False, filter=public_tar_metadata)
digest = hashlib.sha256(output.read_bytes()).hexdigest()
Path(str(output) + ".sha256").write_text(f"{digest}  {output.name}\n", encoding="ascii")
revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root).decode().strip()
dirty = bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=root))
metadata = {"base_revision": revision, "includes_uncommitted_changes": dirty,
            "archive": output.name, "sha256": digest, "includes_git_history": False,
            "public_candidate": args.public, "excluded": sorted(private_files) if args.public else [],
            "files": [{"path": name, "sha256": hashlib.sha256((root / name).read_bytes()).hexdigest()}
                      for name in files if (root / name).is_file()]}
output.with_name("public-source-handoff.json" if args.public else "source-handoff.json").write_text(
    json.dumps(metadata, indent=2), encoding="utf-8")
print(json.dumps({key: value for key, value in metadata.items() if key != "files"}, indent=2))
