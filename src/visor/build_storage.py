"""Build-space checks and explicit cleanup of inactive generated native bundles."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import psutil


def check_space(path, required=2 * 1024 ** 3):
    free = shutil.disk_usage(path).free
    if free < required:
        raise RuntimeError(f"Build needs at least {required // 1024 ** 2} MiB free; {free // 1024 ** 2} MiB available. "
                           "Review scripts/clean_build_cache.py before removing inactive build copies.")


def cleanup_candidates(root: Path, keep=2):
    base = root / "dist/native"
    if not base.is_dir():
        return []
    protected = []
    manifest = root / "dist/latest-build.json"
    if manifest.is_file():
        protected.append((root / "dist" / json.loads(manifest.read_text())["bundle"]).resolve())
    for process in psutil.process_iter(["exe", "cmdline"]):
        try:
            for value in [process.info["exe"], *(process.info["cmdline"] or [])]:
                if value and Path(value).is_absolute():
                    protected.append(Path(value).resolve())
        except psutil.NoSuchProcess:
            continue
        except psutil.AccessDenied as exc:
            # An unknown process could be using any candidate. Fail closed.
            raise RuntimeError("Cannot inspect running processes; automatic build cleanup refused") from exc
    bundles = sorted((p for p in base.iterdir() if p.is_dir() and not p.is_symlink()),
                     key=lambda p: p.stat().st_mtime, reverse=True)
    return [p for p in bundles[keep:] if any((p / name).is_file() for name in
            ("visor/build-manifest.json", "visor.app/Contents/Resources/visor/build-manifest.json"))
            and not any(p.resolve() == item or p.resolve() in item.parents for item in protected)]
