"""Build on the target OS. Output is a local unsigned beta bundle, not a public release."""
import argparse
import importlib.metadata
import importlib.util
import json
import os
import platform
import shutil
import subprocess
import sys
import sysconfig
import uuid
from pathlib import Path

root = Path(__file__).resolve().parents[1]
# Packaged VISOR terminals may export their private Qt/Python loader paths.
# Re-exec before importing build dependencies so both this interpreter and its
# PyInstaller children use the installed build environment. Keep unrelated paths.
clean_env = os.environ.copy()
for key in ("LD_LIBRARY_PATH", "LD_LIBRARY_PATH_ORIG", "QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH"):
    if key in clean_env:
        entries = [entry for entry in clean_env[key].split(os.pathsep)
                   if entry and not Path(entry).resolve().is_relative_to((root / "dist").resolve())]
        if entries:
            clean_env[key] = os.pathsep.join(entries)
        else:
            clean_env.pop(key)
if clean_env != dict(os.environ):
    os.execve(sys.executable, [sys.executable, *sys.argv], clean_env)

sys.path.insert(0, str(root / "src"))
from visor.build_storage import check_space
from visor.bundles import create_archive
from visor.notices import copy_qt_notices, native_inventory

parser = argparse.ArgumentParser()
parser.add_argument("--output-dir", type=Path, default=root / "dist")
output = parser.parse_args().output_dir.resolve()
if not output.is_relative_to(root / "dist"):
    raise SystemExit("Build output must stay within the project dist directory")
check_space(root)
# Never ask PyInstaller to clean a directory that may contain a running installation.
bundle_output = output / "native" / uuid.uuid4().hex
bundle_output.mkdir(parents=True, exist_ok=False)
missing = [name for name in ("PyInstaller", "PySide6") if importlib.util.find_spec(name) is None]
if missing:
    raise SystemExit("Build blocked: missing " + ", ".join(missing) +
                     ". Run scripts/bootstrap.py with package-download access.")
if not (root / "uv.lock").exists():
    raise SystemExit("Build blocked: uv.lock is missing. Run scripts/bootstrap.py to resolve dependencies.")
env = {**os.environ, "PYINSTALLER_CONFIG_DIR": str(root / ".cache" / "pyinstaller")}
for name, windowed in (("visor", True), ("visor-console", False)):
    if sys.platform == "darwin" and not windowed:
        # A console-enabled app bundle lets PyInstaller relocate non-code data
        # out of code directories before the helper is nested in the GUI app.
        subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
                        "--distpath", str(bundle_output),
                        "--workpath", str(root / "build" / name),
                        str(root / "scripts/macos_console.spec")], cwd=root, env=env, check=True)
        continue
    args = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir", "--name", name,
            "--paths", str(root / "src"), "--distpath", str(bundle_output),
            "--workpath", str(root / "build" / name), "--specpath", str(root / "build"),
            "--hidden-import", "visor.ui", "--hidden-import", "PySide6.QtWidgets",
            "--hidden-import", "PySide6.QtCore", "--hidden-import", "PySide6.QtGui"]
    args += ["--add-data", str(root / "src/visor/web") + os.pathsep + "visor/web"]
    if windowed and sys.platform in ("win32", "darwin"):
        args.append("--windowed")
    if windowed and sys.platform == "darwin":
        args += ["--osx-bundle-identifier", "org.visor.supervisor"]
    subprocess.run([*args, str(root / "scripts" / "package_entry.py")], cwd=root, env=env, check=True)
# Both entry points share one onedir runtime on Windows/Linux.
suffix = ".exe" if os.name == "nt" else ""
console = bundle_output / "visor-console"
destination = bundle_output / "visor"
if sys.platform == "darwin":
    app = bundle_output / "visor.app"
    from visor.mac_bundle import prepare_info_plist, verify_info_plist
    prepare_info_plist(app)
    verify_info_plist(app)
    helper_app = app / "Contents/Helpers/visor-console.app"
    shutil.copytree(bundle_output / "visor-console.app", helper_app,
                    dirs_exist_ok=True, symlinks=True)
    verify_info_plist(helper_app, bundle_identifier="org.visor.supervisor.helper")
    resources = app / "Contents" / "Resources" / "visor"
    resources.mkdir(parents=True, exist_ok=True)
else:
    shutil.copy2(console / ("visor-console" + suffix), destination)
    shutil.copytree(console / "_internal", destination / "_internal", dirs_exist_ok=True)
    resources = destination
for filename in ("README.md", "PRISM.md", "LICENSE", "THIRD_PARTY_NOTICES.md", "SECURITY.md",
                 "CONTRIBUTING.md", "uv.lock"):
    shutil.copy2(root / filename, resources)
shutil.copytree(root / "docs", resources / "docs", dirs_exist_ok=True,
                ignore=shutil.ignore_patterns("PLANNING_HISTORY.md", "IMPLEMENTATION_PLAN.md",
                                             "PUBLICATION_CHECKLIST.md", "RELEASE_CANDIDATE.md"))
shutil.copytree(root / "integrations", resources / "integrations", dirs_exist_ok=True)
licenses = resources / "licenses"
licenses.mkdir(exist_ok=True)
inventory = []
for dist in importlib.metadata.distributions():
    name, version = dist.metadata["Name"], dist.version
    inventory.append({"name": name, "version": version,
                      "metadata_license": dist.metadata.get("License-Expression") or dist.metadata.get("License")})
    for file in dist.files or []:
        if ".dist-info/" in str(file).replace("\\", "/") and any(
                part in str(file).casefold() for part in ("license", "notice", "copying")):
            source = Path(dist.locate_file(file))
            if source.is_file():
                target = licenses / name / Path(str(file))
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
for python_license in (Path(sys.base_prefix) / "LICENSE.txt",
                       Path(sysconfig.get_path("stdlib")) / "LICENSE.txt"):
    if python_license.exists():
        shutil.copy2(python_license, licenses / "CPython-LICENSE.txt")
        break
qt_version = importlib.metadata.version("PySide6-Essentials")
from PySide6.QtCore import qVersion

if qVersion() != qt_version:
    raise SystemExit("Qt runtime and PySide versions differ; match upstream notices before distributing this build")
qt_notices = copy_qt_notices(root / "third_party" / ("qt-" + qt_version),
                            licenses / ("upstream-qt-" + qt_version), qt_version)
binary_root = app if sys.platform == "darwin" else destination
(resources / "native-binaries.json").write_text(json.dumps({"binaries": native_inventory(binary_root),
    "hash_scope": "before code signing" if sys.platform == "darwin" else "packaged files",
    "review_status": "Inventory only; platform/native dependency redistribution review remains required"}, indent=2), encoding="utf-8")
manifest = {"application": "VISOR", "version": "1.5.0", "release_status": "unsigned beta",
            "os": platform.system(), "architecture": platform.machine(), "python": platform.python_version(),
            "dependencies": sorted(inventory, key=lambda item: item["name"].lower()),
            "upstream_qt_notices": qt_notices, "native_inventory": "native-binaries.json",
            "dependency_review_complete": False}
manifest["inventory_scope"] = "Installed build environment; includes tooling that may not be bundled"
(resources / "build-manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
if sys.platform == "darwin":
    # Assembly changes the app after PyInstaller's initial ad-hoc signature.
    # This local signature is not Developer ID signing or notarization.
    subprocess.run(["codesign", "--force", "--deep", "--sign", "-", str(helper_app)], check=True)
    subprocess.run(["codesign", "--verify", "--deep", "--strict", str(helper_app)], check=True)
    subprocess.run(["codesign", "--force", "--deep", "--sign", "-", str(app)], check=True)
    subprocess.run(["codesign", "--verify", "--deep", "--strict", str(app)], check=True)
    smoke_exe = helper_app / "Contents/MacOS/visor-console"
else:
    smoke_exe = destination / ("visor-console" + suffix)
smoke = root / ".cache" / "packaged-smoke" / uuid.uuid4().hex
if (smoke / "ui-smoke.png").is_file():
    (smoke / "ui-smoke.png").unlink()
subprocess.run([str(smoke_exe), "--smoke-test", "--data-dir", str(smoke)],
               cwd=root, env={**env, "QT_QPA_PLATFORM": "offscreen"}, timeout=45, check=True)
if not (smoke / "ui-smoke.png").is_file():
    raise SystemExit("Packaged UI did not render its smoke-test image")
archive_name = f"visor-1.5.0-{platform.system().lower()}-{platform.machine().lower()}"
archive = create_archive(app if sys.platform == "darwin" else destination,
                         output / archive_name, windows=os.name == "nt")
(output / "latest-build.json").write_text(json.dumps({"archive": archive.name,
    "bundle": str((app if sys.platform == "darwin" else destination).relative_to(output))}, indent=2), encoding="utf-8")
print(f"Beta bundle: {app if sys.platform == 'darwin' else destination}\nArchive: {archive}")
