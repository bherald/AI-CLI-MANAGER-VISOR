# Third-party notices

Native bundles include a build manifest and collected installed-distribution
license/notice files under `licenses`. Review that inventory before public distribution.

Current builds also include `dependency-sources.json`, exact Linux system-package
copyright/source bindings where available, and
[source access and library replacement instructions](docs/DEPENDENCY_SOURCES.md).
Qt/PySide/Shiboken retain their LGPLv3 terms; VISOR's own MIT license is unchanged.
The optional GNU Readline module used by separate dependency REPLs is excluded
from both frozen entry points; build/extraction checks verify actual absence.
The additional `licenses/upstream-ICU-73.2` snapshot contains ICU's complete official
copyright and third-party notices, including data notices absent from a generic
Unicode license. It applies to ICU 73.2 where identified in the actual payload.

Expected components include CPython, Qt/PySide6 and Shiboken, psutil, websockets, PyInstaller's bootloader,
and their bundled dependencies. Preserve the Qt shared libraries and applicable notices;
review redistribution obligations against the actual chosen versions and distribution format.

Codex CLI, Claude Code, Windows Terminal, Terminal.app, xterm, and wmctrl are external
prerequisites, not bundled or relicensed by this project. This file is a checklist, not a
substitute for the dependencies' license texts. VISOR's own source is MIT licensed; see LICENSE.
The collected environment inventory can include development packages that are not shipped as runtime
code. It is not a verified bill of all bundled native libraries. Exact distribution review remains pending.

Bundles also include the version-pinned official Qt/PySide license/attribution snapshot from
`third_party/qt-6.12.0`, fixing missing open-source texts in the installed wheels. Its source commits
and SHA-256 hashes are recorded and checked offline. `native-binaries.json` lists actual native payload
files; macOS hashes are labelled pre-signing. Review the remaining source/replacement/platform
redistribution work in docs/DEPENDENCY_REVIEW.md before public binary distribution.

