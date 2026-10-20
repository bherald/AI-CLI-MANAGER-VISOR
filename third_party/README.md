# Versioned upstream notice snapshots

`qt-6.12.0` contains unmodified license texts, attribution records and referenced license files
from official Qt/PySide source repositories at the exact commits recorded in its manifest.
The snapshot is a superset: it also includes notices for upstream examples, tools and components
that VISOR does not necessarily ship. Their presence does not imply those components are used.

`scripts/collect_qt_notices.py` is an explicit developer maintenance command. Native builds do
not fetch these files: they check version and SHA-256 integrity before copying the snapshot into
the bundle's `licenses` directory. Review the chosen upstream version before changing the script.

Upstream files retain their own copyrights and licenses. VISOR's root MIT license does not
relicense them. The snapshot fixes missing license texts in the installed Qt wheels, but does not
complete review of every native library, corresponding-source/replacement requirements, platform
redistributable or distribution format. See docs/DEPENDENCY_REVIEW.md and each build's native
binary inventory. No license-compliance clearance is implied by collecting notices.
