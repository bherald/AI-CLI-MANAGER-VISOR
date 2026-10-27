"""Verify the extracted host archive; no real provider sessions or model calls."""
import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
import time
import uuid
from pathlib import Path

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / "src"))
from visor.broker import Client
from visor.bundles import extract_archive
from visor.notices import verify_qt_notices
from visor.paths import data_dir, write_private
from visor.process import hidden_options


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", type=Path)
    args = parser.parse_args()
    suffix = ".zip" if os.name == "nt" else ".tar.gz"
    archive = args.archive or root / "dist" / (
        f"visor-1.5.0-{platform.system().lower()}-{platform.machine().lower()}" + suffix)
    work = root / ".cache" / "native-package-acceptance" / uuid.uuid4().hex / "relocated package"
    extract_archive(archive.resolve(), work)
    if sys.platform == "darwin":
        app = work / "visor.app"
        from visor.mac_bundle import verify_info_plist
        verify_info_plist(app)
        helper_app = app / "Contents/Helpers/visor-console.app"
        verify_info_plist(helper_app, bundle_identifier="org.visor.supervisor.helper")
        subprocess.run(["codesign", "--verify", "--deep", "--strict", str(helper_app)], check=True)
        exe = helper_app / "Contents/MacOS/visor-console"
        gui = app / "Contents/MacOS/visor"
        resources = app / "Contents/Resources/visor"
        subprocess.run(["codesign", "--verify", "--deep", "--strict", str(app)], check=True)
    else:
        resources = work / "visor"
        exe = resources / ("visor-console.exe" if os.name == "nt" else "visor-console")
        gui = resources / ("visor.exe" if os.name == "nt" else "visor")
    for private in ("PLANNING_HISTORY.md", "IMPLEMENTATION_PLAN.md", "PUBLICATION_CHECKLIST.md", "RELEASE_CANDIDATE.md"):
        if (resources / "docs" / private).exists():
            raise RuntimeError("Private release-planning document included in native package")
    for required in (exe, gui, resources / "LICENSE", resources / "README.md",
                     resources / "THIRD_PARTY_NOTICES.md", resources / "licenses",
                     resources / "docs/WEB_ACCESS.md", resources / "docs/PRISM_AGENTS.md",
                     resources / "CONTRIBUTING.md",
                     resources / "native-binaries.json",
                     resources / "integrations/claude/visor/.mcp.json"):
        if not required.exists():
            raise RuntimeError(f"Missing packaged resource: {required}")
    manifest = json.loads((resources / "build-manifest.json").read_text(encoding="utf-8"))
    if manifest["os"] != platform.system():
        raise RuntimeError("Archive belongs to a different OS")
    qt_version = manifest["upstream_qt_notices"]["version"]
    notice_root = resources / "licenses" / ("upstream-qt-" + qt_version)
    verify_qt_notices(notice_root, qt_version)
    inventory = json.loads((resources / "native-binaries.json").read_text(encoding="utf-8"))
    binary_root = app if sys.platform == "darwin" else resources
    if not inventory["binaries"]:
        raise RuntimeError("Native binary inventory is empty")
    for entry in inventory["binaries"]:
        binary = binary_root / entry["path"]
        if not binary.resolve().is_relative_to(binary_root.resolve()) or not binary.is_file():
            raise RuntimeError("Native binary inventory points outside or is missing from the bundle")
        if inventory["hash_scope"] == "packaged files":
            with binary.open("rb") as stream:
                if hashlib.file_digest(stream, "sha256").hexdigest() != entry["sha256"]:
                    raise RuntimeError("Packaged native binary hash mismatch: " + entry["path"])
    for filename in ("LGPL-3.0-only.txt", "GPL-3.0-only.txt"):
        if not (notice_root / "qtbase/LICENSES" / filename).is_file():
            raise RuntimeError("Packaged Qt open-source license text is missing")
    if os.name != "nt" and not all(os.access(path, os.X_OK) for path in (exe, gui)):
        raise RuntimeError("Extracted executables lost their executable permission")
    data = data_dir(str(work / "isolated data"))
    # Smoke rendering must stay unattended even if the previous GUI listener was
    # active. Only a normal launch may offer the explicit restart question.
    preferences = {"web_was_active": True, "web_host": "192.0.2.10", "web_port": 34881}
    write_private(data / "prism-workspace.json", preferences)
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV")}
    env["QT_QPA_PLATFORM"] = "offscreen"
    subprocess.run([str(exe), "--smoke-test", "--data-dir", str(data)], cwd=work,
                   env=env, check=True, timeout=45)
    if not (data / "ui-smoke.png").is_file():
        raise RuntimeError("Extracted UI did not render")
    if json.loads((data / "prism-workspace.json").read_text())["web_was_active"] is not True:
        raise RuntimeError("Smoke rendering changed the saved web restart preference")
    client = Client(data)
    message_id = None
    for iteration in range(2):
        process = subprocess.Popen([str(exe), "--broker", "--data-dir", str(data)],
                                   cwd=work, env=env, **hidden_options())
        try:
            deadline = time.monotonic() + 15
            while True:
                try:
                    client.call("ping")
                    break
                except (OSError, ValueError):
                    if process.poll() is not None or time.monotonic() >= deadline:
                        raise RuntimeError("Extracted broker did not start") from None
                    time.sleep(0.1)
            if not iteration:
                key = client.call("register", provider="claude", native_id=str(uuid.uuid4()),
                                  name="Package test", cwd=str(work))
                client.call("toggle", enabled=False)
                message_id = client.call("send", recipient=key, body="Isolated package test",
                                         request_id="package-once")["id"]
            else:
                snapshot = client.call("snapshot")
                if snapshot["agent_enabled"] or not any(
                        m["id"] == message_id and m["state"] == "queued" for m in snapshot["messages"]):
                    raise RuntimeError("Queue/policy did not survive broker restart")
            client.call("stop")
            process.wait(timeout=10)
            if process.returncode:
                raise RuntimeError("Extracted broker exited with an error")
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=10)
    for script, target in (("verify_single_instance.py", gui), ("verify_claude_package.py", exe),
                           ("verify_upgrade.py", exe), ("verify_state_recovery.py", exe),
                           ("verify_prism_package.py", exe)):
        subprocess.run([sys.executable, str(root / "scripts" / script), str(target)],
                       cwd=root, check=True, timeout=120)
    print("PASS: checksum, extracted resources/modes, relocated UI, broker restart/queue, singleton, simulated Claude, upgrade, private state recovery, authenticated Prism/browser")
    print(f"Isolated acceptance files: {work}")


if __name__ == "__main__":
    main()
