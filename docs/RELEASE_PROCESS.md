# Releasing a platform beta

Source publication and binary distribution have separate checks. Source is MIT licensed; bundled runtime libraries retain their own license obligations.

## Source beta

1. Review README, security guidance, current platform evidence and source-image contents. Export clean public source with `python scripts/source_bundle.py --public` and inspect its file/hash manifest under `dist/`.
2. Keep private history, planning notes, runtime state and publication drafts out of the public repository. Review the exact initial commit and destination before publishing.
3. Configure private vulnerability reporting on the new GitHub repository. Verify public contents and the current README after the approved push.
4. Run the included hosted workflow and record actual job/architecture results. The definition alone is not a passed build.

## Independent Windows and Linux downloads

macOS does not block a Windows or Linux beta release. Each published platform needs its own evidence for the exact source revision.

1. Run the native pipeline on that platform. Retain its manifest, archive and adjacent SHA-256 file. Use the native acceptance checklist and record remaining limits.
2. Complete the dependency-distribution review, including actual bundled libraries and Qt source/replacement obligations. Keep unsigned status and clean-machine prerequisites explicit.
3. Prepare a GitHub prerelease tag such as `v1.2.0`, targeting the reviewed public source commit. The release description must identify which platform assets are attached and tested. Never reuse an old alpha archive with a beta filename.
4. After release approval, attach only the checked platform archives/checksums. CI artifacts are temporary evaluation output, not automatically published releases.
5. Download each published archive, verify its checksum and read back the release description and asset list. Keep a receipt with commit, architecture and test scope. A later platform can be attached after its own review.

Mac CI can build Apple silicon and Intel evaluation artifacts after an approved repository push. A successful build does not establish desktop support. Do not list a Mac binary as verified without the missing native results in [MACOS.md](MACOS.md).

The current workflow has read-only repository permissions and no automatic release-publishing step. Public pushes, release creation, website publication and LinkedIn posts require their own reviewed content and approval.
