"""Offline notice integrity and an inventory of actual packaged native binaries."""
from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path


def verify_qt_notices(source: Path, version: str) -> dict:
    manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
    if manifest["version"] != version or manifest.get("unresolved_license_files"):
        raise ValueError("Qt notice snapshot does not match this build; update and review third_party notices")
    for entry in manifest["files"]:
        path = source / entry["path"]
        if not path.resolve().is_relative_to(source.resolve()):
            raise ValueError("Notice path escaped upstream snapshot")
        if hashlib.sha256(path.read_bytes()).hexdigest() != entry["sha256"]:
            raise ValueError("Upstream Qt notice integrity mismatch: " + entry["path"])
    for name in ("LGPL-3.0-only.txt", "GPL-3.0-only.txt"):
        if not (source / "qtbase" / "LICENSES" / name).is_file():
            raise ValueError("Required Qt open-source license text is missing")
    return {"version": version, "files": len(manifest["files"]), "repositories": manifest["repositories"]}


def copy_qt_notices(source: Path, destination: Path, version: str) -> dict:
    result = verify_qt_notices(source, version)
    shutil.copytree(source, destination, dirs_exist_ok=True)
    return result


def native_inventory(root: Path) -> list[dict]:
    magic = {b"\x7fELF", b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe", b"\xfe\xed\xfa\xcf",
             b"\xfe\xed\xfa\xce", b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca",
             b"\xca\xfe\xba\xbf", b"\xbf\xba\xfe\xca"}
    entries = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if not path.resolve().is_relative_to(root.resolve()):
            raise ValueError("Packaged file resolves outside its bundle: " + str(path.relative_to(root)))
        with path.open("rb") as stream:
            header = stream.read(4)
            if header[:2] != b"MZ" and header not in magic:
                continue
            stream.seek(0)
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        entries.append({"path": path.relative_to(root).as_posix(), "sha256": digest,
                        "size": path.stat().st_size, "symlink": path.is_symlink()})
    return entries
