# Native directory scanning on Linux: race-safe design (RFC)

**Status: IMPLEMENTED THROUGH S3b (public read-only opt-in). S4-2a/S4-2b later added the
guarded directory authoring surface described in `docs/directory-authoring-s4-rfc.md`;
this scanner document authorized no new route and its read-only scope is unchanged. This
document adds only `--directory-artifacts` on `validate` and `inspect`; the default
behavior is unchanged.**

- Version: **v0.4.6** (S3b public read-only opt-in)
- Date: 2026-11-12
- Milestone: M36-NATIVE-LINUX-SCANNER-DESIGN
- Workspace: `/home/user/projects/rungraph` on Boat Box (Linux, Python 3.12.3 in
  `.venv`; package supports Python >= 3.10)
- Authority: JSON manifests remain the production authority. SQLite remains dormant.
  No Git, publication, version bump or public route is part of this work.
- Accepted Stage 3 / 3d semantics are not reopened. In particular, the default
  `validate`/`inspect` behavior, comparability restrictions and the S3P3P7 persistence
  HOLD are unchanged; only the explicitly approved directory opt-in was added.

## Changelog

- **v0.4.7 (S3c documentation accuracy):** corrected the public finding wording.
  Directory findings show the artifact ID and the recorded artifact path (the manifest
  text as written); a nonconforming recorded path may be echoed as written, and the
  resolved scan root is never echoed. Noted that `inspect`'s existing untracked-file
  findings may list member relative paths after a failed scan exactly as without the
  flag, and that `--redact-manifest-path` is JSON-only while text headers may show the
  manifest path (pre-existing). Test-only coverage was added for a real tmpfs refusal,
  real `ext4` digest mismatch and inspect untracked coverage. No code, route, schema or
  policy change.
- **v0.4.6 (S3b public opt-in):** added `--directory-artifacts` to `validate` and
  `inspect` only, described as experimental read-only directory-artifact checking. It
  passes the existing internal switch, adds no route and leaves the default output
  unchanged. Public directory findings now use the artifact ID, the recorded artifact
  path and fixed reason/class text; the scanner's verbose exception text is not copied
  into findings. Still read-only, `ext4`-only on Linux, with no authoring or writing and
  no Linux R5 evidence.
- **v0.4.5 (S3a promotion):** promoted `ext4` into `RELEASE_QUALIFIED_FSTYPES` after the
  accepted ext4 qualification matrix (test evidence only; the runtime never reads it;
  earlier matrix `f25f5a8b...`, accepted S2 final `d82a1752...`). On Linux, with a passing
  backend/facility probe, `native_scan_support().supported` is now true. Every other
  filesystem, including other candidates, is still refused per root as
  `unsupported-filesystem`. This resolves the S1 support erratum for ext4. At v0.4.5 the
  S3b public `--directory-artifacts` flag was still unauthorized; v0.4.6 adds it without
  changing the default (without the flag, `validate`/`inspect` still report a directory
  record as `not-a-file`).
- **v0.4.4 (S2c taxonomy alignment):** aligned §3.5 with the accepted S2c taxonomy: a
  regular file with `st_nlink == 0` is `unstable` (unlinked/disappeared during the scan;
  refused before read and no digest), while `st_nlink > 1` remains `unsafe` under the
  hardlink policy. This is a documentation alignment to the accepted code taxonomy, not a
  new design change.
- **v0.4.3 (S2c erratum):** recorded that an ordinary rename-out-and-back can be
  invisible when all compared metadata is restored within one coarse timestamp tick, and
  that the S2b test synchronizes to a new tick rather than treating that timing as a
  security guarantee. Recorded the S2c code taxonomy change: a regular file observed with
  zero links is `unstable` (unlinked but open), while more than one link stays `unsafe`
  (hardlink policy), and the related ABA test synchronization/erratum. This is a code
  taxonomy change, not a documentation-only statement.
- **v0.4.2 (S1 erratum):** while `RELEASE_QUALIFIED_FSTYPES` is empty,
  `native_scan_support().supported` is false on Linux even with all facility probes
  passing; backend/facilities are still reported. Added the S2a resolution requirement,
  annotated the supported-true expectation, and recorded the ext4-minimum S1 matrix
  artifact as unpackaged test evidence.
- **v0.4.1 (final narrow correction):** made the post-open mount check read a **fresh**
  `/proc/self/mountinfo` table and validate the proven root mount id there, not in the
  preflight snapshot; added the same-numeric-id replacement counterexample; noted that the
  probe requests `STATX_MNT_ID`, not `STATX_MNT_ID_UNIQUE`.
- **v0.4 (third review corrections):** defined topmost stacked-mount resolution and
  post-open mount-id/fstype verification; made qualification policy an explicit
  argument/`ContextVar` with a private shipped symbol and candidate-only seam; corrected
  the `/mnt` prefix example; made fdinfo failure terminal only when statx also fails;
  clarified G1 per-filesystem evidence, probe-fd ownership, S2b ownership of new fixtures,
  Linux root-FS skip behavior in edit (a) and guards on existing FIFO/socket tests.
- **v0.3 (second review corrections):** added a non-circular filesystem qualification
  bootstrap (release gate plus private qualification seam); made the root-FS fixture
  ancestor-aware; specified component-boundary-exact mount-point matching; tightened
  `open_file` so non-FILE probes never open data; replaced the `mount is not None` gate
  with a backend capability flag and fail-closed `mount=None` handling; clarified overlay
  lower-layer qualification, `/proc` facility-role errors, test-edit ownership and
  `_Info` mount consistency; corrected the skip-table double-count footnote and minor
  wording.
- **v0.2 (review corrections):** tightened mount-id proof and ancestor scope; preserved
  the existing `_Walk` check order and error precedence; restricted the name profile to
  the artifact-relative root and members; removed coarse-timestamp filesystems; added
  ancestor filesystem preflight and bounded ancestor spelling; specified optional
  `_Info`/`_Entry` fields; made the filesystem allowlist explicit; fixed `/proc` failure
  classification; corrected skip accounting to at most 398 closable and 16 remaining;
  enumerated required test edits; fixed special-file/root error expectations; specified
  the `_Failure` mapping extension; fixed installed-vector checks; split S2 into
  S2a/S2b.
- **v0.1 (initial design):** race-safe Linux scanner design, digest semantics, CLI/API
  gates, security/privacy, test plan, slices and residuals.

## 0. Decision in one paragraph

Add a Linux-only POSIX backend to the existing native directory scanner, using
component-at-a-time, no-follow directory-relative opens, an `O_PATH` type probe before any
data open, inode/device/mount identity checks, and the existing two-pass stability
detector. Reuse the existing `runevidence-directory-v1` records and byte stream with no
format change. Make backend selection automatic and fail closed, but keep public
directory validation and all directory authoring behind separate explicit gates. The
design is race-safe against ordinary local mutation and symlink-swap attacks, not against a
hostile process that can restore metadata, a privileged writer, an ABA tree change, or a
concurrent mount-namespace change. On current evidence it can close at most **398 of the
414 platform/native-scanner skips**; **16 platform skips** and the **119 Windows R5
evidence skips** remain, and there is no Linux R5 evidence and no proven cross-platform
byte-identical directory digest.

## 1. Goal, scope and success criteria

### 1.1 Goal

Make native directory scanning and directory-artifact validation work on Linux without
weakening the Windows backend, the R5 evidence contract, the v1 digest format, or any
accepted Stage 3 behavior.

“Available” is deliberately split:

1. **Library/internal availability:** `scan_directory()` returns real records and a real
   v1 digest on Linux; the existing internal opt-in directory checks, proposals, previews
   and advisory audits work on Linux. This is the primary goal.
2. **Public read-only availability:** an explicit opt-in on `validate`/`inspect` exposes
   directory validation without changing the default. S3b implements it as
   `--directory-artifacts`; it is experimental, read-only and `ext4`-only on Linux.
3. **Public directory authoring/writing:** remains **NO-GO** under S3P3P7. This RFC does
   not change that.

### 1.2 In scope

- A Linux POSIX backend for `src/runevidence/directory_scanner.py`.
- Backend selection, capability reporting and fail-closed behavior.
- Race/TOCTOU analysis, symlink and special-file policy, mount/filesystem policy.
- Digest/manifest semantics for Linux scans and interaction with existing directory
  artifacts.
- Test design, skip accounting, implementation slices, gates and residual risks.
- A future, explicit read-only CLI opt-in design with no new route.

### 1.3 Out of scope

- Any production, test, schema, example, README or progress change in this task.
- Windows backend changes or new Windows evidence.
- macOS support. The private POSIX prototype may be reused, but macOS is not covered by
  this RFC.
- A new digest format, Unicode names, case-folded names, metadata in the digest, or
  directory nesting records.
- Public `add`/`rehash` for directories, directory writes, cooperative-lock changes,
  SQLite activation, or CAS/ACL claims.
- A Linux R5 evidence lane. The R5 writer explicitly validates only Windows current-user
  `LocalAppData\Temp`; the Linux probe produced 19 passed / 102 failed for that reason.
  That is a separate design.

### 1.4 Success criteria

A Linux implementation is acceptable only when all of the following are demonstrated:

1. The three fixed vectors in `tests/data/directory_digest_vectors.json` are reproduced on
   a real Linux filesystem, including the empty-directory vector.
2. Existing portable tests remain unchanged and pass.
3. Native Linux tests cover root, symlink, hardlink, FIFO/socket, permission, case,
   Unicode, limit and race cases.
4. The Windows backend and all Windows/R5 skip conditions are byte-for-byte untouched.
5. The skip reduction is measured explicitly, and no test is skipped or deleted to make a
   count look better.
6. No public route, default check, schema or writer behavior changes before its own gate.
7. Installed wheel/sdist checks pass with no new dependency.

## 2. Current behavior inventory

### 2.1 Platform split

| Concern | Windows today | Linux today | Portable? |
| --- | --- | --- | --- |
| Backend selection | `_WindowsBackend` for `sys.platform == "win32"` | Raises `unsupported-platform` | Selection is platform-specific |
| Root open | `NtCreateFile` relative to a parent handle, `FILE_OPEN_REPARSE_POINT`, no delete sharing | Private `_PosixBackend` exists but is not selected | No |
| Member open | NT relative handle; files opened without write sharing | Private prototype uses `openat`-style `dir_fd`, `O_NOFOLLOW`, `O_DIRECTORY`, `O_NONBLOCK` | Prototype only |
| Links | Reparse points refused as root, ancestor or member | Prototype checks `lstat`/`O_NOFOLLOW`; not selected or natively covered | Policy is portable; mechanism is not |
| Hardlinks | `nNumberOfLinks == 0` unstable; `> 1` unsafe | Prototype/backend checks the same zero/unstable, more-than-one/unsafe taxonomy | Yes |
| Special files | Device/reparse attributes; non-disk `GetFileType` -> special | Prototype maps non-D/F/L modes to `SPECIAL` | Yes |
| Mount/volume | Fixed local NTFS drive only; volume serial identity | Prototype compares `st_dev`; no mount-id check | No |
| Case behavior | Case-insensitive NTFS; wrong-case root spelling refused | Case-sensitive or insensitive depending on filesystem | No |
| Name profile | ASCII-only v1 profile in the shared encoder | Same shared encoder | Yes |
| Digest | `runevidence-directory-v1` | Same encoder available, but no native scan | Yes |
| Public commands | No directory checking or authoring; internal opt-in only | Same | Yes |
| Tests | Windows-only native evidence on Windows 11 / Python 3.14 | Native tests skip; 414 platform/native skips on Linux baseline | No |

The `directory_hashing.py` encoder, the `Artifact` schema rules, and the internal
proposal/apply/preview/audit/batch/present modules are portable. The missing piece is the
one that reads a real tree into records.

### 2.2 What is Windows-specific

- `NtCreateFile` relative opens with `FILE_OPEN_REPARSE_POINT`.
- Holding every folder without delete sharing, and files without write sharing. This is
  what makes the Windows scan block renames, deletes, writes and hard-link insertion into
  the scanned tree for the duration of the scan.
- Fixed-drive, NTFS-only and volume-serial checks.
- Windows spelling rules (case-insensitive aliases, junctions, reparse points).
- Windows-only tests: held handles block rename/write, sharing-violation `unstable`, DACL
  behavior, `winerror` path behavior.

### 2.3 What is portable

- `DirectoryLimits`, `DirectoryRecord`, `DirectoryDigest`, `check_member_path`,
  `encode_directory`, `directory_digest`.
- The ASCII name profile, protected names, reserved Windows device names, depth/path/size
  limits and case-collision rule.
- `_Walk` two-pass stability logic, `_Info` shape, `_Failure` classification and the
  `DirectoryScanError` reason vocabulary.
- `checks/files.py` opt-in directory path, `checks/directory_policy.py`,
  `directory_authoring.py`, `directory_apply.py`, `directory_preview.py`,
  `directory_plan.py`, `directory_audit.py`, `directory_batch.py`,
  `directory_present.py`, `directory_review.py`.
- The v1 digest byte stream and its three fixed vectors.

### 2.4 The existing POSIX prototype

`_PosixBackend` already has the right shape: it starts at `/`, walks one component at a
time with `dir_fd`, uses `O_NOFOLLOW`/`O_DIRECTORY`, checks `fstat` identity after open,
uses `O_NONBLOCK` for files and compares `(st_dev, st_ino)` for identity. It is retained
but not selected.

It is not yet sufficient for qualification:

- `sys.platform` selection still refuses Linux.
- Root lookup lists the parent and classifies a case-variant as `unsafe` even on a
  case-sensitive filesystem, where the correct result is `missing`.
- There is no `O_PATH` probe, so a name swapped to a device before `open()` could invoke a
  device open.
- There is no mount-id check; `st_dev` alone misses bind mounts of the same filesystem.
- There is no validated mount-id proof (statx mask/nonzero id or parsed fdinfo).
- There is no filesystem-type policy; network and pseudo filesystems are not refused.
- There is no ancestor mountinfo/fstype preflight, ancestor readdir cap or root-component
  cap; a readdir on a network/FUSE ancestor can block or require read permission.
- There is no `/proc/self/fd` reopen-by-handle technique to pin the object before reading.
- There is no timestamp-resolution check and no explicit coarse-timestamp allowlist.
- `/proc` probe failures are not yet normalized to `unsupported-platform`; tests call
  `native_scan_support()` at module level, so an uncaught `OSError` would break collection.
- Native Linux tests, race fixtures, packaging checks and skip accounting do not exist.
- `tests/test_directory_scanner.py::test_posix_and_unknown_platforms_fail_closed`
  currently asserts that Linux is unsupported. Any implementation must revise that
  capability assertion; the full expected test-edit list is in 7.8.

### 2.5 Test and skip inventory (verified on this checkout)

The Stage 3 final suite is recorded as **3193 passed, 533 skipped**. The 533 splits as
**414 platform/native-scanner skips + 119 Windows R5 evidence skips**. The R5 skip hook in
`tests/conftest.py` is basename-scoped to six experimental modules and is unrelated to the
scanner.

A read-only focused inventory on 2026-11-11 (no cache provider, bytecode writing disabled,
temporary basetemp outside the tree) gave:

- 16 directory test modules: **633 passed, 404 skipped**.
- 5 non-directory modules containing the remaining platform markers: **152 passed, 10
  skipped**.
- The two counts sum to the recorded 414 platform skips. This is an inventory, not a full
  suite rerun.

The 404 directory skips split as:

| Group | Count | Meaning | Linux backend closes it? |
| --- | ---: | --- | --- |
| Native scanner unavailable | 399 | Test needs a native scan; sees `unsupported-platform` on Linux | Baseline candidate; at most 398 closable |
| Windows-only directory semantics | 5 | Held-handle sharing violations (2), Windows DACL APIs (2), Windows file-sharing behavior (1) | No; keep skipped on Linux |
| **Directory subtotal** | **404** | | Up to 398 |

The 10 non-directory platform skips split as:

| Group | Count | Meaning | Linux backend closes it? |
| --- | ---: | --- | --- |
| Windows-only path behavior (`winerror`) | 9 | Windows `WinError` behavior in path resolution | No |
| Case-sensitive filesystem branch | 1 | Test is for case-insensitive alias resolution; Linux is case-sensitive | No |
| **Non-directory subtotal** | **10** | | No |

The 414 therefore splits as:

```text
414 = 399 baseline candidate native-scanner skips
    +   5 Windows-only directory semantics
    +  10 non-directory platform skips
```

The verified baseline is 399 candidate skips and 15 platform-only skips. One of the 399
candidates, `test_directory_cooperative_apply.py::test_native_manifest_path_must_be_the_plain_resolved_file[other-case]`,
is itself a Windows case-insensitive behavior case: with a Linux backend it will run and
then skip at its own `os.name != "nt"` guard. It becomes platform skip #16. Therefore the
realistic closure ceiling is **398**, not 399, and the remaining platform count is **16**,
not 15. The full-suite skip count can therefore fall from 533 to at most 135, not 134.

The 119 R5 skips are a third, separate category and are not touched.

Per-module directory skip counts (all from the focused inventory):

| Module | Skips |
| --- | ---: |
| `tests/test_directory_apply.py` | 168 |
| `tests/test_directory_cooperative_apply.py` | 51 |
| `tests/test_directory_scanner.py` | 35 |
| `tests/test_directory_preview.py` | 33 |
| `tests/test_directory_batch.py` | 27 |
| `tests/test_directory_review.py` | 19 |
| `tests/test_directory_plan.py` | 18 |
| `tests/test_directory_authoring.py` | 15 |
| `tests/test_directory_audit.py` | 14 |
| `tests/test_directory_present.py` | 7 |
| `tests/test_directory_writer_races.py` | 6 |
| `tests/test_directory_review_path.py` | 5 |
| `tests/test_directory_check_pipeline.py` | 5 |
| `tests/test_directory_checker.py` | 1 |
| `tests/test_directory_artifacts.py` | 0 |
| `tests/test_directory_policy.py` | 0 |

Do **not** claim “all 414 skips are scanner work”. At most 398 are closable; the baseline
is 399 candidates, and the other 16 are platform evidence gaps that a Linux scanner does
not close. Actual closure must be measured with `-rs` after implementation, because a few
candidate tests create symlinks, hardlinks, FIFOs or sockets and may still skip if the host
or sandbox refuses the fixture.

### 2.6 Three different things that must not be conflated

1. **Native scanner skips (399 baseline; at most 398 closable):** the backend refuses
   Linux. This RFC addresses these, except the one `other-case` test that is itself
   Windows-only behavior.
2. **Windows/R5 evidence skips (119):** the experimental writer intentionally validates
   only Windows `LocalAppData\Temp`; the Linux probe confirms the refusal is not just a
   missing environment variable. A Linux R5 lane is a separate design and is not created
   here.
3. **Platform-only tests (15 baseline; 16 after the `other-case` candidate remains a
   skip):** Windows sharing/DACL/`winerror` behavior and case-insensitive-filesystem
   branches. These are not scanner gaps and must remain skipped on Linux unless a
   genuinely different POSIX test is added. Do not rewrite them to claim parity.

## 3. Race-safe scan design

### 3.1 Principles

1. **One name at a time from an already-open directory handle.** Never open a full path.
   Never use `..`, `.` or an empty component. Start at `/` and traverse the validated
   absolute components.
2. **No-follow by construction.** `O_NOFOLLOW` on every component; an `O_PATH` probe
   identifies the object before anything is opened for data.
3. **Identity before content.** Compare device, inode, type, link count and timestamps
   between the listing, the probe and the data handle.
4. **Fail closed.** Missing facilities, unknown filesystems, mount ambiguity, invalid
   names and special files refuse. There is no path-based fallback.
5. **Detect, do not pretend to freeze.** Two complete passes and per-object before/after
   checks detect ordinary mutation. They cannot make the tree atomic.
6. **No writes.** The scanner opens read-only/O_PATH handles only. It does not create,
   chmod, lock, unlink or truncate.

### 3.2 Traversal algorithm

The caller (authoring/check layer) applies `check_member_path` to the artifact-relative
root path before calling the scanner. The scanner applies it to relative member paths. It
does **not** apply the name profile to absolute ancestor components: an ancestor may
contain non-ASCII characters or a name such as `CON` without making the artifact unsafe.
The root is not encoded in the digest.

For a validated absolute root:

0. **Preflight before any open or listing.**
   - Reject a non-absolute path, a path containing empty/`.`/`..` components, a NUL, and
     the filesystem root `/` itself.
   - Cap root components at 256; more is `limit-exceeded`.
   - Read `/proc/self/mountinfo` once and parse mount id, mount point and filesystem type.
     Mount points are octal-escaped; decode only the fields needed (including octal escapes
     in the mount point used for prefix matching) and refuse malformed or ambiguous records
     rather than guessing. An unreadable or malformed
     `/proc/self/mountinfo` is a facility failure:
     `unsupported-platform` (or `unsupported-filesystem` if the mount table exists but
     cannot identify a type), never `missing` or `unstable`.
   - For every ancestor up to and including the artifact root, determine the covering
     mount by longest mount-point prefix and check its filesystem type against the exact
     allowlist in 3.6 **before** opening or listing it. This avoids a readdir on a network,
     9p or FUSE ancestor and avoids requiring read permission just to discover its type.
     An ancestor whose type cannot be established is refused.
   - Mount-point prefix matching is **component-boundary exact**: `mp == path`, or
     `path.startswith(mp + "/")`. The root mount point `/` is special-cased to match every
     absolute path. `/mnt` matches `/mnt`, `/mnt/data` and `/mnt/database` (the last is a
     child path), but not `/mntdata`. A mount at `/mnt/data` matches `/mnt/data/x` but not
     `/mnt/database`. `/foo` matches `/foo` and `/foo/bar` but not `/foobar`. If no mount
     entry matches an ancestor, refuse; never assume the `/` mount or a default type.
   - **Stacked mounts at one path (topmost resolution):** when several decoded mount
     points equal the chosen longest prefix, do not take the first, oldest or lowest entry.
     Build the mount graph from mount id and parent id. Select the unique mount that is a
     descendant, through the parent-id chain, of every other candidate at that same mount
     point; that is the topmost visible mount. If there is no unique topmost candidate, or
     the chain is cyclic/ambiguous, refuse. Check the **topmost** mount's filesystem type
     against the effective policy. Never fall back to an allowed lower mount. Exact
     counterexample: `ext4` mount id 36 at `/data`, then `nfs4` mount id 48 stacked over
     `/data`; scanning `/data/art` must resolve the topmost id 48 and refuse the `nfs4`
     type, not select id 36.
   - After the artifact root is opened, obtain its actual mount id from the fd proof.
     **Read `/proc/self/mountinfo` again into a fresh post-open table; do not validate the
     actual id against the preflight snapshot.** An open fd keeps its mount alive, so the
     id cannot be reassigned while the fd exists. Require the actual id to exist in the
     fresh table, to equal the preflight-selected topmost id, its filesystem type to pass
     the **effective** policy, and its decoded mount point to equal the preflight-chosen
     mount point. An unknown id, a different id, a type/fstype mismatch, or a mount point
     mismatch refuses. A lazily detached mount missing from the fresh table refuses.
     A replacement mount that reuses the same numeric id (Linux assigns the lowest free id)
     is caught by the fresh table's fstype/mount-point check, not by id equality.
     The fresh post-open table is authoritative for all subsequent descendants; the
     preflight snapshot is used only to choose the root topmost mount and path policy.
   - Ancestors **above** the artifact root are explicitly exempt from the root mount-id
     equality rule. Only the artifact root and its descendants must share the root's mount
     id. This workspace is under a bind-mounted `/home`; requiring ancestor equality would
     false-reject it. Ancestor mount ids are still recorded for diagnostics.
   - Mount-id proof for the artifact root and descendants is valid only when one of these
     succeeds:
     1. `statx` returns the `STATX_MNT_ID` mask bit **and** a nonzero `stx_mnt_id`; or
     2. `/proc/self/fdinfo/<fd>` contains a `mnt_id:` line that parses as a nonzero
        integer.
     A `statx` success without the mask bit, a zero id, a missing fdinfo line, or a
     non-numeric fdinfo value is not proof. If neither source proves an id, refuse.
     Implementations may use fdinfo as the primary source and statx as an optimization;
     the proof rules are identical either way. The statx request asks for
     `STATX_MNT_ID`, not `STATX_MNT_ID_UNIQUE`; a unique-only reply is not accepted.

1. Open `/` with `O_RDONLY | O_DIRECTORY | O_CLOEXEC`.
2. For each root component, relative to the parent handle:
   - `os.stat(component, dir_fd=parent, follow_symlinks=False)`.
     - `ENOENT` -> `missing`. On a case-sensitive filesystem this is the correct answer
       even if a differently-cased sibling exists.
     - Success -> verify exact on-disk spelling with a bounded readdir of the parent:
       stop as soon as the exact name is seen, and scan at most 4,096 entries per
       ancestor. Exceeding the cap before an exact match is `limit-exceeded`. If the exact
       spelling is absent, a case-variant means the filesystem resolved an alias ->
       `unsafe`; if no case-variant exists, the entry disappeared between the stat and the
       listing -> `unstable`. This check requires read permission on the ancestor;
       `EACCES`/`EPERM` is `unreadable`.
     - `EACCES`/`EPERM` on the stat -> `unreadable`; `ENOTDIR` -> `not-a-directory`;
       `ENAMETOOLONG` -> `limit-exceeded`.
     - Symlink mode -> `unsafe`.
     - Non-directory -> `not-a-directory`.
   - Probe with `O_PATH | O_NOFOLLOW | O_CLOEXEC`, `dir_fd=parent`.
   - `fstat` the probe. If it is a link or not a directory -> `unsafe` /
     `not-a-directory`. Compare its identity with the preceding `stat`.
   - Reopen for reading through `/proc/self/fd/<probe>` with
     `O_RDONLY | O_DIRECTORY | O_CLOEXEC`. The probe pins the inode; the reopen is on the
     kernel's fixed magic link, not an attacker-controlled path. **Do not pass
     `O_NOFOLLOW` on this reopen:** the `/proc/self/fd/<n>` entry is itself a magic link and
     `O_NOFOLLOW` would fail with `ELOOP`. The descriptor is scanner-owned and already
     type-checked. Verify `fstat` identity and type against the probe. An `ENOENT` or
     `ELOOP` returned by the `/proc/self/fd` reopen itself is a **facility failure**:
     `unsupported-platform`, never `missing` or `unstable`. A missing or non-numeric
     fdinfo mount id is **not terminal** if statx supplies valid `STATX_MNT_ID` + nonzero
     `stx_mnt_id`; only when neither source proves the id is it a facility failure.
     `EACCES` on the target descriptor remains `unreadable` for that member.
   - Obtain the mount id from the fd proof (statx or fdinfo). For the last component (the
     artifact root), a proven nonzero mount id becomes the scan baseline, is mandatory,
     and must pass the post-open mountinfo check in step 0 using the **fresh** post-open
     table: the id's record exists in that table, its fstype passes the effective policy,
     and its mount point equals the preflight-chosen topmost mount. For an ancestor above
     the root, the id is diagnostic only and its failure is not fatal, provided the
     preflight established its filesystem type; do not compare it with the baseline. For
     descendants, a proven nonzero id equal to the baseline is mandatory, and the id must
     exist in the fresh post-open table; an id absent from the fresh table is refused.
   - Close the probe and hold the directory handle. Every opened ancestor is held until
     the recursive walk finishes. With a 256-component cap and a 64-level depth cap, the
     descriptor bound is approximately `256 + 64 + 1`, plus one transient probe; it is not
     proportional to tree size.
3. Recursively list a directory:
   - `os.scandir(fd)`; bound the number of entries by the remaining member budget.
     More entries than the budget on the first listing is `limit-exceeded`; more than the
     first listing on the re-list is `unstable`.
   - For each name: reject invalid UTF-8/surrogate escapes and every v1 name-profile
     violation before any open. Reject links and special types that are present at listing
     time before any data open.
   - Detect ASCII case collisions within the folder.
   - Sort by UTF-8 name bytes before visiting, so digest input never depends on readdir
     order.
   - For a directory: probe/reopen as above, require its mount id to equal the root
     baseline (a nested mount is `unsafe`), append a `D` record, recurse, then re-stat and
     re-list. A changed listing or metadata is `unstable`.
   - For a file: `open_file` first probes with `O_PATH | O_NOFOLLOW | O_CLOEXEC` and
     `fstat`s the probe. **It reopens through `/proc/self/fd` only when the probe kind is
     `FILE`.** For `LINK` or `SPECIAL`, it returns `(probe_fd, _Info)` and does **not**
     close the probe; `_Walk.open_checked` closes that returned handle in its existing
     `finally` path before raising. It may instead raise `_Failure("link")`/
     `_Failure("wrong-type")`, in which case the probe is closed by `open_file`. No data
     open occurs in either form. `open_file` does not apply link-count or size policy.
     `_Walk.open_checked` performs the shared link/type/identity checks, and for a
     mount-capable POSIX backend it also requires the mount id to equal the root baseline
     (and refuses `mount=None`; see 5.5). `_Walk.visit_file` then applies the link taxonomy
     (zero links -> `unstable`, more than one -> `unsafe`) and the size bounds and reads the
     content. This preserves existing precedence: a name
     listed as a regular file but swapped to an outside hardlink is `unstable`
     (identity/type check), not `unsafe`; a swap to an oversized file is `unstable`, not
     `limit-exceeded`; a swap to a FIFO or device is `unstable` with zero data opens. A
     link or special type discovered by the open probe for a listed regular/directory must
     surface as `_Info` or `_Failure("link")`/`_Failure("wrong-type")`, never as a direct
     `DirectoryScanError("unsafe")`, so the shared mapper keeps it `unstable`. Read in
     bounded chunks, hash raw bytes, then `fstat` again and require the same type, device,
     inode, link count, size, `mtime_ns` and `ctime_ns`.
4. After the first complete pass, run a second metadata-only pass over the same root.
   Compare the root identity and every record's identity, kind, size, times and link count.
   Any difference is `unstable`, except that a root which is missing, permission-denied, a
   link, not a directory or on an unsupported filesystem at the second `open_root` is
   reported with that root reason (`missing`, `unreadable`, `unsafe`, `not-a-directory`,
   `unsupported-filesystem`; `unstable` for an in-use/replaced root), not silently reused
   and not rewritten as a generic `unstable`.
5. Encode the records with the existing v1 encoder and return the digest.

The second pass is a detector, not a snapshot. It exists to catch changes to parts of the
tree that were already closed, including a file modified after its handle was closed.

### 3.3 TOCTOU windows and what closes them

| Window | Check | Residual |
| --- | --- | --- |
| Root path spelling vs on-disk spelling | Exact readdir name plus identity | Case-insensitive alias is `unsafe`; case-sensitive absence is `missing` |
| Listing name -> object | Listing `stat` identity vs `O_PATH` probe `fstat` | Inode reuse with matching identity and timestamps |
| Object -> data handle | `/proc/self/fd` reopen pins the inode; re-verify identity | Kernel/procfs failure; no fallback, so refuse |
| Symlink present at listing | `visit` rejects `LINK` -> `unsafe`; no target read | None for normal symlinks; magic links under `/proc` are not traversed |
| Symlink swapped in after a regular/directory listing | `open_checked` type/link mismatch -> `unstable`; no target read | A link present only between observations is not in the digest |
| File content during read | Size before/after, chunk count, `mtime_ns`/`ctime_ns` before/after | Same-size overwrite with restored metadata, coarse timestamps or privileged/raw writes |
| Folder contents during visit | Listing before vs after; directory metadata before/after | Rename out and back with identical identity/metadata |
| Between pass 1 and pass 2 | Full metadata identity comparison | ABA with restored metadata; inode reuse |
| Root replaced between passes | Root identity differs -> `unstable` | If identity is forged/reused, ABA remains |
| Root renamed away/unavailable at second pass | Second `open_root` reports the root error: `missing` (or `unreadable`/`unsafe`/`not-a-directory`) | Not forced to `unstable`; the first-pass digest is not returned |
| Ancestor renamed | Open handles pin inodes; later path resolution may fail | The path may no longer name the scanned tree; the digest still describes the pinned inodes |
| Mount added/replaced below root or at root | Root/descendant mount-id comparison; topmost parent-chain resolution; fresh post-open id/fstype/mountpoint match | Ancestors above root are exempt; same-id reuse caught by fresh fstype; mount namespace changes after the check |
| Device/FIFO swapped in after a regular-file listing | `open_checked` type mismatch -> `unstable`; no data open | A kernel bug or nonconforming filesystem |
| Device/FIFO present at listing | `visit` rejects `SPECIAL` -> `unsafe`; no data open | A type that changes between listing and `open_checked` becomes `unstable` |
| Overlayfs `(st_dev, st_ino)` collision | Identity plus type/size/times and mount id | Older overlayfs can synthesize colliding inode numbers for different lower files; residual |

### 3.4 Symlink policy

- **Never follow a symlink**, including a symlink as root, as any ancestor, as a member, a
  dangling link, a cyclic link, a link to an ordinary file, or a link to a directory
  outside the tree.
- `O_PATH | O_NOFOLLOW` returns a descriptor for the link itself; `fstat` reports
  `S_ISLNK`, and the scan refuses before `readlink`, `stat`-follow or data open.
- `/proc/self/fd/<n>` is used only after the probe has confirmed a regular file or a
  directory, and only on a descriptor owned by the scanner. It is not a user path and is
  not subject to symlink substitution by the tree.
- If the platform cannot provide `/proc/self/fd` or an equivalent pinned-handle reopen,
  the backend refuses as `unsupported-platform`; it does not fall back to path opens.

This is stricter than “do not follow links by default”. It refuses links even when they
point inside the tree, because a link target is not part of the v1 name/digest model and
could escape the artifact.

### 3.5 Hardlinks, special files and loops

- A regular file with `st_nlink == 0` is `unstable`: it was unlinked/disappeared while
  the scan was observing it. It is refused before any read and produces no digest. Windows
  sharing makes zero links practically unreachable in the supported Windows backend, but
  the condition is still refused before read rather than treated as a valid single-link
  file.
- A regular file with `st_nlink > 1` is `unsafe`: the hardlink policy refuses both an
  outside hardlink and two names for one file inside the artifact. There is no
  inode-deduplication model in v1.
- A regular file with `st_nlink == 1` is the only accepted case.
- Directory hardlinks are not supported by Linux; the scanner does not relax the file
  rule for directories.
- FIFOs, Unix sockets, block devices, character devices and every other non-D/F/L type are
  `unsafe` and are never opened for data or waited on. The `O_PATH` probe makes the type
  decision without opening the device or blocking on a FIFO; `open_file` reopens only a
  verified `FILE` probe.
- Symlink loops are refused at the link itself; no path resolution loop can occur because
  links are never followed. Mount or bind loops are handled by the mount-id rule below.
- A directory that is a mount point below the root is `unsafe` (see 3.6).

### 3.6 Mount and filesystem boundaries

`st_dev` alone is insufficient: a bind mount of the same filesystem has the same device
number but is a different mount. The Linux backend must obtain a mount id for the artifact
root and for every opened descendant object.

**Proof rules for the artifact root and descendants (both sources must be validated, not
merely present):**

- `/proc/self/fdinfo/<fd>` is a valid primary source only when it contains a `mnt_id:`
  line that parses as a nonzero integer.
- `statx(..., STATX_MNT_ID, ...)` is valid only when the returned mask contains
  `STATX_MNT_ID` **and** `stx_mnt_id` is nonzero. A successful syscall without the mask
  bit, or with `stx_mnt_id == 0`, is not proof. This is the old-kernel/old-filesystem
  case that must be tested explicitly.
- If neither source proves an id, refuse. There is no `st_dev`-only fallback and no
  “assume same mount” default.
- An implementation may use fdinfo as the primary source and statx as an optimization;
  the proof rules are identical.
- The probe requests `STATX_MNT_ID`, **not** `STATX_MNT_ID_UNIQUE`. Unique mount ids use a
  different numbering space and are not interchangeable with the classic mount id.
  A reply that provides only `STATX_MNT_ID_UNIQUE` (mask lacks `STATX_MNT_ID`) is rejected
  by the current fail-closed rules; supporting it would need a separate design.

**Scope:** only the artifact root and its descendants must share the root's mount id. A
nested mount or same-filesystem bind mount below the root is `unsafe`. A mount exactly at
the artifact root is allowed: that root mount becomes the baseline. **Ancestors above the
artifact root are explicitly exempt** from mount-id equality. This workspace lives under a
bind-mounted `/home`; requiring ancestor equality would false-reject it. Ancestor mount
ids are still recorded for diagnostics, and ancestors are still checked for link/type
safety. If an opened object's mount id is absent from the **fresh post-open mountinfo
table**, refuse rather than assuming it belongs to the root mount.

**Stacked mounts at one path:** when multiple mountinfo records share a decoded mount
point, the topmost mount is selected by following the parent-id chain, never by file order,
mount age or the first matching line. The topmost record's type must pass the effective
policy; if it is refused, the scan refuses and never falls back to an allowed lower mount.
After the root fd yields an actual mount id, that id must equal the preflight-selected
topmost id and resolve in the **fresh post-open mountinfo table** to a record whose type
passes the effective policy and whose mount point equals the preflight-chosen topmost
mount. A replacement mount that reuses the same numeric id is caught by the fresh table's
fstype/mount-point check; a lazily detached mount missing from the fresh table refuses. An
open fd keeps its mount alive, so the proven id cannot be reassigned while the fd exists;
the fresh read is still required because the preflight snapshot itself can be stale. A
mount added between preflight and open therefore changes the id, type, presence or chosen
mount point and is refused.

**Filesystem type policy (exact allowlist):**

| Filesystem | Decision | Reason |
| --- | --- | --- |
| `ext4` | Candidate | Local, nanosecond timestamps in normal configurations |
| `xfs` | Candidate | Local, nanosecond timestamps |
| `btrfs` | Candidate | Local, nanosecond timestamps |
| `f2fs` | Candidate | Local, nanosecond timestamps |
| `tmpfs` | Candidate | Local, test fixture filesystem |
| `overlay` | Candidate; unqualified at G1 | Container root; lower-layer caveat below |
| `zfs` | Candidate | Local, nanosecond timestamps |
| `ext2`, `ext3` | Refuse | 128-byte inodes can have one-second timestamp granularity |
| `virtiofs` | Refuse | Host-shared/FUSE-style metadata; not locally authoritative |
| `fuse-overlayfs` | Refuse | Userspace FUSE overlay; metadata not locally authoritative |
| `sshfs` and other FUSE | Refuse | Remote/Userspace; blocking and mutation semantics |
| `squashfs`, `erofs` | Refuse | Read-only image filesystems; untested metadata/timestamp model |
| `iso9660`, `udf` | Refuse | Read-only/removable media, coarse timestamps |
| `exfat` | Refuse | Coarse timestamps, case-insensitive, no hardlink model |
| `ntfs3` | Refuse | Linux NTFS driver semantics differ from the Windows backend; Windows owns NTFS |
| `bcachefs` | Refuse until tested | Local but not in the qualified set; fail closed |
| `nfs`, `nfs4`, `cifs`, `smb3`, `9p`, `ceph`, `afs` | Refuse | Network/host-shared; metadata and availability are not local |
| `proc`, `sysfs`, `cgroup`, `cgroup2`, `devpts`, `devtmpfs`, `debugfs`, `tracefs`, `securityfs`, `configfs`, `fusectl`, `bpf`, `mqueue`, `hugetlbfs`, `pstore`, `autofs`, `ramfs` | Refuse | Pseudo/virtual or unbounded; size/content may be synthesized |

- Every candidate filesystem is **refused at runtime until it is promoted to the release
  allowlist** by a separately reviewed release gate. This RFC does not qualify any
  filesystem by itself.
- Unknown filesystem types are refused, not guessed. This mirrors the Windows backend's
  refusal of non-NTFS drives.

**Qualification bootstrap (non-circular).**

The runtime policy and the qualification runs use separate states, so “allow after matrix”
cannot gate the tests that produce the matrix:

1. `RELEASE_QUALIFIED_FSTYPES` is the only set the production `scan_directory` accepts. It
   started **empty** in S1 and grows only by a reviewed code change after a matrix is
   recorded; S3a promoted `ext4` alone. A candidate absent from this set is refused as
   `unsupported-filesystem`.
2. `CANDIDATE_FSTYPES` names the types a qualification run may exercise: ext4, xfs,
   btrfs, f2fs, tmpfs, overlay and zfs. It is not an allowlist and grants no runtime
   permission.
3. The effective policy is an **explicit `ScanPolicy` argument threaded through
   `scan_directory`/`_scan`/`_Walk`/backend**, or a `contextvars.ContextVar` read at the
   top of `scan_directory` and passed down. It is never a mutated module global. The
   ContextVar default is the release policy; a test token sets the test policy and is
   reset in `finally`, so it does not leak across threads or async tasks.
4. The private seam **ships as a private symbol in the production module** (for example
   `_qualification_for_tests`/`_scan_with_test_policy`), not as test-only code in a
   separate module. It is not exported, not a CLI option, not an environment variable, not
   a config file key and not accepted by `native_scan_support()`. A test must assert that
   the public parser has no such option and that the default policy still refuses the same
   filesystem.
5. The seam accepts only fstypes in `CANDIDATE_FSTYPES`. It must raise for `nfs4`, `ext3`,
   another network/pseudo type, an unknown type, or any string outside the candidate set.
   It must not silently add, normalize or fall back to an allowed type. Tests must prove
   `nfs4` and `ext3` cannot be qualified through the seam.
6. A qualification run records
   `tests/data/directory_scanner_filesystem_matrix.json` as **S1 test evidence** (ext4 at
   minimum), not packaged, with the exact fstype, kernel, mount/lower-layer identity, test
   node IDs, counts and hashes. The runtime never reads `tests/` or this file. G1 requires
   at least one complete matrix. Promotion copies the reviewed result into
   `RELEASE_QUALIFIED_FSTYPES` in a later code change and release gate.
7. The seam can exercise ext4, tmpfs and overlay when the corresponding mounts exist.
   G1 requires **at least one** candidate filesystem with a complete matrix; it does not
   require all three. ext4 is the primary target. On an ext4-only host, tmpfs evidence
   comes from a tmpfs `tmp_path`, `/dev/shm`/`/run`, or an approved temporary tmpfs mount
   if the host permits; overlay evidence is available only when the host path is actually
   overlay and the lower layer is recorded. If a filesystem has no evidence, it remains
   unqualified and is not claimed. The seam does **not** promote anything; overlay remains
   unqualified at G1, and the release allowlist remains fail-closed until per-filesystem
   promotion.

- **False-rejects are acknowledged:** a legitimate artifact on `ext2`, `ext3`, `squashfs`,
  `erofs`, `iso9660`, `udf`, `exfat`, `ntfs3`, `bcachefs`, `virtiofs` or
  `fuse-overlayfs` is refused, as is an allowed artifact root whose ancestor is on a
  refused filesystem (the ancestor preflight refuses the whole scan). Expanding the set
  requires its own metadata/timestamp evidence and review.
- **Overlayfs caveat:** `(st_dev, st_ino)` can collide for files from different lower
  layers on older overlayfs. Mount id is the same, so it does not disambiguate. Identity
  also compares type, size, `mtime_ns`, `ctime_ns` and link count, which reduces but does
  not eliminate the risk. The mount table exposes only the overlay mount, not the
  underlying lower filesystem; a remote or refused lower filesystem cannot be detected
  through this layer. Overlay stays **unqualified at G1**. Promotion requires the matrix
  artifact to record the lower-layer filesystem identity and to show that the lower layer
  is not a refused network/pseudo type. This is an explicit exception to the “local
  filesystems only / no remote mount” claim in 6.1–6.2; it is not a claim that overlay
  makes a remote lower layer safe.

**Timestamp-resolution capability check:**

- The exact allowlist excludes filesystems known to use one-second timestamps. In
  addition, the backend performs a read-only `statx` check on the root and a bounded sample
  of existing entries: `STATX_MTIME` and `STATX_CTIME` must be present, and at least one
  observed `stx_mtime.tv_nsec` or `stx_ctime.tv_nsec` must be nonzero. If every sample is
  zero, the filesystem's resolution is unproven and the scan refuses as
  `unsupported-filesystem`.
- This check can false-reject a nanosecond filesystem whose sampled timestamps all happen
  to fall exactly on a second boundary. That is acceptable fail-closed behavior and must
  be documented.
- The check cannot prove that a filesystem never truncates, and the scanner does not write
  a probe file. A same-size overwrite that restores `mtime` can still be invisible if
  `ctime` granularity is coarse or the writer is privileged. This residual is stated in
  section 3.13 and is not claimed as detected.

**`/proc` failure classification and deployment limit:**

- `/proc/self/mountinfo` unreadable, malformed or absent at scan time ->
  `unsupported-platform`; if the mount table exists but the root's type cannot be
  identified -> `unsupported-filesystem`. It is never `missing` or `unstable`.
- `/proc/self/fd` reopen unavailable, unreadable or missing at scan time ->
  `unsupported-platform`, not a member `missing`/`unstable` error. This reopen is
  mandatory.
- `/proc/self/fdinfo` unavailable, unreadable or missing is terminal **only when statx
  also fails to provide a valid `STATX_MNT_ID` + nonzero `stx_mnt_id` proof**. A valid
  statx result makes a missing/non-numeric fdinfo id acceptable. If neither proves the id,
  the result is `unsupported-platform`.
- `native_scan_support()` must catch `OSError` and any probe failure and return
  `supported=False` with a reason. It must never raise during module import/collection;
  tests call it at module level.
- `/proc` itself is a pseudo filesystem and is refused as a scan root. A deployment
  without a mounted `/proc` (minimal container, chroot) is unsupported for this backend;
  it is not silently handled by path opens.
- The `/proc` fstype check is defense-in-depth: a path under `/proc` is refused by both
  the path/mount policy and the fstype allowlist.

### 3.7 Concurrent writers and locking

POSIX has no mandatory sharing modes equivalent to the Windows backend. The Linux backend
does **not** claim the Windows guarantees:

- A writer can open a scanned file for writing while the scanner reads it.
- A process can rename, delete, create or hardlink inside the tree.
- A directory can be moved out of the root and back.

The scanner detects ordinary changes through identity, `size`, `mtime_ns` and `ctime_ns`
checks and the second pass. It does not use `flock`/`fcntl` as a safety claim: advisory
locks bind only cooperating processes, can be ignored, and would change the scanner's
behavior without preventing a nonparticipating writer. It does not use inotify/fanotify as
a snapshot mechanism: events can be lost, coalesced or arrive after the read.

This is the same trust posture as the existing directory documentation: a quiet tree and
one cooperating writer. It is not hostile-writer containment.

### 3.8 Permission and error classification

Preserve the existing `DirectoryScanError.reason` vocabulary. If the implementation adds
`_Failure` codes `limit` or `resource`, it must extend **both** `_ROOT_REASON` and
`_MEMBER_REASON` and the `_Failure` docstring in the same change. A new code that is passed
to `_root_error`/`_member_error` without a mapping will raise `KeyError` and abort the scan.
Alternatively, the backend may raise `DirectoryScanError("limit-exceeded", ...)` or
`DirectoryScanError("unreadable", ...)` directly for those errnos. Either choice is
acceptable; the design does not leave an unmapped code.

| Condition | `_Failure` code | Root reason | Member reason |
| --- | --- | --- | --- |
| `ENOENT` | not-found | `missing` | `unstable` (disappeared) |
| `EACCES`, `EPERM` | denied | `unreadable` | `unreadable` |
| `ENOTDIR`, `EISDIR` | wrong-type | `not-a-directory` | `unstable` (changed type) |
| `ELOOP` | link | `unsafe` | `unstable` (became a link) |
| `EXDEV` | unsupported | `unsupported-filesystem` | `unsafe` (another volume) |
| `EBUSY`, `ETXTBSY` | in-use | `unstable` | `unstable` |
| `ENAMETOOLONG` | limit (extend maps) or direct raise | `limit-exceeded` | `limit-exceeded` |
| `EMFILE`, `ENFILE`, `ENOMEM` | resource (extend maps) or direct raise | `unreadable` | `unreadable` |
| `EIO`, `ESTALE` | other | `unreadable` | `unreadable` |
| `EOPNOTSUPP` | unsupported | `unsupported-filesystem` | `unsafe` |
| Missing `/proc` facility | none (direct raise) | `unsupported-platform` | `unsupported-platform` |
| `EINTR` | retried by Python | — | — |

`EACCES` is not bypassed by retrying with different flags or by falling back to a path
open. If the caller cannot read a member, the scan fails; it never records a partial tree
as if it were complete.

The scanner runs with the caller's existing permissions. It does not elevate, use
capabilities or change ACLs. Tests that depend on denying read access are skipped when the
test user is root or the host refuses the permission change; those skips are environment
gaps, not capability evidence. The same applies to a test root on a refused filesystem:
native tests skip with an explicit reason; they must not fail as 399 collection/setup
errors.

### 3.9 Case sensitivity and spelling

- v1 names are printable ASCII only. Case folding is ASCII-only, matching the encoder.
- Artifact-relative root: exact on-disk spelling is required. On a case-insensitive
  filesystem, `stat` may resolve a differently-cased name; the readdir exact-name check
  then refuses `unsafe`. On a case-sensitive filesystem, a missing exact name is `missing`,
  even if a differently-cased sibling exists.
- Absolute ancestors above the artifact root are not name-profile checked; their exact
  spelling is checked for traversal identity, not against the v1 profile.
- Members: names always come from `readdir`, so there is no user-supplied case alias.
  Within each folder, two names that differ only in ASCII case are `collision`.
- The scanner does not attempt to “fix” case. It does not lowercase paths and then scan.
- A case-sensitive Linux filesystem can contain both `Model.bin` and `model.bin`; that tree
  is refused as a collision, matching the portable v1 rule.

### 3.10 Unicode, invalid names, hidden files

- `check_member_path` applies to relative member paths and to the artifact-relative root
  path (checked by the authoring/check caller before scanning). It does **not** apply to
  absolute ancestor components. An ancestor named `CON`, `café` or containing a space does
  not make the artifact unsafe; it is simply path. The root name is not encoded in the
  digest. This matches the Windows backend, which checks root components structurally and
  applies the profile to relative members.
- Every member component must be printable ASCII `0x20`–`0x7e`, with no forbidden
  `< > : " / \ | ? *`, no empty/`.`/`..`, no trailing space or dot, no Windows device
  names, and no protected `.git`/`.hg`/`.svn`/`.venv`/`venv`/`__pycache__`/RunEvidence
  manifest or backup names at any depth.
- There is no Unicode normalization. NFC, NFD and mixed forms are all rejected because
  they contain characters outside the profile. Latin-1, emoji, combining marks and
  non-ASCII whitespace are rejected, not normalized or transliterated.
- On Linux, `os.scandir` with a text path decodes bytes using the filesystem encoding with
  `surrogateescape`. Names that are not valid UTF-8 therefore appear with surrogate code
  points and fail the printable-ASCII check. The implementation must not silently replace
  or re-encode them; the scan refuses `unsafe`.
- Hidden files are included: any leading-dot name that is not in the protected set is an
  ordinary member. `.gitignore`, `.config`, `.data` and similar names are accepted if they
  satisfy the profile. `.git`, `.hg`, `.svn`, `.venv`, `venv` and `__pycache__` are
  refused at any depth.
- Supporting legitimate Unicode names requires a new, separately versioned digest token
  and a new name profile. It is not part of this RFC.

### 3.11 Ordering determinism

- Folder entries are sorted by their UTF-8 name bytes before traversal.
- `encode_directory` sorts records by the raw UTF-8 bytes of the relative path and rejects
  duplicate paths and missing parents.
- For the ASCII-only v1 profile, byte order is well-defined and locale-independent.
- Read order, inode order, directory hash order and platform readdir order cannot change
  the digest.
- A scan is deterministic for a stable tree: repeating it produces the same records and
  digest. A changing tree is `unstable`, not a different “valid” digest.

### 3.12 Size, time and resource bounds

Existing hard logical bounds (a caller may tighten, never raise):

| Bound | Value |
| --- | ---: |
| Depth below root | 64 |
| Members (records, excluding root) | 10,000 |
| Total file bytes | 1 GiB |
| Per-file bytes | 256 MiB |
| One relative path, UTF-8 | 1,024 bytes |
| Total path bytes | 4 MiB |
| Encoded canonical stream | 8 MiB |
| Root components (absolute path) | 256 |
| Entries scanned per ancestor for exact spelling | 4,096 |

The last two are operational safety caps, not v1 digest limits. A caller cannot raise them
through `DirectoryLimits`.

Implementation properties:

- Only one file is open for content at a time. Directory handles are held along the
  recursion path, so descriptor use is bounded by depth, not by member count.
- Descriptor bound: root components (at most 256) plus recursion depth (at most 64) plus
  `/` and one transient probe, approximately `256 + 64 + 1`. It is independent of member
  count.
- Ancestor exact-spelling search stops at the exact name and scans at most 4,096 entries
  per ancestor. More than the cap before an exact match is `limit-exceeded`. This caps
  readdir work on a huge ancestor directory but cannot prevent a blocking readdir on a
  refused filesystem; the preflight in 3.2/3.6 is intended to refuse those before listing.
- Content is read in 1 MiB chunks and hashed incrementally; the scanner never allocates a
  whole file.
- Records are bounded at 10,000; path bytes and the encoded stream are bounded before the
  stream is built.
- The second pass is metadata-only and does not re-read file contents.
- There is **no wall-clock or CPU deadline**. A tree that is within the logical bounds can
  still take a long time to hash (up to 1 GiB of file bytes, plus metadata), especially on
  a slow local filesystem. Network and pseudo filesystems are refused to remove the worst
  unbounded remote case.
- A future optional deadline would be a separate operational API/gate. If added, it must
  refuse with an existing error class (for example `limit-exceeded`), must never return a
  digest, and must not make digest values depend on machine speed. It is not part of v1.
- Memory use is bounded by records/path bytes plus one 1 MiB read buffer. No measured
  memory ceiling is claimed by this RFC.

### 3.13 What cannot be made atomic

The following are explicit limits, not implementation defects to be hand-waved away:

1. **No tree snapshot.** There is no kernel primitive that atomically reads a whole
   directory tree and its file bytes. The digest describes the objects the scanner
   observed across a time interval.
2. **No freeze.** Linux does not give an unprivileged reader the Windows backend's
   no-delete/no-write sharing behavior. Writers are not blocked.
3. **ABA changes.** A tree can be changed and restored between observations. If identity
   and all compared metadata are restored, the change is invisible. `ctime` makes this
   harder but not impossible for a privileged writer or a nonconforming filesystem.

   **v0.4.3 note (2026-11-11):** an *ordinary* rename-out-and-back can already be
   invisible if every compared field, including `st_mtime_ns` and `st_ctime_ns`, is
   restored within one coarse timestamp tick. Linux stamps `ctime` from a coarse clock,
   so two renames inside a single tick can leave the directory metadata bit-identical to
   the first pass. The S2b test synchronizes: before renaming back it waits, with a
   bounded monotonic deadline and no fixed sleep, until the directory's own metadata has
   advanced beyond the first-pass value; if the host clock cannot advance in that window
   the test skips with an explicit environment reason. That is test synchronization, not
   a security promise: a same-tick ABA remains undetected and outside the hostile-writer
   model.
4. **Inode reuse.** A deleted inode can be reused by a new object with the same device and
   inode number. Identity plus type, link count, size and `ctime_ns` makes this unlikely;
   it is not a generation number guarantee.
5. **Mount namespace changes.** A process with mount privileges can change what a path
   means. Comparing mount ids detects a changed mount at the time of the check; it does not
   freeze the namespace.
6. **Manifest vs tree.** The manifest save path is separate. Even a perfect scan is
   followed by a non-atomic source check and `os.replace`; S3P3P7 remains in force.
7. **Malicious kernel/filesystem.** A hostile kernel, FUSE implementation or root-level
   writer is outside the model. So is a device that lies about its own size or content.
8. **Cross-platform “sameness”.** Two filesystems can present the same names and bytes
   differently through case, Unicode or metadata. v1 reduces this by rejecting non-ASCII
   and case collisions, but it does not prove byte-identical scans across platforms.
9. **Coarse or restored timestamps.** The ns-timestamp check is read-only and best-effort.
   A same-size in-place overwrite that restores `mtime` can be invisible if `ctime`
   granularity is coarse or the writer can use privileged/raw access. The exact allowlist
   reduces the known coarse-filesystem cases, but does not prove detection.
10. **Ancestor read permission and readdir cost.** Exact-spelling verification reads each
    ancestor directory. A traversal-only ancestor (execute but no read) is `unreadable`,
    and an allowed-but-huge ancestor is capped at 4,096 entries. Preflight refuses refused
    filesystem types before listing, but cannot eliminate all blocking on a misbehaving
    local filesystem or a race in the mount table.

## 4. Hash, digest and directory-manifest semantics

### 4.1 Representation

Linux uses the existing `runevidence-directory-v1` representation with no change:

```text
b"RunEvidence.directory\x00v1\x00"
u64 number of member records          # root itself has no record
for each record, sorted by raw UTF-8 path bytes:
    b"D" || u32 path length || path                    # directory, empty payload
    b"F" || u32 path length || path || 32-byte SHA-256 # file contents
```

All integers are unsigned big-endian. The artifact's `sha256` is the lowercase hex SHA-256
of the whole stream. File digests are raw SHA-256 of file bytes, not hex text. Directory
records carry no digest and size zero.

### 4.2 Empty directories and the root

- Every folder below the root, including an empty folder, has a `D` record. An empty
  folder is not omitted.
- The root has no record of its own. An empty root scans to zero records and the digest of
  the header plus a zero count; this is the existing “empty” vector and must work on Linux.
- `members` counts `D` and `F` records, excluding the root. `size_bytes` is the sum of file
  sizes; directories contribute zero.
- Moving or renaming the root does not change the digest, because no absolute path or root
  name is encoded. This is existing behavior and must remain true.
- The artifact-relative root path is profile-checked by the caller before scanning, but it
  is not encoded as a record. Absolute ancestor names are not profile-checked and are not
  encoded. This aligns 3.10 with the digest representation and with the Windows backend.

### 4.3 Metadata exclusion

The digest covers relative names and file bytes only. It does **not** cover:

- permissions, owner/group, ACLs, capabilities or security labels;
- `mtime`, `ctime`, `atime`, birth time;
- inode number, device number, mount id or link count;
- extended attributes, alternate data streams, sparse layout or allocation size;
- symlink targets, device numbers, socket/FIFO identity;
- filesystem type, case sensitivity or locale;
- empty-directory timestamps or metadata.

Those values are used as change detectors and refusal inputs, not as digest content. A
scan that cannot establish a safe value refuses rather than dropping it silently.
Coarse timestamps reduce the strength of the before/after detector; they are not included
in the digest and cannot be compensated for by encoding them.

### 4.4 Reproducibility and parity

- Same relative names and same file bytes -> same canonical stream -> same digest,
  independent of root location, readdir order and machine.
- Raw bytes are hashed. There is no newline conversion, text decoding, whitespace
  normalization or Unicode normalization. A CRLF file and an LF file differ.
- The encoder is platform-independent, so cross-platform digest equality for a portable
  tree is the design intent and a test hypothesis. It is **not** an accepted claim:
  - Windows native evidence exists only for Windows 11 / Python 3.14.
  - Linux native evidence does not exist yet.
  - Windows case-insensitivity and Linux case-sensitivity can produce different tree
    preconditions (for example, `A`/`a` cannot coexist on NTFS; on Linux they cause a
    collision refusal).
  - Empty-root behavior and filesystem-specific quirks have not been compared.
- Do not write “cross-platform byte-identical directory digests are supported” until a
  shared immutable fixture is scanned natively on both platforms and the digest, size and
  member count match, with the exact platform/Python versions recorded.

### 4.5 Interaction with existing directory artifacts

- Existing 0.1/0.2 manifests with `artifact_type: "directory"` and
  `digest_format: "runevidence-directory-v1"` are unchanged. Linux scans validate the
  recorded `sha256` and, when present, `size_bytes`.
- A recorded digest with no recorded size still passes on a digest match, as today. A
  missing digest is the existing `directory-no-digest` warning, not a Linux-specific
  behavior.
- A volatile directory is never scanned. There is still no safe verification model for it.
- A result with a `locator` on a directory remains `directory-locator-unsupported`.
- Nested directory artifacts remain unsupported; the existing overlap rules stay strict.
  This RFC does not add a nesting precedence model.
- File artifacts inside a directory remain independently checked. Their bytes are
  vouched for twice: by the file record and by the directory digest. This is existing
  behavior and is not changed.
- `rehash` changes only `sha256` and `size_bytes`; `add` appends one directory entry. On
  Linux these internal operations now work when the native scanner works, but they remain
  internal and non-public.
- No manifest schema field, digest format token or migration is added.
- Non-ASCII names and Unicode normalization are out of scope. If they become a requirement,
  a new format token (for example a v2 profile) and a new migration/validation decision are
  required.

## 5. CLI and API integration

### 5.1 Backend selection: automatic, not user-selected

- `_native_backend()` keeps `sys.platform == "win32"` first and selects the Windows backend
  exactly as today. It then selects Linux explicitly (`sys.platform.startswith("linux")`).
  There is no `--backend`, `--posix` or `--force` switch.
- **S1 erratum (resolved by S3a for ext4):** while `RELEASE_QUALIFIED_FSTYPES` is empty,
  `native_scan_support().supported` is **False on Linux even when every backend and
  facility probe passes**. S3a now promotes `ext4`, so on a passing Linux host
  `supported` is true for the release policy. A root on any other filesystem is still
  refused per root as `unsupported-filesystem`, and root qualification is never widened
  by the capability report.
- `native_scan_support()` reports `supported=True` only when (a) the backend/facility
  probes pass and (b) at least one filesystem is release-qualified (S3a: `ext4`).
  `supported=True` means the backend facilities and qualification gate exist, not that
  every root filesystem is accepted; `scan_directory` can still refuse
  `unsupported-filesystem` for a particular root.
- `native_scan_support()` wraps every `OSError`, `/proc` read, `statx` probe and mountinfo
  parse in a catch-all that returns `supported=False` with a reason. It must not raise
  during module import/collection; tests call it at module level to compute skip markers.
- Missing `O_PATH`, `O_NOFOLLOW`, `O_DIRECTORY`, `O_CLOEXEC`, `/proc/self/fd`, mount-id
  proof or the mountinfo policy -> `unsupported-platform` or `unsupported-filesystem`. No
  path-based fallback.
- Mount-id checks and the `_Info`/`_Entry` optional mount fields are POSIX-only. Shared
  `_Walk` behavior must preserve Windows exactly: the Windows backend leaves mount fields
  unset, and the Windows path never performs a mount-id comparison.
- The gate is an explicit backend capability flag (for example
  `supports_mount_identity: bool`), **not** `_Info.mount is not None`. When the flag is
  true, `_Walk` must refuse if `info.mount is None` (raise `DirectoryScanError(
  "unsupported-platform")` or fail an internal assertion in tests); it must never skip the
  check. When the flag is false (Windows, FakeFS default), `None` is expected and no
  comparison occurs. `_Walk` must not infer the capability from `sys.platform`.
- POSIX `_Info` construction from listing/`stat`/`fstat`/`open_file` must populate the same
  mount value for the same object. If `open_checked` sees a different mount than
  `check_unchanged`/`stat`, every file would falsely become `unstable`; the capability
  flag and the consistent `_Info` value are part of the same contract.
- macOS and other POSIX systems remain refused by this RFC. The private prototype is not
  silently generalized.
- The effective filesystem policy is an explicit argument or a `contextvars.ContextVar`
  passed down through the scan; it is never a mutated module global. The ContextVar default
  is the release policy and test tokens are reset in `finally`; no thread/task leakage.
- The filesystem qualification seam is a private shipped symbol used by tests. No public
  CLI option, environment variable, config key or `native_scan_support()` argument can
  enable it. A parser test asserts this; exposing it would be a release blocker.

### 5.2 Public surface: explicit opt-in, no new route

The default commands must not change merely because Linux gains a backend. Today
`validate` and `inspect` treat a directory record as a file and report `not-a-file` unless
the internal `enable_directory_artifacts=True` switch is passed. Changing that default
would alter accepted Stage 3 behavior and could surprise existing manifests.

| Surface | Today | After Linux backend (S1/S2a/S2b) | S3b (implemented) |
| --- | --- | --- | --- |
| `scan_directory` (library) | raises `unsupported-platform` on Linux | works on release-qualified filesystems; candidates via private qualification seam; may still refuse `unsupported-filesystem` | same |
| `native_scan_support` | reports unsupported | S1: reports backend/facilities but `supported=False` while no filesystem is release-qualified; S3a: `supported=True` on a passing Linux host because `ext4` is promoted; under an approved S2a test gate `supported=True`; never qualifies a root by itself | same |
| `check_files(..., enable_directory_artifacts=True)` | internal only; refuses on Linux | works on release-qualified filesystems; tests use the seam | same |
| `validate` | no directory opt-in; `not-a-file` | unchanged | optional `--directory-artifacts` read-only flag |
| `inspect` | no directory opt-in; `not-a-file` | unchanged | same explicit flag; untracked coverage as internal |
| `validate-batch` | no directory opt-in | unchanged | unchanged unless separately reviewed |
| `init` | adapter-based file manifest | unchanged; no directory artifacts | unchanged |
| `add` / `rehash` | file-only | unchanged | unchanged |
| directory authoring/apply/preview/audit/batch/present | internal only | internal only; may run on Linux | internal only; no public command |
| `schema`, `migrate`, `conditions`, other routes | unchanged | unchanged | unchanged |

No new route is justified. The existing `validate`/`inspect` commands are the correct
read-only surfaces, and a flag changes only whether directory records are checked. A new
`scan` or `directory` route would add parser/pin surface, a second output contract and a
new privacy review for no user benefit.

S3b uses the flag named `--directory-artifacts`, described as
experimental/read-only. It is not added to `add`/`rehash`, and it cannot enable
writes. `validate-batch` is intentionally left out because its
summary/exit-code contract would need its own review.

### 5.3 Behavior details for the read-only flag

- `validate --directory-artifacts`: pass the existing internal switch to `run_checks`.
  Directory findings use the existing codes (`missing-directory`, `not-a-directory`,
  `directory-unsafe`, `directory-unreadable`, `directory-unstable`,
  `directory-limit-exceeded`, `directory-path-collision`, `directory-hash-mismatch`,
  `directory-size-mismatch`, `directory-no-digest`,
  `directory-locator-unsupported`). No new code is introduced.
- `inspect --directory-artifacts`: same check plus the existing untracked-file coverage
  rule. A successful directory scan covers exactly the files it recorded; a failed,
  volatile or rejected directory covers nothing. `inspect` then lists those member
  relative paths in its existing `untracked-file` findings, exactly as it does without
  the flag; those are not directory diagnostics.
- A directory never becomes a readable JSON/file path. `ok_paths`/`readable_paths` behavior
  stays as the internal contract.
- Exit codes, `--strict`, `--summary-only`, `--json` and `--redact-manifest-path` keep
  their existing meanings. The flag does not make a directory a file and does not change
  comparability or condition checks; those still reject directory artifacts.
- No output field is added. Directory findings do not emit member lists, per-member names
  or file contents; the pre-existing `untracked-file` findings are separate (see §5.4).

### 5.4 Output and privacy implications

The scanner's internal `DirectoryScanError` may contain a relative member path and a short
reason. The public directory finding surface must not leak more than the existing
contract:

- Show the artifact ID and the recorded artifact path (the manifest text as written). A
  recorded path that fails the strict relative portable-path profile is rejected before
  scanning and may be echoed as written; that is user manifest text, not the resolved scan
  root.
- Show a fixed reason/class and, where already established, the finding code.
- Do not echo the resolved absolute scan root, `/proc` paths, OS `strerror` text, member
  names, file bytes, inode/device numbers or mount ids in a directory finding.
- Do not include a newly computed digest in an error message. A digest that is already in
  the manifest may be shown as existing output does.
- Do not write scan diagnostics to logs, telemetry or network destinations. There is no
  network code and no daemon.
- `--redact-manifest-path` keeps its pre-existing JSON-only behavior: it requires
  `--json`, and text headers may still show the manifest path. Directory findings add no
  new manifest path to redact, and a redacted JSON `manifest` field must remain redacted
  even when a scan error occurs.
- `inspect` keeps its existing untracked-file findings. After a failed, volatile or
  rejected directory scan it may list member relative paths there, exactly as without the
  flag; those findings are not directory diagnostics and the flag does not add them.

The internal `DirectoryScanError` text may still include the absolute root in some
messages, which is why S3b sanitizes at the finding boundary rather than weakening the
internal exception contract. The scanner and its library callers keep verbose messages;
only the public findings use fixed reason/class text. Output as a whole is not path-free:
the manifest path (and its JSON redaction) and the pre-existing untracked findings remain.

### 5.5 API shape

- No new public function is required. `scan_directory(root, limits=DEFAULT_LIMITS)` and
  `native_scan_support()` remain the API.
- `DirectoryScan` and `DirectoryScanError` keep their shapes.
- The backend is an implementation detail. Callers must not be able to select a weaker
  path-based backend.
- The scanner remains synchronous and side-effect-free. It does not accept callbacks,
  network handles or a user-provided filesystem object.
- If `_Info` or `_Entry` needs a mount id, it is an **optional trailing field with a
  default**, for example `mount: Optional[int] = None`. Existing `_Info(kind, identity,
  size, times, links)` and `_Entry(name, kind, identity)` constructions in `FakeFS` and
  the Windows backend remain valid. No required field may be added. The mount check lives
  in the POSIX backend or in a clearly gated POSIX branch, not in shared Windows behavior.
  A FakeFS edit to add the field is allowed only with the same trailing default and must
  be listed in the expected test changes.
- The shared `_Walk` gate is the backend's explicit mount-capability flag. If the flag is
  true and `_Info.mount is None`, that is a hard refusal (`unsupported-platform`) or an
  assertion failure in tests, not a reason to skip the check. If the flag is false,
  `mount=None` is valid. `mount=None` must never be used to infer “no mount check”.
- POSIX `stat`/listing/`fstat` `_Info` values must carry the same mount id as `open_file`
  for the same object. Otherwise `check_unchanged` compares different values and marks
  every file `unstable`. The fake backend must set consistent values or keep both `None`.

### 5.6 Packaging and installed checks

- `pyproject.toml` already packages `src/runevidence`; no new dependency, data file or
  entry point is needed.
- Python >= 3.10 is the support floor. The design uses `O_PATH`, `dir_fd`, `scandir(fd)`
  and `/proc/self/fd`, all available on supported Linux/Python combinations. The capability
  probe must fail closed on an older kernel or restricted container, not crash.
- Installed checks must import the installed package, call `native_scan_support()`, scan a
  synthetic tree, reproduce the vectors, and run `pip check`. In S1, `supported=False` was
  expected and recorded; after S3a, a passing Linux host with an ext4 tree expects
  `supported=True` and a real scan. If no candidate filesystem has been promoted to
  `RELEASE_QUALIFIED_FSTYPES`, the installed scan must use the private qualification seam
  from the installed test harness or wait for the release gate; it must not claim a
  production scan on an unqualified filesystem.
- `tests/data/directory_digest_vectors.json` is **not packaged** in the wheel/sdist. An
  installed-package check must either copy the vector file to an approved temporary
  location outside the package and read it from there, or compute the three expected
  vectors independently in the check script. It must not import `tests/` or assume the
  repository checkout is present.
- `tests/data/directory_scanner_filesystem_matrix.json` is S1 test evidence (ext4 at
  minimum) and is also **not packaged**. G1 requires at least one complete matrix; the
  runtime does not read it.
- No repository build artifacts are permitted by this RFC; any future build output belongs
  under an approved temporary directory and is evidence only.

## 6. Security and privacy

### 6.1 Trust model

Authorized use is a local caller who may read the manifest and the tree and who has
authorized disclosure of the result. The scanner:

- reads local files only, with one explicit exception: an overlay root's lower layer is
  not visible to the mount policy. Overlay is unqualified at G1 and promotion requires the
  lower-layer filesystem to be recorded and non-network/non-pseudo; the scanner does not
  make the lower layer safe by itself;
- makes no network connection, DNS lookup, socket or remote mount; it does not initiate or
  mount anything. A pre-existing overlay whose lower layer is remote is outside the
  policy's visibility and is not a supported configuration;
- does not elevate privileges, set capabilities, change ownership or ACLs;
- does not write, create, delete, lock or chmod anything;
- assumes a quiet tree and one cooperating writer;
- does not claim protection from a hostile kernel, FUSE server, root user or a writer that
  restores metadata.

### 6.2 Threats and controls

| Threat | Control | Residual |
| --- | --- | --- |
| Path traversal via `..`, absolute paths, backslashes | Strict v1 root profile; component validation; root join under the manifest folder | A caller-supplied manifest root is still caller-selected; the manifest is the authority |
| Symlink escape from root or member | `O_NOFOLLOW`; `O_PATH` probe; refuse all links; pinned `/proc/self/fd` reopen | None for normal symlinks; kernel/procfs failure refuses |
| Magic-link substitution (`/proc/self/fd`, `/dev/fd`) | Only scanner-owned fds are reopened; absolute components that are symlinks are refused; pseudo filesystems refused | A malicious kernel is out of scope |
| Hardlink alias to outside content | `st_nlink == 1` for regular files; case-collision check | A hardlink swapped in after listing is `unstable` under existing precedence; one created and removed entirely between observations is not in the digest |
| Device/FIFO/socket side effects | `O_PATH` type probe before data open; present at listing -> `unsafe`; swapped after listing -> `unstable` | A nonconforming filesystem/kernel |
| Mount escape or bind-mount substitution | Root/descendant mount-id equality with proof; mountinfo type policy; ancestors above root exempt | Mount namespace changes after a check; root-level mount authority |
| Old-kernel mount-id proof | Require `STATX_MNT_ID` mask plus nonzero id, or parsed nonzero fdinfo; refuse if neither | A kernel that reports a wrong nonzero id |
| Resource exhaustion | Hard logical limits; chunked hashing; one file open; depth-bounded descriptors; 256 root components; 4,096 ancestor entries | Time/CPU is not bounded; a within-limit hostile tree can be slow |
| Hostile names | ASCII profile for relative members/root; invalid UTF-8/surrogates refused; reserved/protected names refused | Absolute ancestor names are not profile-checked, by design |
| Hostile tree with changing content | Identity/metadata checks and second pass | ABA, restored metadata and coarse timestamps |
| Concurrent writer | Detection, not prevention | No mandatory locking; no Windows sharing guarantee |
| `/proc` absent or restricted | `native_scan_support` catches; scan refuses `unsupported-platform` | Deployment without `/proc` is unsupported |
| Information leakage in output | Directory findings: fixed reason/class and recorded artifact path only (a nonconforming path may be echoed as written); no resolved absolute root, bytes or OS text. `inspect`'s pre-existing untracked-file findings may still list member relative paths | Internal library callers can see verbose exceptions |
| Network/remote access | No network code; network filesystems refused | A user-mounted local-looking FUSE filesystem is refused by type policy; overlay lower-layer visibility is the documented exception and overlay is unqualified at G1 |
| Unauthorized write | Scanner is read-only; public directory writes remain NO-GO | Internal apply/preview code is not called by public commands |

### 6.3 Denial of service

The scan can consume CPU and I/O proportional to the bounded tree. It cannot loop on a
symlink, allocate a file-sized buffer, recursively descend without depth bound, or open an
unbounded number of file descriptors. It also caps root components at 256 and ancestor
readdir work at 4,096 entries per ancestor. It can still take a long time on a large but
within-limit local tree. The explicit read-only flag (S3) is the user's opt-in to pay that
cost. A future deadline is an operational addition, not a v1 digest semantic.

### 6.4 Authorization and privacy

- Local-only: no service mode, no remote API, no upload.
- The scanner does not authenticate the tree or the manifest. A successful scan is not a
  provenance or authenticity certificate.
- Output sensitivity: the digest and size can reveal equality/difference of otherwise
  private data. Treat directory validation output as sensitive evidence metadata.
- Shell history, process arguments, CI logs and host audit records are outside the
  redaction boundary. `--redact-manifest-path` does not erase those records.

## 7. Test plan

### 7.1 Layer A: unchanged portable tests

- `tests/test_directory_artifacts.py` (encoder, name profile, limits, vectors) must pass
  unchanged. The vector file is pinned by its own SHA-256
  (`cbdaf1e07db967f1083c8fa94cf1e293911a31f0a301e8e488ac874626b932f1`).
- `tests/test_directory_policy.py` must pass unchanged.
- No schema, model, CLI parser or default-check test is expected to change. The
  enumerated directory test edits in 7.8 are separate and are not schema/default changes.

### 7.2 Layer B: backend selection and capability

| Case | Expected |
| --- | --- |
| Linux with all facilities | S3a: stable POSIX backend/facilities reported and `supported=True` because `ext4` is release-qualified; a non-ext4 root is still refused as `unsupported-filesystem` |
| Missing `O_PATH`/`O_NOFOLLOW`/`O_DIRECTORY`/`O_CLOEXEC` | unsupported; `scan_directory` raises `unsupported-platform` |
| No `/proc/self/fd` reopen | unsupported; no path fallback |
| `/proc/self/fdinfo` missing or non-numeric, but valid statx `STATX_MNT_ID` + nonzero id | accepted via statx |
| `statx` success without the `STATX_MNT_ID` mask bit, but valid fdinfo | accepted via fdinfo |
| Both statx mask/id and fdinfo proof unavailable | refuse; no `st_dev`-only claim |
| statx returns `STATX_MNT_ID` with `stx_mnt_id == 0` | not proof; fall back or refuse |
| statx provides only `STATX_MNT_ID_UNIQUE` (mask lacks `STATX_MNT_ID`) | not proof; reject; unique ids are not interchangeable |
| fdinfo `mnt_id: 0`, missing, empty or non-numeric | not proof; fall back or refuse |
| `/proc/self/mountinfo` missing/unreadable/malformed | `unsupported-platform`/`unsupported-filesystem`; never `missing`/`unstable` |
| `native_scan_support()` gets `OSError` from any probe | returns unsupported with a reason; does not raise during collection |
| Root and all ancestors allowed by the effective policy | scan proceeds under that policy; S3a: the release policy allows ext4 roots, so `native_scan_support().supported` is true on a passing host |
| Any ancestor on 9p/virtiofs/fuse-overlayfs/NFS/unknown/refused type | `unsupported-filesystem`, tests skip with an ancestor-aware root-FS reason |
| Mount-capable backend returns `_Info.mount is None` | hard refusal (`unsupported-platform`) or test assertion; never skip the check |
| Non-mount backend (Windows/FakeFS) with `mount=None` | valid; no mount comparison |
| macOS/Darwin | unsupported (unless a separate RFC says otherwise) |
| Unknown platform | unsupported |
| `os.name == "nt"` | Windows backend unchanged; Windows selected before Linux |

**S1 support erratum (resolved by S3a):** with `RELEASE_QUALIFIED_FSTYPES` empty,
`native_scan_support().supported` was `False` on Linux even when every backend/facility
probe passed. The backend name and facilities were still reported. Native tests using the
module-level `native` marker therefore skipped in S1; this was deliberate and not a
capability claim. S1 matrix evidence came from the private-seam qualification harness
(ext4 at minimum). S3a now promotes ext4 from the accepted matrix, so a passing Linux host
reports `supported=True`; the per-root `unsupported-filesystem` refusal is unchanged and
every non-ext4 type is still refused at runtime.

The existing `test_posix_and_unknown_platforms_fail_closed` must be revised so the
`scan_directory` `unsupported-platform` assertion applies **only to Darwin and unknown
platforms**. On Linux, the test must scan a real tree (under the private qualification seam)
and assert the native result, not expect a refusal. This is a capability assertion change,
not a skipped test and not a semantic relaxation.

Mount-id tests must use fake probes, not the host kernel's actual statx/fdinfo:

- `fake_statx_ok_without_mnt_id_mask` (old-kernel shape) must require the fdinfo fallback,
  and must refuse when that fallback is also absent.
- `fake_fdinfo_missing`, `fake_fdinfo_non_numeric`, `fake_fdinfo_zero` must fall back to a
  valid statx result or refuse; they must never be treated as mount id 0.
- Valid statx `STATX_MNT_ID` + nonzero id with missing/non-numeric fdinfo is accepted; the
  mandatory `/proc/self/fd` reopen still happens. Only when both proof sources fail is the
  result `unsupported-platform`.
- A statx reply that provides only `STATX_MNT_ID_UNIQUE` is rejected; unique ids are not
  treated as classic mount ids.
- A fake mount table with the same filesystem type but different mount ids for an
  ancestor above the root must scan successfully (ancestor exemption).
- A fake mount table with a different mount id for a descendant must be `unsafe`.
- A mount-capable backend whose `_Info.mount` is `None` must be refused/asserted; a
  non-mount backend with `None` must remain valid.
- A fake backend must return the same mount value from listing/`stat` and `open_file` for
  the same object; a deliberately inconsistent fake must fail the consistency test.
- Component-boundary prefix matching: with mounts `/mnt` and `/`, a path under
  `/mnt/data` **and** `/mnt/database` selects `/mnt` (both are children); `/mntdata` must
  not match `/mnt`; with a mount at `/mnt/data`, `/mnt/database` must not match; with
  mounts `/foo` and `/`, `/foobar` must not match `/foo`; a path with no covering mount
  must be refused, not assigned `/`.
- Stacked mounts: with `ext4` id 36 at `/data` and `nfs4` id 48 stacked over `/data`
  (parent-id chain 48 -> 36), scanning `/data/art` must select id 48, see `nfs4`, and
  refuse. It must not select id 36 or any first/oldest entry. If two entries at the same
  mount point have no unique topmost parent chain, refuse as ambiguous.
- Mount added between preflight and open: preflight chooses `ext4` id 36; before the root
  fd is read, `nfs4` id 48 is mounted over the same path. The root fd's actual id 48 must
  be looked up in the **fresh post-open table**, fail the id/effective-policy/mount-point
  match, and refuse. An id absent from the fresh table also refuses.
- Same numeric id reuse: preflight chooses `ext4` id 36 at `/data`; after a replacement
  mount, the fresh post-open table has `nfs4` id 36 at `/data` (Linux reused the lowest
  free id); the root fd proves id 36. The check must consult the fresh table, see `nfs4`
  at `/data`, and refuse. It must not validate against the preflight snapshot and accept
  `ext4`.
- Lazily detached mount: the root fd proves id X but the fresh post-open table no longer
  contains X; refuse.
- A real root under a bind-mounted ancestor (`/home` is bind-mounted on this workspace)
  must scan successfully when the host permits the fixture. If the host cannot create the
  bind mount, use the fake equivalent and record the real case as environment-gated.

### 7.3 Layer C: native Linux scanner tests

Run on a real filesystem under `tmp_path`, but gate the whole native layer on the
**ancestor-aware** root filesystem policy first. The scanner calls in this layer use the
private qualification seam; the fixture uses the same effective policy for every
ancestor. A session/module fixture must inspect the
host filesystem type for every absolute ancestor of `tmp_path` (not only `tmp_path`) and
must reuse the scanner's own preflight/policy helper, or apply the same exact policy and
**same effective qualification state as the scan** to the whole chain. If any ancestor is
refused by that effective policy, the native tests **skip with an explicit reason naming
the ancestor and type** rather than failing as collection/setup errors. Concrete case:
under a policy where `/tmp` is tmpfs (allowed) but `/` is an unqualified overlay (refused),
the fixture must skip, not let the scanner refuse the ancestor after 399 tests have
started. Under the G1 seam that explicitly allows both tmpfs and overlay, the same chain
runs and G1 is satisfiable. This is a root-filesystem gate, not a reduction of the
398-candidate accounting.

- Empty root, one file, nested tree, empty nested directories.
- All three fixed vectors: digest, size, member count and exact encoded bytes.
- Root move/rename keeps the digest.
- Raw file bytes (including CRLF, NUL, bytes 0–255) hash exactly.
- Files spanning multiple read chunks.
- Adding/modifying/renaming/removing a member changes the digest.
- Scanning does not modify the tree (metadata snapshot excluding atime).
- Committed fixture under `examples/generic-experiment/data` is unchanged.
- Root problems: missing, missing parent, file, file below a file, relative path, `..`,
  `/`, trailing slash, NUL.
- Root component cap: more than 256 components -> `limit-exceeded` before any scan.
- Ancestor readdir cap: an ancestor with more than 4,096 entries before the exact name ->
  `limit-exceeded`; an exact name found early stops the search.
- Ancestor name profile: a root under a non-ASCII or reserved-name ancestor (`café`,
  `CON`, a name with a space) scans successfully when the ancestor path is traversable;
  the ancestor is not profile-checked and is not encoded.
- Wrong-case root: `missing` on a case-sensitive filesystem; `unsafe` where the
  filesystem resolves the alias.
- Disallowed names: `.git`, `venv`, `__pycache__`, nested `runevidence.json`, backup names,
  non-ASCII, control characters, trailing dot/space, reserved device names, backslash,
  colon.
- Invalid UTF-8 member name: create it through raw bytes APIs
  (`os.open(bytes_path, ...)` / `os.mkdir(bytes_path)`), let `os.scandir` return the
  surrogate-escaped text, and expect `unsafe`. Do not construct `Path("bad\ud800")`; path
  construction itself can fail before the scanner sees it.
- Case collision `A`/`a` on a case-sensitive filesystem; expected `collision`.
- Limits: depth, members, per-file, total-file, path bytes, total path bytes, encoded
  bytes; each tight limit refuses and the boundary value passes.
- Entry member/root/ancestor symlink (file, directory, dangling, cyclic) -> `unsafe`.
- Hardlinked file present at listing -> `unsafe`; no read.
- FIFO -> `unsafe`; process does not block. Guard the existing FIFO test's `os.mkfifo` in
  `try/except OSError` and skip with an explicit reason if the host refuses it; do not add
  a duplicate only.
- Unix socket -> `unsafe`; guard the existing test's `socket.socket(socket.AF_UNIX)`
  constructor and `sock.bind(...)` in `try/except OSError`, skipping with an explicit
  reason if either fails.
- Character/block device: create only where the host permits; otherwise skip and report
  the environment gap. Do not use a real device.
- Unreadable file/folder: chmod 0 on non-root; expect `unreadable`; skip as root and report
  why.
- Large directory enumeration (for example 900 entries with long names) reads all members
  and stays within limits.
- Timestamp-resolution check: a fake/coarse backend whose `statx` times are second-
  granularity must be refused as `unsupported-filesystem` when every sampled nsec is zero.
  A same-size overwrite with restored `mtime` remains a documented residual, not an
  assertion that it is detected.
- File-swapped-to-FIFO/device-before-`open_file`: a name listed as a regular file but
  replaced by a FIFO or device before `open_file` probes it must be refused with **zero
  data opens**. `open_file` does `O_PATH`+`fstat`, sees `SPECIAL`, and returns without
  reopening. Expected shared-walk reason is `unstable`; a special type present at listing
  is `unsafe` before opening.

Filesystem-policy fake tests (mountinfo and mount-id probes are injected; no privileged
mount is required):

| Injected root fstype | Expected |
| --- | --- |
| `ext4` | **Release-qualified by S3a** (accepted matrix `d82a1752...`); other candidates refused unless promoted, or allowed by the private qualification seam |
| `xfs`, `btrfs`, `f2fs`, `tmpfs`, `zfs` | Candidate; refused unless promoted to release-qualified, or allowed by the private qualification seam |
| `overlay` | Candidate; unqualified at G1; qualification seam only; promotion requires recorded lower-layer FS |
| `ext2`, `ext3` | `unsupported-filesystem` |
| `virtiofs`, `fuse-overlayfs`, `fuse.sshfs`, other FUSE | `unsupported-filesystem` |
| `squashfs`, `erofs`, `iso9660`, `udf`, `exfat`, `ntfs3`, `bcachefs` | `unsupported-filesystem` |
| `nfs`, `nfs4`, `cifs`, `smb3`, `9p`, `ceph`, `afs` | `unsupported-filesystem` |
| `proc`, `sysfs`, `cgroup2`, `devpts`, `devtmpfs`, `debugfs`, `tracefs`, `securityfs`, `configfs`, `fusectl`, `bpf`, `mqueue`, `hugetlbfs`, `pstore`, `autofs`, `ramfs` | `unsupported-filesystem` |
| Unknown fstype | `unsupported-filesystem` |
| Known allowed fstype but no valid mount-id proof | Refuse; no `st_dev`-only fallback |

Qualification-seam and ancestor-aware fixture tests:

- The private seam allows ext4/tmpfs/overlay for a test scan; for tmpfs and overlay (and
  for ext4 before S3a) the default release policy refused them. The same scan without the
  seam is refused on a root whose filesystem is not in the effective policy.
- The seam raises for `nfs4`, `ext3`, another network/pseudo type, an unknown type or any
  string outside `CANDIDATE_FSTYPES`; it cannot qualify them.
- The public CLI has no option, flag, environment variable or config key that enables the
  seam; assert this from the parser and the default policy.
- The same effective qualification state is used by the root-FS fixture for every
  ancestor; a simulated `/tmp` tmpfs + unqualified `/` overlay chain skips, not errors.
- A pre-recorded matrix artifact is validated for shape/hash but is not consulted by
  runtime; promotion is a release-gate code change.

A native root on any refused fstype must skip the native layer with an explicit
root-filesystem reason. It must not fail 399 tests, and it must not be reported as scanner
coverage.

### 7.4 Layer D: race and injection tests

Keep the existing in-memory `FakeFS` tests; they test decisions, not OS safety. Add
real-object timing tests in the style of `SwapBefore`:

- Directory swapped for a symlink just before probe -> `unstable` (listed directory,
  opened link); a symlink present at listing -> `unsafe`. No target read in either case.
- File swapped for a hardlink to an outside file just before probe -> `unstable`; no read.
  This is the existing precedence and those tests must remain unchanged. In particular,
  `test_native_file_replaced_just_before_open_is_not_read`,
  `test_injected_hardlinked_file_is_refused_before_reading` and the existing oversized-file
  policy tests keep their expected reasons.
- File swapped for an oversized file just before probe -> `unstable`, not
  `limit-exceeded`; size policy is applied only after `open_checked`.
- File replaced by a different file just before probe -> `unstable`.
- File replaced after probe but before data read -> old pinned inode read; second pass
  detects the new object.
- File grows/shrinks during read -> `unstable`.
- File metadata changes during read -> `unstable`.
- Member added while a folder is read -> `unstable` at that folder.
- Member added to a full tree -> `unstable`, not a false `limit-exceeded`.
- Root replaced between passes -> `unstable`.
- Root renamed away before the second pass -> second `open_root` reports `missing` (not
  `unstable`); add the analogous `unreadable`, `unsafe` and `not-a-directory` second-pass
  root-error cases.
- Directory renamed out and back between passes -> detected if metadata/identity differs;
  document the ABA case as a residual.
- Mount-id mismatch for a descendant (fake backend) -> `unsafe`; a mismatching ancestor
  above the root does not fail the scan.
- Mount-capable backend with `_Info.mount is None` -> hard refusal/assertion, never a
  skipped check. Non-mount backend with `None` -> valid.
- Fake backend returns different mount values from `stat`/listing and `open_file` for the
  same object -> the consistency check fails (normally `unstable`); the real POSIX
  `_Info` must not do this.
- File swapped to FIFO/device before `open_file` -> zero data opens; `open_file` returns
  after the `O_PATH` probe and `_Walk` reports `unstable`.
- Inode reuse (fake backend) -> `unstable` when identity/ctime differs; document the
  residual if all compared fields are forged.
- Special-file substitution (fake backend): present at listing -> `unsafe`; swapped in
  after a regular-file listing -> `unstable`; no read call in either case.
- `_Failure` mapping coverage: fake `ENAMETOOLONG`, `EMFILE`, `ENFILE`, `ENOMEM` must not
  raise `KeyError`; they map to `limit-exceeded`/`unreadable` as specified.
- Overlayfs identity collision (fake backend with identical `(st_dev, st_ino)` for two
  different members) must be documented as a residual; if the design chooses to reject
  the ambiguity, assert `unsafe` rather than silently accepting both.

These tests use synthetic/disposable trees only. They do not prove hostile-writer
containment; they prove the scanner's checks fire at the intended boundaries.

### 7.5 Layer E: integration

- `check_files`/`run_checks` with `enable_directory_artifacts=True` on Linux:
  - digest/size match;
  - digest mismatch, size mismatch, missing digest warning;
  - volatile never scanned;
  - missing/not-a-directory/unsafe/unreadable/unstable/limit/collision mapping;
  - locator unsupported;
  - untracked coverage exactly matches recorded files;
  - file artifacts inside the directory keep their own checks;
  - directory root never appears in `ok_paths`/`readable_paths`.
- `directory_authoring` add/rehash, `directory_apply` dry-run/checked apply,
  `directory_preview`, `directory_plan`, `directory_audit`, `directory_batch` and
  `directory_present` on Linux. These may write only disposable synthetic manifests under
  a test temp directory. No public command is invoked.
- Existing writer-race characterization remains as is. The Linux backend does not claim to
  fix S3P3P7; tests must not turn a partial characterization into a green write gate.

### 7.6 Layer F: parity with Windows semantics

- Run the same immutable portable fixture on Windows and Linux; compare digest, size and
  member count. Record exact OS, filesystem, Python and backend.
- Run the same error taxonomy fixture on both: missing root, file root, symlink,
  hardlink, special file, depth/member/byte limits, case collision. Compare the reason
  codes (not necessarily identical OS detail text).
- Do not port Windows sharing/DACL tests to Linux and do not mark them as passing. Those
  remain Windows-only evidence.
- No cross-platform byte-identical claim until the shared fixture passes on both. If it
  fails, record the divergence and keep the formats/claims separate rather than weakening
  v1.

### 7.7 Layer G: Linux CI

A future approved CI lane should:

- use `ubuntu-latest` with Python 3.10, 3.12 and 3.14 (or the project's supported matrix);
- run `tests/test_directory_*.py` plus the existing portable suite;
- set `PYTHONDONTWRITEBYTECODE=1` and `-p no:cacheprovider` if repository cleanliness is
  part of the gate;
- run unprivileged; mount/device fixtures are optional and separately marked;
- report `passed/failed/skipped/errors` and list every skip reason, including the root
  filesystem gate and environment fixture skips;
- not require network, a Windows runner or `R5_EVIDENCE_ROOT`.

This RFC does not create CI configuration. Hosted CI is not claimed until it runs.

### 7.8 Skip-vs-run accounting

Baseline and expected ceiling:

| Category | Baseline | After Linux backend (ceiling) | Notes |
| --- | ---: | ---: | --- |
| Candidate native-scanner skips | 399 | 0–398 | Only actual passes count; host-refused fixtures may remain |
| `other-case` candidate reclassified as platform behavior | 0 | 1 | Windows case-insensitive behavior; runs then skips on Linux |
| Windows-only directory semantics | 5 | 5 | Keep unchanged on Linux |
| Non-directory platform skips | 10 | 10 | 9 `winerror`, 1 case-insensitive branch |
| **Platform subtotal** | **414** | **16–414** | Ceiling assumes 398 candidates run and pass |
| Windows R5 evidence skips | 119 | 119 | Separate design; not touched |
| **Total suite skips** | **533** | **135–533** | Report actual, not the ceiling |

Footnote: the 399 baseline **already includes** `other-case`. The `0–398` after-range is
`399 - 1`, not a second category. The `other-case` row records a reclassification of one
baseline test from “native scanner unavailable” to “platform behavior”; it is not an
additional test and must not be added as `398 + 1`.

Rules:

- Run `-rs` before and after; record exact node IDs and reasons. Do not report only a total.
- A test that fails is fixed or classified; it is never skipped to improve the count.
- Do not delete, rename or rewrite Windows/R5 tests to reduce skips.
- The 399-candidate baseline and the 15-platform baseline stay visible. The practical
  closure is at most 398 because `other-case` is a Windows behavior test, not scanner work.
- Environment-created symlink/hardlink/FIFO/socket/device/permission cases that skip are
  environment gaps, not closure. Report them separately.
- The R5 hook in `tests/conftest.py` and the six R5 module names stay untouched.
- The 119 R5 skips and the 16 platform skips remain explicit limitations in the final
  report. Do not describe a 135-skip run as full platform qualification.
- If the actual reduction is less than 398, report the exact remaining nodes. The RFC's
  ceiling is not a promise.

**Expected test-contract changes and new fixtures (complete list).** These are reviewed
changes, not skip suppression. No test may be deleted or silently skipped to avoid a
failure.

(a) `tests/test_directory_scanner.py::test_posix_and_unknown_platforms_fail_closed`: the
    `scan_directory` `unsupported-platform` assertion applies only to **Darwin and unknown
    platforms**; on Linux the test must scan a real tree under the private qualification
    seam and assert the native result. It must use the ancestor-aware root-FS skip fixture,
    so a refused ancestor skips with a reason rather than failing the Linux branch.
(b) `tests/test_directory_authoring.py::test_native_proposals_read_only_through_the_scanner_and_write_nothing`:
    the `FORBIDDEN` list currently forbids `os.open`, `os.scandir`, `os.listdir` and
    `os.walk` outright. It must allow read-only scanner opens/scans while still forbidding
    every write, replace, rename, remove, mkdir, temp-file and manifest-writer path. The
    test's intent (no writes) must remain.
(c) `tests/test_directory_batch.py::test_native_cross_add_checked_path_collisions` for
    `('data', 'DATA')`: the current `alias = os.name == 'nt'` branch is Windows-only. On
    Linux the two names are distinct directories and the casefold overlap rule may raise
    `path-collision`/`batch-overlap`. The test must either be Windows-only for the alias
    case or assert the actual POSIX behavior; it must not assume Windows semantics.
(d) `_Info`/`_Entry`/`FakeFS` (S1 only): if mount fields are added, use optional trailing
    fields with defaults (`mount: Optional[int] = None`) so existing FakeFS constructors
    and Windows backend calls remain valid. A FakeFS edit is allowed only with that default
    and must keep the fake's `None`/mount values consistent.
(e) **New fixture, not an edit:** an ancestor-aware root-filesystem skip fixture must apply
    the same exact policy and qualification state to every ancestor of `tmp_path` (reuse
    the scanner preflight or a shared helper) and skip native tests with an ancestor/type
    reason. It must not produce 399 setup/collection errors; `/tmp` tmpfs + unqualified `/`
    overlay is the concrete case.
(f) **Existing tests plus new fixture:** guard the existing unguarded FIFO test
    (`os.mkfifo`) and Unix-socket test (`socket.socket(socket.AF_UNIX)` construction and
    `sock.bind`) with `try/except OSError` and explicit skips when the host refuses. Do not
    only add duplicate fixtures; fix the existing tests that would otherwise error.
(g) **New test:** create an invalid-UTF-8 member through raw bytes APIs and let
    `os.scandir` produce the surrogate-escaped text; do not build `Path("bad\ud800")`,
    which can fail before the scanner is called.

Any additional test change must be justified in its own gate. This list replaces the
earlier v0.1 claim that only one test edit was expected.

## 8. Implementation slices, gates and HOLDs

No slice below is authorized by this RFC alone. Each begins only after the orchestrator
and independent reviewers accept the prior gate and the owner authorizes the specific
change.

### S0 — Design (this document)

- Deliverable: this RFC only.
- Gate G0: review for race/TOCTOU, privacy, skip accounting and Stage 3 non-regression.
- **HOLD-0:** no production/test/schema/example/README/progress change; no Git.

### S1 — POSIX backend core (internal only)

Candidate files:

- `src/runevidence/directory_scanner.py` (backend, capabilities, mount/filesystem probe).
- `tests/test_directory_scanner.py` (Linux capability and native cases).
- Possibly `tests/conftest.py` only if a new shared fixture is strictly needed; the R5 hook
  must not change.

Work:

1. Add the capability probe (`O_PATH`, `O_NOFOLLOW`, `O_DIRECTORY`, `O_CLOEXEC`,
   `/proc/self/fd`, validated mount-id source, mountinfo), with `native_scan_support()`
   catching all `OSError`/probe failures.
2. Implement exact root lookup, bounded ancestor readdir, `O_PATH` probe, pinned reopen,
   validated mount-id check, filesystem type policy with release/test qualification
   separation, timestamp-resolution check and POSIX error classification.
3. Select the POSIX backend on Linux after the `win32` branch; leave Windows and other
   platforms unchanged.
4. Do not touch `checks/`, CLI, models, schema or docs beyond a later documentation update.
5. Apply the enumerated test-contract change (a) and the S1-only field change (d). New
   fixtures/tests (e), (f) and (g) may be added here if the native scanner tests need them;
   they are not edits to existing tests. Add the private qualification seam and its
   parser/default-policy tests. No other test change is authorized at this gate.

Gate G1:

- Fixed vectors, empty root, root move, native Linux scanner suite, existing portable
  encoder tests, all run through the private qualification seam on the available candidate
  filesystems (ext4/tmpfs/overlay where mounted) where the default release policy would
  refuse the candidate. G1 needs at least one candidate filesystem with a complete matrix
  (ext4 primary); tmpfs/overlay evidence is recorded only where those mounts exist, and
  absent evidence leaves them unqualified.
- **S1 erratum:** `native_scan_support().supported` is false while
  `RELEASE_QUALIFIED_FSTYPES` is empty, so module-level `native` markers skip. G1 matrix
  evidence (ext4 at minimum) comes from the private-seam qualification harness, not from a
  pulled `native_scan_support` claim. S2a must resolve this before G2a by promoting a
  filesystem or adding an approved facilities+seam test gate.
- Qualification-seam test: the seam allows the named candidates for the test call; the
  same scan without the seam is refused; `nfs4`, `ext3`, unknown/network/pseudo types raise
  and cannot be qualified; no CLI option, environment variable or config key can enable it.
- Mount-id proof tests (statx mask/nonzero, fdinfo parse, both absent; valid statx with
  missing/non-numeric fdinfo; unique-only statx rejected), ancestor-exemption test,
  mount-capable `mount=None` refusal, non-mount `None` validity, component-boundary prefix
  matching (`/mnt` vs `/mntdata`; `/mnt/data` vs `/mnt/database`; `/foo` vs `/foobar`),
  no-match refusal, stacked-mount topmost `ext4 id36`/`nfs4 id48` refusal, and
  mount-added-between-preflight-and-open refusal, plus same-numeric-id replacement
  (`id36` reused by `nfs4`) refusal against the fresh post-open table.
- Non-ASCII/reserved ancestor test, ancestor-aware root-FS gate fixture, ancestor
  readdir/component caps.
- `open_file` non-FILE probe test with zero data opens.
- No public command behavior change.
- No default `enable_directory_artifacts` activation.
- G1 uses the private qualification seam; it does not promote ext4/tmpfs/overlay into
  `RELEASE_QUALIFIED_FSTYPES`. The default runtime policy remains fail-closed.
- Windows tests and R5 hook unchanged.
- No new dependency; Python 3.10-compatible.
- **HOLD-1:** do not proceed to integration/public work until independent review accepts
  the race analysis and the actual pass/skip evidence.

### S2a — Internal pipeline integration

Candidate files:

- `tests/test_directory_*.py` integration suites.
- `src/runevidence/checks/` only if a boundary message or switch bug is found; no default
  behavior change.
- `tests/test_directory_authoring.py` (read-only-scanner allowance in the FORBIDDEN test).
- `tests/test_directory_batch.py` (`data`/`DATA` POSIX behavior).

Work:

1. Run the internal opt-in check pipeline, authoring, apply dry-run, preview, plan, audit,
   batch and present suites on Linux.
2. Apply the enumerated test edits (b) and (c) from 7.8, with their review justification.
3. Resolve the S1 support gate before G2a: either promote at least one filesystem to
   `RELEASE_QUALIFIED_FSTYPES` through the release gate, or add an approved
   facilities+seam test gate that lets native tests run without claiming production
   `supported=True`. Update the S1-erratum expectations and matrix evidence accordingly.
4. Keep directory writes synthetic and disposable; no public activation.

Gate G2a:

- The S1 `native_scan_support().supported == False` gate is resolved (promotion or
  approved facilities+seam test gate); the resolution and per-filesystem evidence are
  recorded.
- All internal pipeline tests pass or are explicitly refused by design.
- Test edits are exactly the enumerated list; no hidden skips.
- No S3P3P7 write claim; no public route.
- No Windows/R5 change.
- **HOLD-2a:** do not start the large race/evidence expansion until pipeline behavior is
  stable.

### S2b — Race, fixture, determinism and packaging evidence

Candidate files:

- `tests/test_directory_scanner.py` and the directory test modules for race/fixture tests.
- `tests/conftest.py` only for the root-filesystem skip fixture if a shared fixture is
  required; the R5 hook must not change.
- Packaging check scripts under an approved temporary directory.

Work:

1. Add the race/injection, symlink/FIFO/permission, determinism and ancestor-aware
   root-FS-gate tests and fixtures from section 7.
2. Add new fixtures/tests (e), (f) and (g) from 7.8 **if not already added in S1**; do not
   re-edit (d), which belongs to S1 only.
3. Add installed wheel/sdist checks, using a temporary copy of the vector JSON or
   independently computed vectors.
4. Measure skip conversion with `-rs`; report every remaining node.

Gate G2b:

- Actual skip reduction recorded; no hidden skips.
- Installed checks pass without `tests/` or the repository checkout.
- Windows/R5 unchanged.
- **HOLD-2b (satisfied by the S3b approval):** public read-only activation still requires a
  separate approval.

### S3 — Public read-only opt-in (implemented as S3a + S3b)

S3a promoted `ext4` for library callers. S3b adds the public flag.

Candidate files:

- `src/runevidence/cli.py` (`--directory-artifacts` on `validate` and `inspect` only).
- `src/runevidence/checks/` (sanitized boundary messages; pass the existing switch).
- CLI tests and CLI pin refresh.
- README/help text and directory documentation.

Gate G3:

- **Met by S3b:** no new route; the 26-route count and parser pins were refreshed only for
  the two new option strings.
- At least one filesystem is promoted to `RELEASE_QUALIFIED_FSTYPES` by the release gate;
  S3 does not use the qualification seam in production. S3a promoted `ext4`.
- **Met by S3b:** privacy review; directory findings carry no resolved absolute root,
  member names, content, OS text or network data. `inspect`'s pre-existing untracked-file
  findings may still list member relative paths.
- **Met by S3b:** default behavior without the flag is byte-identical to today.
- **Met by S3b:** `--redact-manifest-path`, `--strict`, `--json`, `--summary-only` remain
  correct and no output field was added.
- Full suite and installed CLI checks pass; skips fully accounted.
- **HOLD-3 unchanged:** directory authoring/writing is still NO-GO.

### S4 — Public directory authoring/writing

**NO-GO in this RFC.** Blocked by the existing S3P3P7 persistence safety HOLD: source
comparison and `os.replace` are not atomic CAS; nonparticipating writers, ABA tree changes,
custom ACLs and crash/durability are unresolved. A Linux scanner does not fix any of those.
A separate design and owner approval are required.

### S5 — Linux R5 evidence lane

**Not this RFC.** The experimental writer refuses non-Windows. A Linux R5 lane needs its
own design, environment contract, evidence roots and qualification. Do not claim it from
the scanner work.

### 8.1 Packaging and installed checks (all gates)

- Build under an approved temporary directory; no repository artifacts.
- Install the wheel into a fresh venv; run `pip check`.
- From outside the checkout, import the installed package and:
  - call `native_scan_support()`;
  - scan a synthetic tree and reproduce the vectors from an approved temporary copy of
    `tests/data/directory_digest_vectors.json` or independently computed vectors;
  - use the private qualification seam if no filesystem is release-qualified yet;
  - run a synthetic internal check if the API is exposed;
  - run the installed `validate`/`inspect --directory-artifacts` flag on a synthetic
    manifest (S3b);
- Confirm the installed check does not read `tests/data/directory_scanner_filesystem_matrix.json`,
  does not assume `tests/data/directory_digest_vectors.json` is installed, and does not
  claim a production scan on an unqualified filesystem.
- Confirm no new dependency, data file or network call.
- Confirm the sdist/wheel contain the scanner module and no test/evidence files.

### 8.2 What remains unqualified after all approved slices

- No filesystem is release-qualified *by this RFC*; G1 used the private qualification seam,
  and promotion is a later release gate. S3a has since promoted `ext4` by code change from
  the accepted matrix (test evidence only); every other filesystem remains unqualified.
- No Linux R5 evidence; the R5 writer remains Windows-only.
- No cross-platform byte-identical directory digest claim without dual-platform evidence.
- No atomic snapshot, CAS, ACL, crash-durability or hostile-writer guarantee.
- No public directory authoring/writing.
- No timeout/CPU/memory ceiling beyond logical bounds.
- No bind-mount descendant, mount-namespace, network-FS, overlay-collision or
  coarse-timestamp qualification beyond the refusal policy and tests that the host permits.
- No deployment without `/proc`; it is explicitly unsupported.
- No macOS support.
- No hosted CI qualification until a Linux lane actually runs and is recorded.

## 9. Alternatives, rejections and residual risks

### 9.1 Alternatives considered

| Alternative | Why it is rejected (or retained only as a fallback) |
| --- | --- |
| Windows-only forever | Honest and lowest risk, but leaves Linux directory validation unavailable and 398 closable skips as a capability gap. Retained as the fallback if the Linux design cannot be qualified. |
| Naive `os.walk` | Path-based opens race with renames and symlinks; special files may be opened; no handle identity; default limits are wrong for v1. Rejected. |
| `os.walk(followlinks=False)` plus `lstat` | Still path-based; a component can be swapped between `lstat` and `open`; symlink escape is possible. Rejected. |
| Follow symlinks and record targets | Escapes the artifact, creates loops, leaks outside content and has no v1 representation. Rejected. |
| Hash a full tree with no bounds | Memory/CPU/disk DoS; not reproducible under the v1 limits. Rejected. |
| Path-based `open()` after a pre-check | Classic TOCTOU; cannot meet the no-follow/identity requirement. Rejected. |
| Advisory `flock`/`fcntl` as a safety claim | Only cooperating processes honor it; does not freeze nonparticipants; changes behavior without a guarantee. Rejected as a safety mechanism. |
| inotify/fanotify as a snapshot | Events are not a consistent snapshot; can be lost/coalesced; race remains. Rejected. |
| New digest format for Linux | Unnecessary: v1 is platform-independent and already rejects Unicode/case ambiguity. A new token would require migration and parity work. Rejected for this milestone. |
| New `scan`/`directory` CLI route | Adds parser/pin/privacy surface without user benefit; existing commands suffice. Rejected. |
| Automatic public directory validation on Linux | Changes accepted default behavior and could surprise existing manifests. Rejected; explicit read-only flag only, and only after S3 approval. |
| Copy Windows sharing guarantees to POSIX | Linux has no mandatory share modes; claiming them would be false. Rejected. |
| macOS in the same slice | Different path semantics (`/tmp` symlink, no `/proc/self/fd` guarantee) and no coverage. Rejected; separate design. |
| SQLite-backed scan registry | SQLite is dormant and not the authority; it would not make the filesystem read atomic. Rejected. |
| Claim cross-platform byte-identical digests now | Encoder parity is a hypothesis, not evidence. Rejected until a dual-platform fixture passes. |

### 9.2 Residual risk register

| Risk | Level | Handling / gate |
| --- | --- | --- |
| ABA tree change with restored metadata | Medium | Documented; quiet-tree assumption; ctime and second pass reduce but do not close; no hostile claim |
| Inode reuse matching identity/timestamps | Low | Device+inode+type+nlink+size+ctime comparison; no generation number; documented |
| Bind mount of the same filesystem below root | Medium | Root/descendant mount-id equality; ancestors above root exempt and allowed to differ |
| Old-kernel statx without `STATX_MNT_ID` | Medium | Require mask bit and nonzero id; fdinfo fallback; refuse if neither proves it |
| Overlayfs `(st_dev, st_ino)` collision and hidden lower FS | Medium | Mount id plus type/size/times/link count; residual documented; fake collision test; underlying lower FS not visible |
| Coarse/restored timestamps | Medium | Exact allowlist excludes known coarse FS; read-only ns check; same-size overwrite residual documented |
| Mount namespace changed after a check | Low/Medium | Detected at the next check, not frozen; privileged actor out of scope |
| Network/pseudo filesystem misclassification | Medium | Explicit allowlist and refuse unknown; fake policy matrix; ancestor-aware root-FS gate |
| Ancestor read permission / readdir cost | Medium | Preflight fstype; 4,096-entry cap; 256-component cap; unreadable/limit-exceeded; residual |
| Refused ancestor above an allowed root | Medium | Ancestor preflight refuses the whole scan; fixture skips; documented false-reject |
| Qualification seam exposed or circular | High | Private shipped seam, release-gated promotion, parser test; G1 passes via seam without runtime permission |
| Mount-point prefix false match | Medium | Component-boundary exact matching; no-match refusal; `/mnt` vs `/mntdata` and `/mnt/data` vs `/mnt/database` tests |
| Stacked mounts / topmost ambiguity | Medium | Parent-id chain selection; unique topmost required; post-open id/fstype/mountpoint check; no first/oldest fallback |
| Mount added/replaced between preflight and open, including reused numeric id | Medium | Fresh post-open mountinfo table validates actual id/fstype/mount point; same-id replacement caught by fresh fstype; detached mount missing from fresh table refuses |
| `/proc` absent or restricted | Medium | `unsupported-platform`; no path fallback; `native_scan_support` returns unsupported |
| Special-file open side effect | Low | `O_PATH` probe before data open; special at listing `unsafe`; swap `unstable` |
| Time/CPU exhaustion within limits | Medium | Logical bounds, refusal of remote FS, explicit opt-in at S3; future deadline separate |
| Permission differences / root user | Low | Caller permissions; unreadable fails; tests skip as root and report it |
| Unicode/case edge cases | Low | v1 rejects non-ASCII and case collisions; v2 needed for support |
| Public error text leaks paths/member names | Low after S3b | Directory findings use fixed reason text; internal scanner messages stay verbose but are not copied into findings. `inspect`'s pre-existing untracked-file findings may still list member relative paths |
| Cross-platform digest divergence | Medium | Shared fixture test; no claim until evidence |
| Windows/R5 regression | Low | Windows files untouched; `win32` selected first; R5 hook untouched |
| Public writer races | High | S3P3P7 HOLD remains; S4 NO-GO |
| Installed-package drift | Low | Wheel/sdist import and installed checks; vectors from temp copy/computation |

### 9.3 Recommendation

Proceed only with **S0 review**. If accepted, authorize **S1** as a scanner-only change
with no public surface, then **S2a** for internal pipeline integration and **S2b** for the
race/fixture/determinism/packaging evidence. G1 runs under the private qualification seam;
it does not promote any filesystem. Promotion to `RELEASE_QUALIFIED_FSTYPES` is a separate
release gate with a recorded matrix. Do not authorize **S3** until the race and privacy
reviews accept the actual implementation, the release allowlist and the skip accounting.
Do not authorize **S4** at all while S3P3P7 stands. Do not describe the Linux scan as an
atomic snapshot, a hostile-writer defense, a Linux R5 qualification, or proof of
cross-platform byte-identical digests.

## Appendix A. Evidence and inventory method

Read-only inventory performed for this design (no repository writes, no Git):

```text
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest \
  tests/test_directory_scanner.py tests/test_directory_checker.py \
  tests/test_directory_check_pipeline.py tests/test_directory_artifacts.py \
  tests/test_directory_authoring.py tests/test_directory_apply.py \
  tests/test_directory_preview.py tests/test_directory_plan.py \
  tests/test_directory_audit.py tests/test_directory_batch.py \
  tests/test_directory_present.py tests/test_directory_review.py \
  tests/test_directory_review_path.py tests/test_directory_policy.py \
  tests/test_directory_cooperative_apply.py tests/test_directory_writer_races.py \
  -q -p no:cacheprovider --basetemp=<outside-tree> --no-header -rs
# 633 passed, 404 skipped

PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest \
  tests/test_acceptance.py tests/test_edit.py tests/test_files.py \
  tests/test_manifest_resolve_loop.py tests/test_schema_0_2.py \
  -q -p no:cacheprovider --basetemp=<outside-tree> --no-header -rs
# 152 passed, 10 skipped
```

The full-suite Stage 3 baseline (**3193 passed, 533 skipped**) is recorded in the
project progress/manual and was not re-run for this design. Counts are an inventory, not a
qualification. The v0.4.2 revision is documentation-only; it adds no test execution or
capability evidence. The only file changed by this task is
`docs/native-directory-scanner-linux-rfc.md`.
