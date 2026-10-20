"""Explicit maintenance task: vendor version-pinned official Qt license/attribution records."""
import concurrent.futures
import hashlib
import json
import posixpath
import urllib.request
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
VERSION = "6.12.0"
REPOSITORIES = {"qtbase": "qt/qtbase", "qtsvg": "qt/qtsvg", "pyside-setup": "pyside/pyside-setup"}


def fetch(url, limit=4 * 1024 * 1024):
    # A process-local direct connection; no machine proxy or provider settings are changed.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(url, timeout=30) as response:
        data = response.read(limit + 1)
    if len(data) > limit:
        raise ValueError("Upstream notice exceeds collection bound")
    return data


def main():
    destination = ROOT / "third_party" / ("qt-" + VERSION)
    destination.mkdir(parents=True, exist_ok=True)
    manifest = {"version": VERSION, "scope": "Upstream notice superset, not binary compliance clearance",
                "repositories": {}, "files": [], "unresolved_license_files": []}
    for label, repo in REPOSITORIES.items():
        api = f"https://api.github.com/repos/{repo}"
        commit = json.loads(fetch(f"{api}/commits/v{VERSION}"))["sha"]
        tree = json.loads(fetch(f"{api}/git/trees/{commit}?recursive=1", limit=16 * 1024 * 1024))
        if tree.get("truncated"):
            raise ValueError("Upstream source listing is incomplete")
        paths = {entry["path"] for entry in tree["tree"] if entry["type"] == "blob"}
        selected = {p for p in paths if p.startswith("LICENSES/") or p.endswith("qt_attribution.json")}
        raw = f"https://raw.githubusercontent.com/{repo}/{commit}/"
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            contents = dict(zip(sorted(selected), pool.map(lambda p, raw=raw: fetch(raw + p), sorted(selected))))
        extra = set()
        for name, data in contents.items():
            if not name.endswith("qt_attribution.json"):
                continue
            # Some upstream copyright strings contain literal control characters.
            records = json.loads(data, strict=False)
            for record in records if isinstance(records, list) else [records]:
                licenses = record.get("LicenseFile", [])
                if isinstance(licenses, str):
                    licenses = licenses.split(";")
                for license_file in licenses:
                    candidate = posixpath.normpath(str(PurePosixPath(name).parent / license_file.strip()))
                    if candidate.startswith(("../", "/")) or candidate not in paths:
                        manifest["unresolved_license_files"].append({"repository": label, "record": name,
                                                                    "license_file": license_file})
                    else:
                        extra.add(candidate)
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            contents.update(zip(sorted(extra), pool.map(lambda p, raw=raw: fetch(raw + p), sorted(extra))))
        for name, data in sorted(contents.items()):
            target = destination / label / name
            if not target.resolve().is_relative_to(destination.resolve()):
                raise ValueError("Upstream path escaped notice directory")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            manifest["files"].append({"path": f"{label}/{name}", "sha256": hashlib.sha256(data).hexdigest(),
                                      "source": raw + name})
        manifest["repositories"][label] = {"repository": repo, "tag": "v" + VERSION, "commit": commit}
    (destination / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"files": len(manifest["files"]), "unresolved_license_files": manifest["unresolved_license_files"]}, indent=2))


if __name__ == "__main__":
    main()
