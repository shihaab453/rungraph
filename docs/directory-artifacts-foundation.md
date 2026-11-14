# Directory artifacts: foundation (experimental read-only opt-in)

**Experimental.** `validate` and `inspect` accept `--directory-artifacts` to
check a directory record. Since S4-2b, `add PATH DIR --directory --id ID` and
`rehash PATH ID --directory` author one directory record on Linux/ext4 (text
only, guarded publication); no route was added. Every other command remains
read-only with respect to directory records: only those two flags author one.
On Linux the scanner's release policy supports `ext4` only. Without the flag a
directory record is still treated like a file and reports `not-a-file`.

## What exists

- Two optional artifact fields in `src/runevidence/models.py`:
  - `artifact_type`: `"file"` (the default, and what an omitted field means)
    or `"directory"`.
  - `digest_format`: required as `"runevidence-directory-v1"` when
    `artifact_type` is `"directory"`, and absent for files.
  Any other combination or value is `schema-invalid`. The generated schema
  carries the same rule as an `if`/`then`/`else` on `Artifact`.
- File artifacts are written exactly as before: neither field appears in a
  saved manifest unless the artifact is a directory.
- `src/runevidence/directory_hashing.py`: a pure encoder that turns a list of
  in-memory member records into the canonical v1 byte stream and its SHA-256.
  It does not read the filesystem.

`validate`, `inspect` and every other command still treat a directory record
like any file record unless `--directory-artifacts` is passed, so by default a
path that names a folder reports `not-a-file`.

## Public opt-in check

`--directory-artifacts` on `validate` or `inspect` passes
`run_checks(loaded, enable_directory_artifacts=True)` (and the same keyword on
`check_files` and `check_results`). It is off by default. The flag is not
accepted by `validate-batch`, `add`, `rehash`, `migrate`, `conditions` or any
other route. With it on:

- The artifact path must pass the member name profile below, plus the depth
  and path-length bounds, before the scanner is called. Anything else is
  `directory-unsafe` (or `directory-limit-exceeded`) and is never scanned.
- The manifest folder joined with those components goes to the native
  scanner; scan failures map to `missing-directory`, `not-a-directory`,
  `directory-unsafe`, `directory-unreadable`, `directory-unstable`,
  `directory-limit-exceeded` or `directory-path-collision`, all errors.
  Unsupported platforms and filesystems are `directory-unsafe`.
- A wrong digest or a wrong recorded size is an error. A matching digest
  with no recorded size passes. No recorded digest is a
  `directory-no-digest` warning.
- A volatile directory is never scanned and is an error: there is no safe
  way to verify it yet.
- A directory root never becomes a readable file, so a result with a
  `locator` on a directory artifact is a `directory-locator-unsupported`
  error and puts claims citing that result at risk. A result linked to a
  directory without a locator is fine while the directory checks out.
- With `include_untracked` (`inspect`), a directory covers exactly the files
  its successful scan recorded. A failed, volatile or rejected directory
  covers nothing, so its files still show as `untracked-file` with their
  member relative paths, exactly as they do without the flag. Those are the
  existing untracked-file findings, not directory diagnostics.
- File artifacts inside a directory artifact keep their usual file checks.
- Public directory messages are sanitized: they show the artifact ID, the
  recorded artifact path (the manifest text as written) and fixed
  reason/class text. A recorded path that fails the strict relative
  portable-path profile is rejected before scanning and may be echoed as
  written; that is user manifest text, not the resolved scan root. The
  scanner's verbose exception text (absolute root, `/proc` path, member
  names, OS `strerror`, inode/device/mount ids, a newly computed digest) is
  never copied into a directory finding. Exit codes, `--strict`, `--json`,
  `--summary-only` and `--redact-manifest-path` keep their usual meanings,
  and the scan is read-only. `--redact-manifest-path` is JSON-only and text
  headers may still show the manifest path (pre-existing).

## The v1 byte stream

All integers are unsigned big-endian.

1. Header: `b"RunEvidence.directory\x00v1\x00"`.
2. `u64` count of member records (the root itself has none).
3. Records sorted by the raw UTF-8 bytes of their relative path, each
   `kind || u32 path length || path || payload`, where `kind` is `D`
   (directory, empty payload) or `F` (file, 32-byte raw SHA-256 of its bytes).

Every folder below the root needs its own `D` record, including empty ones.
The directory `sha256` is the lowercase hex SHA-256 of the whole stream.
`tests/data/directory_digest_vectors.json` holds three fixed vectors that were
computed independently of this encoder.

## Name profile and limits

Member names are deliberately narrow in v1: printable ASCII only, no
`< > : " / \ | ? *`, no empty, `.` or `..` components, no trailing space or
dot, no Windows device names (`CON`, `NUL`, `COM1`, ...), and no `.git`,
`.hg`, `.svn`, `.venv`, `venv`, `__pycache__` or RunEvidence manifest/backup
names at any depth. Paths that differ only in ASCII case collide. Real
datasets with Unicode names are rejected rather than normalized; supporting
them needs a new, separately versioned format.

Fixed bounds (a caller may tighten them, never raise them): depth 64, 10,000
members, 1 GiB of file bytes in total, 256 MiB per file, 1,024 bytes per path,
4 MiB of path bytes in total, 8 MiB of encoded stream.

## Scanner (no public route)

`src/runevidence/directory_scanner.py` reads a real folder into records for
the encoder. The opt-in check above calls it. It opens every member by one name relative
to an already open parent handle and never follows a link; where that is not
available it refuses to run (`unsupported-platform`) instead of falling back
to path-based checks.

| Platform | Backend | Status |
| --- | --- | --- |
| Windows, fixed local NTFS drive letter | `NtCreateFile` relative to a parent handle with `FILE_OPEN_REPARSE_POINT` (ctypes) | Native tests on Windows 11 / Python 3.14 only |
| Windows, other drives (removable, network, `subst`, non-NTFS, UNC) | none | Refused: `unsupported-filesystem` |
| Linux, release-qualified `ext4` | POSIX no-follow, handle-relative opens | Implemented; see the Linux scanner RFC |
| Linux with another filesystem, macOS | none | Refused: `unsupported-filesystem` / `unsupported-platform` |
| Anything without those facilities | none | Refused: `unsupported-platform` |

Refused as `unsafe`: symlinks, junctions and every other reparse point (as
members, as the root or as any ancestor), files with more than one hard link,
devices, FIFOs, sockets, members on another volume, a root or ancestor whose
on-disk spelling differs, and every name the profile above rejects. A folder
whose contents change while it is read, including between two full passes, is
`unstable`.

On Windows, while a scan runs, the scanned folders cannot be renamed or
deleted, files being read cannot be written, and nothing can be renamed or
hard-linked into the scanned folders. Ancestor folders cannot be renamed or
deleted but otherwise stay usable.

## Proposals for recording a directory (internal, development only)

`src/runevidence/directory_authoring.py` plans directory edits without making
them itself. The public S4-2b flags (`add --directory`, `rehash --directory`)
call it through the guarded-publication core; without those flags, `add` and
`rehash` still handle files only.

- `propose_add_directory(loaded, raw, *, path, artifact_id, run_id=None, kind=None, volatile=False)`
  proposes one new directory artifact for the folder `path`.
- `propose_rehash_directory(loaded, raw, *, artifact_id)` measures a recorded
  directory again.

Each returns a `DirectoryProposal` holding a changed deep copy of `raw`, the
old and new digest and size, the member count, a `changed` flag, and the IDs
of linked results, citing claims and file artifacts inside the folder. Nothing
is written: no manifest, no backup, no temporary file. The caller's `raw` is
never modified, and any failure raises `DirectoryAuthoringError` (an
`EditError`) with a `reason` and no partial proposal.

For add, mandatory frozen scalar fields `add_kind` and `add_run_id` capture
the caller's optional strings after `Artifact` validation, independently of
the mutable `raw` and `entry` dictionaries. `None` means no value; an empty
kind string remains an empty string. A run ID must name an existing run,
including when the supplied string is empty. Rehash sets both intent fields
to `None`, even if the original artifact has a kind and run ID, because it
introduces no new intent. Its existing values remain in the manifest.

There are no constructor defaults or automatic conversions for proposals
created before these fields existed. Make a new proposal from trusted input
and review it again; copying intent from an earlier mutable entry would
reintroduce the defect. The proposal's internal constructor interface has
changed, while the two proposing function signatures remain the same.

- The root path must pass the name profile and bounds above before anything
  is opened, and the native scanner is the only thing that reads the folder.
  Every scanner refusal, including an unsupported platform or drive, is a
  `scan-failed` error. There is no path-based fallback.
- Add refuses a used ID, an unknown run, `volatile=True`, another artifact at
  the same root or one differing only in case, a directory artifact above or
  below the root, and a file artifact above it. It appends one entry and
  never changes or expands anything else.
- A file artifact inside the folder is allowed and listed. It keeps its own
  file checks, so those bytes are vouched for twice: by the file record and by
  the directory digest.
- Rehash needs a unique directory ID whose path in `raw` matches `loaded`,
  refuses volatile directories, applies the same overlap rules, and changes
  only that entry's `sha256` and `size_bytes`, only when the scan differs.
- A result with a `locator` on the directory is refused, since a locator needs
  a single JSON file. Other linked results are listed and never re-read.

## Saving a proposal (internal, development only)

`src/runevidence/directory_apply.py` has `apply_directory_proposal(loaded,
proposal, *, dry_run)`. No command, check or adapter calls it, and so far it
has only written synthetic manifests in tests. It writes only `loaded.path`,
and only after these steps, in this order:

1. Pure checks: the argument types, that `loaded.manifest` matches
   `loaded.source_bytes`, and that the proposal is exactly one change to a
   fresh parse of those bytes (one appended directory entry, or a new
   `sha256` and `size_bytes` on one directory entry), with the proposal's
   own fields agreeing with that entry. Add's kind and run ID must match its
   separately captured intent; rehash must carry no new intent. Missing or
   corrupted intent is `proposal-mismatch`, before any scan or write.
2. The manifest file must still hold `loaded.source_bytes`; otherwise the
   error is `stale-source`, before the folder is read.
3. The proposal is made again from that fresh parse with a new native scan,
   using add's captured intent rather than recovering it from mutable entries,
   and must equal the given one field for field, with JSON key order and
   value types. A folder that changed is `stale-tree`; a proposal is never
   refreshed. Any scanner or rule refusal is `recheck-failed`.
4. An unchanged rehash or a dry run returns without touching any write path.
   Otherwise the manifest made again in step 3 (never the proposal's own
   mutable `raw` or `entry`) is saved by `save_raw` with
   `expected_bytes=loaded.source_bytes`.

Errors are `DirectoryApplyError` (an `EditError`) with a `reason`. Limits:

- Intent binding detects ordinary changes to `raw` or `entry`, including a
  consistent change to both, and a corrupted scalar with untouched dictionaries.
  It is not cryptographic authenticity. A public Python dataclass is forgeable
  by code with access to its constructor, `object.__setattr__` or process
  memory. Forging all fields consistently is outside this boundary; stronger
  issuer or token trust would need a separately authorized design.
- Not an atomic compare-and-swap. Another writer can change the manifest
  between `save_raw`'s byte comparison and `os.replace`, and the folder
  between the last scan and the save. Both assume one cooperating writer.
  An injected write during scanning exercises an earlier interval only;
  it does not prove safety in the final comparison/replace window. Hostile
  TOCTOU and ABA tree changes remain unresolved.
- A failed save leaves the old manifest as `save_raw` does, and removes its
  staged copy on a best-effort basis. The new file is the staged copy, so on
  Windows it may inherit different permissions from the folder. Preservation
  of the original manifest's custom ACL is not guaranteed. `ReplaceFileW`
  is a separate Windows operation documented to preserve DACLs, but has not
  been adopted here. Byte-range locks do not unconditionally protect against
  noncooperating writers or memory mappings. No ACL or locking policy changes
  are part of this internal fix.

Intent binding is fixed for this internal interface; persistence safety remains
on hold. Writes remain limited to fresh disposable synthetic test manifests.
General user writes and public CLI/CI/platform release need separate approval.
Tests with monkeypatched scanners or injected failures establish apply logic,
not native OS security. Older supported Pydantic versions have not been tested.

## Not covered yet

- Linux `ext4` runs natively under the release policy; other Linux filesystems
  and macOS have not been release-qualified. Windows symbolic links
  were only tested where creating them is allowed (not on the development
  machine without Developer Mode).
- No protection against a malicious process that changes the folder and
  restores its metadata between the scanner's checks. The checks detect
  changes in an experiment that is supposed to be quiet; RunEvidence assumes
  one cooperating writer.
- Alternate data streams, extended attributes, permissions and timestamps are
  not part of the digest.
- No measured memory or time ceiling; only the logical limits above.
- A successful zero-byte NT directory listing is refused (fail closed); that
  native condition has not been reproduced in a test.
- Public surface: `validate`/`inspect --directory-artifacts` reports the
  directory finding codes, and the two explicit S4-2b flags
  (`add --directory`, `rehash --directory`) are the only authoring options.
  There is no adapter support, no batch mode and no other directory option.
- Proposals are only as safe as the scan: they assume a quiet folder and can
  be outdated as soon as they return. No overlap model for nested directory
  records exists, so those are refused rather than given a precedence.

## Cooperative writer lock (internal, development only)

`runevidence.directory_cooperative_apply.apply_directory_proposal_cooperatively`
wraps the save above so that writers which all call it, for the same
manifest, take turns. No command calls it, and nothing else imports it.
`apply_directory_proposal` remains the only checker and the only writer.

- The lock is the sibling file `<manifest name>.cooperative-writer.lock`,
  derived from `loaded.path`. That path must already be the resolved, plain
  file (no `..`, link, junction, other spelling or second hard link).
- An existing lock of any kind gives `busy` (regular file) or
  `invalid-lock-target` before any scan. It is never read, overwritten,
  removed or waited for; there is no retry, sleep or stale-lock age rule.
- A dry run, and a proposal that the full check finds unchanged, never look
  at, make or remove a lock. A changed proposal is checked without the lock,
  then the lock is made by one exclusive create, then everything is checked
  again and saved by `apply_directory_proposal` while it is held.
- The lock is removed only if it is still the same file holding the same
  token. Otherwise, or if removing it fails, it is left in place and the
  call fails with `lock-ownership-lost` or `lock-cleanup-failed`, carrying
  the saved result if the manifest was already written.
- A writer that crashes leaves its lock behind. A person has to find out why
  and remove it; nothing breaks stale locks automatically.

Limits. The lock binds only callers of this function. Editors, scripts, the
public `add` and `rehash` commands and any other writer ignore it, and can
still change the manifest between `save_raw`'s byte comparison and its
`os.replace`: this is still not an atomic compare-and-swap. The lock does
not keep the scanned folder still, does not preserve a custom Windows ACL,
and its token is not security against a deliberate writer. Tested only on
Windows with synthetic manifests, by threads and processes of the tests.

## Read-only change preview (internal, development only)

`runevidence.directory_preview.preview_directory_proposal(loaded, proposal)`
returns an immutable summary after a fresh checked
`apply_directory_proposal(..., dry_run=True)`. This repeats the source-byte
check and native rescan; stale source/tree and authoring refusals remain
errors. Neither public commands, default checks nor adapters import it.

`would-change` describes a checked add or changed rehash, never a saved edit.
An unchanged rehash returns `unchanged`. The summary includes old/new digest
and logical size, member count, and separate linked-result, claim and child
file ID samples. Each sample holds at most 16 sorted IDs, shortened to 160
characters, with total, omitted and truncation fields. The artifact ID and
path have 160/1024-character display limits and a truncation flag. It returns
no manifest text, claim text, result values, member list or mutable proposal.
Impact IDs rely on the checked proposal contract, not cryptographic identity.

Preview only, not a write authorization. It never saves, takes or inspects a
cooperative lock, accepts a claim, re-reads a result value or rehashes a child
file artifact's own record. Member coverage is not integrity verification.
The cooperative quiet-tree assumption remains, with no CAS/ACL guarantee or
hostile-writer containment. A later operation must independently revalidate;
a successful preview lifts no persistence hold.


### Internal single-call directory planner (S3P3P1)

`directory_plan.plan_directory_change` accepts an explicit add or rehash request,
loads the raw validated snapshot, forms a proposal and immediately obtains the
existing checked dry-run preview. Only the frozen, bounded `DirectoryPreview`
is returned; valid requests perform two independent native scans, including an
unchanged rehash. Invalid types/selector combinations fail before scanning.
This internal interface has no public command or default import and never
writes a manifest or accesses cooperative locks. All preview caveats remain: a
quiet cooperating tree is assumed, later operations must revalidate, and a
preview grants no write authorization or CAS/ACL/hostile-writer guarantee.

### Internal bounded directory audit (S3P3P2)

`directory_audit.audit_recorded_directories(loaded)` considers only parsed
recorded directory artifacts, sorted by ID, with a hard capacity of 16.
Each directory uses the existing planner once and retains its original frozen
checked preview, including unchanged rehashes and all impact clipping flags.
The frozen report counts unchanged, would-change and explicitly refused entries.
Expected authoring/checker errors use bounded known reason classifications;
unknown reason text becomes `refused`, never exception messages or raw data.
Duplicate IDs, source/model mismatch, stale source and capacity overflow are
fatal. Source bytes are compared before and after, even with no directories;
`no-directory-records` makes no clean integrity assertion. The default JSON
encoding of the dataclass report is bounded at 1 MiB, with fatal overflow.

This is advisory, read-only and internal, under quiet-tree assumptions. Scans
are non-atomic across directories; source reads do not prevent ABA or races.
Would-change suggests reviewing bytes and metadata, without accepting the new
digest. Unchanged is not an authenticity certificate. Later action must
independently revalidate each directory and manifest. No public startup path
imports the audit; it never writes, authorizes a save or inspects a lock.


### Internal explicit directory batch review (S3P3P3)

`directory_batch.review_directory_changes(loaded, requests)` reviews a tuple
of at most eight frozen `DirectoryChangeRequest` add/rehash intents, in caller
order. It calls the existing planner once per intent and retains each actual
frozen checked preview, including unchanged rehashes and all impact caveats.
Every request uses the same loaded baseline; no cumulative manifest is built.
Shape/count/duplicate-ID checks precede manifest loading and scanning. Loaded
model/source and disk bytes are checked before work and again before return,
even for `no-requests`, which makes no integrity assertion.

Any invalid intent, planner refusal, source/model mismatch, duplicate ID or
component-wise overlapping checked add paths aborts the whole report. Existing
planner exceptions propagate without retry or a partial success report. Windows
case aliases are unsafe; sibling prefixes do not collide. Multi-add paths at
the preview display limit are conservatively refused because clipping cannot
prove non-overlap. Rejecting overlaps does not establish that other changes
can safely be applied together. Default JSON serialization is capped at 1 MiB.

The report is read-only and advisory: independent checks are non-atomic across
requests/directories and assume cooperative quiet trees. Later operations must
revalidate. It grants no persisted write authorization, CAS, Windows custom-ACL,
hostile-writer or ABA protection. It neither writes nor inspects locks, and no
public package, command, default check or adapter imports it.


### S3P3P4: internal supplied-report presentation

`directory_present.render_directory_review` returns deterministic ASCII text
from an existing exact `DirectoryAudit` or `DirectoryBatchReview`. It performs
no I/O or fresh source/tree check and cannot authenticate that a scan happened.
All report and preview caveats remain visible, including on empty/refused
reports. Invalid shape refuses without output; the complete escaped UTF-8 text
is capped at 262,144 bytes with `render-capacity-exceeded` rather than warning
truncation. Labels use JSON ASCII escaping, so control characters, Unicode line
separators, quotes and backslashes cannot inject status lines. Earlier preview
clipping and omitted impact identifiers retain explicit flags and counts.

For example, after the formatter caveats, a supplied empty batch displays:

```text
batch state="no-requests" total=0 adds=0 rehashes=0 would_change=0 unchanged=0
caveat scope="batch" text="Read-only advisory review; independent per-request checks against the same loaded baseline."
```

Every remaining batch caveat follows in full. Nonempty displays include old/new
digests and logical sizes (`null` explicitly means absent), member counts, and
separate linked-result, claim and child-file-overlap identifier groups. These
are identifiers, never result values or claim content. Audit refusals retain
their reason codes; would-change never means written. Rendering is advisory,
non-atomic and never grants write authorization. Later operations must
independently revalidate. No public command, startup module or adapter imports
the formatter, and no persistence or public activation is authorized.


## Internal one-call operator review (S3P3P5)

Internal callers may use the checked workflow and bounded escaped presenter
with one explicit call; this module has no public CLI or package export:

```python
from runevidence.directory_batch import DirectoryChangeRequest
from runevidence.directory_review import review_directories_for_operator

# Audit the recorded directory artifacts.
audit = review_directories_for_operator(loaded)
# Explicitly review no changes: this is a batch with state no-requests.
empty_batch = review_directories_for_operator(loaded, requests=())
# Review independent intents against the same loaded baseline, in this order.
batch = review_directories_for_operator(loaded, requests=(
    DirectoryChangeRequest("add", "new-data", "new-data"),
    DirectoryChangeRequest("rehash", "recorded-data"),
))
# Each frozen result retains mode, the actual structured report, text and caveats.
```

Inspect the structured report and all caveats as well as its text. Audit
refusals remain visible; fatal service and renderer errors propagate without
partial output or fallback. The wrapper dispatches once and renders that exact
report once. The renderer displays supplied data; only the delegated checked
service performs scans, with necessary read-only filesystem handles. Separate
member scans and directory checks are non-atomic and can race; a preview may
already be stale. Neither unchanged nor would-change authorizes applying a
change. Any later action requires fresh validation. No timestamp, freshness,
authentication, atomic consistency, cross-request transaction, writer protection
or persistence permission can be inferred from this result. Injected test mocks
prove control flow only. Public startup and file-only workflows are unchanged.


## S3P3P7: writer race and persistence safety characterization (2026-11-01)

**Decision: S3P3P7 PARTIAL/SAFETY HOLD. Real directory writes and public
activation remain NO-GO pending a separately reviewed design and independent
adjudication.** No production writer was changed. This gate characterizes
existing behavior; it supplies no CAS fix or custom-DACL guarantee.

### Observed local counterexample and boundaries

`tests/test_directory_writer_races.py` adds synthetic-only characterizations.
The executed initial revision used fresh disposable files under the user's
Temp root `rg7-9c1987b7`. One test temporarily hooks only `os.replace`, after
the real `save_raw` comparison has returned equal, then delegates to the
original `os.replace`. It neither replaces byte reads nor mocks success.

The observed order was: stage C and fsync; genuine `read_bytes() == A` check;
enter replacement hook with real target A and staged C; write real competing
B to that target and read B back; delegate to real replacement; return None
with real final C. B was lost. Exact JSON bytes (hex), hashes and ordered
events are retained in `observed-receipts.json` and the fixture receipt.

| Executed primary bytes | Length | SHA-256 |
| --- | ---: | --- |
| A, original | 85 | `03e4abad0eeb0df348fc4a3c138de0cd31ccaeb808e20acac98e06bcce30130f` |
| B, nonparticipating competitor | 96 | `2b037073cc0a675cbdb56ab4a9b7098c2199f0efa9472d1e4fa03e412572a300` |
| C, proposed and final | 94 | `d0a5c0f8c2c78cfa1565b9bf7f2f38628f5539e6f40f5c8668e456dc4855d7ea` |

Positive controls used real files: B present before comparison raised the
documented OSError and retained B; a missing source raised FileNotFoundError
without creating a manifest. Both removed newly staged temporary files.
Actual A -> B -> A writes with readback were accepted and C saved. Equality
can report current content but has no version/history information. This ABA
observation establishes that limitation, not general write safety.

These are controlled local interleavings, not live multi-process coverage.
The initial fixture omitted the model's required `project.id`. `save_raw`
does not validate a Manifest, so the raw-byte counterexample and controls
executed, but four new DirectoryProposal/apply cases and the `save_manifest`
DACL case failed model validation before their intended experiment. The
fixture was corrected and collection re-proven; the final revision was not
executed after the safety stop below. No checked/cooperative interfering
replacement result or final-revision pass is claimed.

### Existing guards and cooperative-lock boundary

Pinned-source inspection establishes the checked apply order: validate loaded
snapshot/proposal; compare current manifest with loaded source; rebuild using
a fresh native directory scan; compare the rebuilt proposal; call `save_raw`
with expected source bytes. The cooperative path checks once without a lock,
exclusively creates its sibling lock, checks source and ownership under that
lock, and performs checked apply again before releasing its own lock.

Existing native cooperative tests passed in the partial focused attempt,
including ordinary native saves and lock-lifetime checks. The new add/rehash
interference cases were not reached because of the fixture error. Their code
places a real competitor replacement after the re-scan, before `save_raw`'s
last comparison; the competitor does not call any lock API. This is intended
guard-placement coverage, currently unexecuted in the corrected revision.
The new code saves actual held-lock bytes separately before normal production
release; it does not suppress release or touch historical locks.

The lock serializes only callers following its protocol. Editors, scripts
and other writer entry points can ignore it. Additional ordinary comparisons
move the last observation but leave a later interval before replacement.
`os.replace` publishes a whole staged file; it does not atomically condition
publication on the target's expected bytes or history. Neither mechanism
establishes CAS against nonparticipating writers or freezes scanned members.

### Independent Windows DACL problem

Both existing-file writers create `NamedTemporaryFile` beside the target,
write/flush/fsync, and replace the target with that staged object. Only the
non-Windows branch copies POSIX mode bits. No existing descriptor is read,
copied or assigned by these writer paths; `directory_apply` delegates to
`save_raw`. `save_manifest` has no expected-byte comparison at all. A new-file
`save_manifest` follows its direct `_write` branch instead.

The executed `save_raw` observation called only read APIs
`GetFileSecurityW` and `ConvertSecurityDescriptorToStringSecurityDescriptorW`
on a default-created synthetic target and its newly created staging file.
Original, staged and final DACL strings were all
`D:(A;;FA;;;SY)(A;;FA;;;BA)(A;;FA;;;OW)`. This is consistent with default
creation, but cannot distinguish a copy from coincident defaults, prove
inheritance details, or establish custom-DACL preservation. The source-only
test found no security-descriptor transfer. The `save_manifest` observation
did not reach its writer in the executed revision. No ACL, owner, privilege
or security descriptor was set, removed, reset, copied or altered. No custom
DACL fixture was made and no historical unreadable folder was accessed.

### Validation, preservation and evidence limits

The final collection proof has 1,400 baseline identities, 1,398 selected,
and 798 focused. Baseline minus selected is exactly the two prohibited
unreadable-member scanner IDs; reverse difference is empty. All new cases
are in the focused subset. Collections ran no test bodies or fixtures.
Execution of the initial focused revision produced 710 passed, 8 skipped,
5 failed and 75 setup errors, with 602 deselected. Every one of its 798
starts and JUnit classname/name pairs maps exactly to its collected identity;
neither banned setup started, and no xpass/xfail occurred.

The 75 setup errors were existing `shutil.copytree` fixtures: stdlib
`copystat` indirectly calls `os.chmod`. The external runtime audit guard
blocked those calls before they reached the OS. Recursive repository source
inspection had missed this indirect dependency. The guard was kept intact;
execution stopped rather than allowing permission-mode mutation or changing
existing fixtures. The broad suite was not run. Eight skips are accounted
for by Windows case behavior, unavailable symbolic-link privilege and POSIX
cases. One recorded pytest warning is the existing `record_property` versus
JUnit xunit2 incompatibility, not a product warning.

Evidence is retained outside both checkouts at the fresh Temp root above:
initial and final source revisions, scripts and hashes, raw stdout/stderr,
all collection attempts/proofs, JUnit and runtime origins, blocked calls,
receipts and preserved basetemp fixtures. Before/after hashed inventories
include ignored files and non-followed reparse metadata, both indexes and
FETCH_HEAD, raw diffs/statuses and Git binding/ref records. The original
checkout must remain unchanged; only this byte-prefix-preserving appendix
and the one new test module are authorized target changes. Historical
S3P3P6 `after.json` was unavailable on this Windows machine, so preservation
is measured against the captured current baseline and all 14 supplied pins.
The 272 historical ref identities cannot be independently reconstructed
without that snapshot; all 274 refs in this attempt's baseline are protected.
No remote CI or independent external verification is claimed.

### Choices requiring a separate design review

| Candidate policy/design | Nonparticipating writer and ABA boundary | Windows DACL and remaining work | Gate position |
| --- | --- | --- | --- |
| Keep internal review read-only | No new persisted directory update to lose | No replacement-related DACL change | Retain now |
| Enforce one writer for every writer path | Requires an enforceable admission rule; a voluntary lock alone is insufficient | Descriptor policy still separate | Review policy and enforcement first |
| Dedicated transactional record store/service with monotonic versions | Could provide conditional updates and history if every supported write uses it | Migration, export semantics and permissions need specification | Candidate architecture, unimplemented |
| OS-specific replacement/handle protocol | Must prove admission and atomic conditions for supported writers; another byte check is insufficient | Review documented descriptor behavior and authorized custom-DACL validation separately | Research only, no implementation authorized |
| Explicitly restrict supported ACL/writer environments | Requires a justified, enforceable refusal policy; does not fix races | Cannot infer custom-DACL safety from default-only observations | Separate policy decision |

No speculative CAS, lock stealing, DACL fixer or OS hook is approved. Restore
neither real writes nor public activation on the strength of this partial
characterization. Independent adjudication and a separately reviewed design
remain required.


### S3P3P7 preservation closeout qualification

The post-inventory comparison found no original regular-file additions,
removals, byte changes or metadata changes. All target prior regular-file
bytes survived except the authorized appended document. Both indexes,
FETCH_HEAD files, HEADs, branches, trees, staged/tracked diffs, worktree
registrations and all 274 captured ref name/object pairs are unchanged.
However, 90 original `.git` directories report size 0 before versus 4096
after. Every other recorded field, including modification time, identity,
mode and attributes, is identical. Read-only rechecks retained 4096 for
sample directories. The cause is unproven; raw records are preserved in
`raw-preservation-differences.json` and `directory-size-recheck.json`.
This is an explicit unresolved metadata qualification, not a claim of full
metadata equality or proof of historical S3P3P6 preservation. Execution was
already stopped; return for independent review with SAFETY HOLD retained.
