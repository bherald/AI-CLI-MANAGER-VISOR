"""Copy distribution copyright metadata for actual PyInstaller native inputs.

Build tooling only: no network, installs, runtime policy or compliance verdict.
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from urllib.parse import quote


def copy_reviewed_snapshot(source, destination):
    manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
    for key in ("component", "version"):
        if not isinstance(manifest.get(key), str) or not re.fullmatch(r"[A-Za-z0-9.+_-]+", manifest[key]):
            raise ValueError("Invalid reviewed notice identity")
    for entry in manifest["files"]:
        path = source / entry["path"]
        if (not path.resolve().is_relative_to(source.resolve()) or not path.is_file()
                or hashlib.sha256(path.read_bytes()).hexdigest() != entry["sha256"]):
            raise ValueError("Reviewed notice is missing, escaped or changed")
    target = destination / ("upstream-" + manifest["component"] + "-" + manifest["version"])
    target.mkdir(parents=True, exist_ok=True)
    for entry in manifest["files"]:
        path = target / entry["path"]
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / entry["path"], path)
    shutil.copyfile(source / "manifest.json", target / "manifest.json")
    return manifest


def _entries(value):
    if isinstance(value, (tuple, list)):
        if len(value) == 3 and all(isinstance(part, str) for part in value) and value[2] in ("BINARY", "EXTENSION"):
            yield value
        else:
            for child in value:
                yield from _entries(child)


def collect_linux_notices(analyses, native, destination, *, doc_root=Path("/usr/share/doc"),
                          common_root=Path("/usr/share/common-licenses"), runner=None, distribution=None):
    """Use source paths from this build, never guess package identity from a SONAME."""
    runner = runner or subprocess.run
    report = {"packages": [], "bindings": [], "without_distribution_metadata": [], "without_analysis_source": [], "issues": [],
              "distribution": {key: (distribution or {}).get(key) for key in ("ID", "VERSION_ID")}}
    originals = {}
    for analysis in analyses:
        if not analysis.is_file():
            report["issues"].append({"reason": "analysis_missing", "entrypoint": analysis.parent.name})
            continue
        for target, source, _kind in _entries(ast.literal_eval(analysis.read_text(encoding="utf-8"))):
            originals.setdefault(target, {}).setdefault(Path(source).resolve(), set()).add(Path(source))
    prepared, aliases = [], set()
    for item in native:
        target = item["path"].removeprefix("_internal/")
        candidates = originals.get(target, {})
        if not candidates:  # Includes entry-point bootloaders and duplicated layout aliases.
            report["without_analysis_source"].append(item["path"])
            continue
        if len(candidates) != 1:
            report["issues"].append({"path": item["path"], "reason": "ambiguous_native_source"})
            continue
        source = next(iter(candidates))
        paths = candidates[source] | {source}
        prepared.append((item, source, paths))
        aliases.update(paths)
    owners_by_path = {}
    if aliases:
        query = runner(["dpkg-query", "-S", *map(str, sorted(aliases))], capture_output=True, text=True,
                       check=False, env={**os.environ, "LC_ALL": "C"})
        # Exit1 can contain complete matches plus ordinary unowned wheel paths.
        if query.returncode not in (0, 1):
            report["issues"].append({"reason": "package_owner_query_failed"})
        for line in query.stdout.splitlines():
            if ": " in line:
                owner, path = line.rsplit(": ", 1)
                owners_by_path.setdefault(Path(path), set()).add(owner)
    admitted = []
    for item, source, paths in prepared:
        owners = set().union(*(owners_by_path.get(path, set()) for path in paths))
        if not owners:
            report["without_distribution_metadata"].append(item["path"])
        elif len(owners) != 1 or not re.fullmatch(r"[a-z0-9][a-z0-9+.-]*(?::[a-z0-9-]+)?", next(iter(owners))):
            report["issues"].append({"path": item["path"], "reason": "ambiguous_package_owner"})
        else:
            admitted.append((item, source, next(iter(owners))))
    metadata, packages = {}, {}
    if admitted:
        result = runner(["dpkg-query", "-W", "-f=${binary:Package}\t${Version}\t${source:Package}\t${source:Version}\n",
                         *sorted({package for _item, _source, package in admitted})],
                        capture_output=True, text=True, check=False, env={**os.environ, "LC_ALL": "C"})
        for line in result.stdout.splitlines():
            fields = line.split("\t")
            if len(fields) == 4 and all(fields):
                metadata[fields[0]] = fields
    for item, source, package in admitted:
        if package not in packages:
            fields = metadata.get(package, [])
            notice = doc_root / package.split(":")[0] / "copyright"
            if (len(fields) != 4 or not all(fields) or fields[0] != package
                    or not notice.is_file() or not notice.resolve().is_relative_to(doc_root.resolve())):
                report["issues"].append({"package": package, "reason": "package_metadata_or_copyright_missing"})
                continue
            folder = destination / package.replace(":", "_")
            folder.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(notice, folder / "copyright")
            record = dict(zip(("package", "version", "source_package", "source_version"), fields))
            record["copyright"] = {"path": (folder / "copyright").relative_to(destination).as_posix(),
                                   "sha256": hashlib.sha256(notice.read_bytes()).hexdigest()}
            record["referenced_licenses"] = []
            for name in sorted(set(re.findall(r"/usr/share/common-licenses/([A-Za-z0-9.+-]*[A-Za-z0-9])", notice.read_text(encoding="utf-8")))):
                license_file = common_root / name
                if not license_file.is_file() or not license_file.resolve().is_relative_to(common_root.resolve()):
                    report["issues"].append({"package": package, "reason": "referenced_license_missing", "license": name})
                    continue
                shutil.copyfile(license_file, folder / name)
                record["referenced_licenses"].append({"path": (folder / name).relative_to(destination).as_posix(),
                                                      "sha256": hashlib.sha256(license_file.read_bytes()).hexdigest()})
            if (distribution or {}).get("ID") == "ubuntu":
                record["source_access"] = "https://launchpad.net/ubuntu/+source/" + quote(fields[2], safe="") + "/" + quote(fields[3], safe="")
            packages[package] = record
        report["bindings"].append({"path": item["path"], "packaged_sha256": item["sha256"],
                                   "source_observed_sha256": hashlib.sha256(source.read_bytes()).hexdigest(), "package": package})
    report["packages"] = [packages[key] for key in sorted(packages)]
    report["scope"] = "Matched distribution native inputs; missing owner queries are not absence proof. Wheel/Qt/bootloader notices remain separate. No legal-clearance flag."
    return report
