"""macOS bundle metadata preparation; no signing or host-specific execution."""
import plistlib
from pathlib import Path

from . import __version__

AUTOMATION_REASON = (
    "VISOR uses Terminal to open agent sessions and control the terminal windows you select."
)


def prepare_info_plist(app: Path) -> None:
    path = app / "Contents/Info.plist"
    with path.open("rb") as stream:
        info = plistlib.load(stream)
    info.update(NSAppleEventsUsageDescription=AUTOMATION_REASON,
                CFBundleShortVersionString=__version__.split("b")[0],
                CFBundleVersion="1")
    with path.open("wb") as stream:
        plistlib.dump(info, stream)


def verify_info_plist(app: Path, *, bundle_identifier="org.visor.supervisor") -> None:
    with (app / "Contents/Info.plist").open("rb") as stream:
        info = plistlib.load(stream)
    if (info.get("CFBundleIdentifier") != bundle_identifier
            or info.get("NSAppleEventsUsageDescription") != AUTOMATION_REASON
            or info.get("CFBundleVersion") != "1"
            or info.get("CFBundleShortVersionString") != __version__.split("b")[0]):
        raise ValueError("macOS bundle metadata or Automation usage description is missing or stale")
