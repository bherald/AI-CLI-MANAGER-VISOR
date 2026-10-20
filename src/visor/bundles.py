"""Native archive helpers. Unix tar archives retain executable modes and app symlinks."""
from __future__ import annotations

import hashlib
import os
import shutil
import tarfile
import tempfile
import zipfile
from pathlib import Path


def create_archive(directory: Path, archive_base: Path, *, windows: bool) -> Path:
    archive = Path(str(archive_base) + (".zip" if windows else ".tar.gz"))
    archive.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".archive-", dir=archive.parent) as temporary:
        if windows:
            staged = Path(shutil.make_archive(str(Path(temporary) / "bundle"), "zip",
                                              directory.parent, directory.name))
        else:
            staged = Path(temporary) / "bundle.tar.gz"
            with tarfile.open(staged, "w:gz") as bundle:
                bundle.add(directory, arcname=directory.name, filter=public_tar_metadata)
        os.replace(staged, archive)
    with archive.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    Path(str(archive) + ".sha256").write_text(f"{digest}  {archive.name}\n", encoding="ascii")
    return archive


def public_tar_metadata(entry: tarfile.TarInfo) -> tarfile.TarInfo:
    entry.uid = entry.gid = 0
    entry.uname = entry.gname = ""
    return entry


def extract_archive(archive: Path, destination: Path) -> None:
    with archive.open("rb") as stream:
        actual = hashlib.file_digest(stream, "sha256").hexdigest()
    expected = Path(str(archive) + ".sha256").read_text(encoding="ascii").split()[0]
    if actual != expected:
        raise ValueError("Archive checksum mismatch")
    destination.mkdir(parents=True, exist_ok=True)
    root = destination.resolve()
    if zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive) as bundle:
            for entry in bundle.infolist():
                if not (root / entry.filename).resolve().is_relative_to(root):
                    raise ValueError("Archive member escapes extraction directory")
            bundle.extractall(root)
    else:
        with tarfile.open(archive, "r:gz") as bundle:
            bundle.extractall(root, filter="data")
