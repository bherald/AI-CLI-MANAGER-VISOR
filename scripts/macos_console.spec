# Keep console=True for broker/MCP stdio, while BUNDLE lays out code and data
# according to macOS signing rules rather than embedding an onedir verbatim.
from pathlib import Path
import sys

root = Path(SPECPATH).parent
sys.path.insert(0, str(root / "src"))
from visor.mac_bundle import AUTOMATION_REASON
from visor import __version__

a = Analysis(
    [str(root / "scripts/package_entry.py")],
    pathex=[str(root / "src")],
    hiddenimports=["visor.ui", "PySide6.QtWidgets", "PySide6.QtCore", "PySide6.QtGui"],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [], exclude_binaries=True, name="visor-console",
    console=True,
)
coll = COLLECT(exe, a.binaries, a.datas, name="visor-console")
app = BUNDLE(
    coll, name="visor-console.app", bundle_identifier="org.visor.supervisor.helper",
    info_plist={
        "NSAppleEventsUsageDescription": AUTOMATION_REASON,
        "CFBundleShortVersionString": __version__.split("b")[0],
        "CFBundleVersion": "1",
    },
)
