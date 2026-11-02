# Dependency sources and library replacement

VISOR's own source is MIT licensed. Bundled dependencies keep their own licenses;
their copyright and license texts are under `licenses`. Qt, PySide6 and Shiboken
are used under their LGPLv3 option. The official 6.12.0 notice snapshot includes
the applicable LGPL/GPL texts and third-party attributions.

`dependency-sources.json` identifies the exact Qt/PySide source commits and direct
source-archive links, plus the interpreter version and official Python source.
Its additional component records also preserve the complete official ICU 73.2
license and data notices with the immutable upstream source commit and archive.
The lock file pins the wheel versions and hashes. For distribution-owned Linux
libraries, `native-system-notices.json` binds packaged native hashes to observed
original inputs, binary/source package versions, copied copyrights and referenced
license texts. Ubuntu entries link directly to that source-package version and
its original source, distribution patches and build files. Wheel and bootloader
notices are separate; a path without a distribution owner is not a missing-wheel
license verdict. Source input hashes are observed after the native build and may
differ from relocated packaged files.

Obtain the VISOR source matching the release tag from its GitHub release source
archive. It includes the application, build scripts and lock file. Obtain the
matching library sources from `dependency-sources.json` and any native source
package links. Keep those complete sources, patches and build instructions when
modifying a library. These are source-access directions, not an assertion that
dependency source archives are embedded in the binary download.

The native bundle keeps shared libraries separate from the application. You may
modify or replace the LGPL libraries and debug those modifications; VISOR imposes
no additional restriction on reverse engineering for that purpose. Work in your
own extracted copy, preserve the library ABI and matching PySide/Shiboken/plugin
set, and retain all licenses. The application and nested macOS helper can each
contain library copies. Alternatively, build the modified libraries or wheels in
your own environment and rebuild VISOR using `scripts/bootstrap.py` and
`scripts/build.py`. The normal macOS build reassembles and locally ad-hoc signs
both app bundles; normal operating-system security/permission choices remain.
This does not require modifying VISOR's saved profiles or credentials.

Before publishing each binary, the publisher must verify its source links and
exact native inventory, keep corresponding source available through the stated
distribution route, and resolve recorded notice issues. Linux collection does
not clear Windows runtime/software-OpenGL DLLs or other macOS dylibs; their actual
platform payload remains the evidence. A build's inventory-only review flag is
not a test failure or automatic redistribution approval.

References: [Qt LGPL obligations](https://www.qt.io/development/open-source-lgpl-obligations),
[Qt for Python licenses](https://doc.qt.io/qtforpython-6/licenses.html),
[PyInstaller's bootloader exception](https://pyinstaller.org/en/stable/license.html).
