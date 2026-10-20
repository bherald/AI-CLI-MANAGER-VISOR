# Dependency distribution review — 2026-10-19

This records inspected local metadata, not completed binary-distribution clearance.
Source publication under VISOR's MIT license and distribution of bundled dependencies are separate checks.

| Installed component | Version inspected | Metadata license | Local notice observation |
| --- | --- | --- | --- |
| CPython | 3.12.10 | Python license texts | Build collects LICENSE.txt from the Python prefix or standard library |
| PySide6-Essentials | 6.12.0 | LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only | Wheel RECORD lists only LicenseRef-Qt-Commercial.txt as a license file |
| shiboken6 | 6.12.0 | LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only | Same license-file gap as the PySide6 wheel |
| websockets | 17.2 | BSD-3-Clause | LICENSE collected from dist-info/licenses |
| psutil | 7.2.2 | BSD-3-Clause | LICENSE collected from dist-info |
| PyInstaller | 6.22.3 | GPLv2-or-later with bootloader exception | COPYING.txt collected; build tooling inventory includes more than shipped runtime code |
| pyinstaller-hooks-contrib | 2026.8 | Inspect packaged LICENSE | LICENSE collected; build-time hooks are not all runtime dependencies |

The missing Qt open-source texts have been supplied from official 6.12.0 source repositories.
`third_party/qt-6.12.0` contains 190 exact upstream files: license texts, Qt attribution records and
their referenced license files from qtbase, qtsvg and pyside-setup. The manifest pins source commits,
URLs and SHA-256 hashes. All referenced license files were resolved. Builds check the snapshot's
version/integrity and package it under `licenses/upstream-qt-6.12.0`; no build-time fetch is needed.

The snapshot is a superset of upstream notices, including examples/tools that VISOR does not use.
`native-binaries.json` now identifies actual packaged native files by relative path, size and SHA-256.
On macOS its hashes explicitly describe the pre-signing payload because signing changes Mach-O bytes;
archive integrity and codesign verification cover the shipped app. Installed-distribution metadata is
separately labelled as build-environment inventory and may include unbundled tooling.

Still unresolved: match each actual native library/plugin to its applicable attribution and complete
the chosen distribution route's corresponding-source/replacement and platform redistribution review.
The inspected Windows payload includes Qt Core/Gui/Network/Svg/Widgets, PySide/Shiboken, CPython,
psutil, Microsoft runtime DLLs and a software OpenGL DLL; the wheel metadata alone does not establish
all their redistribution requirements. Repeat the mapping on each OS/architecture; native library sets
differ. Collecting notices and checksums is not completed binary-distribution clearance.

Primary references for the remaining review:

- [Qt for Python third-party licenses](https://doc.qt.io/qtforpython-6/licenses.html)
- [Qt licensing](https://doc.qt.io/qt-6/licensing.html)
- [PyInstaller license and bootloader exception](https://pyinstaller.org/en/stable/license.html)
- [psutil source/license](https://github.com/giampaolo/psutil/blob/master/LICENSE)

The implementation tracker keeps R01 open. The local build remains a beta for evaluation;
no completed license-compliance review is claimed.
