# Schema 0.3 verified value locators (slice B)

Status: **slice B delivered (2026-11-13).** Schema 0.3 supports verified JSONL
object locators and a strict whole-document JSON profile for string locators.
CSV (`csv-cell`), TensorBoard and other adapters are not implemented; CSV is a
later slice C, not part of slice B. Schema 0.1 and 0.2 are unchanged and remain
byte-frozen.

## Locator union

Schema 0.3 is an explicit opt-in. `Result.locator` is optional and accepts:

- a **string** JSON Pointer: empty, or `/`-prefixed with only `~0`/`~1`
  escapes.
- an **object**:

  ```json
  {"kind": "jsonl", "line": 41, "pointer": "/val_rmse"}
  ```

  `kind` is the only admitted kind; `line` is a one-based integer from 1 to
  `2**63 - 1`; `pointer` follows the same pointer grammar. The object is closed
  (`additionalProperties: false`).

Both pointer forms reject CR, LF, U+2028 and U+2029 anywhere. Empty, `/`,
`/~0/~1`, and ordinary slash-separated pointers remain valid.

In 0.3 any non-null locator requires `artifact_id`. A dangling ID is a
`dangling-reference` error. An object locator in a 0.1/0.2 manifest is a schema
error. Unknown object kinds are load refusals (exit 2).

## Protected snapshot

An artifact named by any 0.3 locator (string or object) is read once through a
protected open:

- the manifest folder is opened once and each parent component of the artifact
  path is opened relative to its verified parent descriptor with
  `O_RDONLY|O_DIRECTORY|O_NOFOLLOW|O_CLOEXEC`; `..`, `.`, absolute components,
  empty components and a symlinked parent are refused. An absolute artifact
  spelling is shape-checked component-by-component before its root prefix is
  removed, so a `..` (or a `.` or empty component) inside an absolute spelling
  is refused and never collapsed by path arithmetic. **Absolute follows the
  running host**: on POSIX only a spelling starting with `/` is absolute. An
  artifact spelling with backslashes is normalised first by the manifest-path
  rule (with `non-posix-path`), so a drive spelling `C:\x.jsonl` arrives as
  `C:/x.jsonl`. A drive-shaped artifact path such as `C:/x.jsonl` is a literal
  relative name inside the manifest folder, never a path resolved against the
  process cwd. For a locator-less 0.1/0.2 artifact, a drive-root spelling
  (`C:/...`, or `C:\...` after normalisation) keeps the legacy `absolute-path`
  warning and relocation hint. A drive-relative spelling (`C:x`,
  `C:data/...`, bare `C:`) or a non-letter `1:/...` is resolved as an ordinary
  relative name and does **not** get that warning, so `--strict` can exit 0
  where older builds exited 1; a single-letter `X:/...` spelling is warned as
  drive-shaped even when it is a literal folder name on POSIX. A backslash UNC
  artifact spelling such as `\\srv\share\x.jsonl`
  normalises to `//srv/share/x.jsonl`, which is host-absolute on POSIX and is
  refused `path-escapes-root`; it is never read as a literal relative artifact
  name, even when a file of that literal name exists — unless a same-digest
  file elsewhere in the folder matches a suffix of the spelling, in which case
  the existing `missing-file` relocation hint is reported (as in slice A). (A
  link target is different: it is never normalised — see below.)
- the leaf is opened relative to its parent descriptor with
  `O_RDONLY|O_NOFOLLOW|O_CLOEXEC|O_NONBLOCK|O_NOCTTY`;
- an in-root symlink alias to the leaf is still permitted: the alias is
  resolved, then its resolved components are walked under the same rules; a
  symlinked parent is never silently followed. A relative target may use `..`
  only to climb the artifact's real parent directories (the walk has verified
  each as a directory, not a link). A `..` after a component that came from the
  target itself is refused, because that component can be a link and the kernel
  would climb out of the link's target, not out of the spelling. The target is
  split on `/` only and is never normalised: on POSIX a backslash is a literal
  filename byte, so `C:x.jsonl` or `\\srv\share\x.jsonl` is a relative target
  (resolved against the link's own directory, like the kernel) and never a
  drive/UNC path resolved against the cwd. A `.` component in a target is
  refused, because a trailing `/.` after a regular file is `ENOTDIR` for the
  kernel. An empty target component the kernel can resolve (a doubled
  separator, or a trailing slash after a directory) is refused too, for
  defense in depth: dropping it would verify a spelling the shape rules do not
  accept. A target the kernel itself cannot resolve before the shape check —
  `x.jsonl/.` or `x.jsonl/` after a regular file — is reported `missing-file`
  by the pre-stat instead; only a target the kernel can open, such as
  `./x.jsonl`, reaches the shape refusal and gets `locator-unreadable` with
  the fixed link-target message;
- `fstat` must report a regular file;
- the leaf's `(st_dev, st_ino)` is taken with `stat(parent_fd, leaf,
  follow_symlinks=False)` immediately before opening and again after the read;
- the same bytes and digest serve both the file check and every locator on that
  artifact, so there is no second read;
- the snapshot is released after that artifact's locators are checked; there is
  no cross-run cache.

A parent swap after containment resolution therefore fails the next component
open and is reported as `artifact-changed` (or `locator-unreadable` when the
errno so dictates). Validation never reads the swapped file and never exits `0`
on it. The component walk is available on platforms whose `os.open`/`os.stat`
support directory descriptors (POSIX); elsewhere the leaf keeps the protected
flags, but the post-read identity re-stat is unavailable and therefore skipped,
the parent-component race is not closed, and no Windows or macOS parity is
claimed for it.

A targeted artifact whose path is not physically inside the manifest folder is
never handed to the protected walk: it keeps the legacy `path-escapes-root`
error (plus `absolute-path` when absolute) with or without a locator, and is
never described as `artifact-changed`. That includes an unsearchable outside
parent (`EACCES`), a leaf symlink loop whose entry resolves outside (`ELOOP`),
and a lexically in-root spelling whose loop or parent link physically leaves
the folder (absolute leaf link, relative leaf link, or a parent link to an
outside folder that contains the loop). `os.path.realpath` is used only to
make that diagnosis; it never authorises an open, and the component-by-
component descriptor walk remains the safety boundary.

Three `..` cases are kept distinct:

- **Artifact path spelling.** A path spelled with `..` that is physically
  inside the folder (for example `sub/../x.jsonl`) is valid and unchanged, but
  it is not normalised: the targeted reader refuses the spelling with the
  existing `locator-unreadable` code and the fixed value-free message
  `artifact path uses '..'; write it without parent references`, never the
  mid-read `artifact-changed` text. This applies to an absolute spelling too:
  `<folder>/sub/../x.jsonl` is refused rather than prefix-stripped, because
  `relpath` would collapse the `..` on paper before the shape check. A spelling
  refused for its shape but with no `..` (a doubled separator, any `'.'`
  component — including a leading `./x.jsonl`, `sub/./x.jsonl` or a trailing
  `x.jsonl/.` — or a trailing slash) gets a distinct fixed message,
  `artifact path has an empty, '.' or trailing-slash component; write the
  plain file path`, because blaming `..` there would be false. That refusal
  runs before the extension/kind test and before any artifact digest is
  accepted, so it reports only `locator-unreadable` rather than also a
  `locator-kind-mismatch` that would name the wrong defect. A `'.'` component
  in particular is refused rather than dropped, because a trailing `/.` after a
  regular file is `ENOTDIR` for the kernel while dropping it would open and
  verify that file. Normalising
  would verify a different file than the kernel opens when a parent component
  is a link, so the plain spelling is required instead. This adds no new
  finding code. A locator-less artifact (0.1/0.2, or a 0.3 artifact that no
  locator names) is not subject to this refusal: it keeps ordinary `Path`
  semantics, so a leading `./`, a `.` component and a trailing slash are
  normalised by `Path` and the file still verifies (exit `0`). The shape
  message never names a component the spelling lacks either: a host-absolute
  spelling whose root prefix cannot be removed without leaving the folder
  keeps `path-escapes-root`, not the empty/`'.'`/trailing-slash text.
- **Leaf symlink target inside the folder.** A relative alias target may use
  `..` and stays usable while it remains inside: `sub/alias.jsonl ->
  ../metrics.jsonl` is resolved and verified, exit `0`. The `..` may climb only
  the artifact's real parent directories. A target component is not known to be
  a directory, so `<folder>/alias.jsonl -> "sub/../x.jsonl"` (or the same
  target written absolutely) is refused with `locator-unreadable` and the fixed
  message `the artifact's symbolic link target is not verified; point the link
  at a plain path inside the folder`, never `artifact-changed`: the kernel
  would follow `sub` if it is a link and open a different file than the
  normalised spelling names, and nothing was opened.
- **Leaf or parent resolving above/outside the folder.** A target that climbs
  above the manifest folder (even one that lexically re-enters) or resolves
  outside keeps `path-escapes-root`, not `artifact-changed`, and is never
  opened. This includes `root/esc -> outside` with a regular outside leaf or
  with an outside loop.

A parent component that is a symbolic link and resolves **inside** the folder
is refused with `artifact-changed` and the fixed message `a parent directory is
a symbolic link; use the real path`. It is not reported as a mid-read change,
because nothing is opened. This covers an in-folder alias layout such as
`latest -> run_2025` (or `sub -> run_2025`); an artifact without a locator
still follows it. A parent that physically resolves **outside** keeps
`path-escapes-root` instead (the Blocker A rule above), never the parent-link
message and never the mid-read message. Moved-file hints are hashed through the
same protected open, so a hint candidate behind a symlinked parent is not
named.

Error mapping is fixed: `ENOENT`/`ENOTDIR` → `missing-file`; `EACCES`/`EPERM` → 
`locator-unreadable`; `ELOOP`, an identity change, a non-regular descriptor or
a parent component that is no longer a directory → `artifact-changed`; other
errno → `locator-unreadable`. Diagnostics never echo OS text or parser text.

Only artifacts named by a 0.3 locator use the protected snapshot. All other
artifacts, including every artifact in a 0.1 or 0.2 manifest, are hashed with an
ordinary open that follows symbolic links after a separate containment check,
so a concurrent swap of a parent directory can make validate hash, and report
the digest and size of, a file outside the folder.

The profile cap is 50 MiB. Above it, the file check still streams and hashes the
file through the protected descriptor, while each locator emits
`locator-unreadable`. An over-cap targeted artifact with no recorded digest
reports the result-level `locator-no-digest` instead (its size is still
compared), so the over-cap `locator-unreadable` outcome applies to over-cap
artifacts that have a recorded digest.

A targeted artifact with no recorded digest is still stat'ed and read for
readability, and its `size_bytes` is still compared, before the result-level
`locator-no-digest` is reported. A targeted directory artifact also runs the
ordinary artifact-level check: without `--directory-artifacts` it is
`not-a-file`; with the flag it gets the normal directory findings
(`missing-directory`, `directory-hash-mismatch`, `directory-unsafe`, ...).
`directory-locator-unsupported` is added on top in both cases.

### Permission failures

`Path.exists()`, `is_file()` and `is_dir()` are not used on the artifact path.
Before Python 3.14 those calls raised `PermissionError` when a parent directory
denied search permission; on Python 3.14 they return `False`. The checker uses
one `os.stat`/`os.lstat` and maps the errno itself, so the documented
`EACCES`/`EPERM` behavior is the same on every supported Python version: a
targeted artifact becomes `locator-unreadable`, and a legacy 0.1/0.2 artifact
keeps a fixed `missing-file` error (no OS or exception text) and broken-set
membership.

Legacy containment still calls `Path.resolve`, so an in-folder symlink
self-loop keeps that pre-existing version-dependent outcome: `path-escapes-root`
on 3.10/3.12 and `missing-file` on 3.13/3.14. It is not a locator rule, and a
0.3 targeted artifact uses the descriptor walk instead.

## Outcomes (0.3)

Reference checks come first: dangling `artifact_id`, directory artifact
(`directory-locator-unsupported` regardless of `--directory-artifacts`, on top
of the normal directory or `not-a-file` finding), `not-a-file`, and
extension/kind mismatch (`locator-kind-mismatch`). A missing file is reported by
the file check and locator checks are skipped.

For a readable targeted artifact:

| Condition | Code | Severity |
| --- | --- | --- |
| Recorded digest differs | `hash-mismatch` | error |
| Targeted artifact has no digest | `locator-no-digest` | error |
| Volatile artifact with a locator | `locator-unverified` | error |
| Snapshot identity changed | `artifact-changed` | error |
| Line/pointer absent, blank, or out of range | `locator-mismatch` | error |
| Profile refusal (encoding, caps, parse) | `locator-unreadable` | error |
| Selected value is an array/object | `locator-unsupported-value` | error |
| Resolved scalar differs | `result-value-mismatch` | error |
| Absent locator (0.3 only) | `result-unverified` | warning |
| Claim cites only unverified results | `claim-unverified` | warning |

A digest mismatch still compares the value (legacy behavior) but the result is
never presented as verified. `locator-no-digest` suppresses the artifact-level
`no-digest` warning for targeted artifacts and its hint says whether `rehash`
can record the digest. `rehash` refuses only an artifact that an object
locator reads, so the hint is artifact-scoped: an artifact with no object
locator gets "rehash can record the missing digest", and an artifact read
through one gets the refusal wording with manual recording or regeneration
suggested. Other artifacts in the same manifest remain rehashable. Locator errors add the result to the broken set, so a claim resting on
it gets `claim-at-risk`. Unverified warnings do not add to the broken set.

An extension/kind mismatch (`locator-kind-mismatch`) is a reference refusal: it
does not emit `locator-no-digest`/`locator-unverified`, so it must not suppress
the artifact-level state warning. A digestless targeted artifact with a
mismatched extension therefore keeps `no-digest` exactly as it would without a
locator. The `no-digest` suppression applies only when the replacement locator
finding is actually emitted. That restored `no-digest` hint uses the same
artifact-scoped `rehash` predicate and wording as `locator-no-digest`, including
when every locator on the artifact is kind-mismatched.

`locator-verifier-unavailable` remains only as a defensive code for an
implemented-path gap; `jsonl` is implemented.

## JSONL profile

- UTF-8; one leading BOM stripped; a BOM later in the file is data. The BOM is
  stripped before the 1 MiB line cap is applied, so it does not shorten an
  otherwise legal first line.
- LF-byte split; one trailing CR before LF stripped.
- Bare CR, NUL and invalid UTF-8 are refused file-wide, including a CR that is
  legal JSON whitespace and a CR on a line that is not selected.
- Line cap 1 MiB; file cap 50 MiB.
- Physical one-based line numbers count blanks and whitespace-only lines; those
  are never selectable (`locator-mismatch`).
- The selected line must be one JSON value; other malformed lines are
  tolerated. An unterminated final line is selectable.
- Duplicate keys are refused at every depth; `NaN`, `Infinity` and `-Infinity`
  are accepted; `1e999` overflow and nonzero underflow are refused.
- A 64-level nesting pre-check runs before parsing.
- The selected value must be a scalar; arrays/objects are
  `locator-unsupported-value`. A pointer may select a scalar inside the line.
- The file is validated as a whole with C-level byte scans (a NUL membership
  test and two CR/CRLF counts), then decoded once. The 1 MiB line cap makes one
  C-level `find` per newline in a Python loop: it allocates nothing per line,
  but a newline-dense file still costs roughly one short Python step per line
  (about 0.4 s per 5 MiB of bare newlines, about 4 s at the 50 MiB cap). No
  per-line Python object list is built and no loop runs per byte or per CR, so
  17 million short lines do not cost gigabytes. Only a selected physical line
  is sliced out of the decoded text and parsed, once and cached.
- Requested line numbers are indexed in one forward scan before any value is
  read: only the selected offsets are kept (never a full offset table for
  millions of lines), and the scan stops after the last requested line. Many
  distinct locators on one file are therefore not O(results × file size); the
  scan is linear in the file size for the whole selection set.

## 0.3 string JSON profile

A string locator targets a `.json` artifact and applies the same strict value
rules to the whole document: one optional BOM, NUL and bare CR refused,
duplicate keys refused, `NaN`/`Infinity` accepted, overflow/underflow refused,
512-digit integer literals, 64 nesting levels, 50 MiB cap. This is stricter than
the unchanged 0.1/0.2 JSON reader.

## Numbers and comparison

- Integer literals up to 512 digits are exact; longer literals are refused.
- Fraction/exponent literals are floats; overflow to infinity and underflow
  from a nonzero significand are refused. Python's 4300-digit conversion limit
  maps to `locator-unreadable`, never a crash.
- `int`/`int` compares exactly. Mixed `int`/`float` uses the existing
  `values_match` tolerance (`rel_tol=1e-9`, `abs_tol=1e-12`) and is approximate.
  Booleans never equal numbers; `NaN` equals `NaN`; null equals null.

## Other commands

- `validate`, `inspect` and `validate-batch` run these checks. Exit `1` on any
  error; warnings only exit `0` (`1` with `--strict`).
- `rehash` refuses an artifact read through an object locator before any
  write with `LOCATOR_REFRESH_UNSUPPORTED` and exit `2`; an unrelated artifact
  in the same manifest can still be rehashed. `--all` refuses when any
  selected artifact is read through one. String locators keep the legacy
  re-read behavior; `validate` applies the strict 0.3 profile afterwards.
- `run`, `claim` and `lineage` serialize object locators as JSON objects and
  use bounded escaped text in human output.
- Fingerprint v1 still accepts only 0.1 and refuses 0.2/0.3.
- `migrate --to 0.3` remains pre-checked and transactional. Whole-migration
  refusal uses the fixed value-free `LOCATOR_MIGRATION_UNSUPPORTED`; the message
  does not list result IDs, so locate the offending locator yourself.
- Recorded-condition comparability supports 0.1/0.2 legacy manifests and
  refuses any 0.3 operand with `UNSUPPORTED_VERSION` in both verdict and
  preflight modes. It is not a "0.2-only" command.
- Conditions authoring remains 0.2-only; a 0.3 manifest is refused with
  `UNSUPPORTED_VERSION`, and the text notes there is no 0.3 downgrade path.

## Not implemented

CSV (`csv-cell`), TensorBoard, other adapters, snapshots beyond the protected
per-artifact read, and any new route or flag are later work. CSV is slice C.
