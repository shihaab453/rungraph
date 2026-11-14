# Public directory authoring: S4 design (RFC)

**Status: IMPLEMENTED THROUGH S4-2b (2026-11-12). The internal S4-1 core is accepted;
S4-2a (built-in writer admission lock) is closed; S4-2b (public `add PATH DIR --directory`
/ `rehash PATH ID --directory`, Linux/ext4, text only) is implemented under owner
authorization and is delivered only after the G4-2b independent review accepts it. General
S4 public directory authoring beyond those two explicit flags remains NO-GO, as do Windows
parity, batch and any CAS/R5 claim. This v0.3 text is otherwise the accepted design; the
§4.3 examples are corrected to the implemented fixed wording.**

- Version: **v0.3** (final S4-1 text pass; v0.2 was the review-correction revision)
- Date: 2026-11-12
- Milestone: **M44-S4-DIRECTORY-AUTHORING-DESIGN**
- Workspace: `/home/user/projects/rungraph` on Boat Box (Linux, Python 3.12.3 in
  `.venv`; package supports Python >= 3.10)
- Authority: JSON manifests remain the production authority. SQLite remains dormant.
  No Git, publication, version bump or public route is part of this work.
- Baseline: native directory scanner S1-S3 is complete; `ext4` is the only
  release-qualified Linux filesystem; `validate`/`inspect --directory-artifacts` is the
  only public directory surface and is read-only. S3P3P7 remains a **SAFETY HOLD**.
- Non-regression: accepted Stage 3 / 3d semantics, the default `validate`/`inspect`
  behavior, the 26-route CLI, the scanner S3 contracts, and the R5 boundary are not
  reopened here.

## Changelog

- **v0.3 (final S4-1 text pass):** made `INTERNAL_ERROR` three-phase with exact wording and
  a step-11 final-guard injection test; split backup failure before/after the `.bak.N`
  link and made a post-link directory-`fsync` failure refuse replacement while preserving
  the complete backup; made inode-flag handling exact on the user-settable mask and
  fail-closed for unknown/unclassified bits; clarified that `security.*` labels are
  policy-assigned and are verified/rejected rather than deleted; named the project-ID/
  `PROJINHERIT` `EXDEV` -> `write-failed` path; made dry-run explicitly no-`fsync`,
  read-only and free of source-durability/write-permission refusals; and applied the G5,
  lock-step-3, checked-apply re-read, post-metadata `fsync` order, S4-2a prerequisite and
  absolute-`DIR` symlink nits. Still design-only; no code, schema, example, README or
  progress change.
- **v0.2 (review corrections):** added the private staging-cell policy for inherited
  ACL/xattr exposure; switched staging/backup descriptors to `O_RDWR` with held-descriptor
  read-back; added the source/parent durability precondition and process-crash vs
  power-loss distinction; added the public `INTERNAL_ERROR` boundary (v0.2's two-phase
  wording is superseded by v0.3's three phases); narrowed G2/G6/G7 and the success criterion; made backups
  always-fresh with temp+`os.link` publication and newest+1 numbering; made all manifest
  operations descriptor-relative to the locked folder; added mount-info filesystem
  classification, symlink-alias rules, lock lifetime/self-busy rules, phase-specific
  assertions, subprocess termination and second-user access tests, and S4-2a lock-retrofit
  entry conditions. Still design-only; no code, schema, example, README or progress change.
- **v0.1 (initial design):** inventories the S3P3P7 gaps, proposes a guarded-publication
  protocol with an explicit non-CAS guarantee, defines a narrowed Linux/ext4 public
  surface, directory-record semantics, tests, slices/HOLDs, alternatives and residual
  risks. No code, schema, example, README or progress change.

---

## 0. Decision in one paragraph

General public directory authoring is **not safe enough under the current JSON-authority
architecture**, because no available primitive makes "replace the manifest only if it
still holds these exact bytes" atomic against a nonparticipating writer. The only
defensible narrowing is an **explicit, offline, Linux/ext4-only `add --directory` and
`rehash --directory`** mode on the existing commands: one directory per invocation, an
advisory admission lock shared by this version's built-in manifest writers, a private
staging cell, a durable backup, source/parent durability, a staged-and-fsynced manifest,
`os.replace`, a parent-directory `fsync`, and an exact metadata preflight. That protocol can
guarantee serialization among cooperating RunEvidence writers, whole-file atomic publication
for this replacement operation, stale-source/tree detection up to the last check, and
recoverability from a backup when the old source was durable. It **cannot** guarantee
compare-and-swap (CAS) against editors, scripts, older RunEvidence versions, library
callers or other nonparticipating writers, does not freeze the scanned tree, cannot detect
every metadata attribute on every host, and does not preserve Windows DACLs. The
recommendation is to keep general S4 **NO-GO**: this v0.3 revision permits the internal
S4-1 core (both reviewers accept it after these corrections); S4-2a (lock retrofit) is
closed and S4-2b (public directory write) is implemented under owner authorization,
pending G4-2b acceptance. If the owner requires no-lost-update protection against
nonparticipating writers, or Windows parity, those remain outside the accepted guarantee.

---

## 1. Scope, non-goals and exactly what S4 would add publicly

### 1.1 What "S4" means here

"S4" is the gate after S3 that decides whether public directory authoring/writing can
become safe. This RFC describes **S4-v1**, a deliberately narrow candidate for a future,
separately authorized implementation. It does not add behavior now.

S4-v1 would add exactly these public option strings and no route:

| Command | New explicit mode | What it does | What it never does |
| --- | --- | --- | --- |
| `runevidence add PATH DIR --directory --id ID` | `--directory` on `add` | Records the folder `DIR` as **one** directory artifact, appended to the manifest. | Does not expand the folder into file records, does not infer a directory from a bare folder argument, does not infer a run, does not derive an ID. |
| `runevidence rehash PATH ID --directory` | `--directory` on `rehash` | Re-measures **one** recorded directory artifact and updates only its `sha256` and `size_bytes`. | Does not accept `--all`, globs, folder/run selectors, `--volatile`, or a file artifact. |

The full grammar and refusal rules are in §4. The directory record semantics are in §5.

### 1.2 What S4-v1 deliberately does not add

- **No public write from this revision.** v0.3 accepts only the internal S4-1 core after
  these corrections. S4-2a (admission-lock retrofit, which changes concurrency behavior of
  existing writers) and S4-2b (public `--directory`) remain NO-GO until separately
  authorized.
- **No new CLI route.** The route count stays **26**; only option strings on `add` and
  `rehash` change. A new `directory`/`record` command is rejected in §8.
- **No automatic inference.** A folder passed to plain `add` keeps its current meaning:
  add every untracked file inside it. Directory authoring requires `--directory`. There is
  no "this looks like a directory artifact" heuristic, no add-vs-rehash inference, no run
  inference, and no default ID.
- **No writes by `validate` or `inspect`.** Their existing
  `--directory-artifacts` flag remains read-only. They take no lock, create no backup or
  temporary file, and do not import the authoring/publication modules.
- **No Windows or macOS public authoring.** Windows parity is a separate design. S4-v1
  refuses non-Linux platforms before any scan or write, even though the Windows scanner
  exists. See §4.10.
- **No non-ext4 Linux filesystem.** The release allowlist remains `{"ext4"}`. Other
  filesystems fail closed as `unsupported-filesystem`.
- **No new digest format, schema field or artifact field.** `runevidence-directory-v1`
  and the existing `Artifact` rules are unchanged.
- **No metadata authoring.** S4-v1 does not add `--metadata`, `--dataset-id`,
  `--sha256`, `--size-bytes` or any user-supplied digest. Existing `metadata` and
  `dataset_id` on a rehashed entry are preserved untouched.
- **No removal, rename, nesting or batch transaction.** There is no public remove/rename;
  nested directory artifacts remain refused; multiple directory changes in one invocation
  remain unsupported.
- **No CAS, hostile-writer, snapshot, mandatory-lock, daemon or network claim.**
- **No SQLite, no Linux R5 lane, no version bump, no publication.**
- **No JSON output for `add`/`rehash` in S4-v1.** They remain text-only, as today. A
  machine-readable authoring envelope is deferred to a separate design (§4.3).

### 1.3 What is preserved (non-regression contract)

| Contract | S4-v1 effect |
| --- | --- |
| `validate`/`inspect` default behavior | Unchanged: without the flag, a directory record is still `not-a-file`. |
| `validate`/`inspect --directory-artifacts` | Unchanged: read-only, same finding codes, same privacy boundary. |
| `validate-batch` | Unchanged; no directory flag. |
| `init`/adapters | Unchanged; never author directory artifacts. |
| `migrate`/`conditions` | Manifest bytes/semantics unchanged; the S4-2a admission lock may add a fixed exit-2 `BUSY` refusal and a JSON busy envelope (§7.3). |
| `schema`/`adapters` | Unchanged. `schema -o` is audited as an existing file-overwrite surface; S4 does not use it as an authoring path and does not silently repurpose it (§7.3, §8.2). |
| 26-route CLI and registration order | Unchanged. |
| JSON schema/models | Unchanged. |
| Scanner S3 backend/policy | Unchanged: Linux POSIX no-follow, `ext4` release policy, Windows backend. |
| Stage 3 / 3d comparability/conditions | Unchanged; directory artifacts remain unsupported in comparability. |
| S3P3P7 HOLD | Not lifted by this document. |
| R5 | Unchanged; still Windows-only and separate. |

### 1.4 Success criteria for a future S4-v1 implementation

A future implementation is acceptable only when all of the following are demonstrated:

1. No public write without all of `--directory`, the exact platform/filesystem policy, the
   admission lock, the staging cell, source/parent durability, the complete metadata
   preflight and the durable publication order.
2. `validate --directory-artifacts` accepts the newly written record on a real `ext4`
   tree; a wrong size or digest is an error.
3. A crash injected at every protocol step leaves either the old manifest or the new
   complete manifest, never a truncated one; when G8's source/parent durability succeeded,
   the backup always holds the old complete bytes. A process crash is distinguished from
   power loss; no old-or-new claim is made for a non-durable source. A directory-`fsync`
   failure after a successful `.bak.N` link preserves the complete backup and refuses
   replacement.
4. Two cooperating writers cannot both publish; the loser exits `2` with a fixed `busy`
   message and writes nothing.
5. A nonparticipating writer in the final compare-to-replace window is documented as
   lost, not claimed safe; the negative control is retained.
6. The command performs **no explicit tree mutations** (the G7 list: no write, create,
   delete, chmod, chown, utime or xattr write on the scanned tree). Literal byte/metadata
   identity of the tree is **not** asserted: scanner reads may update `atime`/relatime,
   and concurrent writers are outside the model.
7. The staging cell and cleanup pass a second-user access test at every metadata boundary:
   an inherited default ACL or xattr never exposes the staged manifest or backup before
   the final restrictive metadata is applied.
8. The loaded source and its parent directory are `fsync`ed before staging; a failure
   refuses rather than claiming old-or-new power-loss recovery from a non-durable source.
9. An unexpected exception at the public boundary prints a fixed `INTERNAL_ERROR` response
   with no traceback and uses the three phases in §3.8: before cell -> "nothing was
   written"; cell/backup may remain but manifest unchanged; from just before `os.replace`
   -> "the manifest may have been replaced".
10. The public output never contains the resolved scan root, member names, OS error text,
   inode/device/mount IDs or a newly computed digest from a refused scan.
11. No new route, schema, model, digest format, dependency or network call.
12. Installed wheel/sdist checks pass from outside the checkout; `pip check` passes.
13. Skips are fully accounted for with `-rs`; no existing Windows test is rewritten to
    claim parity. Skipped ext4/ACL/second-user coverage is recorded as missing evidence,
    not as a pass.

---

## 2. Current S3P3P7 gap inventory

### 2.1 What already exists internally

The following modules are implemented and tested, but **no command calls them for a
public write**:

- `directory_authoring.py`: `propose_add_directory` / `propose_rehash_directory` build a
  frozen `DirectoryProposal` from a validated manifest snapshot and a native scan. They
  never write. They enforce the v1 root profile, limits, duplicate IDs, unknown runs,
  volatility refusal, locator refusal, overlap/nesting rules, and case-insensitive
  path-collision checks.
- `directory_apply.py`: `apply_directory_proposal` performs pure one-change checks against
  a fresh parse, a source-byte check, a fresh native re-scan, a field-for-field proposal
  comparison, and a `save_raw(..., expected_bytes=...)` write unless dry-run/unchanged.
- `directory_cooperative_apply.py`: wraps the apply with the sibling
  `<manifest>.cooperative-writer.lock` (one exclusive `O_CREAT|O_EXCL` create, token and
  identity checks, no wait/retry/stale-lock recovery). It is internal-only.
- `directory_preview.py`, `directory_plan.py`, `directory_audit.py`,
  `directory_batch.py`, `directory_present.py`, `directory_review.py`: read-only,
  bounded, advisory, and explicitly non-authorizing.
- `directory_hashing.py` and `directory_scanner.py`: canonical v1 stream; native
  no-follow, handle-relative Linux scanner with `O_PATH` probes, pinned
  `/proc/self/fd` reopens, identity/mount checks and a two-pass stability detector.
- `checks/directory_policy.py` and `checks/files.py`: the public read-only
  `validate`/`inspect --directory-artifacts` path, with sanitized public findings.

### 2.2 Gap table: handled internally vs not handled

The S3P3P7 characterization and the pinned sources establish the following. "Handled"
means an existing internal mechanism addresses the concern for its intended internal
caller; it does not mean a public write is safe.

| Concern | What exists today | Handled internally? | Gap for public S4 |
| --- | --- | --- | --- |
| **Source-vs-manifest race** | `save_raw` stages, fsyncs, compares the current file with `expected_bytes`, then `os.replace`. `apply_directory_proposal` re-checks the source before the fresh scan. | Partly. A change before the comparison is refused (`stale-source`). | The comparison and `os.replace` are not one atomic step. S3P3P7's controlled counterexample wrote a real competitor `B` after the genuine comparison returned equal; the final `C` won and `B` was lost. |
| **Compare-and-swap (CAS)** | None. No content-conditional replace primitive is used. | **No.** | There is no portable kernel primitive for "rename only if the target still holds these bytes." Advisory locks bind only participants. A manifest generation field would be a schema change and still ignored by external writers. |
| **Crash / durability** | Staged file is written, flushed and `fsync`ed; POSIX mode is copied; `os.replace`; staged cleanup is best-effort. `save_manifest` has a direct `_write` path for new files. | Partly. Readers see a complete old or new file, never a torn JSON file. | No parent-directory `fsync`, so rename durability after power loss is not established. No backup in `add`/`rehash`/apply. A crash can leave `.{manifest}.*.tmp`; `is_own_file` does not recognize it. |
| **Source durability** | `load_manifest` reads the manifest; no `fsync` of the source or parent before a write. | **No.** | G8 requires source+parent `fsync` or `source-durability-failed`; a just-created, non-fsynced manifest cannot support an old-or-new power-loss claim. |
| **Descriptor read-back** | `save_raw` writes with a `NamedTemporaryFile` handle and does not read the staged file back through the write descriptor. | Not applicable internally. | Staging/backup must use `O_RDWR` and `pread`/seek verification through the held descriptor; an `O_WRONLY` descriptor would make read-back fail with `EBADF`. |
| **Staging exposure** | Internal apply creates the staged file directly beside the manifest; no private cell/default-ACL handling. | **No.** | A folder default ACL can expose the staged file before the original ACL is copied. S4 uses an owner-only cell first and a complete-set both-ways comparison. |
| **ACLs / xattrs / modes / ownership** | POSIX `chmod` copies only `stat.S_IMODE` to the staged file. Windows DACL is not transferred. | Mode bits only, POSIX, best-effort. | No POSIX ACL (`system.posix_acl_access`), no `user.*`/`security.*`/`trusted.*` xattr, no owner/group preservation, no timestamp preservation, no Windows DACL. The S3P3P7 Windows observation was default-DACL-only and did not reach the `save_manifest` writer in the executed revision. |
| **Hardlinks / symlinks** | The scanner refuses links, hardlinks and special files in the tree. The cooperative path requires the manifest to be a plain resolved file with one link. `save_raw` itself does not check links/hardlinks. | Tree: yes. Manifest: only in the cooperative wrapper. | A public durable writer must require a plain, non-symlink, `nlink == 1` manifest and use `O_NOFOLLOW`/`lstat` identity. A final-window swap remains possible. |
| **Concurrent writers** | Cooperative lock serializes callers that use it. Every other writer ignores it. | Only opt-in callers. | No admission lock across built-in commands. A file `add`/`rehash` or an external editor can still race an S4 directory write. |
| **Cooperative locks** | Sibling `O_EXCL` lock with token/identity; no retry/sleep; a crash leaves the lock and a person must remove it; no stale-age rule. | Internal only. | Not suitable as a public admission lock: it leaves an artifact, requires manual recovery, and only wraps apply, not the load/proposal phase. |
| **Rollback / backup** | `init --force`, `migrate` and `conditions` keep `.bak`, `.bak.2`, ... generations, never overwrite/delete earlier ones. `add`/`rehash` and directory apply do not back up. | Partial. | No backup for directory writes; no restore command; no documented rollback procedure for a directory record. |
| **Digest/path TOCTOU** | Scanner verifies root spelling, component identities, mount ids, two-pass metadata; authoring validates the relative v1 profile before scanning; apply rebuilds and compares. | Ordinary local mutation: detected. | The digest does not encode the root path or metadata. The path is a label, not tree identity. `plan_add` resolves symlinks for file mode; a directory mode must not reuse that. A tree change after the final scan is not detected. |
| **Scanner-vs-write race** | Scanner is read-only; two-pass metadata; identity/`mtime`/`ctime` checks; no freeze. | Detection only. | A writer can change the tree during or after the scan; ABA with restored metadata is invisible; there is no snapshot. |
| **Manifest atomic publication** | `os.replace` publishes a whole staged file. | Yes, for concurrent readers on POSIX. | Not conditional; last writer wins. No parent-dir fsync. |
| **Multi-directory transaction** | Audit/batch are read-only, independent, bounded (16/8), and explicitly non-transactional. | Read-only only. | No cumulative manifest, no transaction, no rollback across multiple directories. |
| **Privacy** | Public directory findings use fixed reason/class text and the artifact ID/recorded path. Authoring/apply exceptions remain verbose internally. | Validate/inspect only. | A public authoring CLI must not print `str(exc)` for scan failures (it can contain the absolute root, member names, OS text, inode/mount ids). |
| **Public unexpected boundary** | Expected `EditError` paths print a summary; unexpected exceptions can propagate and print a traceback. | No public authoring surface yet. | Add a fixed `INTERNAL_ERROR` response, no traceback/exception text, with three phases: before cell ("nothing"), cell/backup may remain but manifest unchanged, and from just before `os.replace` ("may have been replaced"). Internal APIs may still raise. |
| **Platform parity** | Windows native scanner exists; Windows DACL/writer behavior unresolved. | Linux ext4 validation; Windows scanning. | No Windows public authoring; no DACL/`ReplaceFileW` evidence; no parity claim. |
| **Performance / DoS** | Logical bounds (10,000 members, 1 GiB, depth 64, etc.); no wall-clock deadline; one file open at a time. | Bounds only. | A real directory write needs a second full scan; holding the admission lock during that scan can make other writers wait or see `busy`. No timeout/lease. |
| **Evidence** | Synthetic/injected writer-race tests; no live multi-process safety proof; S3P3P7 final revision was not executed after the safety stop; no custom-ACL fixture was completed. | Characterized, not proven. | A public gate needs new injected crash tests, real subprocess lock tests, and explicit evidence limits. Injected tests are not native OS security proof. |

### 2.3 The S3P3P7 counterexample, in context

The executed characterization used real disposable files. After `save_raw`'s genuine
`read_bytes() == A` comparison returned equal, a test hook wrote competitor `B` to the
real target, then delegated to the real `os.replace`, which installed `C`. The observed
final file was `C`; `B` was lost. The recorded bytes were:

| Role | Length | SHA-256 |
| --- | ---: | --- |
| A, original | 85 | `03e4abad0eeb0df348fc4a3c138de0cd31ccaeb808e20acac98e06bcce30130f` |
| B, nonparticipating competitor | 96 | `2b037073cc0a675cbdb56ab4a9b7098c2199f0efa9472d1e4fa03e412572a300` |
| C, proposed and final | 94 | `d0a5c0f8c2c78cfa1565b9bf7f2f38628f5539e6f40f5c8668e456dc4855d7ea` |

The positive controls (B present before the comparison, and a missing source) behaved as
documented; an actual A -> B -> A interleaving was accepted because byte equality carries
no history. This is the basis for the no-CAS rule in §3.

### 2.4 What "internal" buys and does not buy

Internal modules are deliberately **non-authorizing**: previews, plans, audits, batches
and presenter output repeat that they never grant write permission, later operations must
revalidate, and `unchanged` is not an authenticity certificate. S4 must not turn any of
those advisory results into a write token. A future public command must recompute under
its own admission rule and must not accept a caller-supplied preview/proposal as
authorization.

---

## 3. Safety design: the guarded-publication protocol (S4-GP/1)

### 3.1 Exact guarantee (and the words we do not use)

**S4-GP/1** is the proposed publication protocol. Its exact guarantee is:

| # | Guarantee | Meaning |
| --- | --- | --- |
| G1 | **Cooperating-writer serialization** | Every built-in RunEvidence command in **this installed version's CLI** that writes the manifest takes the same admission lock for the manifest's folder. At most one such writer holds it at a time. A loser refuses with `busy`; it never waits, retries or sleeps. Older RunEvidence versions, library callers, the internal `directory_cooperative_apply` sibling lock, editors and scripts are **nonparticipants**; G1 says nothing about them. |
| G2 | **Whole-file atomic publication for this operation** | For this publication's `os.replace`, a concurrent reader of the manifest path sees the complete old JSON or the complete new JSON, never a partial document, on the release-qualified local filesystem. An unrelated writer that truncates or rewrites the manifest in place can still expose partial JSON; G2 does not cover that writer. |
| G3 | **Stale-source detection up to the last check** | If the manifest bytes or identity differ from the loaded snapshot at the final check, the write is refused (`stale-source`/`stale-metadata`) and the existing file is not replaced. |
| G4 | **Stale-tree detection up to the last scan** | If the folder's digest, size or member count differs between the proposal and the checked rebuild, the write is refused (`stale-tree`). |
| G5 | **Crash-safe ordering, with source durability** | The loaded source and its parent directory are `fsync`ed before staging; the staged manifest and the backup are fully written and `fsync`ed before replacement; the parent directory is `fsync`ed after backup publication and after replacement. A **process crash** before replacement leaves the old source readable from the page cache; it is on disk only after step 4's source `fsync` completes. A **power loss** is only covered when the source durability precondition succeeded. After replacement, a process crash leaves a complete new name; a power loss leaves the new name or the durable old name as the filesystem recovers (filesystem-dependent). No torn JSON file is produced by this operation's replacement. |
| G6 | **Metadata preconditions (detect or narrow)** | The manifest's owner/group/mode, default and access POSIX ACLs, xattrs, inode flags (`FS_IOC_GETFLAGS`) and project ID (where detectable) are preserved exactly on the complete resulting attribute set, or the write is refused before replacement. The comparison is **both ways**: every source attribute must exist on the staged file, and every staged attribute must exist on the source (an inherited **non-security** attribute absent from the source is removed or refused). `security.*` labels are policy-assigned: they are verified against the source/expected policy label and fail closed, **never deleted**. Inode flags are detected in full; immutable/append refuse; the **user-settable mask must be exactly equal** on source and staged; filesystem-managed bits are recorded under an explicit G6 narrowing rather than a portability claim; any **unknown/unclassified flag bit refuses** (`metadata-unsupported`). Unsupported metadata is a refusal, not a silent loss. Where an ioctl/namespace cannot be inspected, G6 is explicitly narrowed and the gate records the missing evidence; it is not claimed as preserved. |
| G7 | **No explicit tree mutations** | The command performs no explicit write, create, delete, chmod, chown, utime or xattr write on the scanned tree. Only the manifest folder (an ancestor of the recorded folder, never inside it) is written. Scanner reads may update `atime`/relatime, and concurrent writers are not controlled; literal tree metadata identity is not claimed. |
| G8 | **Source-durability precondition** | Before staging, the loaded manifest file and its parent directory are `fsync`ed, or the write is refused (`source-durability-failed`). The old-or-new recovery statement in G5 is not claimed for a source that was never durable (for example, a manifest created immediately before the command and not fsynced). |

**We do not claim CAS.** The protocol has a compare step and a replace step, but they are
not one atomic operation. A nonparticipating writer can change the target between the last
comparison and `os.replace`, and its bytes can be lost. That is a documented limitation,
not a bug to be waved away. We also do not claim:

- a snapshot or freeze of the scanned tree;
- whole-file atomicity for an unrelated writer that truncates/rewrites the manifest in
  place (G2 covers only this operation's `os.replace`);
- detection of an ABA change that restores all compared metadata;
- protection from a hostile same-user process, a privileged writer, a hostile kernel/FUSE,
  or a malicious filesystem;
- Windows DACL/owner/attribute preservation;
- preservation of every metadata attribute on every host (G6 is detect-or-narrow for some
  namespaces/flags);
- power-loss recovery without G8's durable source/parent precondition;
- authenticity, provenance or freshness of the recorded digest;
- no-lost-update semantics for older versions, library callers, `cooperative_apply`,
  editors, scripts, IDEs, sync clients or any writer that does not take the admission lock.

### 3.2 Admission preconditions

Before any scan or write, S4-v1 must establish all of the following. A failure is a fixed
refusal with exit `2`; the manifest is not replaced. Failures before the staging cell
create no artifact; a cell-creation/cleanup failure is phase-specific (§3.8).

| Precondition | Check |
| --- | --- |
| Platform | `sys.platform.startswith("linux")`; otherwise `unsupported-platform`. |
| Filesystem | The scanned root passes the scanner's release policy (`ext4`). The manifest's own folder is classified by the **same mount-info/mount-id proof** as the scanner (fresh `/proc/self/mountinfo`, proven nonzero mount id, release-qualified type), not by `statfs`/`st_dev` alone; network/pseudo/unknown filesystems are refused. |
| Manifest path | The resolved manifest target is an absolute, plain regular file, `nlink == 1`, not a junction/special file. A symlink **alias to the manifest file** is accepted only when `resolve(strict=True)` reaches that plain file; the write replaces the resolved target, not the alias. A symlink in the **artifact-relative directory path** is never accepted; the scanner refuses it. The parent is a plain directory. |
| Manifest access | The effective user can read and write the manifest and can write/execute the parent folder. A read-only manifest is refused before scanning. |
| Manifest ownership | The effective user owns the manifest, or is root and can restore the exact owner/group. Otherwise `metadata-unsupported`. |
| Metadata | Every detectable ACL/xattr, default ACL, inode flag and project ID is supported by the preservation policy in §3.9, or G6 is explicitly narrowed with recorded evidence; otherwise `metadata-unsupported`. |
| Source durability | The loaded manifest fd and its parent directory are `fsync`able; the protocol `fsync`s them before staging. A failure is `source-durability-failed`; old-or-new power-loss recovery is not claimed. A dry-run skips this write precondition and does not require write permission. |
| Directory root | Manifest-relative, strict v1 profile, within depth/path bounds, no protected names, no links/hardlinks/specials, not volatile. |
| Command shape | Exactly one directory, exactly one explicit mode (`--directory`), exact `--id` for add, exact directory ID for rehash; no `--all`/globs/`--volatile`/`--json`. |
| No user digest | The command accepts no `--sha256`/`--size-bytes`; the scanner is the only source of `sha256`/`size_bytes`. |

"Resolved" here follows existing `load_manifest` behavior: the manifest target is
resolved, but the **directory path** is not `realpath`-resolved. Its components are
lexically normalized, required to stay within the manifest root, and then checked by the
scanner's exact-spelling/no-follow walk. Resolving the directory path would hide a
symlink alias; the scanner must see it and refuse it.

### 3.3 Admission lock: a stable directory-fd advisory lock

**Chosen model:** an exclusive, non-blocking `flock(LOCK_EX | LOCK_NB)` on an
`O_RDONLY | O_DIRECTORY | O_CLOEXEC` descriptor of the manifest's **parent directory**.

Why this model:

- The lock is on a stable directory inode. `os.replace` changes the manifest inode, so
  locking the manifest file itself would let two writers lock different inodes across a
  replacement.
- It creates **no lock artifact**: no sibling lock file, no `/tmp` state, no
  `is_own_file`/name-profile change, no untracked-file noise in `inspect`.
- The kernel releases the lock automatically on process exit or crash. There is no stale
  lock to remove, no token, no age rule, no manual recovery step.
- It serializes all manifests in the same folder. That is coarser than per-manifest, but
  an experiment folder normally has one manifest; the simplicity is worth it.
- It is advisory only. It binds writers that take it. It does not stop `mv`, `rm`, an
  editor, a sync client or a script.

Lock lifecycle:

1. Resolve the manifest target (file or folder containing `runevidence.json`) as today.
   A symlink alias to the manifest file is accepted if `resolve(strict=True)` reaches a
   plain regular file; the resolved target is the write target. A symlink alias in the
   artifact-relative directory path is refused by the scanner, not resolved away.
2. **Acquire the lock separately from loading.** Open the resolved parent directory
   read-only, `O_DIRECTORY|O_CLOEXEC`. `fstat` it and record `(st_dev, st_ino)`.
   `flock(LOCK_EX|LOCK_NB)`. On `EWOULDBLOCK`/`EAGAIN`, print a fixed `busy` message and
   exit `2`. Do not wait, sleep, retry or spin. This step needs no manifest, so `init` on a
   folder with no manifest can take the lock before creating one.
3. **Load under the same held handle.** Re-open the manifest with
   `O_RDONLY|O_NOFOLLOW|O_CLOEXEC` via `dir_fd`, verify regular/`nlink == 1`, read its
   bytes from the fd, and verify the path's `lstat` identity matches the fd. This load is
   the authoritative snapshot; there is no **earlier** snapshot to compare against at this
   point. Later checks (step 7 and the final guard) compare against this snapshot. Do not
   acquire the lock again for the load, proposal, rebuild or publication; pass the single
   lock handle through the operation.
4. **One handle, one acquisition.** A second acquisition in the same process (a second
   `open` + `flock` on the same folder) must fail `busy`; this is the self-busy test. The
   operation must not fork while holding the lock; `O_CLOEXEC` prevents exec inheritance,
   but `fork` inherits the descriptor and the lock, so the protocol must not fork or spawn
   a child that outlives the operation. A child that inherits the fd must not be relied on
   to release it.
5. Hold the lock through the proposal, the checked rebuild, staging-cell creation, backup,
   replacement and the parent `fsync`. Release by closing the fd in `finally`; `flock` is
   released by the kernel. Release is idempotent and happens on every exit path, including
   expected refusals and unexpected exceptions. A close failure is a cleanup warning, not
   a second write.
6. Before replacement, re-verify the parent directory identity; if it changed, refuse
   (`lock-ownership-lost`/`stale-source`). A renamed directory keeps the same inode and
   the lock still applies; a replaced directory does not. All later manifest operations use
   the held directory fd (`dir_fd`), not the path, so a parent swap cannot redirect them.

Failure behavior:

| Condition | Result |
| --- | --- |
| Lock busy | `busy`, exit `2`, nothing written, no scan started. |
| Parent unreadable / `O_DIRECTORY` unsupported / `flock` unsupported | `lock-failed` or `unsupported-platform`, exit `2`, nothing written. |
| Process crashes or is killed | Kernel releases the lock. No stale lock remains. The next writer proceeds. |
| Process hangs while holding the lock | Other built-in writers see `busy` until it exits. There is no timeout or lock stealing. This is a liveness limitation, not a safety claim. |
| Fork/exec while held | `O_CLOEXEC` blocks exec inheritance; the protocol must not fork. An inherited fd is a bug/refusal, not a sharing mechanism. |
| Nonparticipating writer | Ignored by the lock. The final compare/replace window remains. Documented in §3.10. |

**Retrofit requirement.** "Cooperating" means **only this installed version's CLI** for
the built-in manifest-writing commands: `init` (including `--force`), `migrate`,
`conditions`, `add` and `rehash`. Read-only commands (`validate`, `validate-batch`,
`inspect`, comparability, reports, `schema` without `-o`, `adapters`) never take it. Older
RunEvidence versions, library callers, the internal `directory_cooperative_apply` sibling
lock, editors, scripts and sync clients are nonparticipants. **S4-2a is a hard
prerequisite for S4-2b, not an optional retrofit.** If the owner declines S4-2a, S4-2b
remains NO-GO because a concurrent `init --force` or file `rehash` could destroy a
directory write; the guarantee is not silently narrowed.

The existing `directory_cooperative_apply` O_EXCL sibling lock is **not** reused for the
public admission rule. It remains internal and unchanged: it leaves a stale lock on crash,
needs a human to remove it, and only wraps the apply, not the load/proposal phase. It is
also a Windows-tested mechanism with different cleanup semantics, and S4-v1 is Linux-only.

**Alternative considered and rejected:** a persistent sibling lock file. It works, but it
is an extra artifact in the experiment folder, would need `is_own_file`/untracked-file
recognition, and reintroduces stale-lock/cleanup policy. It is a fallback only if a
release-qualified host cannot `flock` a directory; S4-v1 should fail closed instead of
silently switching models.

### 3.4 Publication protocol, step by step

The **real-write path** performs exactly this order. The **dry-run path is a separate
branch that never takes the admission lock** and stops after the checked rebuild (step 6);
it writes nothing. The real-write path takes the lock once and passes the same handle
through every step.

1. **Resolve and admit.** Resolve the manifest target; open the resolved parent directory
   and take the admission lock (§3.3). Refuse on `busy` before any scan. Lock acquisition
   is separate from loading: `init` can lock a folder with no manifest.
2. **Load under the same handle.** Re-open the manifest with `O_RDONLY|O_NOFOLLOW|
   O_CLOEXEC` via `dir_fd`; verify regular/`nlink == 1`; read and parse the bytes from the
   fd; verify the path identity. Keep the fd and the locked parent fd. Do not re-lock.
3. **Pure command checks.** Validate the command shape, `--directory` exclusivity, one
   directory, exact ID/path, no `--all`/glob/volatile/JSON, and the v1 root profile/limits.
   These run before any scan.
4. **Metadata and source-durability preflight.** Capture the complete source metadata set
   (§3.9): owner/group, mode, access/default ACLs, xattrs, inode flags and project ID where
   detectable. `fsync` the loaded source fd and the locked parent directory; a failure is
   `source-durability-failed` and nothing is staged. (`fsync` on the read-only source fd
   is expected to work on Linux; if a host refuses it, open the source `O_RDWR` when
   permitted or refuse; do not skip the precondition.) Refuse `metadata-unsupported` before
   scanning if the complete set cannot be reproduced or if any detectable attribute is
   unsupported. **A dry-run does not execute this source/parent `fsync`, does not require
   write permission on the manifest/parent, and does not raise source-durability/write-only
   failures; it performs only the read-only metadata capture.**
5. **Propose.** Call the existing `propose_add_directory` or `propose_rehash_directory`
   with the validated snapshot. This performs the first native scan. It may refuse for
   duplicate ID, unknown run, path collision, overlap, locator, volatility, profile,
   limit, or scan failure. No write has happened.
6. **Checked rebuild.** Call the existing checked apply logic in dry-run form (or an
   equivalent refactored `check_directory_proposal`) to perform the second native scan,
   compare the rebuilt proposal field-for-field, and confirm the source and tree. The
   checked apply's own source re-read is **informational/defense-in-depth**; it does not
   replace the step-7 held-descriptor source/identity check. If the result is `unchanged`
   (rehash), release the lock and return; **no staging cell, no backup, no temporary
   file**. If the command is `--dry-run`, return here without having taken a lock.
7. **Re-verify source and metadata.** Immediately before creating the staging cell,
   compare the manifest bytes read from the held fd with the loaded source bytes; compare
   the path `lstat` identity with the fd; compare the current complete metadata set with
   the preflight. **This held-descriptor check is the authoritative one**; a checked-apply
   path re-read is only informational. Any difference is
   `stale-source`/`stale-metadata`; refuse.
8. **Create the private staging cell.** Create a cell directory
   `.runevidence-s4-stage-<pid>-<random>` with mode `0700` via `dir_fd` in the manifest
   folder. Before creating any file, set its **access ACL and default ACL to owner-only**
   (remove inherited entries) and remove or reject inherited **non-security** xattrs;
   `security.*` labels are policy-assigned and are **never deleted**—verify or fail closed
   per §3.9. Verify that no second user has any access. If the manifest folder's default ACL
   cannot be overridden or the inherited attributes cannot be made restrictive, remove the
   cell and refuse `metadata-unsupported`. The cell is outside the scanned tree.
9. **Stage the new manifest in the cell.** Open `new` inside the cell with
   `O_RDWR|O_CREAT|O_EXCL|O_NOFOLLOW|O_CLOEXEC`, mode `0600` (the cell has no permissive
   default ACL). Immediately after creation, list inherited attributes on the staged fd;
   remove/reject **non-security** attributes absent from the source before writing content,
   but **never delete `security.*`**—verify the policy label or fail closed per §3.9. Then
   write the complete `dump_raw(new_raw)` text, flush, `fsync`, and verify with `pread`/seek
   read-back **through the held descriptor** — never with an `O_WRONLY` descriptor and
   never by path. Build the final metadata from that restrictive base:
   if the staged file's `uid`/`gid` are not already the exact original values, `fchown`
   to them (a same-owner no-op is skipped); `fchmod` to the exact original mode, copy
   access/default ACLs, xattrs, inode flags and project ID per §3.9, then verify the
   **complete** resulting attribute set both ways, and `fsync` the staged fd again after
   the metadata copy/verification. A second-user access check runs at each boundary (cell
   created, file created, after write, after metadata copy, before publish).
10. **Publish the backup.** Choose generation **newest existing + 1** (not the first gap;
    see the gap test). Create `backup` in the cell with `O_RDWR|O_CREAT|O_EXCL|O_NOFOLLOW|
    O_CLOEXEC`, mode `0600`; immediately remove/reject inherited **non-security** xattrs on
    the backup fd (or refuse); `security.*` labels are policy-assigned, never deleted, and
    must be verified or fail closed per §3.9, then write the exact old source bytes; flush;
    `fsync`; verify with `pread` through the held
    descriptor. Publish it with
    `os.link(backup_name, bak_name, src_dir_fd=cell_fd, dst_dir_fd=locked_fd,
    follow_symlinks=False)`. The link is the exclusive name creation: `EEXIST` means the
    generation was taken; choose the next generation and retry a bounded number of times.
    Unlink the cell copy (best effort), then `fsync` the locked directory. A `.bak.N`
    therefore appears complete or not at all; no partial `.bak.N` is ever trusted. If
    unlinking the cell copy fails, the `.bak.N` remains complete (with a second link) and
    the command reports a cleanup warning; it must not remove the valid `.bak.N`. If the
    directory `fsync` **after a successful link** fails, the `.bak.N` is preserved, the
    manifest is **not** replaced because the backup's durability is unproven, and the
    command reports `backup-durability-failed` with "the backup may remain; the manifest
    was not changed." The second-user access check covers the backup cell/file as well as
    the staged manifest. **S4 always creates a fresh backup**; it never reuses an existing
    `.bak` (which may have different ownership,
    mode, ACL or xattr policy).
11. **Final guard.** Re-check the parent directory identity, the manifest path identity via
    `dir_fd`, the manifest bytes via the held fd, and the complete metadata once more. If
    anything changed, remove the staging cell (best effort), leave any already-published
    backup in place (it is a valid old copy), release the lock, and exit `2`. This is the
    last observation; it is still not atomic.
12. **Publish the manifest.** `os.replace("new", manifest_name, src_dir_fd=cell_fd,
    dst_dir_fd=locked_fd)`. The cell file name disappears; the manifest name now points to
    the new complete file. On POSIX the replacement is atomic for this operation. Do not
    use a full path or a re-opened path for any of these operations.
13. **Directory durability.** `fsync` the locked directory fd. If it succeeds, report
    `written`. If it fails, report `written` plus a fixed `durability-unknown` warning and
    return `0`; do **not** claim power-loss durability. Do not retry automatically, because
    the manifest has already been replaced and a retry could create a duplicate artifact.
14. **Cleanup and release.** Remove the empty staging cell (best effort), close the
    manifest fd and the parent fd in `finally`, and release the lock. Print the result and
    the backup path.

**Descriptor-relative rule.** Manifest reopen, read, staging-cell creation, staging-file
write/read, backup creation/link/unlink, replacement, cleanup and all xattr/ACL/flags
operations use the locked directory fd and held file descriptors (`dir_fd=`, `fchmod` on
the fd, fd-based `getxattr`/`setxattr`/`listxattr`). No operation reopens the manifest or
the staging file by path. If a host's Python API lacks an fd form for one xattr operation,
use a verified `/proc/self/fd/<fd>` handle for the held descriptor and re-check identity,
or refuse; never fall back to the manifest path. This is what makes the final-window
parent-swap test meaningful: the operation keeps writing to the inode it locked, and the
final guard refuses if the path no longer names that inode.

**Scan count.** A real changed write performs **two native scans**: one in the proposal
(step 5) and one in the checked rebuild (step 6). A dry-run performs the same two scans but
no lock, cell, backup or write. An unchanged rehash performs two scans and then writes
nothing. There is no pre-lock dry-run and no third scan under the lock. This is deliberate:
it reuses the tested checked-apply contract and avoids a mutable-proposal trust shortcut.
The cost is bounded by the scanner's logical limits and is documented as a DoS/liveness
consideration (§3.10).

**What is written when (phase-specific).** Only step 12 changes the manifest path. Step 8
creates a cell; step 9 creates a file in it; step 10 publishes a backup. A dry-run or a
pre-staging refusal creates none of these. A refusal after step 8 may leave a cell; a
refusal after step 10 may leave a completed backup. A final guard refusal after backup
publication therefore **does** leave a backup; it must not be described as "nothing was
written" without the phase qualifier. A crash can leave a cell or a temp; no partial
`.bak.N` is possible from the link protocol. Neither a cell nor a temp is a manifest.

### 3.5 Crash-consistency matrix

| Crash point | Manifest path | Backup | Cell / temporary | Lock | Outcome after restart |
| --- | --- | --- | --- | --- | --- |
| Before step 1 | Old, complete if the source was durable; otherwise unknown after power loss | None new | None | Not held | A process crash loses nothing; a power loss from a never-fsynced source is not covered (G8). |
| Holding lock, before cell | Old, complete | None new | None | Released by kernel | Nothing happened. |
| During cell creation (step 8) | Old, complete | None new | Empty/partial cell may remain | Released | Old manifest valid; no sensitive file content was created before the cell ACL was restrictive. Remove the cell manually. |
| During staging (step 9) | Old, complete | None new | Cell with a partial/completed `new` file may remain | Released | Old manifest valid; the cell is not a manifest. Remove it manually. |
| After staging, before backup | Old, complete | None new | Cell with a complete `new` file | Released | Old manifest valid; staged file candidate for manual removal. |
| During backup (step 10, before link) | Old, complete | No `.bak.N` yet | Cell with a partial/completed `backup` file | Released | Old manifest valid. No partial `.bak.N` can exist; a cell copy is not a backup. |
| After backup link, before replace | Old, complete | Complete `.bak.N` (old bytes), fsynced | Cell with `new` file; a backup copy may remain | Released | Old manifest valid; backup complete. Re-run always creates a new generation. |
| After backup link, directory `fsync` fails | Old, complete | Complete `.bak.N` preserved, but its durability is unproven | Cell with `new`; backup copy may remain | Released | **No manifest replacement.** `backup-durability-failed`; the backup may remain; do not retry blindly or delete the backup. |
| During `os.replace` | Old or new, never a torn document for a concurrent reader (POSIX rename); after a power loss the recovered name is filesystem-dependent | Complete `.bak.N` | Cell may remain; `new` name renamed away | Released | Validate the manifest; the backup holds the old bytes. |
| After replace, before parent `fsync` | New, complete in the page cache; after power loss the rename may or may not be durable | Complete `.bak.N` | Cell may remain | Released | The backup is the recovery point. Do not assume the new name survived a power loss. |
| After parent `fsync` | New, complete, durable | Complete `.bak.N`, durable | Cell may remain until cleanup | Released | Success; remove any leftover cell. |
| Parent `fsync` fails | New, complete | Complete `.bak.N` | Cell may remain | Released | Report `written` + `durability-unknown`; do not retry blindly. |
| Lock release fails | New, complete | Complete `.bak.N` | Cell may remain | Process exit releases the kernel lock | The write stands; report a cleanup warning, not a second write. |

A crash test must assert these states with real files. It must not mock `os.replace` to
"success"; it may inject a failure at a named boundary and then inspect the real files. A
**process crash** (including `SIGKILL`) does not lose the page cache; the old source remains
readable. A **power loss** additionally depends on the source/parent durability in G8 and
on the filesystem's rename/fsync behavior; without G8, no old-or-new recovery claim is
made. Injected failures establish protocol order, not power-loss guarantees on every
filesystem.

### 3.6 Durability details and what `fsync` does not prove

- `fsync(source_fd)` plus `fsync(parent_dir)` at admission makes the **old** manifest and
  its name durable before staging (G8). If this fails, the command refuses
  (`source-durability-failed`); it does not claim old-or-new recovery from a source that
  was never durable. This matters for a manifest created immediately before the command
  and not yet fsynced.
- `fsync(staged_fd)` makes the staged bytes durable before the rename, subject to the
  filesystem's implementation.
- `fsync(backup_fd)` plus `fsync(parent_dir)` makes the backup name and bytes durable before
  replacement. The backup is published by `os.link`; the `.bak.N` name appears only after
  the bytes are complete.
- `os.replace` is atomic with respect to concurrent path lookups on POSIX for this
  replacement; it is not a content-conditional operation, and it does not protect against
  an unrelated writer that truncates or rewrites the manifest in place (G2 is narrowed).
- `fsync(parent_dir)` after replacement is what makes the rename durable on filesystems
  that implement directory fsync (the release policy is `ext4`). Without it, a power loss
  can leave the old name or the new name; the backup is the recovery point, but only if
  the source was durable under G8.
- **Process crash vs power loss:** a process crash (`SIGKILL`, Python exception, host
  OOM kill) does not discard the page cache; the old source is still readable and the
  old-or-new statement holds at the process level. A power loss or kernel crash also
  depends on the device cache, the filesystem journal and the directory `fsync`; only the
  post-`fsync` states are claimed.
- `fsync` returning success does not prove that a drive's write cache honored it; a
  lying/failing device is outside the model. A network/FUSE filesystem is refused by the
  policy, not trusted.
- `fsync` failing before replacement is a refusal (`write-failed`); the old manifest is
  intact. `fsync` failing after replacement cannot be undone; report
  `durability-unknown`.
- The protocol does not preserve the manifest's `mtime`/`atime`/`ctime`; replacement
  changes them. It does not preserve birth time. This is documented, not a hidden claim.

### 3.7 Backup and rollback

- **When:** every changed directory add/rehash creates one backup generation. An unchanged
  rehash, a dry-run, a refusal before step 10, and a plain file `add`/`rehash` do not create
  an S4 backup. Plain file writes keep their existing no-backup behavior; S4 does not
  silently change them.
- **Naming:** reuse the existing scheme: `runevidence.json.bak`, then
  `runevidence.json.bak.2`, `.bak.3`, ... . A generation is never overwritten or deleted.
  Choose **newest existing + 1**, not the first free gap. The same names are already
  protected by `is_own_file`, so `inspect` does not report them as untracked content.
- **Selection:** S4 **always creates a fresh generation**. It never reuses an existing
  `.bak`, because an existing file (for example from `init --force`) may have different
  ownership, mode, ACL or xattr policy than a compliant S4 backup. The new backup is
  `0600`, owner = the effective writer (which must equal the manifest owner or
  root-restored owner), with no inherited ACL/xattrs; this is the same policy every time.
  A symlink, hardlink, directory or mismatched file at a candidate name is never reused or
  followed; choose the next generation and retry a bounded number of times.
- **Publication:** write the old bytes to a cell file, `fsync`, verify with `pread`, then
  `os.link` it to the chosen `.bak.N` (exclusive create by link), unlink the cell copy, and
  `fsync` the locked directory. The `.bak.N` name is complete or absent; there is no
  partial `.bak.N` state.
- **Metadata:** a backup is a recovery copy, not a replacement. It is `0600`, owner-only,
  and does not copy a custom ACL/xattr. It is never more permissive to group/other than
  `0600`. This is stricter than some existing `init` backups (which use the umask). The
  design does not retroactively change existing backup modes.
- **Durability and failure phases:** write and `fsync` the backup file; if that fails,
  no `.bak.N` is published, the old manifest stays, and the command refuses. If `os.link`
  fails, no `.bak.N` is published for that generation. If `os.link` succeeds but the
  subsequent directory `fsync` fails, the complete `.bak.N` is **preserved**, the manifest
  is **not** replaced because the backup's durability is unproven, and the command reports
  `backup-durability-failed` with "the backup may remain; the manifest was not changed."
  It must not delete the backup.
- **Rollback:** there is no automatic rollback and no `--restore` command in S4-v1. The
  success output prints the backup path. Manual rollback is a **JSON-byte restore only**:
  stop all writers, copy the chosen backup's bytes over the manifest (or use `mv`), then
  run `validate --directory-artifacts`. Moving or copying the `0600` backup does **not**
  restore the original owner, mode, default/access ACL or xattrs; reapply those separately
  if needed. The backup is a complete previous JSON document, not a delta. Do not roll back
  while a writer is active; the admission lock is advisory.
- **After a failed write:** the old manifest is intact. A new backup may remain (it holds
  the loaded snapshot's bytes; if a nonparticipating writer changed the file, the backup
  does not contain that competitor's bytes); this is harmless and is the recovery point for
  the loaded snapshot. A staged temp may remain after a crash; it is never read as a
  manifest and never used as a rollback source.

### 3.8 Conflict and refusal rules

Every refusal leaves the **manifest path unchanged** and exits `2` unless explicitly noted.
A phase-2 (`cell-or-backup`) or phase-3 (`at-replace`) `INTERNAL_ERROR`, or a
`durability-unknown`, is the exception: the manifest may be unchanged with a backup/cell
remaining, or may already be replaced, and the text says which. The public text is a fixed
reason and the artifact ID/manifest-relative path; it never includes a resolved absolute
scan root, a member name, OS `strerror` text, inode/device/mount ids, or a newly computed
digest from a refused scan.

"Nothing was written" is only phase 1 (before the staging cell, step 8). From step 8
through the step-11 final guard, say "the manifest was not changed; a backup or staging
folder may remain." From just before step 12's `os.replace`, say "the manifest may have
been replaced; run validate." Tests assert the exact phase, not a blanket "no files."

| Reason | When | Write? |
| --- | --- | --- |
| `busy` | The admission lock is held by another built-in writer. | No; no scan. |
| `lock-failed` | The parent folder cannot be opened/locked, or the directory-fd lock is unsupported. | No. |
| `lock-ownership-lost` | The parent folder identity changed while held. | No. |
| `stale-source` | The manifest bytes/identity differ from the loaded snapshot at any check. | No; manifest unchanged. |
| `stale-metadata` | The manifest's owner/group/mode/ACL/xattr/flags/project ID changed after preflight. | No; manifest unchanged. |
| `stale-tree` | The directory digest/size/member count changed between proposal and checked rebuild. | No; no cell/backup if detected before staging. |
| `source-durability-failed` | The source/parent `fsync` failed; old-or-new power-loss recovery is not claimed. | No; no cell/backup. |
| `recheck-failed` | The proposal cannot be made again (scan/refusal). | No. |
| `proposal-mismatch` | Internal checked rebuild disagrees with the proposal (internal path only). | No. |
| `metadata-unsupported` | An ACL/xattr/owner/group/mode/default ACL/inode flag/project ID cannot be preserved or made restrictive. | No; before scan if detectable; before writing sensitive bytes in the cell. |
| `unsupported-platform` | Not Linux, or the no-follow/lock facilities are missing. | No. |
| `unsupported-filesystem` | The scanned root or manifest folder is not release-qualified local (`ext4`) by mount-info/mount-id proof. | No. |
| `invalid-path` / `limit-exceeded` | The root fails the v1 profile or bounds. | No. |
| `outside-root` | The lexically resolved `DIR` is not within the manifest folder. | No. |
| `duplicate-id` / `path-collision` | The ID or root is already used (case-insensitively for paths). | No. |
| `overlap` | A directory above/below, or a file above, the new root. | No. |
| `unknown-run` | `--run` names no run. | No. |
| `volatile-unsupported` | The directory is/would be volatile. | No. |
| `locator-unsupported` | A result reads a JSON locator from the directory. | No. |
| `not-a-directory-artifact` | `rehash --directory` names a file. | No. |
| `not-found` | The rehash ID does not exist. | No. |
| `permission-denied` | The manifest/parent is not writable by the effective user. | No. |
| `write-failed` | Staging/backup/replace failed before replacement, including a pre-link backup failure or `EXDEV` from project-ID/`PROJINHERIT` mismatch (fixed note `project-id-mismatch`). | No replacement; old manifest intact; a cell may remain; **no `.bak.N` if the failure was before the link**. |
| `backup-durability-failed` | `os.link` published a complete `.bak.N`, but the subsequent directory `fsync` failed. | No manifest replacement; the complete backup is preserved and may remain; exit `2`. |
| `durability-unknown` | Parent `fsync` failed after replacement. | **Written**; warning; exit `0`. |
| `INTERNAL_ERROR` | An unexpected public-boundary exception occurred. | Three phases (below): before cell -> nothing written; cell/backup may remain but manifest unchanged; from just before `os.replace` -> manifest may have been replaced. Fixed text; no traceback. |

The command must never turn an unexpected exception into a success or a partial write.
Expected `EditError`/`OSError`/`ValidationError`/`ValueError` map to fixed reasons. The
internal library API may retain exceptions; the **public CLI boundary** catches an
unexpected `Exception`, prints a fixed `INTERNAL_ERROR` response with no exception text or
traceback, and uses a three-valued phase flag:

1. **`before-cell`** (before step 8): `ERROR (INTERNAL_ERROR): the directory update
   failed internally. Nothing was written.`
2. **`cell-or-backup`** (after step 8, through the step-11 final guard, but before the
   `os.replace` call in step 12): `ERROR (INTERNAL_ERROR): the directory update failed
   internally. The manifest was not changed; a backup or staging folder may remain.`
3. **`at-replace`** (from just before the `os.replace` call in step 12 onward):
   `ERROR (INTERNAL_ERROR): the directory update failed internally after the manifest may
   have been replaced. Run runevidence validate PATH --directory-artifacts.`

The boundary has exactly these three phases. A final-guard failure after the backup is
published is phase 2, not phase 1: the manifest is unchanged, but a complete `.bak.N`
(and possibly a staging cell) may remain. It does not catch
`KeyboardInterrupt`/`SystemExit` as a normal refusal.

### 3.9 Metadata preservation policy

The **scanned tree is never modified**. The following applies only to the **manifest file**
and to the staging cell/backup inside the manifest folder.

**Creation policy (ACL staging exposure).** Do not create a readable staged manifest
directly in a folder that may have a default ACL. Create a private cell directory first,
mode `0700`, and set its **access ACL and default ACL to owner-only** (removing inherited
entries) and remove/reject inherited **non-security** xattrs **before creating any file with
content**. `security.*` labels are policy-assigned and must never be deleted; verify them
against the expected policy label or fail closed (§3.9 security rows). Only then create the
file `0600` inside that cell. The cell has no permissive default ACL, so the new file does
not inherit one; immediately after creation, list the file's inherited attributes and
remove/reject **non-security** attributes not present on the source before copying source
metadata; `security.*` is verified or fails closed, never deleted. The cell is empty during
the short create/restrict window, so no manifest bytes are exposed; if the platform cannot
restrict the cell before it becomes writable by a second user, the command refuses before
creating any content. If the host cannot override/remove the inherited default ACL, or
cannot make the cell owner-only, the command refuses `metadata-unsupported` before writing
sensitive bytes. This closes the window in which a `0600` -> `fchown` -> `fchmod` sequence
could unmask an inherited default ACL.

**Complete-set verification.** After building the final metadata, compare the **complete**
attribute set in both directions:

- every source attribute (owner/group, mode, access ACL, default ACL, each xattr name and
  value, inode flags, project ID where detectable) exists on the staged file with the same
  value;
- every staged attribute exists on the source with the same value; an inherited
  **non-security** attribute from the cell/folder that is **absent from the source** is
  removed or the write is refused. A `security.*` label absent from the source is **not
  deleted**; it is verified as the expected policy label or the write is refused. It is not
  enough to check that the source's attributes were copied.

A second-user access test runs at each metadata boundary: after cell creation, after file
creation, after write, after metadata copy, and before publish. The test must show that a
different unprivileged user has no read/write/execute access to the cell, staged file or
backup at any boundary; if the environment cannot provide a second user, the gate records
that as missing evidence (§6.4).

| Metadata | Policy |
| --- | --- |
| Staging cell ACL/xattr base | `0700`, access/default ACL owner-only, inherited **non-security** xattrs removed/rejected, verified before content. `security.*` is policy-assigned; never deleted, verify/fail closed. Refusal `metadata-unsupported` if not achievable. |
| Mode bits | Preserve exactly with `fchmod` on the held fd after writing/copying; verify with `fstat`. A read-only manifest is refused before scan. Setuid/setgid/sticky bits are preserved if the OS permits; otherwise refuse. |
| Owner/group | The effective user must own the manifest, or be root. For non-root, the original `uid` must equal the effective uid and the original `gid` must be the effective gid or a supplementary group. If the staged file's `uid`/`gid` already match, skip the `fchown`; otherwise `fchown` on the fd to the exact original values and verify. Any failure is `metadata-unsupported`. |
| POSIX access ACL (`system.posix_acl_access`) | If detectable, copy via fd-based `getxattr`/`setxattr` after mode changes, then verify the exact bytes and that mode bits still match. If it cannot be copied/verified, refuse. |
| POSIX default ACL (`system.posix_acl_default`) | Detect, copy and verify, or refuse. A regular file normally has no default ACL; an inherited or unusual one must still be in the complete-set comparison, not dropped. |
| Other `system.*` | Refuse if present and not one of the supported POSIX ACL entries. |
| `user.*` | Copy each name/value exactly and verify. Refuse on any failure. |
| `security.selinux` | Policy-assigned; **never delete** it. If present and readable, verify the staged file's label matches the source/expected policy label; if it differs and cannot be set, refuse `metadata-unsupported`. Do not overwrite a label with a guessed value. If the source has no label but the policy assigns one, that is not a reason to delete; verify it is the expected policy label or fail closed. |
| Other `security.*` (e.g. capabilities) | **Never delete.** If present on the source, copy/verify or refuse; if an unexpected `security.*` exists on the staged file and is not copyable/verifiable, fail closed rather than removing it. |
| `trusted.*` | If visible, copy/verify or refuse. Unprivileged `listxattr` may hide `trusted.*`; absence of evidence is not proof. This is a documented residual (§8.2). |
| Inode flags (`FS_IOC_GETFLAGS`) | Detect all flags on the held source fd. If `FS_IMMUTABLE_FL`/`FS_APPEND_FL` (or equivalent `FS_XFLAG_*`) is set, refuse `metadata-unsupported` before staging: it would block replacement/unlink and cannot be preserved atomically by this protocol. For the **user-settable mask** (`NODUMP`, `NOATIME`, `SYNC`, `COMPR`, `JOURNAL_DATA`, `NOTAIL`, `PROJINHERIT` where supported), require **exact equality** between source and staged; set on the staged fd and verify, or refuse. Filesystem-managed bits (for example `EXTENT`, `INLINE_DATA`, `ENCRYPT`, `VERITY`, `DAX`) are recorded and compared but are **G6-narrowed**: they are not claimed as a portable preservation contract. Any flag bit outside the known user-settable/managed classification refuses `metadata-unsupported` (do not ignore an unknown bit). If the ioctl is unavailable, S4-v1 refuses `metadata-unsupported` (it cannot rule flags out); this is the fail-closed reading of "detect or narrow G6". |
| Project ID / `PROJINHERIT` (`FS_IOC_FSGETXATTR` / `FS_IOC_FSSETXATTR`) | Detect the project ID and `FS_XFLAG_PROJINHERIT` where available. A mismatch between the source and the staging cell/folder can make `os.link`/`os.replace` fail `EXDEV`; report `write-failed` with the fixed note `project-id-mismatch`, not a generic unknown error. Preflight the mismatch where the ioctl is available and refuse before staging if it cannot be reconciled; if the link/rename still fails `EXDEV`, preserve the old manifest and clean the cell best-effort. If the ioctl is unavailable, G6 is explicitly narrowed to exclude project ID and the gate records the missing evidence; no claim of preservation. |
| Timestamps | Not preserved. Replacement changes `mtime`/`ctime`; `atime` is OS-dependent. This is not part of any guarantee. |
| Windows DACL/attributes | Not supported. S4-v1 refuses Windows. `ReplaceFileW` is a separate design. |

Preflight ordering: create cell -> restrict cell ACL/default ACL/xattrs -> create staged
file `0600` -> remove/reject inherited **non-security** file attrs absent from source
(security.* is verified/fail-closed, never deleted) -> write/fsync ->
verify via `pread` -> `fchown` (if needed) -> `fchmod` -> copy access/default ACL, xattrs,
flags, project ID -> verify complete set both ways and second-user access at each boundary
-> `fsync`. This ensures the staged document is never more permissive than the original
while it sits in the folder. The backup uses the same cell policy and is published `0600`
with no inherited ACL/xattr.

Detectability limit: an xattr namespace, inode flag or project-ID field that the current
user cannot read cannot be proven absent. The policy refuses what it can detect, narrows
G6 explicitly where an ioctl is unavailable, and records the missing evidence; it does not
claim "all metadata preserved" on a hostile or privileged host. A skipped ACL/inode-flag
test is missing evidence, not a pass.

### 3.10 Manifest/tree races that remain

| Window | What can happen | Handling |
| --- | --- | --- |
| Before admission | Any writer changes the manifest. | The load under lock sees the new bytes; the command works from that snapshot. |
| Before admission, source never durable | A power loss can lose the old manifest. | G8 refuses if source/parent `fsync` fails; no old-or-new power-loss claim from a non-durable source. |
| During proposal/rebuild scanning | A tree writer changes members. | The scanner's identity/metadata/two-pass checks may return `unstable`; otherwise the digest describes the observed objects. |
| Between the last scan and replacement | A tree writer changes the folder. | **Not detected.** The manifest can be stale immediately. `validate --directory-artifacts` later reports a mismatch unless an ABA restores metadata. No freeze/snapshot is claimed. |
| Between the final source check and `os.replace` | A nonparticipating manifest writer changes the file. | **Lost update possible.** The final bytes win; the competitor can be lost. The backup holds the old A, not the competitor B. This is the S3P3P7 limitation. |
| An unrelated writer truncates the manifest in place | A reader can observe partial JSON. | G2 covers only this operation's `os.replace`; the command cannot prevent or mask an unrelated truncating writer. |
| A -> B -> A before the final check | Byte equality sees A. | **Not detected.** No history/version information. |
| Final path swapped to a symlink after the last `lstat` | The `dir_fd` replacement targets the held directory/name, not a newly resolved path. | Narrowed by `O_NOFOLLOW`/identity checks and `dir_fd`; a final-window name swap remains possible but cannot redirect the write to another folder. |
| Manifest hardlink created after the last `nlink` check | The other name keeps old bytes. | Final `lstat`/`nlink` check narrows; a final-window creation remains. |
| Parent folder renamed | The lock and all operations follow the held directory inode. | Parent identity check; if the path no longer names the locked folder, refuse. |
| Parent folder replaced | A new inode at the path. | Identity check refuses; `dir_fd` operations cannot be redirected to the new folder. |
| Filesystem lies about `fsync`/rename | Durability/atomicity not guaranteed. | Outside the model; `ext4` release policy only. |

The only honest description is: **the command records the tree as observed during a
double-pass scan, under a manifest-writer admission lock, with a durable backup. It does
not freeze the tree and does not provide CAS against nonparticipating writers.**

### 3.11 Why CAS is not implemented

The following were considered and rejected as CAS mechanisms:

| Mechanism | Why it is not CAS |
| --- | --- |
| `read` -> compare -> `os.replace` | Two steps. A writer can change the target between them; S3P3P7 observed exactly this. |
| `renameat2(RENAME_NOREPLACE)` | Fails only if the target name exists; it does not compare content. A different-content existing target still blocks it; an unchanged target still cannot be replaced. |
| `renameat2(RENAME_EXCHANGE)` | Swaps two names atomically, but publishes the new file before the old bytes can be validated. A later swap-back is not atomic with concurrent writers and can lose their writes. |
| `link` + `rename` | Still last-writer-wins; no content condition. |
| `O_TMPFILE` + `linkat` | Removes a named temp, not the compare/replace race; linking is not conditional on target content; portability/permission caveats. |
| Advisory `flock`/`fcntl` | Binds only participants. It cannot stop an editor, a script or an mmap writer. |
| Mandatory locking / `chattr +i` | Requires privilege/FS support, changes permissions, not portable; not a user-space protocol. |
| `fanotify`/`inotify`/`F_SETLEASE` | Notification/permission events are not a snapshot or a conditional write; privileged/complex; events can be lost or coalesced. |
| Generation/version field in the manifest | Would enable optimistic concurrency only among writers that honor it; external writers ignore it; schema change; Stage 3 contract change. |
| btrfs/LVM snapshot | FS-specific/privileged; not an `ext4` generic primitive. |
| SQLite `BEGIN IMMEDIATE` + version | Could provide a conditional update if every writer moved to SQLite; SQLite is dormant and not the authority; migration/cutover is out of scope. |

Therefore S4-GP/1 is **guarded publication**, not CAS. The words "atomic compare-and-swap"
must not appear in user-facing S4 documentation.

---

## 4. Public CLI and API surface

### 4.1 Grammar

S4-v1 uses the existing `add` and `rehash` routes:

```text
runevidence add PATH DIR --directory --id ID [--kind KIND] [--run RUN | --no-run] [--dry-run]
runevidence rehash PATH ID --directory [--dry-run]
```

`PATH` is the experiment folder or manifest file, exactly as today. `DIR` is one folder.
`ID` is one artifact ID. The grammar is deliberately minimal:

| Rule | Behavior |
| --- | --- |
| `DIR` base | `DIR` is resolved **lexically against the current working directory**, like existing `add` file paths, with no `realpath`/symlink resolution. It must stay within the manifest folder; otherwise `outside-root` (exit `2`). An absolute `DIR` is accepted only if the lexical path is within the manifest folder. To address a folder relative to the manifest, use an absolute path or change directory. **If an absolute path goes through a symlinked alias of the manifest folder, the lexical comparison may refuse `outside-root` even though `realpath` is inside; this is deliberate fail-closed. Use the resolved/canonical path or a relative path.** This is the single pinned base; manifest-relative interpretation is not a second mode. |
| `--directory` on `add` | Exactly one positional folder. Without it, `add` keeps its current file-expansion behavior byte-for-byte. |
| `--id` with `add --directory` | Required. No derived ID. Rationale: an ID derived from a path can collide or be silently surprising; the user must name the record explicitly. |
| `--kind` | Optional free-form label, passed through the existing `Artifact` validation. No default. |
| `--run` | Optional; must name an existing run. |
| `--no-run` | Optional explicit "no run". Without either flag, a directory artifact has no `run_id`; it never infers a run from neighbours. |
| `--volatile` | Refused with `--directory`, before any scan. Volatile directories have no safe verification model. |
| `--dry-run` | **Separate branch: no admission lock, no staging cell, no backup, no temp.** Full checks and two scans; prints the would-be result; writes nothing. |
| `--directory` on `rehash` | Exactly one exact directory artifact ID (bare or `id:`); no `--all`, no globs, no `folder:`/`run:`/`path:` selectors, no `--volatile`/`--no-volatile`. |
| `rehash --directory` target | Must be a unique directory artifact. A file artifact is `not-a-directory-artifact`; an unknown ID is `not-found`. |
| Multiple directories | Refused. One directory per invocation. No batch transaction. |
| `--json` on add/rehash | Not accepted; argparse exits `2`. S4-v1 is text-only. |
| `--force`, `--allow-*`, `--backend`, `--sha256`, `--size-bytes`, `--metadata` | Not accepted. There is no override. |

### 4.2 Behavior matrix

| Invocation | Scans | Lock | Backup | Manifest write | Exit |
| --- | ---: | --- | --- | --- | ---: |
| `add --directory` changed | 2 | One acquisition, held through rebuild/cell/backup/publish | 1 fresh generation | Yes | 0 |
| `add --directory` duplicate/pre-staging refusal | 0–2 | One (real) if the refusal is after admission; shape errors before admission take none | No | No | 2 |
| `add --directory` post-backup refusal | 2 | One | May leave a completed backup | No | 2 |
| `add --directory --dry-run` | 2 | **No lock** | No | No | 0 (would-add) or 2 |
| `rehash --directory` changed | 2 | One | 1 fresh generation | Yes (sha/size only) | 0 |
| `rehash --directory` unchanged | 2 | One | No | No | 0 |
| `rehash --directory --dry-run` | 2 | **No lock** | No | No | 0 (would-change/unchanged) or 2 |
| `rehash` without `--directory` on a directory ID | 0 | One (real) | No | No | 2 |
| `rehash --all` with any directory artifact | 0 | One (real) | No | No | 2 |
| Plain file `add`/`rehash` | As today | One, after S4-2a retrofit | As today (none for add/rehash) | As today | As today |
| `validate`/`inspect` with the flag | 1 per directory | No | No | No | As today |

The "no scan" cases are command-shape refusals. A shape error must not pay the cost of a
full tree scan. A dry-run must not take the lock and therefore cannot return `busy`; it may
run while a real writer holds the lock and may describe an older manifest snapshot. It
performs no step-4 source/parent `fsync`, needs only read access, and does not raise
write-path `source-durability-failed`/`write-failed`/`permission-denied`; it may still
refuse `metadata-unsupported` from a read-only metadata check.

### 4.3 Exit codes and text output

The existing stable exit codes are unchanged: `0` success, `1` evidence problems (not used
by add/rehash), `2` could not check/write or bad arguments. S4-v1 adds no exit code.

Text output is deterministic and fixed-field. Exact wording will be pinned by tests; the
shape is:

```text
# add success
  data: data  sha256 0123456789ab...  5 members  1.2 MiB
Added 1 directory artifact
Wrote /home/u/exp/runevidence.json
Saved the previous manifest to /home/u/exp/runevidence.json.bak
Next: runevidence validate /home/u/exp --directory-artifacts

# rehash changed
  data: sha256 0123456789ab... -> def012345678..., 1.2 MiB -> 1.3 MiB, 5 members
Re-recorded 1 directory artifact
Wrote /home/u/exp/runevidence.json
Saved the previous manifest to /home/u/exp/runevidence.json.bak
Next: runevidence validate /home/u/exp --directory-artifacts

# rehash unchanged
  data: unchanged (sha256 0123456789ab..., 5 members)

# dry run
  data: would record sha256 0123456789ab..., 5 members
Dry run: nothing was written.

# busy (the accepted S4-2a fixed line; no path, no retry hint)
ERROR (BUSY): another RunEvidence writer is updating the manifest. Nothing was written.

# refusal (fixed code plus the phase suffix; no path, member name or scanner text)
ERROR (INVALID_PATH): the directory path is not an accepted v1 path. Nothing was written.

# internal error, before the staging cell (phase 1)
ERROR (INTERNAL_ERROR): the directory update failed internally. Nothing was written.

# internal error, cell/backup may exist but manifest unchanged (phase 2)
ERROR (INTERNAL_ERROR): the directory update failed internally. The manifest was not
changed; a backup or staging folder may remain.

# internal error, from just before os.replace (phase 3)
ERROR (INTERNAL_ERROR): the directory update failed internally after the manifest may
have been replaced. Run runevidence validate PATH --directory-artifacts.
```

Rules:

- Success/dry-run may print the freshly computed digest because writing/reviewing it is
  the command's purpose. A **refused scan** never prints a freshly computed digest.
- Member **names** are never printed. Member **count** is allowed.
- The artifact path printed is the manifest text (the recorded relative path), not the
  resolved absolute scan root.
- The manifest path and backup path are local paths already known to the user; existing
  add/rehash behavior prints the resolved manifest path. `--redact-manifest-path` is not
  available on add/rehash and is not added.
- No JSON, no machine-readable envelope, no per-field object in S4-v1. A future `--json`
  authoring contract needs a versioned schema, sanitization and exit-code review; it is
  explicitly deferred. (`migrate`/`conditions` keep their existing JSON envelopes; S4-2a
  adds only a fixed `BUSY` error code to them.)
- **Unexpected exceptions:** the public boundary catches `Exception`, prints exactly one
  fixed `INTERNAL_ERROR` document/line with no exception text, path, traceback or partial
  output, and exits `2`. It uses the three phases from §3.8: `before-cell` says "Nothing
  was written"; `cell-or-backup` says the manifest was not changed but a backup or staging
  folder may remain; `at-replace` says the manifest may have been replaced and directs the
  user to validate. It never falsely claims nothing was written. Internal APIs may still
  raise.
- "Nothing was written" is never printed once a cell, backup or replacement may exist;
  phase-specific wording is mandatory.
- No logs, telemetry, network, subprocess, environment-variable or config-file output.

### 4.4 Privacy boundary

| Allowed in public output | Forbidden in public output |
| --- | --- |
| Artifact ID, manifest-relative artifact path, operation, old/new digest on success/dry-run, size, member count, backup path, manifest path, fixed reason code, fixed help/next-step text. | Resolved absolute scan root; member names or member bytes; OS `strerror` text; inode/device/mount ids; `/proc` paths; a digest computed by a failed/refused scan; exception tracebacks or internal scanner messages; environment variables; network data. |

The internal `DirectoryAuthoringError`/`DirectoryApplyError` messages can contain verbose
scanner text (including the absolute root and member names). The public command must
translate them at the boundary:

- For `scan-failed`/`recheck-failed`, print only the fixed outer reason and, where useful,
  the fixed inner scan class from the allowed set.
- Never call `_edit_error(exc)` directly on a raw authoring/apply exception.
- For an unexpected exception, print the fixed `INTERNAL_ERROR` text only. Never print the
  exception class, `str(exc)`, `repr(exc)`, a traceback or a path from the exception. The
  complete response is rendered before it is emitted, so a formatting failure produces no
  partial document; if the operating system itself fails partway through writing stdout,
  lines already accepted by the stream remain and the fixed phase line follows, which is a
  stream failure rather than a command document.
- Keep the internal exception contract verbose for library callers; sanitize only the
  public surface, as S3b did for findings.
- The legacy manifest-load report is a deliberate exception: `add`/`rehash` without
  `--directory` keep it, and `validate`/`inspect` use it. The directory forms never use it.
  Real writes and `--dry-run` refusals both use the fixed reason lines and never a path,
  `strerror` or parser message. A dry-run load failure is `NOT_FOUND`,
  `INVALID_MANIFEST` or `INVALID_PATH` (a FIFO, directory or other special file). A pre-admission readability pre-check reports a missing or dangling
  manifest as `NOT_FOUND` and an unreadable one as `PERMISSION_DENIED`; a source that
  changes after admission can still be refused as `STALE_SOURCE` by the core. All are
  fixed, value-free lines. The pre-check opens with `O_NONBLOCK` and requires a regular
  file, so a FIFO swapped in cannot make it wait. The dry-run loader and the core's
  held-descriptor load also open with `O_NONBLOCK` and require a regular file, so a FIFO
  swapped at any point is refused (`INVALID_PATH` before admission, `STALE_SOURCE` under
  the lock) rather than waited on. This covers the directory commands only: the shared
  `load_manifest` used by `validate`, `inspect` and the non-directory commands still has
  the pre-existing `is_file()`→`read_bytes()` window and is out of this slice. A dangling alias is `NOT_FOUND` whether or not its
  target folder exists; an unusable path (symlink loop, NUL, special file) is
  `INVALID_PATH`. The reused `PERMISSION_DENIED` wording comes from the fixed core
  catalogue.
- A test must inject a scanner error whose message contains canaries for the absolute
  root, a member name, `strerror`, inode/device/mount ids and a digest, and assert none
  reach stdout/stderr. A second test injects an unexpected `RuntimeError` with the same
  canaries in all three phases (before cell, step-11 final guard, from just before
  `os.replace`) and asserts the fixed three-phase `INTERNAL_ERROR` wording.

`inspect`'s pre-existing untracked-file findings are outside this command; they may list
member relative paths exactly as they do without the directory flag. S4 does not change
that.

### 4.5 Permissions and authorization

- Authorization is the local filesystem: the effective user must be able to read/write the
  manifest and write/execute its parent folder. There is no account, token, daemon or
  network authorization.
- The command never elevates privileges, sets capabilities, uses `sudo`, changes ownership
  of another user's file, or bypasses a read-only mode.
- A read-only manifest or parent is refused before scanning.
- A manifest owned by another user is refused unless the writer is root and can restore
  the exact owner/group; a non-root writer does not attempt a chown to another user.
- The admission lock requires read permission on the manifest folder. A write-only/execute
  setup that cannot open the directory read-only is refused (`lock-failed`), not silently
  unlocked.
- The lock fd is `O_CLOEXEC`, held for one operation and released in `finally`; the
  operation must not fork or spawn a child that inherits it. A second acquisition in the
  same process is `busy` (self-busy), not a re-entrant lock.
- The loaded source fd and parent directory are `fsync`ed before staging (G8). A
  source/parent durability failure refuses; it does not proceed on a best-effort basis.
- The staging cell is `0700` with owner-only access/default ACLs before any content; the
  staged file starts `0600` and is built up to the original metadata from that restrictive
  base. The backup is `0600` with no inherited ACL/xattr; the final mode is the original
  mode.
- No `--force`, no environment variable, no config key and no parser option can disable the
  source durability check, the metadata preflight, the staging cell, the lock or the
  backup.

### 4.6 Dry-run and preview

`--dry-run` is the only public preview. It:

- performs the same command-shape checks, **read-only** metadata inspection, two native
  scans, source check and checked rebuild as a real write;
- **does not perform step 4's source/parent `fsync`**, does not require write permission
  on the manifest or parent, and does not raise `source-durability-failed`,
  `write-failed`, `permission-denied`, `backup-durability-failed` or `durability-unknown`
  for write-only preconditions. It may still check/read metadata and refuse
  `metadata-unsupported` if a required attribute cannot be inspected read-only; it never
  mutates metadata.
- is a **separate branch that takes no admission lock**, creates no staging cell/backup/
  temp, and never touches the manifest; it cannot return `busy`;
- prints `would record`/`would change`/`unchanged` and the would-be digest/size/member count;
- exits `0` for a would-change/unchanged result and `2` for any refusal;
- is explicitly **not** a write authorization and is stale as soon as it returns. A later
  real invocation recomputes everything; there is no preview token, timestamp or cache.

The internal `DirectoryPreview`, `DirectoryAudit`, `DirectoryBatchReview` and presenter
remain internal. S4-v1 does not expose them, does not accept a preview object on the CLI,
and does not treat `unchanged` as an all-clear.

### 4.7 Composition with `validate` and `inspect`

- A successful add/rehash prints a next step:
  `runevidence validate PATH --directory-artifacts`.
- The command does **not** run validate/inspect automatically. There is no nested scan or
  hidden second operation.
- `validate --directory-artifacts` reads the new record and reports the existing codes
  (`directory-hash-mismatch`, `directory-size-mismatch`, `directory-no-digest`,
  `directory-unsafe`, etc.). No new code is added.
- `inspect --directory-artifacts` keeps its existing untracked-file coverage rule: a
  successful directory scan covers exactly the files it recorded; a failed/volatile
  directory covers nothing.
- Without the flag, the new directory record is still `not-a-file`. This is intentional
  Stage 3 behavior and is documented in help/README.
- `validate`/`inspect` take no admission lock. They may read the old or new manifest
  atomically while a writer holds the lock; their scan is independent and may race with a
  tree change. They never write.

### 4.8 API

S4-v1 adds **no public Python API**. The CLI is the only public authoring surface.

Internal-only shape for the future implementation (not exported from
`runevidence/__init__.py`):

```python
# Internal, not a public contract.
with manifest_admission_lock(target) as locked:   # directory-fd flock only; no load yet
    loaded = load_manifest_under_lock(locked, target)  # same handle, no re-lock
    proposal = propose_add_directory(loaded, ...)      # or propose_rehash_directory(...)
    result = publish_directory_proposal(                # checked rebuild + cell + backup + replace
        locked, proposal, dry_run=False,
    )
```

- `manifest_admission_lock` is internal; it resolves the target, locks the parent and
  yields a handle. It does **not** load the manifest, so `init` on a folder with no
  manifest can use it.
- `load_manifest_under_lock` is internal; it loads/validates with the same handle and
  verifies identity. It must not acquire the lock again.
- `publish_directory_proposal` is internal; it assumes the held lock token, performs the
  checked rebuild and the durable publication, and returns a frozen result plus a phase
  flag for the public boundary.
- A dry-run uses a separate no-lock branch that loads and checks without
  `manifest_admission_lock`.
- Existing `apply_directory_proposal`/`apply_directory_proposal_cooperatively` keep their
  current behavior for internal callers. The public path must not silently change them.
- The CLI composes the calls; no adapter, check, preview, audit, batch or presenter
  imports the new module. A test must assert the read-only startup path does not import it.

### 4.9 No network and no artifacts except the scan

- No network code, DNS, socket, subprocess, daemon, service, telemetry or remote mount.
- `validate`/`inspect` create **no** artifact: no lock file, no temp, no backup, no cache,
  no report file. They only read the manifest and the tree.
- A changed S4 write creates exactly: the replaced manifest, one **fresh** backup
  generation, and (transiently) a private staging cell `.runevidence-s4-stage-*` with a
  `new` and a `backup` file in the manifest folder. The lock is an in-memory kernel lock,
  not a file. Source/parent `fsync` creates no artifact.
- A crash can leave the staging cell and its files; a crash after step 10 can leave the
  completed `.bak.N`. No partial `.bak.N` is possible from the link protocol. The backup
  is published only by an atomic `os.link` of a complete file.
- A leftover staging cell/file is not a manifest. It is not protected by `is_own_file`
  today, so `inspect` may list it as untracked and a broad `add .` could try to track it.
  S4-v1 does not auto-delete unknown cell/temp files (that could delete user data); after a
  crash, remove the `.runevidence-s4-stage-*` cell manually. A future cleanup/own-file
  policy is a separate change.
- The scanned folder is never written. Because the manifest cannot be a directory artifact
  path (protected name/root profile) and the manifest folder is an ancestor of the
  recorded folder, the staging cell and backup are outside the scanned tree. This is why
  the command cannot perturb the digest it just measured.
- No file is created outside the manifest folder. No `/tmp` lock, no user cache, no
  sidecar database.

### 4.10 Platform and filesystem selection

| Platform / filesystem | S4-v1 behavior |
| --- | --- |
| Linux + `ext4` | Allowed, subject to every precondition. |
| Linux + any other filesystem | `unsupported-filesystem`, exit `2`, no write. The scanner's release allowlist is unchanged. |
| Manifest folder filesystem | Classified by the scanner's fresh `/proc/self/mountinfo` + proven mount-id proof and release allowlist, not `statfs`/`st_dev` alone. A bind/overlay/network/pseudo classification that does not pass is refused. |
| Windows (NTFS fixed drive) | `unsupported-platform` for public authoring, even though the Windows scanner can scan. No DACL/`ReplaceFileW` evidence; no parity claim. |
| macOS / other POSIX | `unsupported-platform`. |
| Linux without `O_PATH`/`O_NOFOLLOW`/`O_DIRECTORY`/`/proc/self/fd`/mount-id proof | `unsupported-platform`. The scanner already fails closed; authoring inherits it. |
| Linux without directory `flock` support | `lock-failed`/`unsupported-platform`; no fallback to a weaker lock. |
| Symlink alias to the manifest file | Accepted only if `resolve(strict=True)` reaches a plain regular file; the resolved target is replaced, not the alias. A symlink alias swapped in after admission is refused by identity checks. |
| Symlink in the artifact-relative directory path | Refused by the scanner's no-follow walk; the path is not `realpath`-resolved. |

There is no `--backend`, `--posix`, `--force` or environment/config override. Backend
selection remains automatic and fail-closed.

---

## 5. Directory record authoring semantics

### 5.1 Field ownership

| Field | Source | Add | Rehash |
| --- | --- | --- | --- |
| `id` | User (`--id`, required) | Set. Must pass `Artifact`/ID validation and be globally unused. | Selected by exact ID; unchanged. |
| `path` | User folder, lexically resolved against the **current working directory** and normalized to the manifest-relative portable path; no `realpath` | Set. Must stay within the manifest folder and pass the v1 root profile/bounds. | Must equal the loaded artifact's path; no rename. |
| `artifact_type` | Fixed | `"directory"`. | Must already be `"directory"`. |
| `digest_format` | Fixed | `"runevidence-directory-v1"`. | Must already be v1. |
| `sha256` | Scanner only | Computed by the native scan; never user-supplied. | Recomputed; updated only if different. |
| `size_bytes` | Scanner only | Sum of file bytes; directories count zero. | Recomputed; updated only if different. |
| `members` | Scanner only | Output only; not stored in the manifest. | Output only. |
| `kind` | User (`--kind`, optional) | Copied after `Artifact` validation; absent → `null`. | Preserved unchanged. |
| `run_id` | User (`--run`, optional) | Must name an existing run; no inference; absent → `null`. | Preserved unchanged. |
| `volatile` | Not authorable | Always false; `--volatile` refused. | Must be false; volatile refused. |
| `metadata` | Not authorable in S4-v1 | Empty object (existing model default). | Preserved untouched. |
| `dataset_id` | Not authorable in S4-v1 | Omitted for 0.1/0.2 as `artifact_to_dict` does today. | Preserved untouched. |

No command option supplies a digest, size, member count, `artifact_type`, `digest_format`
or metadata. A hand-edited manifest can contain those; rehash corrects `sha256`/`size_bytes`
and leaves the rest alone.

### 5.2 Digest computation

The digest is the existing `runevidence-directory-v1` canonical stream:

```text
b"RunEvidence.directory\x00v1\x00"
u64 member-record count                 # the root itself has no record
for each record, sorted by raw UTF-8 path bytes:
    b"D" || u32 path length || path                  # directory, empty payload
    b"F" || u32 path length || path || 32-byte SHA-256 # file contents
```

All integers are unsigned big-endian. `sha256` is the lowercase hex SHA-256 of the whole
stream. File digests are raw SHA-256 bytes, not hex. Directory records carry no digest and
size zero. The root has no record; an empty root is the header plus a zero count.

`size_bytes` is the sum of file sizes; directories contribute zero. It is an independent
manifest check, not part of the v1 stream. `members` is the count of `D` + `F` records
excluding the root; it is output/diagnostic only.

The digest excludes permissions, owner/group, ACLs, xattrs, timestamps, inode/device/mount
ids, link counts, sparse layout, filesystem type and symlink targets. A metadata-only
change is invisible to the digest. S4 does not change this.

### 5.3 Add semantics

- `DIR` is resolved lexically against the **current working directory** (not the manifest
  folder), with no `realpath`; it must stay inside `loaded.root` or the command refuses
  `outside-root`. The artifact path is the forward-slash relative path from `loaded.root`.
  This is the same base as existing `add` file paths; a user who wants manifest-relative
  input must use an absolute path or change directory. An absolute path through a
  symlinked alias of the manifest folder can be over-strictly refused `outside-root`; use
  the canonical path or a relative path, and do not expect a `realpath` fallback.
- Append exactly one artifact entry to the manifest's `artifacts` list. Create the list at
  the end of the top-level object if it does not exist. Do not reorder, rewrite or resave
  any existing entry, key or value.
- Validate the resulting document with `Manifest.model_validate` and `dump_raw` before
  writing; the write is the new text, not a model re-serialization of the old document.
- Refuse before scanning if the ID is used anywhere in the manifest (project, runs,
  artifacts, results, evidence), the path is already tracked (case-insensitively), the
  path is protected, the root fails the profile/bounds, `--volatile` is set, `--run` names
  no run, or a linked result has a `locator` on the directory.
- Refuse overlap: a directory artifact at the same root or above/below the root, or a file
  artifact above the root. A file artifact **inside** the root is allowed and reported as
  an overlapping child; it keeps its own checks. The directory digest then vouches for the
  same bytes a second time.
- Append does not expand subfolders into separate records; the whole tree is one artifact
  and subfolders are `D` records only.
- Add is intentionally **not idempotent**: a retry after success is `duplicate-id` (or
  `path-collision`) and writes nothing. Review before writing; use `--dry-run` first.

### 5.4 Rehash semantics

- Select exactly one existing directory artifact by exact ID. It must be unique and must
  already be `artifact_type: "directory"` with the loaded path. A file artifact is
  `not-a-directory-artifact`; a missing ID is `not-found`; a duplicate ID is `duplicate-id`.
- Re-scan the recorded path. Change **only** the entry's `sha256` and `size_bytes`, and
  only when they differ. If both already match, return `unchanged` and write nothing (no
  backup, no temp).
- Preserve `id`, `path`, `kind`, `run_id`, `metadata`, `dataset_id`, `artifact_type`,
  `digest_format` and the entry's key order. Do not re-read linked results, accept/refresh
  claim text, rehash child file artifacts, or add/remove entries.
- A result with a `locator` on the directory is refused (`locator-unsupported`), because a
  directory is not a single JSON file.
- A volatile directory is refused. A missing digest/size is corrected by rehash (it becomes
  a complete record); `directory-no-digest` is then resolved.
- Rehash is idempotent: a second run after a successful change returns `unchanged` and
  writes nothing. A failed write leaves the old manifest; a retry re-scans and tries again.
- Rehash does not detect a rename: the path is the label. If the folder moved, the scan is
  `missing`; edit the manifest or add a new record (manual, outside S4-v1). There is no
  public rename/remove in S4-v1.

### 5.5 Path profile and limits

The v1 profile is unchanged:

- The artifact-relative root and every member path must be relative, forward-slash, strict
  printable ASCII (`0x20`–`0x7e`), with no `< > : " / \ | ? *`, no empty/`.`/`..`
  components, no trailing space or dot, no Windows device names, and no `.git`, `.hg`,
  `.svn`, `.venv`, `venv`, `__pycache__`, RunEvidence manifest or backup names at any depth.
- Paths that differ only in ASCII case collide. Real Unicode names are refused, not
  normalized; support would need a new versioned profile.
- Absolute ancestors above the artifact root are not profile-checked; they are path, and
  the scanner verifies their identity/spelling. The root name is not part of the digest.

| Bound | Value | Enforced by |
| --- | ---: | --- |
| Depth below root | 64 | scanner + authoring root |
| Members (records excluding root) | 10,000 | scanner |
| Total file bytes | 1 GiB | scanner |
| Per-file bytes | 256 MiB | scanner |
| One relative path (UTF-8) | 1,024 bytes | scanner + authoring root |
| Total path bytes | 4 MiB | scanner |
| Encoded canonical stream | 8 MiB | encoder |
| Root components (absolute) | 256 | scanner |
| Ancestor entries read for exact spelling | 4,096 | scanner |

S4-v1 exposes **no** flag to raise, lower or ignore these bounds. The internal
`scan_directory` API may accept a tightened `DirectoryLimits`, but the public CLI always
uses the fixed defaults. No wall-clock/CPU deadline exists; a within-limit tree can still
take a long time.

### 5.6 Links, special files, empty directories and mounts

- Symlinks, junctions/reparse points, hardlinked files (`st_nlink > 1`), device nodes,
  FIFOs, sockets and every other non-directory/non-regular-file member are **unsafe** and
  refused by the scanner. S4 exposes no override. A link is refused even if it points
  inside the tree.
- A regular file observed with `st_nlink == 0` is `unstable`; it is not recorded.
- Empty folders are recorded as `D` records, including a completely empty root (zero
  records). An empty directory is valid and rehashable.
- A mount exactly at the artifact root is allowed only if its filesystem passes the
  release policy; a descendant mount is `unsafe`. The digest does not encode the mount;
  unmounting later may reveal the underlying directory (which then validates as a
  mismatch) or make the path `missing`, depending on what is mounted underneath. Do not
  assume unmount always produces `missing`.
- A root or member with a different on-disk spelling is refused (`unsafe` on a
  case-insensitive filesystem; `missing` if the exact name is absent on a case-sensitive
  one).
- The command runs with the caller's permissions; it never elevates. An unreadable member
  fails the whole scan; there is no partial tree.

### 5.7 Idempotence and retries

| Operation | First run | Second run |
| --- | --- | --- |
| Add a new directory | Writes one entry. | `duplicate-id`/`path-collision`, exit `2`, no write. Not idempotent by design. |
| Rehash changed | Updates `sha256`/`size_bytes`, backup. | `unchanged`, exit `0`, no write. |
| Rehash unchanged | `unchanged`, no write. | Same. |
| Dry-run | Reports would-add/would-change, no write. | Recomputes; may differ if the tree changed; no token. |
| Failed write (pre-replace) | Old manifest intact. | Safe to retry; a cell and a completed backup may remain (phase-specific). |
| Crash after replace | New manifest may be present; backup old if G8 held. | Validate; do not blindly retry add (duplicate) or assume the new record is correct. |

Retries never reuse a proposal, preview or digest from a previous invocation. Every real
invocation re-loads, re-locks and re-scans.

### 5.8 Multiple directories, overlap and transactions

- One directory per invocation. No `--all` for directories, no glob expansion, no batch.
- Nested directory artifacts remain unsupported: a directory above or below another is
  `overlap`; a file above is `overlap`; a file inside is allowed.
- There is no transaction across directories. Two separate invocations are two separate
  publications; the second can be based on a manifest that includes the first, but there is
  no all-or-nothing guarantee. The internal audit/batch remain read-only and non-atomic.
- There is no removal/rename. A user can hand-edit the JSON (with the usual risks) or wait
  for a future separately designed operation. S4-v1 does not authorize either.

### 5.9 Schema versions and preserved content

- S4-v1 writes the manifest's existing `schema_version` (0.1 or 0.2) through
  `artifact_to_dict`; it does not migrate or add a schema version.
- 0.2 `conditions`, `dataset_id` and `metric_definition` content elsewhere in the manifest
  is preserved because the edit operates on the raw JSON copy.
- A new directory artifact does not set `dataset_id` or `metadata`; `metadata` is `{}` and
  `dataset_id` is omitted (or preserved if the model already carries it), matching today's
  `artifact_to_dict` behavior.
- No schema, model, digest token or migration change. Existing directory records from 0.1
  or 0.2 validate and rehash unchanged.

### 5.10 Example entry

For `add . data --directory --id data --kind dataset --run preprocess`:

```json
{
  "id": "data",
  "path": "data",
  "artifact_type": "directory",
  "digest_format": "runevidence-directory-v1",
  "sha256": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
  "size_bytes": 1259520,
  "kind": "dataset",
  "run_id": "preprocess",
  "metadata": {}
}
```

Without `--kind`/`--run`, the serialized entry still carries `"kind": null` and
`"run_id": null` (the model's current `exclude_defaults=False` behavior), plus
`"metadata": {}`. The exact key order is the existing `Artifact` model order; the apply's
one-change check preserves it.

---

## 6. Test plan

Tests are part of a future authorized implementation, not this design task. The plan is
specified here so the gate can be judged before code starts.

### 6.1 Layers

| Layer | Purpose | Files (candidate) |
| --- | --- | --- |
| A. Pure/unit | Record fields, digest, profile, limits, ownership of fields, one-change checks, refusal mapping. | `tests/test_directory_authoring.py` (existing) + new pure tests |
| B. Publication core | Lock, stage, backup, fsync order, metadata preflight, replace, cleanup. | `tests/test_directory_publication.py` (new) |
| C. Crash injection | Every step; real files; no mocked success. | same |
| D. Concurrency/lock | Two processes, one handle/self-busy, kill/release, busy, folder replacement, symlink/hardlink. | same + subprocess helpers |
| E. Metadata | Mode, owner/group, access/default ACL, user xattr, SELinux, inode flags, project ID, cell exposure, backup mode, refusal. | same, POSIX/root skips |
| F. Links/special | Tree and manifest symlink/hardlink/FIFO/socket/device, swaps. | same + scanner fixtures |
| G. CLI | Grammar, exit codes, text, dry-run no-lock, DIR base, no inference, no JSON, pins. | `tests/test_cli_directory_authoring.py` (new) |
| H. Privacy/boundary | Canaries for root/member/strerror/ids/digest; sanitized errors; three-phase `INTERNAL_ERROR`. | CLI test |
| I. Installed package | Wheel/sdist, fresh venv, `pip check`, ext4 smoke, tmpfs refusal, no tests/docs, no network. | gate script under an approved temp dir |
| J. Cross-platform | Windows/macOS refusals; non-ext4 refusal; skips accounted. | CLI test + existing markers |
| K. Negative controls | Nonparticipant lost, ABA, no inference, no flag, `--all`, forged proposal, user digest. | publication/writer-race tests |
| L. Descriptor/lifecycle | `O_RDWR` read-back, `dir_fd` operations, parent swap, lock inheritance/release. | publication tests |
| M. Filesystem proof | Manifest folder mount-info/mount-id classification, not `statfs`; bind/overlay refusal. | publication tests |

### 6.2 CAS / crash / source-durability injection

Use real files and the real `os.replace`; inject at named boundaries (monkeypatch a helper
or raise a sentinel) and inspect the actual tree. Assertions:

1. **Source not fsynced:** create a manifest and start the command before any explicit
   source `fsync`; assert the protocol attempts `fsync(source_fd)` and `fsync(parent_dir)`
   before staging. Inject a source-`fsync` failure → `source-durability-failed`, no cell/
   backup, old file unchanged; do not claim old-or-new power-loss recovery.
2. Crash before cell creation → old manifest, no cell, no backup, lock released.
3. Crash during cell creation → old manifest, empty/partial cell may remain, no file
   content, no backup.
4. Crash during staging (cell file write) → old manifest, partial/completed `new` file in
   the cell, no backup. Assert the cell was owner-only before the file was created.
5. Crash after `new` `fsync` before backup → old manifest, complete `new` in cell, no
   `.bak.N`.
6. Crash during backup cell write, before `os.link` → old manifest; no `.bak.N`; a cell
   copy may remain; a new generation is used on retry.
7. Crash after backup link/`fsync` before replace → old manifest, complete `.bak.N`,
   complete `new` in cell; backup bytes equal old; no new manifest.
8. **Directory `fsync` fails after a successful backup link** → old manifest unchanged,
   complete `.bak.N` preserved, no replacement, `backup-durability-failed`; the error says
   the backup may remain. Assert the `.bak.N` bytes are the old manifest and are not
   deleted.
9. `os.replace` raises → old manifest, complete backup, cell cleaned; exit `2`.
10. Crash after replace before parent `fsync` → new complete manifest, complete backup;
    after a simulated restart the backup is a valid recovery point; no claim that the
    rename is durable across power loss without the parent `fsync`.
11. Parent `fsync` raises after replace → new complete manifest, backup old, fixed
    `durability-unknown` warning, exit `0`; no second write.
12. Staged `fsync` raises → no replace, old manifest, cell cleaned, exit `2`.
13. Backup cell `fsync` raises (before link) → no replace, old manifest; no `.bak.N`
    published; cell cleaned best-effort; exit `2`.
14. Cell/file creation collision or inherited symlink → bounded retry/refusal; never
    follows a symlink.
15. Backup generation collision (file/symlink/directory/hardlink) → next generation;
    never overwrite/follow. A pre-existing `.bak`, `.bak.2` gap test → newest+1, not the
    first gap.
16. Final source check failure after backup publication → old/competitor file retained,
    cell cleaned, completed backup may remain, exit `2`.
17. Final metadata check failure after backup publication → same; no replace.
18. **Step-11 final-guard unexpected exception after backup publication:** inject a
    `RuntimeError` at the guard, assert the manifest is unchanged, the complete `.bak.N`
    and cell may remain, and the public phase is `cell-or-backup` with "The manifest was
    not changed; a backup or staging folder may remain"—not "Nothing was written."
19. **Power-loss distinction:** a process crash test may assert the old or new name; a
    power-loss test (where injectable) may only assert the post-`fsync` states. Do not
    claim power-loss recovery for a source that failed G8.

A test must record the full `fsync` order: source file, source parent, staged file, staged
file again after metadata copy, backup file, backup parent (before replace), replace,
parent (after replace). The backup `fsync` must be in the assertion, not only the staged
file. Do not mock `fsync` success; call the real one on `tmp_path` (skip if the filesystem/FS
refuses directory fsync and record it).

**Phase-specific write assertions.** Every crash/refusal test names the phase and asserts
the exact artifacts: pre-cell "nothing"; post-cell "cell may exist, no backup"; post-cell
file "complete/partial cell file, no `.bak.N`"; post-link `fsync` failure "old manifest,
complete `.bak.N` preserved, no replacement"; post-backup final-guard "old manifest,
complete `.bak.N`, cell may remain"; post-replace "new/old manifest, complete backup". The
phrase "nothing was written" is not used for post-cell failures.

### 6.3 Concurrent writers and locks

- **Winner demonstrably holds the lock:** the winner child signals after `flock` succeeds
  and before scanning; the loser then runs and must exit `2` `busy` without scanning. The
  test asserts the winner still holds the lock while the loser is refused (not just that
  both eventually fail).
- **Two processes, different IDs:** one writes; the loser gets `busy`; retry the loser;
  final manifest contains both entries; no lost update among participants.
- **Two processes, same ID:** one writes; the loser gets `busy`, then `duplicate-id` on
  retry; exactly one entry.
- **Self-busy / one handle:** a second `flock` acquisition on the same folder from the
  same process fails `busy`; the operation passes one lock handle and does not re-acquire
  for load/propose/rebuild/publish.
- **Dry-run while held:** hold the lock in another process; a `--dry-run` must succeed
  (no `busy`) and report the current/old manifest snapshot; it creates no cell/backup.
- **SIGKILL while holding:** a child acquires the lock and is killed; the next writer
  proceeds immediately (kernel release); no stale lock file exists.
- **Real termination at boundaries:** a subprocess is killed at staging, after backup
  publication, and during/after replacement; assert the phase-specific artifacts from §6.2
  and that the next writer can acquire the lock.
- **Inherited descriptor:** a forked child that inherits the lock fd must not be relied on
  to release it; the protocol does not fork. A test asserts `O_CLOEXEC` is set and that a
  child exec does not inherit the lock.
- **Lock folder identity / parent swap:** rename/replace the manifest folder during the
  scan; assert `lock-ownership-lost`/`stale-source`, no write to the new folder, and all
  `dir_fd` operations stayed in the locked inode.
- **Symlink/hardlink lock attacks:** a symlink or hardlink at the folder path is refused;
  the lock never follows it.
- **Read-only folder:** the lock open fails; exit `2`, no write.
- **No lock file:** snapshot the folder; assert no `.lock`/lock artifact was created.
- **`init` with no manifest:** lock acquisition succeeds before any manifest exists; the
  load step is separate and may be skipped by `init`.

### 6.4 ACL / xattr / mode preservation (including staging exposure)

- **Inherited default ACL on the manifest folder:** set a default ACL granting a second
  user read/write on the folder; run a write; at each boundary (cell creation, file
  creation, after write, after metadata copy, before publish) assert the second user has
  no access to the cell, staged file and backup. Use a real second uid/subprocess where
  possible; otherwise inspect the ACL and record the missing live-user evidence. This is
  the regression test for the `0600 -> fchown -> fchmod -> copy-ACL` unmask window.
- **Cell restriction failure:** make the folder's default ACL unremovable/unsupported;
  assert `metadata-unsupported` before any sensitive byte is written, not a partial file.
- **Complete set both ways:** add a synthetic `user.*` xattr to the source; assert it is
  present on the staged/final file. Inject an extra **non-security** xattr on the staged
  file that is absent from the source; assert it is removed or the write is refused. Inject
  an unexpected `security.*` label; assert it is **not deleted**, and the write verifies it
  as the expected policy label or fails closed. A source-attribute subset check is not
  sufficient.
- Mode: `0600`, `0640`, `0660`, `0755` manifests; assert the final file has the exact
  original mode, and a read-only `0444` manifest is refused before scanning.
- Owner/group: same-user manifest; supplementary-group manifest if the host permits
  `fchown`; assert exact final `(uid, gid)`. Root-owned/non-root case: refusal (skip as
  root). Root case: exact restore or refusal.
- Access/default POSIX ACL: use `setfacl`/`getfacl` if installed (skip with an explicit
  reason if not); a custom access ACL and default ACL are preserved exactly. If the ACL
  cannot be set/copied/verified, the write is refused.
- `user.*` xattr: set a synthetic value; assert it is preserved and the backup has none
  (backup is `0600`).
- `security.selinux`: skip if absent/unreadable; if present, assert the staged/final label
  matches or the write is refused; assert no `security.*` `removexattr` call is made. No
  claim on hosts without SELinux.
- Other `security.*`: synthetic capability/label test where permitted; assert copy/verify
  or fail closed, never delete; an unexpected staged `security.*` is not removed.
- `security.capability`/unknown `system.*`: synthetic xattr test where permitted; refusal.
- Inode flags: read `FS_IOC_GETFLAGS` where permitted; assert the user-settable mask is
  **exactly equal** on source/staged, managed bits are recorded under the G6 narrowing, an
  immutable/append flag yields `metadata-unsupported` before staging, and an injected
  unknown/unclassified bit also refuses. If the ioctl is unavailable, assert
  `metadata-unsupported` (fail closed) and record it.
- Project ID: set a project ID where the host/fs supports `FS_IOC_FSGETXATTR`; assert
  preserve/refuse. Inject a project-ID/`PROJINHERIT` mismatch that makes `os.link`/
  `os.replace` return `EXDEV`; assert `write-failed` with the fixed `project-id-mismatch`
  note, old manifest intact, no partial `.bak.N`. If unavailable, record the G6 narrowing
  as missing evidence.
- Timestamps: assert `mtime` is not claimed/preserved; the final file has a new
  `mtime`/`ctime`; no test treats that as failure.
- Backup: mode `0600`, owner writer, exact old bytes; no custom ACL/xattr copied; an
  existing 0644/ACL `.bak` is not reused.
- **Read-back descriptor:** assert staging and backup files are opened `O_RDWR` and
  verified with `pread`/seek on the held fd; a test that forces `O_WRONLY` must produce a
  clear refusal, not `EBADF`/a false success.

### 6.5 Symlink / hardlink / special-file policy

- Tree root symlink, member symlink, symlink to inside, dangling symlink, link loop →
  `directory-unsafe`, no write, no backup.
- Member hardlink (`st_nlink > 1`) → unsafe; a hardlink created during the scan → unstable
  or unsafe; no partial digest.
- FIFO, socket, block/character device (where creatable; skip if privilege/OS refuses) →
  unsafe; never opened for data.
- Root replaced by a symlink between proposal and rebuild → `stale-tree`/`scan-failed`, no
  write.
- Manifest final path replaced by a symlink/hardlink between checks → `stale-source`/
  `unsafe`, no write; final-window swap documented as residual.
- Directory mount point at root on an allowed FS → allowed; descendant mount → unsafe.
  Skip if the host cannot create a mount.

### 6.6 Backup and rollback

- Success creates exactly one **fresh** generation with the old bytes; earlier generations
  are untouched. An existing `.bak` with different ownership/mode/ACL/xattr is never
  reused.
- Existing `.bak`, `.bak.2` → newest+1 (`.bak.3`), no overwrite. Gap test: with `.bak`
  and `.bak.3`, the next is `.bak.4`, not `.bak.2`.
- A symlink/hardlink/directory at a candidate name is skipped; a new generation is used.
- Backup publication is temp+`fsync`+`os.link`+unlink; a crash before the link leaves no
  `.bak.N`, and no partial `.bak.N` is ever observed.
- Failed backup **before `os.link`** → no replace; old manifest intact; no `.bak.N`
  published; cell cleaned best-effort.
- `os.link` succeeds but the following directory `fsync` fails → old manifest intact; the
  complete `.bak.N` is preserved (do not delete it); no replacement;
  `backup-durability-failed` with "the backup may remain; the manifest was not changed."
- Unchanged/dry-run/refusal before backup publication → no `.bak.N`; a post-backup refusal
  may leave a completed backup (phase-specific assertion).
- Manual rollback: copy the backup **JSON bytes** over the manifest; `validate
  --directory-artifacts` accepts the old record; the new record is gone. The backup's
  `0600` mode/owner is not the original metadata; moving it does not restore the original
  owner, mode, ACL or xattrs. (The test does not add a `--restore` command.)
- Backup mode/owner as specified; no group/other read; no inherited ACL/xattr.

### 6.7 Dry-run, privacy, boundary and no-artifact checks

- Dry-run: snapshot the folder before/after; only reads; no manifest bytes/mtime change,
  no staging cell, no backup, no temp, **no lock**, **no step-4 fsync**, and no requirement
  for write permission on the manifest/parent; exit `0`/`2` as expected.
- Dry-run on a read-only manifest/parent succeeds if the scan/metadata reads succeed; it
  must not return `source-durability-failed`/`write-failed`/`permission-denied` merely
  because a real write would. A read-only metadata failure may still be
  `metadata-unsupported`.
- Dry-run while another process holds the lock: it must not return `busy` and must not
  create a cell/backup.
- A dry-run that would change still exits `0`; it must not be treated as authorization.
- Privacy canaries: absolute root, member name, `strerror`, inode/device/mount id, new
  digest. Inject a scanner error with all canaries; assert none appear in stdout/stderr.
  Run add, rehash, dry-run, busy, stale-tree and metadata-unsupported cases.
- **Unexpected-exception boundary:** inject `RuntimeError` with canaries in each of the
  three phases: before the cell, at the step-11 final guard after the backup is published,
  and from just before `os.replace`. Assert one fixed `INTERNAL_ERROR` response, no
  traceback/exception text, exit `2`, and the exact phase wording from §3.8; in the
  step-11 case assert the manifest is unchanged and the complete `.bak.N`/cell may remain.
- Success output: assert the artifact ID/path/digest/size/member count/backup path are
  present; member names/absolute root are absent.
- No `--json`: assert argparse rejects it and the manifest is unchanged.
- No network: monkeypatch `socket.socket`/`socket.create_connection` to raise; run a
  successful write; assert no call. (The implementation has no network code; this is a
  tripwire.)
- No writes by validate/inspect: snapshot; run both with the flag; only reads; no lock/
  backup/cell; the manifest bytes are unchanged.
- **Tree mutation check:** snapshot the tree's explicit content/metadata (ignoring atime
  if the read updates it); run a successful write; assert no explicit tree write, create,
  delete, chmod, chown, utime or xattr write. Do not assert literal metadata identity.

### 6.8 Installed package

- Build wheel and sdist in an approved temp directory outside the repo; install into a
  fresh venv; `pip check`.
- From outside the checkout, import the installed package and run
  `add --directory`/`rehash --directory` on a synthetic `ext4` tree (use an approved temp
  root; skip or refusal if the host's temp is not ext4).
- Reproduce the three directory digest vectors from a temp copy (the vector JSON is not
  packaged) or independent computation; do not import `tests/`.
- Assert the wheel/sdist contain the new module and no `tests/`, `docs/`, `examples/` or
  evidence files.
- Assert no new dependency and no network.
- Run the installed `validate`/`inspect --directory-artifacts` on the written manifest.
- Check that importing `runevidence.cli` on a non-Linux host does not import `fcntl`
  unconditionally (guard the import); a Windows import smoke test where CI provides it.

### 6.9 Cross-platform and skip accounting

| Case | Expected |
| --- | --- |
| Windows `add/rehash --directory` | `unsupported-platform`, exit `2`, no write. Native Linux tests skip. |
| macOS `add/rehash --directory` | `unsupported-platform`, exit `2`, no write. |
| Linux non-ext4 (`tmpfs`) | `unsupported-filesystem`, exit `2`, no write; no scan-root leak. |
| Linux ext4, facilities missing (`/proc`, `flock`, `O_PATH`) | `unsupported-platform`/`lock-failed`, exit `2`. |
| Test user is root | Permission/owner tests skip with an explicit reason. |
| No `setfacl` | Access/default ACL preservation test skips; record as missing evidence, not a pass. |
| No second user / cannot drop uid | Second-user access test skips; record as missing evidence. |
| No `FS_IOC_GETFLAGS` / project-ID ioctl | Flag/project-ID test records the G6 narrowing; not a preservation claim. |
| No SELinux label | SELinux test skips; no claim. |
| No mount privilege | Mount test skips; no claim. |
| Host FS cannot directory-`fsync` | Durability test skips or records `durability-unknown`; no false success. |
| Host temp is not ext4 | Real ext4 smoke/skip; record the missing ext4 evidence; do not use tmpfs as a release proof. |

Report `-rs` and account for every skip. Do not rewrite Windows sharing/DACL tests to
claim Linux parity; do not delete a test to improve a count.

### 6.10 Negative controls

These are required to prevent an overclaim:

1. **Nonparticipating writer lost:** hook the final window, write B, replace with C; assert
   B is lost and the command reports success. Name the test with `lost`, not `safe`.
2. **ABA manifest:** A -> B -> A before the final check; byte equality accepts; C wins.
3. **Tree change after the final scan:** modify a member after the rebuild but before
   replace; the command succeeds with a stale record; a later validate reports a mismatch
   (unless ABA). Document, do not "fix" by a third scan claim.
4. **No automatic inference / DIR base:** plain `add folder` adds files, not a directory;
   `rehash ID` without `--directory` refuses a directory; `validate` without the flag says
   `not-a-file`. Run `add --directory` from a different current folder: a relative `DIR`
   is interpreted against the current folder, not the manifest folder; a path outside the
   manifest folder is `outside-root`, and a manifest-relative interpretation is not tried
   as a fallback.
5. **No user digest:** there is no `--sha256`/`--size-bytes`; a hand-edited wrong digest is
   corrected only by rehash's scan, never trusted from input.
6. **Forged proposal (internal):** the existing `proposal-mismatch` tests still pass; the
   public path does not accept a caller-supplied proposal/preview.
7. **`rehash --all` with a directory:** exits `2`, writes nothing; it does not silently
   skip or auto-convert.
8. **Phase-specific failure writes:** every refusal test snapshots the manifest and names
   the phase. Pre-cell says no artifact; post-cell says cell only; post-backup says a
   completed `.bak.N` may exist; post-replace says the new manifest may exist. No blanket
   "nothing was written."
9. **No CAS claim:** a documentation/test assertion that the public help/README does not
   say "compare-and-swap" or "atomic CAS."
10. **`schema -o` audit:** confirm `schema -o` still writes only the requested schema file
    and is not repurposed as an authoring path; if pointed at a manifest path, the existing
    overwrite behavior is documented as an out-of-scope risk, not silently fixed in S4.

### 6.11 Existing tests and pins to update

S4-2a/S4-2b may change the CLI parser or its concurrency behavior, so these are expected
edits, not hidden changes:

| Test | Expected change |
| --- | --- |
| `tests/test_experimental_cli_parser_contract.py` | Update the current CLI hash (LF/CRLF), assert `--directory` is present on `add`/`rehash` only, route list/order remains 26, historical pins untouched. |
| `tests/test_directory_apply.py::test_no_command_or_other_module_uses_the_apply_module` | Allow the new publication module/CLI as an authorized user; internal apply behavior unchanged. |
| `tests/test_directory_cooperative_apply.py::test_public_paths_do_not_import_the_cooperative_module` | Keep true if the public path uses the new admission lock, not the O_EXCL cooperative module; update only if the implementation chooses otherwise (not recommended). |
| `tests/test_directory_writer_races.py` | Add the public negative control; existing S3P3P7 characterizations remain. |
| `tests/test_init_backups.py` | S4-2a: add a busy test; existing sequential backup tests should pass unchanged. |
| `tests/test_cli_migrate.py` / `tests/test_cli_conditions.py` | S4-2a: assert the fixed `BUSY` text/JSON envelope and exit `2`; success output unchanged. |
| `tests/test_cli_validate_directory.py` | Unchanged behavior; add a no-write/no-lock assertion if it fits the existing module. |
| `tests/test_cli_schema.py` (or equivalent) | Audit `schema -o` existing overwrite behavior; no S4 behavior change. |
| `tests/conftest.py` | Possibly a shared ext4/lock fixture; the R5 hook must not change. |
| `tests/test_directory_authoring.py` | Internal proposal tests unchanged; the read-only `FORBIDDEN` fixture remains for proposals. New public tests use a separate write-aware fixture. |
| `README.md`, `docs/directory-artifacts-foundation.md` | S4-2b documents the new flags, guarantee/limits and next steps; no behavior change in this design task. |

No schema/model tests change. No scanner tests change. No Windows test is rewritten.

### 6.12 Evidence limits

- Injected failures and monkeypatched scanners establish control flow, not native OS
  security or power-loss durability.
- A live subprocess lock test is stronger than a thread test but still not a hostile-writer
  proof.
- No custom Windows DACL fixture exists; no Windows S4 claim.
- No live multi-process directory write with a real editor/sync client is claimed.
- The S3P3P7 final corrected revision was not executed after the safety stop; S4 must not
  cite it as a pass.
- No Linux R5 evidence; the R5 writer remains Windows-only.
- A skip is not a pass; record every skipped node and why. Skipped ext4, ACL, inode-flag,
  project-ID or second-user coverage is **missing evidence**, not a release claim.
- The source-durability tests exercise the protocol's `fsync` order; they do not simulate
  a real power loss or a lying device.
- `schema -o` overwrite behavior is audited but not changed; it is not part of S4.

---

## 7. Implementation slices, gates and HOLDs

No slice is authorized by this RFC alone. Each starts only after the orchestrator/owner
accepts the prior gate and authorizes the specific change. This task starts none of them.

### 7.1 S4-D0 — Design (this document)

- Deliverable: `docs/directory-authoring-s4-rfc.md` v0.3 only.
- Gate G4-0: review for CAS/durability, staging-cell/ACL exposure, descriptor operations,
  three-phase boundary, backup phases, privacy, platform scope, Stage 3/3d/S3
  non-regression, and the recommendation.
- **HOLD-0:** no production/test/schema/example/README/progress change; no Git; no
  implementation. Internal S4-1 is accepted after this revision.

### 7.1a v0.2/v0.3 review correction map

| Accepted correction | Where addressed |
| --- | --- |
| **v0.3 C1** three-phase `INTERNAL_ERROR` + step-11 injection test | §3.8, §4.3, §4.4, §6.2 item 18, §6.7, Appendix B |
| **v0.3 C2** exact user-settable inode mask; unknown bits refuse | §3.1 G6, §3.9, §6.4, §8.2 |
| **v0.3 C3** `security.*` policy labels never deleted; verify/fail closed | §3.4 steps 8-10, §3.9, §6.4, §8.2 |
| **v0.3 C4** project-ID/`PROJINHERIT` `EXDEV` -> `write-failed` | §3.8, §3.9, §6.4, §8.2 |
| **v0.3 C5** dry-run no `fsync`, read-only, no write-path refusals | §3.4 step 4, §4.2, §4.6, §6.7, §7.3 |
| **v0.3 backup phases** pre-link vs post-link/fsync; preserve `.bak.N` and refuse replace | §3.4 step 10, §3.5, §3.7, §3.8, §6.2, §6.6 |
| **v0.3 nits** G5, lock step 3, checked-apply re-read, post-metadata fsync, S4-2a prerequisite, absolute `DIR` alias | §3.1 G5, §3.3 step 3, §3.4 steps 6-9, §6.2, §7.3/§7.4, §4.1/§5.3 |
| ACL staging exposure / inherited default ACL | §3.4 step 8-9, §3.9 creation policy, §6.4 |
| `O_RDWR` read-back through held descriptors | §3.4 step 9-10, §6.4, §2.2 |
| Source/parent durability; process crash vs power loss | §3.1 G5/G8, §3.2, §3.4 step 4, §3.5, §3.6, §6.2 |
| Public unexpected-exception `INTERNAL_ERROR` boundary | §3.8, §4.3, §4.4, §6.7 |
| No explicit tree mutations; narrowed G2 | §1.4 #6, §3.1 G2/G7, §3.10, §6.7 |
| Always-fresh backup; JSON-byte-only rollback | §3.4 step 10, §3.7, §6.6 |
| Dry-run takes no lock | §4.1, §4.2, §4.6, §6.3, §6.7 |
| One lock handle / self-busy / inherited fds | §3.3, §4.5, §4.8, §6.3 |
| `dir_fd`-relative operations and parent-swap test | §3.4 descriptor rule, §3.10, §6.3 |
| Default ACL, inode flags, project ID detect-or-narrow | §3.1 G6, §3.9, §6.4 |
| Backup temp+link, newest+1, gap test | §3.4 step 10, §3.7, §6.6 |
| Mount-info/mount-id manifest-folder proof | §3.2, §4.10, §6.1, §8.1 |
| Separate lock acquisition from loading (`init`) | §3.3, §4.8, §6.3 |
| Symlink aliases accepted/refused | §3.2, §3.3, §4.10 |
| `schema -o` overwrite audit | §6.10, §7.3, §8.2 |
| Unmount may reveal underlying dir/mismatch | §5.6 |
| Phase-specific "nothing was written" | §3.8, §3.4 phase note, §6.2, §6.10 |
| Subprocess termination; winner-holds-lock | §6.3 |
| Skipped ext4/ACL/flag coverage is missing evidence | §1.4 #13, §6.9, §6.12, §8.2 |
| R2/R9/R10/R11 and S4-2a entry | §4.1, §5.1, §5.3, §7.3, §7.4, §7.9 |

### 7.2 S4-1 — Internal guarded-publication core (no public write)

Candidate files:

- New internal module, e.g. `src/runevidence/directory_publication.py` (admission lock,
  metadata preflight, stage/backup/publish/fsync, sanitized error mapping).
- `src/runevidence/directory_apply.py` only if a refactor is needed to reuse the checked
  rebuild without changing `apply_directory_proposal` behavior.
- `src/runevidence/manifest.py` only for a new internal durable-save helper; `save_raw`
  behavior must remain identical.
- New `tests/test_directory_publication.py`; existing internal tests unchanged.

Work:

1. Implement the directory-fd admission lock as a **separate acquisition** from loading
   (init with no manifest), one handle, self-busy, `O_CLOEXEC`, release in `finally`, no
   fork/inheritance.
2. Implement load-under-lock identity/source checks and the source/parent `fsync`
   precondition (`source-durability-failed`).
3. Implement the private staging cell (owner-only access/default ACL, inherited xattr
   removal) before any content; `O_RDWR` staging/backup files with `pread` read-back.
4. Implement complete-set metadata preservation/refusal, including default ACL, inode
   flags and project ID detect-or-narrow; second-user access boundaries.
5. Implement `dir_fd`-relative operations throughout, backup temp+`fsync`+`os.link`+
   unlink with newest+1 numbering and pre-link/post-link failure phases, final guard,
   replace, parent `fsync`, a three-phase flag and sanitized `INTERNAL_ERROR` boundary.
6. Do not touch the CLI, routes, schema, models, scanner, adapters or docs.

Gate G4-1:

- Crash/source-durability injection at every step; real files; old/new manifest complete;
  backup exact; no partial `.bak.N`; pre-link/post-link backup failure phases.
- Two-process lock serialization, winner-holds-lock, self-busy, SIGKILL release,
  dry-run-no-lock/no-fsync, inherited-fd checks; three-phase `INTERNAL_ERROR` including
  the step-11 final guard.
- Staging-cell/second-user/ACL/default-ACL/flag/project-ID evidence; explicit skips are
  missing evidence.
- `O_RDWR`/`pread`, `dir_fd` parent-swap and mount-info classification tests.
- No public route/behavior; no CLI change; no schema/model/scanner change.
- Full existing suite passes; no hidden skips; installed package unchanged.
- **HOLD-1:** no public write, no README/route, until independent review accepts the
  crash/lock/metadata evidence.

### 7.3 S4-2a — Admission-lock retrofit (no directory write)

S4-2a is a prerequisite for S4-2b, not a directory-authoring feature. It changes
concurrency behavior of existing writers and must be separately authorized.

Candidate files:

- `src/runevidence/cli.py` (all built-in manifest writers resolve → lock → load → write).
- `src/runevidence/directory_publication.py` (shared lock helper).
- `tests/test_init_backups.py`, `tests/test_cli_migrate.py`,
  `tests/test_cli_conditions.py`, `tests/test_cli_*` for add/rehash busy.
- Parser pins only if option/help text changes (no new route).

Entry conditions (from review):

- **R2:** built-in writers use `resolve → lock → load → write`; `init --force` holds the
  lock across backup and save. No writer loads before locking.
- **R10:** define the public contract changes: `init`/`add`/`rehash` gain a new exit-2
  `busy` outcome; `migrate`/`conditions` gain a fixed `BUSY` error code in their existing
  JSON envelopes (`format` unchanged, `ok:false`, `exit_code:2`, `error.code:"BUSY"`) and
  the fixed text `ERROR (BUSY): another RunEvidence writer is updating the manifest.
  Nothing was written.` `--dry-run` for every writer takes **no lock**, performs no
  source/parent `fsync`, requires only read access, and cannot return `busy`; it may still
  check metadata read-only. Tests pin these outcomes. The existing error-code catalogues
  must add `BUSY` explicitly; an unknown code must not be echoed.
- **Schema `-o` audit:** `schema -o PATH` can currently write/overwrite whatever `PATH`
  names, including a manifest path. S4 does not use it as an authoring path and does not
  silently change it; the existing overwrite behavior is documented as an out-of-scope
  risk. Hardening `schema -o` would be a separate change.
- No directory `--directory` option is added in S4-2a. No public directory write.

Gate G4-2a:

- All built-in writers lock before load and serialize; winner holds the lock; loser exits
  `2` writes nothing; JSON busy envelopes are fixed and path-free.
- `init --force` backup+save under one lock; no manifest exists yet case works.
- Dry-run no-lock tests for all writers; existing success output unchanged.
- Full suite and skip accounting; no directory write.

### 7.4 S4-2b — Public explicit directory CLI (Linux/ext4 only)

S4-2b is implemented (2026-11-12) after S4-2a closed and the owner authorized it under the
standing reviewer-acceptance rule. Delivery is gated on G4-2b review acceptance.

Candidate files:

- `src/runevidence/cli.py` (`--directory` on `add`/`rehash`; uses the S4-2a admission
  lock; sanitized output).
- New `tests/test_cli_directory_authoring.py`.
- `tests/test_experimental_cli_parser_contract.py` (pins; 26 routes).
- `tests/test_directory_apply.py`/`test_directory_cooperative_apply.py` only for the
  authorized import assertions.
- `README.md`, `docs/directory-artifacts-foundation.md` (public surface; no guarantee
  inflation).

Work:

1. Add the grammar and refusal rules in §4, including the pinned current-folder `DIR`
   base and `outside-root` refusal.
2. Compose the internal S4-2a lock/propose/publish; no new route.
3. Add the public sanitizer, phase-specific `INTERNAL_ERROR` boundary and text output;
   no JSON.
4. Add `--dry-run` as a separate no-lock branch; no preview token.
5. Wire the staging-cell/source-durability/metadata policy into the public command; no
   override.

Gate G4-2b:

- **R9:** `DIR` is pinned to the current working directory (lexical, no `realpath`),
  must remain inside the manifest folder, and refuses `outside-root`; no manifest-relative
  fallback. Tests cover a different cwd and an escaping path.
- S4-2a accepted; owner accepts the exact guarantee in §3.1 and the R11 limitations
  (cooperating = this version's CLI only; non-CAS lost updates; no tree freeze; metadata
  detectability limits; no Windows parity; `durability-unknown` exit `0`).
- CLI success/refusal/exit codes/dry-run/no-lock/no inference/no JSON tests.
- Privacy canaries absent in text/errors; unexpected `INTERNAL_ERROR` in all three
  phases (including the step-11 final guard);
  no writes by validate/inspect.
- Staging-cell second-user/ACL/default-ACL/flag/project-ID evidence or recorded missing
  evidence; source/parent fsync.
- Real `ext4` end-to-end add/rehash + validate; tmpfs/Windows/macOS refusal; manifest
  folder mount-info classification.
- Concurrent built-in writers serialize; `busy` writes nothing; winner holds the lock.
- Installed wheel/sdist smoke; `pip check`; no new dependency.
- Full suite and `-rs` skip accounting.
- **HOLD-2b:** no release/version bump; no Windows parity; no batch; no CAS/ACL/R5 claim.

### 7.5 S4-3 — Windows parity (separate, not authorized)

- Requires `ReplaceFileW` or an equivalent that preserves the destination DACL/owner/
  attributes, a Windows writer-race/ACL evidence matrix, and a separate privacy/exit
  review.
- Not part of S4-v1. Do not infer parity from the Windows scanner.

### 7.6 S4-4 — Batch/multi-directory transaction (separate, not authorized)

- Requires a transaction/rollback model across multiple directory records; the current
  audit/batch are read-only, independent and explicitly non-transactional.
- SQLite or another transactional store is a candidate but dormant; a separate
  migration/cutover design is required. Do not use it to smuggle CAS into S4-v1.

### 7.7 S4-5 — Linux R5 evidence lane (separate, not authorized)

- The R5 writer remains Windows-only. S4 does not create a Linux R5 lane and must not claim
  R5 qualification.

### 7.8 Gate table

| Gate | Entry | Exit evidence | Forbidden after the gate |
| --- | --- | --- | --- |
| G4-0 | This v0.3 RFC | Review accepts scope/guarantee/no-CAS/limits and the final S4-1 corrections | Any implementation beyond S4-1 authorization |
| G4-1 | Owner authorizes internal core after this revision | Crash/source-durability/lock/cell/metadata/descriptor tests; existing suite; no public surface | CLI/public write |
| G4-2a | G4-1 accepted + owner authorizes lock retrofit | All built-in writers resolve→lock→load→write; busy text/JSON; dry-run no-lock; `init --force` backup+save under lock | Directory write |
| G4-2b | G4-2a accepted + owner accepts exact guarantee/R11 | CLI/privacy/INTERNAL_ERROR/installed/skip evidence; 26 routes | Windows, batch, JSON, release/version bump, CAS/ACL/R5 claims |
| G4-3 | Separate Windows design | DACL/replacement evidence | Linux parity claims |
| G4-4 | Separate transaction design | Multi-record atomicity/rollback evidence | Silent use of SQLite |
| G4-5 | Separate R5 design | R5 evidence | Linux R5 claim from S4 |

### 7.9 Recommendation

**Recommendation: keep general S4 NO-GO; narrow, do not proceed broadly.**

1. **Do not authorize general public directory authoring.** The JSON-authority architecture
   cannot provide CAS against nonparticipating writers, and S4 must not claim it. Windows
   DACL/`ReplaceFileW` evidence does not exist.
2. **The only defensible candidate is the narrow S4-v1 in this RFC:** explicit
   `add --directory`/`rehash --directory`, Linux `ext4` only, one directory per invocation,
   this version's built-in writers admission-locked, durable fresh backup, staging cell,
   source/parent durability, metadata preflight, two scans, no JSON, no new route.
3. **Authorize S4-1 first, internal only.** Its gate is crash/source-durability/lock/cell/
   metadata correctness with no public surface. This is the lowest-risk way to learn
   whether the protocol is implementable and testable on the target host.
4. **Authorize S4-2a (lock retrofit) separately.** It changes existing writer concurrency
   contracts (`init`/`add`/`rehash` busy; `migrate`/`conditions` JSON `BUSY`; dry-run no
   lock). It is a prerequisite, not a directory feature.
5. **Authorize S4-2b only after G4-2a and an explicit owner decision** that accepts the
   exact guarantee in §3.1 and R11: cooperating means only this version's CLI; older
   versions/library callers/cooperative_apply are nonparticipants; non-CAS lost updates;
   no tree freeze; metadata detectability limits (G6 narrowing); no Windows parity;
   `durability-unknown` exit `0`.
6. **If the owner requires no-lost-update protection against nonparticipating writers, or
   Windows parity, S4 must remain NO-GO.** Those need a transactional store/daemon or a
   separate Windows replacement/DACL protocol, not an incremental flag.
7. **Do not start implementation in this task.** No production/test/schema/example/README/
   progress change, no Git.

### 7.10 What would change this recommendation

- A real transactional authority that every supported writer uses, with conditional
  updates, history and rollback (for example a fully migrated SQLite design). This is a
  separate architecture/cutover, not S4-v1.
- Proven Windows `ReplaceFileW`/DACL/owner/attribute preservation with a Windows writer
  matrix.
- A kernel/filesystem primitive that makes a tree snapshot or a content-conditional
  manifest replacement available to an unprivileged local writer (none is assumed today).
- An owner policy that explicitly accepts the nonparticipating-writer limitation and the
  metadata residuals. Without that acceptance, the answer is NO-GO.

---

## 8. Alternatives, rejections and residual risks

### 8.1 Alternatives

| Alternative | Decision and reason |
| --- | --- |
| **Keep internal-only (no public writes)** | **Retained default.** Zero new public write risk; no CAS/metadata/Windows claim. This is the correct choice if the owner will not accept the narrowed guarantee. |
| **Advisory lock only** | Rejected as a *safety claim*. It serializes participants only; it does not stop editors/scripts; it does not freeze the tree. It is one component of S4-GP/1, not the whole guarantee. |
| **Existing O_EXCL cooperative lock as the public lock** | Rejected. It leaves a stale lock on crash, needs manual removal, has no wait/stale-age policy, only wraps apply, and is a Windows-tested internal mechanism. It remains internal. |
| **Persistent sibling lock file** | Considered as a fallback. It works but is an extra artifact, needs own-file/untracked recognition, and reintroduces stale-lock policy. Not chosen; fail closed if directory `flock` is unavailable. |
| **Reuse an existing `.bak` generation** | Rejected for S4. An existing backup may have different ownership/mode/ACL/xattr (for example an `init` backup); S4 always creates a fresh compliant generation. |
| **Direct staged file in the manifest folder** | Rejected. A folder default ACL can grant a second user access before the original ACL is copied; S4 uses a private owner-only staging cell first. |
| **`statfs`/`st_dev` filesystem classification** | Rejected. It misses bind/overlay and mount identity; S4 uses the scanner's fresh mount-info/mount-id proof. |
| **Combine lock retrofit with public `--directory`** | Rejected. Lock retrofit changes existing writer contracts and gets its own S4-2a gate before any S4-2b directory write. |
| **`flock` the manifest file** | Rejected. `os.replace` changes the inode; two writers can lock different inodes across a replacement. |
| **`flock` the manifest's parent directory** | **Chosen.** Stable inode, no artifact, kernel-released on crash; binds only participants. |
| **Compare-then-replace as "CAS"** | Rejected. S3P3P7 observed B lost after the comparison and before `os.replace`. The words CAS must not be used. |
| **`renameat2(RENAME_NOREPLACE)`** | Rejected. It is an existence test, not a content test; it cannot replace an existing manifest. |
| **`renameat2(RENAME_EXCHANGE)`** | Rejected as CAS. It publishes the new file before validating the old bytes; swapping back is not atomic with concurrent writers and can lose their writes. |
| **`link`/`unlink`/`O_TMPFILE` publication** | Rejected as CAS. They can reduce or rename the temp window but cannot condition publication on the target's expected bytes. |
| **Mandatory locking / `chattr +i` / immutable** | Rejected. Privilege/FS-specific, changes permissions, nonportable, and not a user-space protocol. |
| **`fanotify`/`inotify`/leases** | Rejected. Not a snapshot or conditional write; events can be lost/coalesced; privileged or complex. |
| **Copy-on-write / reflink / snapshot** | Rejected as CAS. A staged copy plus rename is still last-writer-wins; FS snapshots are FS-specific/privileged. |
| **Backup-only** | Retained as part of S4-GP/1, but rejected as a substitute for conflict avoidance. It recovers A, not a lost competitor B. |
| **Separate daemon / transaction service** | Considered. It could serialize all writers that use it, but adds a service, lifecycle, auth, IPC/network, availability and privacy surface. Not authorized for S4-v1. |
| **SQLite (dormant) as the writer authority** | Considered. `BEGIN IMMEDIATE`/versioned rows could provide conditional updates if every writer migrated; SQLite is dormant and JSON is authoritative. Migration/cutover/history/export is a separate design; not S4-v1. |
| **Manifest generation/version field** | Rejected for S4-v1. It is a schema change; external writers ignore it; it does not create CAS by itself. |
| **Windows `ReplaceFileW`** | Considered. It is documented to preserve DACLs, but has not been adopted or evidenced; still not CAS. Separate Windows gate. |
| **New `directory`/`record` command** | Rejected. Adds a route, parser/pin/privacy surface and a second output contract; existing `add`/`rehash` are the right verbs. |
| **Automatic directory inference** | Rejected. It would change established `add folder` behavior and silently choose a record shape. Explicit `--directory` is required. |
| **JSON authoring output** | Deferred. It needs a versioned envelope, sanitization, exit-code and compatibility review. |
| **Multiple directories / batch transaction** | Rejected for S4-v1. No transaction/rollback model; one directory per invocation. |
| **Remove/rename directory records** | Out of scope. No safe public operation; manual JSON remains outside S4. |
| **Linux R5 lane** | Separate design. Not claimed from S4. |

### 8.2 Residual risk register

| Risk | Level | Handling / gate |
| --- | --- | --- |
| Nonparticipating writer lost between final check and replace; includes older versions, library callers and `cooperative_apply` | **High** | Documented; no CAS claim; backup holds A, not B; owner must accept R11 or remain NO-GO. |
| ABA manifest change (A -> B -> A) invisible to byte equality | Medium | Documented; no history/version; final check only. |
| Tree changes after the final scan; record stale immediately | **High/Medium** | Documented; no freeze/snapshot; validate later detects ordinary changes; ABA remains. |
| Source was never durable before a power loss | **High** | G8: source+parent `fsync` or `source-durability-failed`; old-or-new recovery is not claimed from a non-durable source. |
| Crash after replace before parent `fsync` | Medium | Durable fresh backup; `durability-unknown` warning; no power-loss guarantee. |
| Crash during cell/staging/backup leaves a cell or temp; post-link dir-fsync failure leaves a complete `.bak.N` | Low/Medium | Old manifest intact; cell/temp never trusted; no partial `.bak.N`; post-link failure preserves the backup and refuses replacement (`backup-durability-failed`); manual/future cleanup; backups never overwritten. |
| Inherited default ACL exposes the staged file before metadata copy | **High before mitigation** | Private owner-only cell + ACL/default-ACL removal before content + second-user access tests at every boundary; refusal if not achievable. |
| Custom ACL/xattr/owner/group/default ACL cannot be preserved | Medium | Preflight refusal (`metadata-unsupported`); complete-set both-ways verification; no silent loss. |
| `trusted.*` xattrs invisible to unprivileged `listxattr` | Medium | Documented residual; refusal when visible; no all-metadata claim. |
| Inode flags / project ID not inspectable or not preservable | Medium | Exact user-settable mask; immutable/append and unknown bits refuse; managed bits and project ID are G6-narrowed with recorded evidence; no all-metadata claim. |
| SELinux label mismatch/set failure | Medium | Policy-assigned `security.*` is verified/fail-closed, never deleted; host-dependent; no claim without evidence. |
| Windows DACL/owner/attributes | **High for Windows** | S4-v1 refuses Windows; separate `ReplaceFileW` design required. |
| Admission lock held during a long scan | Medium | `busy`, no timeout/steal; liveness limitation; bounded scan limits. |
| Lock retrofit changes existing writer contracts | Medium | S4-2a separate gate; fixed busy text/JSON; dry-run no lock; success output unchanged. |
| Directory `flock` unsupported / parent unreadable | Low/Medium | Fail closed (`lock-failed`/`unsupported-platform`); no weaker fallback. |
| Manifest folder renamed/replaced while held | Medium | Parent identity check; `dir_fd` operations stay in the locked inode; refuse if path changed. |
| Symlink/hardlink final-window swap | Low/Medium | `O_NOFOLLOW`/`lstat`/`nlink`/`dir_fd` checks narrow; final window remains. |
| Path alias / symlink-hiding directory path | Low | Lexical current-folder base, no `realpath`; scanner exact spelling/no-follow; `outside-root` refusal. |
| Manifest folder filesystem misclassified | Low/Medium | Fresh mount-info/mount-id proof and release allowlist; `statfs` not used; refuse unknown/bind/overlay/network. |
| Disk full/quota during cell/stage/backup | Medium | Old manifest intact; no `.bak.N` published; cell cleaned best-effort; exit `2`; no replace. |
| Cell/temp leftover appears as untracked in `inspect` or is picked up by a broad `add .` | Low/Medium | Documented; S4-v1 does not auto-delete unknown cells; manual removal after a crash; future cleanup/own-file policy. |
| Unexpected exception after cell/backup creation but before replace | Medium | Phase-2 text: manifest unchanged, backup/cell may remain; no false "nothing was written"; no traceback. |
| Unexpected exception from just before replace | Medium | Phase-3 text: manifest may have been replaced; validate; no traceback. |
| Privacy leak via verbose authoring error | Medium | Public sanitizer + canary tests; internal exceptions stay verbose. |
| User confusion: `validate` without the flag says `not-a-file` | Low | Help/README; next-step text. |
| `rehash --all` refuses when a directory artifact exists | Low | Documented rough edge; explicit file targets; no silent skip. |
| No public remove/rename | Medium | Manual JSON; future design; do not imply S4 covers it. |
| No timeout/CPU/memory ceiling | Medium | Scanner logical bounds; long scans possible; explicit command. |
| Injected tests are not native security proof | Medium | Evidence limits documented; no overclaim; independent review. |
| `schema -o` can overwrite a manifest path | Low/Medium | Audited as an existing out-of-scope surface; S4 does not use it as authoring; hardening is separate. |
| Project ID/`PROJINHERIT` mismatch makes link/rename fail `EXDEV` | Medium | Preflight where detectable; surface as `write-failed` with `project-id-mismatch`; old manifest intact; no partial `.bak.N`; no generic unknown error. |
| Skipped ext4/ACL/second-user/flag coverage | Medium | Recorded as missing evidence, not a pass; S4-2b gate may not claim it. |
| S3P3P7 final corrected revision not executed | Medium | No pass claim; S4 must supply its own evidence. |
| No Linux R5 | Out of scope | Separate S5; do not claim. |

### 8.3 Rejected claims

S4-v1 documentation, help, commit messages and reviews must not say:

- "atomic compare-and-swap", "CAS", "no lost updates", "safe against concurrent editors";
- "snapshot", "frozen tree", "consistent point-in-time digest";
- "whole-file atomic publication" without the G2 qualifier "for this operation's
  replacement"; an unrelated truncating writer can still expose partial JSON;
- "preserves all metadata"; G6 is detect-or-narrow for inode flags/project ID and has
  unreadable-namespace residuals;
- "power-loss safe" without G8's durable source/parent precondition;
- "no `.bak.N` on any failure"; a post-link directory-`fsync` failure preserves a complete
  backup and refuses replacement;
- "delete `security.*` labels to match the source"; policy-assigned labels are verified or
  fail closed, never deleted;
- "dry-run proves source durability" or "dry-run performs the write-path `fsync`"; it is
  read-only and skips step 4's source/parent durability check;
- "cooperating" without R11's definition (this version's CLI only; older versions/library
  callers/`cooperative_apply` are nonparticipants);
- "preserves Windows DACLs";
- "hostile-writer safe", "multi-writer safe", "lock prevents edits";
- "crash-proof"; only the protocol ordering and the backup are claimed;
- "Windows parity", "cross-platform byte-identical directory digests";
- "Linux R5";
- "validate/inspect write" or "directory authoring is enabled by default".

### 8.4 Closing statement

This document is a v0.3 design gate only. It changes no production, test, schema, example,
README or progress file, makes no Git write or publication operation, adds no public write
behavior, keeps JSON as the authority, leaves SQLite dormant, preserves Stage 3/3d and
scanner S3, and claims no Linux R5. Only internal S4-1 may be authorized after this
revision; S4-2a and S4-2b remain NO-GO until separately authorized. The owner must choose:
remain NO-GO, or authorize the narrowed S4-v1 path under the exact non-CAS guarantee in
§3.1 and the R11 limitations. Implementation must not begin without that decision.

---

## Appendix A. Evidence and inventory method

Read-only inventory for this design (no repository writes other than this RFC, no Git
write/publication operation, and no test/build/install command that writes the tree):

- Read the full `docs/native-directory-scanner-linux-rfc.md` v0.4.7 (status/changelog,
  §3.7 concurrent writers, §3.13 atomicity limits, §4 digest semantics, §5 CLI/API,
  §6 security/privacy, §7 test layers, §8 S4 NO-GO, §9 alternatives/residuals).
- Read the full `docs/directory-artifacts-foundation.md`, including the S3P3P7
  characterization and preservation closeout.
- v0.2 revision: applied the accepted review corrections for staging-cell/default-ACL
  exposure, `O_RDWR` read-back, source/parent durability, the earlier two-phase
  `INTERNAL_ERROR` boundary (superseded by v0.3), narrowed G2/G6/G7, always-fresh backups with temp+link, `dir_fd`
  operations, mount-info filesystem proof, symlink aliases, lock lifetime/self-busy, and
  the S4-2a lock-retrofit entry conditions. No code or test was run.
- v0.3 revision: applied the final S4-1 text corrections for the three-phase
  `INTERNAL_ERROR` boundary, backup pre-link/post-link failure phases, exact inode-mask/
  unknown-bit handling, policy-assigned `security.*` labels, project-ID/`PROJINHERIT`
  `EXDEV`, dry-run no-`fsync`/read-only behavior, and the noted nits. No code or test was
  run.
- Read the S3P3P7/directory persistence/cooperative/apply/review material:
  `directory_authoring.py`, `directory_apply.py`, `directory_cooperative_apply.py`,
  `directory_preview.py`, `directory_plan.py`, `directory_review.py`,
  `directory_batch.py`, `directory_present.py`, `directory_audit.py`,
  `directory_hashing.py`, `directory_scanner.py`, `checks/directory_policy.py`,
  `checks/files.py`, `manifest.py` (`save_raw`/`save_manifest`/`dump_raw`/`artifact_to_dict`),
  `models.py` (`Artifact`/`is_own_file`/backup names), `edit.py` (`plan_add`/`plan_rehash`),
  and `cli.py` (`add`/`rehash`/exit codes/backup generation).
- Read the relevant tests: `test_directory_writer_races.py`, `test_directory_apply.py`,
  `test_directory_cooperative_apply.py`, `test_directory_authoring.py`,
  `test_directory_s2b_races.py`, `test_directory_scanner.py`,
  `test_cli_validate_directory.py`, `test_experimental_cli_parser_contract.py`,
  `test_init_backups.py`, and the directory policy/artifacts tests.
- Verified the current baseline hashes used by the progress records:

| File | SHA-256 |
| --- | --- |
| `docs/native-directory-scanner-linux-rfc.md` (v0.4.7) | `5f2fc4b82cb1a488018d26ace1586611ea71a9491d8dc953c7666d88dae1cb4e` |
| `docs/directory-artifacts-foundation.md` | `3ba6a5fee3e4f75805c00f78d8f6072b5d474da05b7e6127bd2cf714dad1c06e` |
| `src/runevidence/directory_scanner.py` | `db86c9a5e9edb138f8064a49292de215494ee54143e143ad1cb5e9d1f003d512` |
| `src/runevidence/manifest.py` | `40fb33f1d0b3e420eb9f9158089340ad02e9a977ceea8dbfa288b669c50f7b78` |
| `src/runevidence/cli.py` | `7244f68942a3c5d39ca7bbab66e508c21cc6c37f053d850e7335ed92bbb1abf9` |
| `src/runevidence/models.py` | `0e3ac57f3148d01be1e0193d149c3f127f23915bcb44dd0a33064daa6e9a9caa` |

- The directory-fd `flock` behavior was checked on the target host with a read-only
  directory descriptor (open `O_RDONLY|O_DIRECTORY`, `LOCK_EX|LOCK_NB`, unlock, close).
  This is a capability check, not a write test.
- No test suite, build or install was run. Only read-only inventory commands (including
  read-only `git status`/`git log`) and the directory-`flock` capability check above were
  used. The only file produced by this task is this RFC.

## Appendix B. Definitions

| Term | Meaning in this document |
| --- | --- |
| **S4-v1** | The narrowed future public surface described in §1/§4, not implemented here. |
| **Cooperating writer** | A built-in command in **this installed version's CLI** that takes the admission lock in §3.3. Older versions, library callers, `cooperative_apply`, editors, scripts, IDEs, sync clients and other tools are nonparticipating (R11). |
| **Admission lock** | The non-blocking directory-fd `flock` that serializes cooperating writers for a manifest folder. It is acquired separately from loading and passed as one handle. |
| **Guarded publication (S4-GP/1)** | The ordered lock/load/source-fsync/propose/rebuild/cell/backup/fsync/replace/parent-fsync protocol in §3.4. It is not CAS. |
| **Staging cell** | A private `0700` directory with owner-only access/default ACLs and no inherited xattrs, created before any staged file content. |
| **CAS** | Compare-and-swap: an atomic operation that replaces the manifest only if it still holds the expected bytes. S4-v1 does **not** provide it. |
| **Stale source** | The manifest bytes/identity/metadata no longer match the loaded snapshot; the write is refused. |
| **Stale tree** | The directory digest/size/member count changed between the proposal and the checked rebuild; the write is refused. |
| **Source durability (G8)** | The source file and parent directory are `fsync`ed before staging, or the write refuses; old-or-new power-loss recovery is not claimed without it. |
| **Durable backup** | A complete previous manifest in a fresh generation, written/`fsync`ed and published by `os.link` before replacement. |
| **`backup-durability-failed`** | The `.bak.N` link succeeded but the following directory `fsync` failed; the backup is preserved, the manifest is not replaced, and the backup may remain. |
| **Metadata preflight** | The owner/group/mode/access/default ACL/xattr/inode-flag/project-ID classification, complete-set both-ways verification and refusal in §3.9. |
| **INTERNAL_ERROR boundary** | The public CLI's fixed, traceback-free response for an unexpected `Exception`, with three phases: before cell ("nothing was written"), cell/backup may remain but manifest unchanged, and from just before `os.replace` ("may have been replaced"). |
| **Tree immutability** | The command performs no explicit write/create/delete/chmod/chown/utime/xattr write on the scanned folder; only the manifest folder is written. Scanner `atime`/relatime and concurrent writers are not controlled. |
