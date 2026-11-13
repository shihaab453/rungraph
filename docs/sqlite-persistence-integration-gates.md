# SQLite persistence integration gates, R5R1 private staging

Status: R5R1 PARTIAL / HOLD, pending independent adjudication.
During final evidence duplication, the unguarded parent driver used
shutil.copytree from callsite-sources/ to source-copies/ inside the new Temp
evidence root. The installed copytree/copystat implementation invokes os.chmod
on copied ordinary directories. This violated the no-permission-operation
constraint despite being confined to new external evidence. No contemporaneous
audit trace or before/after ACL capture exists for those calls. Exact Windows
permission effects cannot be claimed. The affected copy route was stopped and
no cleanup, permission reversal, test rerun or product repair was attempted.
The passing test results below remain observations, not gate acceptance.
The isolated module passed 56 guarded cases with 56 exact JUnit identities, no
failures, skips or xfails and no unexpected guard denial. This is Windows-only
synthetic Temp evidence. It does not approve production persistence.
S3P3P7 PERSISTENCE SAFETY HOLD remains in force.

All 19 protected source hashes and both initial R5 draft hashes matched before
work. The private product module remains byte-identical to its original R5
draft. Only its isolated tests were extended and this repository document was
created. The original drafts and every failed attempt are retained externally.
R1D guard source was extracted unchanged from the authorized original ZIP after
size/SHA-256 and complete archive CRC verification. Strict selftest observed
54 checks and 48 expected denials; only the two console getters were callable.

Final independent collections: baseline 1,456 nodes, selected 1,454, focused 56.
Baseline minus selected is exactly the unreadable scanner file/folder pair;
reverse difference is empty. All collections recorded zero test/fixture starts.
Only the focused module executed. Fresh public core/Hydra/MLflow imports and
CLI --help returned success with target source origins and no private-module
or directory-module startup import. These checks do not establish compatibility
of every existing reader or writer.

The first runtime attempt had 11 passes and 36 setup errors because the receipt
plugin rejected pytest's direct parameter pseudo-fixtures. The corrected plugin
admits the reviewed request.param getter for actual collected parameter names.
The next 47-case run passed. An additional coverage pass extended this to 56.
An initial public driver incorrectly expected SystemExit from cli.main; the
reviewed main catches it and returns 0. Its failed output is preserved. Three of
four permitted routine correction batches were used. None changed R1D policy.
The third completed guarded evidence-script dispatch after a mislabeled extra
selftest. That selftest's actual arguments/results are retained as selftest
evidence, not counted as a database readback.

## Source binding and current contract

The 19 exact handoff pins passed before edits. Target branch/HEAD/tree match the
handoff; all source references below use target worktree bytes. Every path's
full SHA-256 appears in the appendix; exact function ranges were extracted via
AST and checked against bounded source reads. Full source copies and raw
callsite hits are in source-copies/ and source-callsite-scan.json. The scan is
bounded to core, Hydra and MLflow source roots, plus a read-only repository
Python callsite search. Existing tests are consumers but are not public
application routes. Other applications/scripts outside this repository remain
unknown. Source mapping is not runtime compatibility evidence.

`manifest.LoadedManifest` L26-35 is a frozen dataclass containing a mutable
Manifest, absolute resolved source path and exact validated `source_bytes`.
`load_manifest` decodes UTF-8, json.loads then Manifest.model_validate, retaining
the original bytes. Path resolution can follow links; the private staging seam
adds advisory ordinary path/identity checks and restricts its source to the
explicit synthetic Temp parent. This cannot establish hostile-filesystem safety.

In `models.py`, `_Strict` L83-86 forbids extra schema fields, `Project` L89-94
supplies identity through project.id, `Artifact` L107-158 supports file/directory
records, and `Manifest` L211-218 has schema_version Literal['0.1'] and all six
record/edge lists. There is no independent manifest UUID in this model. Default
artifact_type=file and digest_format=None preserve older representations;
directories require runevidence-directory-v1 and files reject a directory
format. Digests normalize to lowercase; volatile and metadata remain accepted.
Project metadata, run params, artifact/result/evidence metadata are free-form.
Unknown schema fields refuse; unknown metadata keys are allowed and must survive.
Schema validation is Pydantic's current non-strict/coercing behavior, not a
new schema. Graph integrity/duplicate IDs are checked separately, not by the
loader. Python json.loads duplicate-key/nonfinite-number behavior is inherited;
this gate does not claim a stricter JSON standard than the actual loader.

Canonical serialization differs from source: dump_raw adds two-space indentation
and a final LF with ensure_ascii=False; dump_manifest expands model defaults and
normalizes model values, and artifact_to_dict removes default file/type/format
and false volatile fields. Original whitespace/CRLF, omitted fields, ordering
and uppercase digest bytes are not guaranteed to survive canonical rewriting.
The private component stores a raw exact BLOB and its hash/length alongside the
validated model identity, including omitted/default fields and opaque metadata.
The executed tests cover all record categories and directory/volatile fields,
and all 56 isolated cases passed. Malformed UTF-8/JSON, foreign schema versions, extra schema fields
and invalid directory formats refuse in isolated tests through the actual model,
never silently drop fields or downgrade to a different manifest representation.

## Per-caller inventory

All existing application routes below retain their present JSON authority. None
is covered by the private DB. Reads of files and JSON do not acquire the private
cooperative lock; only the explicit cooperative apply writer participates.

| Source functions and exact lines | Route behavior and lock relation | Authority today |
|---|---|---|
| `src/runevidence/manifest.py`: `resolve_manifest_path` L38-42, `load_manifest` L45-87, `load_raw` L144-146 | Read JSON; path resolver accepts folder/file; parser retains exact bytes; no lock. | Existing source JSON and its loaded snapshot. No DB route. |
| `src/runevidence/manifest.py`: `dump_raw` L109-110, `dump_manifest` L103-106, `artifact_to_dict` L90-100 | Serialize/export to returned text/dict only; no file writes or lock. | Caller-supplied raw/model. Canonical text is not original byte authority. |
| `src/runevidence/manifest.py`: `save_manifest` L113-141, `_write` L178-181 | Create or replace JSON, sibling staging for existing file, fsync, os.replace; bypass cooperative lock. | Existing JSON writer; no expected revision for whole-model saves. |
| `src/runevidence/manifest.py`: `save_raw` L149-175 | Stage sibling JSON, compare expected bytes then replace; bypass cooperative lock. | JSON remains authoritative. Compare/replace lost-update window remains open. |
| `src/runevidence/cli.py`: `_load_or_report` L37-52, `cmd_validate` L55-65, `cmd_inspect` L68-80 | Read JSON; run checks; print text/JSON report. No writes or lock. | load_manifest snapshot plus independently read artifact files. Default file route. |
| `src/runevidence/cli.py`: `cmd_init` L83-136, `_backup_existing` L275-305, `_newest_backup` L308-315, `_same_bytes` L318-322, `_write_new_file` L325-339 | Discover/extract model, optionally read old JSON and create exclusive backup, then save_manifest; bypass lock. | JSON output is current authority; backup is exact original bytes, not an epoch/recovery policy. |
| `src/runevidence/cli.py`: `cmd_rehash` L185-234, `cmd_add` L237-272, `_finish_edit` L171-182 | Load JSON/raw; plan edits; dry-run prints; actual write calls save_raw; bypass cooperative lock. | JSON snapshot. No new SQLite coverage or writer admission. |
| `src/runevidence/cli.py`: `cmd_schema` L386-394, `cmd_adapters` L397-400, `build_parser` L403-502, `main` L505-515 | Schema exports stdout or caller output file; adapters lists registrations; parser routes seven public commands. | No staging switch/import. schema output is schema, not a manifest migration. |
| `src/runevidence/edit.py`: `select_artifacts` L123-182, `plan_rehash` L185-282, `_reread_values` L298-334, `plan_add` L340-428 | Read artifact paths/digests/JSON locators and mutate in-memory raw; validate model; no own persistence/lock. | Caller LoadedManifest/raw plus file reads; CLI ultimately saves. Metadata/omitted fields carried in raw. |
| `src/runevidence/directory_authoring.py`: `propose_add_directory` L107-151, `propose_rehash_directory` L154-193, `_measure` L297-305 | Read/scan directory, construct proposal/raw; check serialization; no save or lock. | Loaded JSON plus native read measurements. Proposed bytes are advisory. |
| `src/runevidence/directory_apply.py`: `apply_directory_proposal` L121-164, `_pristine` L170-183, `_check_source` L255-261, `_rebuild` L264-279 | Revalidate raw/model/source, rescan, dry-run or save_raw. Direct actual apply bypasses cooperative lock. | JSON authority and separate directory scans. No atomic cross-resource snapshot. |
| `src/runevidence/directory_cooperative_apply.py`: `lock_path_for` L103-104, `apply_directory_proposal_cooperatively` L107-162, `_lock_path` L168-190, `create` L230-253, `owned` L255-266, `release` L268-290 | Actual changed writes acquire exclusive companion lock, check owner/source then delegate apply; release owned lock. Dry-run delegates without lock. | Cooperative JSON writers only. init/add/rehash/direct-save/external writers bypass it. Custom DACL/companion security unresolved. |
| `src/runevidence/directory_preview.py`: `preview_directory_proposal` L64-98 | Read-only checked dry-run via apply; never save or inspect/acquire lock. | Fresh advisory proposal/scan, no reusable authorization. |
| `src/runevidence/directory_plan.py`: `plan_directory_change` L18-51 | Parse loaded raw, propose once, preview with fresh checked dry-run; no persistence or lock. | JSON snapshot plus non-atomic native scans. |
| `src/runevidence/directory_audit.py`: `audit_recorded_directories` L77-126, `_model` L129-142, `_source` L145-151 | Validate model/source, plan each recorded directory, recheck; no write or lock. | Bounded advisory per-directory views; not a common instant or CAS seal. |
| `src/runevidence/directory_batch.py`: `review_directory_changes` L64-110, `_model` L134-144, `_source` L147-153 | Validate bounded requests, plan previews, source checks; no writes/lock. | Advisory batch over independent scans and original JSON source. |
| `src/runevidence/directory_present.py`: `render_directory_review` L182-251 | Pure shape-check/render supplied audit/batch; no fresh scan, load, save, export file or lock. | Supplied report, which display cannot authenticate. |
| `src/runevidence/directory_review.py`: `review_directories_for_operator` L30-48 | Dispatch audit or batch, render report, no persistence or lock. | Delegated advisory report; None audits, explicit empty tuple is empty batch. |
| `src/runevidence/directory_review_path.py`: `review_existing_directory_manifest` L11-24 | Read through load_manifest exactly once; dispatch operator review. | Existing JSON at target; private internal entrypoint, no CLI activation. |
| `src/runevidence/checks/__init__.py`: `run_checks` L47-60 | Read/check supplied LoadedManifest; directory switch defaults False and is not set by public CLI. | JSON snapshot plus separate files/results/graph checks. |
| `src/runevidence/checks/files.py`: `check_files` L489-518, `check_artifact` L196-421, `_check_directory_internal` L424-486, `iter_files` L63-108 | Default file checks/listing; opt-in internal directory scanner; no persistence or lock. | Model record and artifact filesystem. Existing directory/native gaps remain. |
| `src/runevidence/checks/results.py`: `check_results` L68-148 | Read bounded JSON result artifacts at locators; optional directory policy refuses directory locators; no writes/lock. | Supplied model and readable_paths, not a DB read. |
| `src/runevidence/checks/graph.py`: `build_index` L44-69, `collect_edges` L72-88, `check_graph` L121-388 | In-memory graph/index/reference/cycle checks; no persistence/lock. | Supplied model. Loader model validation alone does not perform these checks. |
| `src/runevidence/report.py`: `format_overview` L43-84, `format_findings` L37-40, `to_json` L87-103 | Format supplied model/report as text/JSON, no disk persistence. | Caller report/model, no new authority. |
| `src/runevidence/adapters/__init__.py`: `available_adapters` L17-28, `get_adapter` L31-35 | Read entry-point registrations and instantiate adapter; no manifest save. | Adapter selection; CLI cmd_init owns persistence. |
| `src/runevidence/adapters/generic.py`: `discover` L48-51, `extract` L53-88 | Discover files and read/hash them, build file-only Manifest in memory; no save or lock. | Artifact filesystem discovery; excludes own manifest/backup files via is_own_file. |
| `packages/runevidence-hydra/src/runevidence_hydra/adapter.py`: `discover` L81-85, `extract` L87-128, `_read_run` L131-209 | Discover via GenericAdapter, read .hydra config/overrides/settings YAML/JSON, hash files; create model only. | Hydra metadata/files, not an existing RunGraph manifest reader. CLI init later saves JSON; no DB coverage. |
| `packages/runevidence-mlflow/src/runevidence_mlflow/adapter.py`: `discover` L92-104, `extract` L106-205, `_read_experiments` L302-349, `_read_yaml_mapping` L383-399, `_read_params` L507-520, `_read_metric` L523-557 | Read bounded local-store metadata, params and metric history; hash discovered files; create model only. | MLflow file-store metadata, not existing RunGraph manifest authority. No remote/server writes, no DB coverage. |
| `src/runevidence/_experimental_versioned_manifest.py`: `stage_validated_manifest` L266-310, `read_staged_manifest` L313-324, `compare_and_update_stage` L327-372 | Executed isolated component: explicit synthetic Temp staging, exact-byte readback and participating SQLite CAS/receipt transaction. | Non-authoritative staging only. No public importer, application writer changes or activation; 56 synthetic cases passed; production remains unverified. |

`src/runevidence/__main__.py` L1-3 dispatches cli.main; `__init__.py` defines
package metadata only. Hydra/MLflow __init__ exports their adapters. No existing
source imports the new private module (bounded source evidence plus a guarded fresh-process public startup check). Public package exports, CLI commands,
entry points, dependencies and installation files were not edited.

## Private staging behavior and threat boundaries

The private component restricts writes to new stage-UUID children of a specially named
synthetic test parent below Windows current-user LocalAppData Temp, based on
profile/layout checks and ordinary lstat metadata. It checks path components
before resolving, refuses reparse/UNC/device/alternate-stream/relative/dotdot
aliases and multiply linked files, uses exclusive mkdir/xb before SQLite mode=rw,
and retains partial scratch files on refusal. Parent naming/layout are trusted
caller conventions, not proof of freshness, ownership, DACL or authenticity.
The worker independently established its fresh short external evidence root
before source edits. No historical fixture/evidence root is reused for writes.

It uses the existing model, preserves exact source bytes, checks source content
and file identity before/after staging, and records advisory provenance. It
returns frozen receipts, a new store ID, generation=1 and revision=1. Windows-only
admission is a declared gate limitation. Python path checks do not contain a
malicious OS actor, do not make cross-resource operations atomic and cannot
exclude TOCTOU swaps. There is no DACL/ownership proof.

The tested reader opens mode=ro with no JSON fallback, checks application ID,
user_version, exact expected schema, integrity, identity, content hash/length,
real Manifest validation and bounded receipt history. Unexpected sidecars refuse;
no recovery is authorized. The component bounds source to 256 KiB, DB to 16 MiB,
operation IDs to 64 ASCII characters, and history to 32 successful receipts.
Those are synthetic-gate bounds requiring future product review.

The tested writer validates replacement/model before connection; BEGIN
IMMEDIATE, revision-conditional UPDATE and receipt INSERT share one transaction.
Only participating writers are addressed. Matching retries bind original expected
revision and exact replacement bytes, returning their historic committed revision;
same ID/different intent collides; new identical-byte intents increment revision.
Typed busy/missing/invalid/fatal refusals do not retry. Revision overflow is
blocked. Logical rollback, corruption refusal, sequential contention and exact-byte
roundtrip passed isolated tests. In the injected receipt failure, physical DB
bytes were also equal before/after, with both hashes retained. SQLite does not
promise physical byte identity for every rollback; logical state/receipt atomicity
is the required semantic contract. No crash/power-loss behavior was tested. The tests use separate connections and
injected receipt failure/source changes/overflow, not live process/crash evidence.

No existing JSON save, native directory scan, restore or public writer integration
was called. The legacy save_raw lost-update counterexample remains open.
R3's same-transaction operation receipts are the conceptual connection, not an
import of unreviewed Temp code or proof of unknown-acknowledgement recovery here.
R4's surviving authority generation and declared quiescence would require a
separately reviewed restore controller. This single-generation component has no
anchor restore, epoch transition or recovery logic. R4 did not establish global
cross-DB atomicity or resurrect acknowledged data lost after backup.

## Blocking decision register

| Blocker, all unresolved | Required evidence before activation |
|---|---|
| SQLite state, journal/WAL/SHM custom DACL and owner | Reviewed Windows companion-path policy, native setter/getter provenance and adversarial sidecar tests; no permission changes at R5. |
| External JSON writer bypass | Enumerate and stop every legacy/direct/external writer; prove rejected writes after exclusive cutover. |
| Trusted writer admission and quiescence | Owner-approved exclusive admission protocol, held writer-stop proof and refusal when exclusivity cannot be established. |
| Cross-resource directory scan/DB consistency | Reviewed snapshot/race policy and mutation schedules across scans and commit; independent security review. |
| Controlled restore and acknowledged data loss | Offline quiesced restore drill with known lost operations, verified backups and explicit owner loss policy. |
| Authority rollback/epochs | Trusted surviving authority design, non-reuse proof and rollback/ABA tests; separate-DB atomicity gaps must be contained. |
| Unknown acknowledgement | Coupled receipts, crash before/after COMMIT, historical retry/collision readback and explicit retention semantics. R5R1 covers sequential historical replay and collision only; crash acknowledgement remains unproven. |
| Retention/backup | Consistent SQLite backup, retention/capacity/receipt policy and independent restore verification, never live file-copy claims. |
| Crash/power loss/filesystem trust | Platform durability and hostile swap boundaries, journal/recovery drills and qualified fsync/storage assumptions. |
| Native scanner POSIX/Windows gaps | Reviewed native pointer provenance and supported-platform evidence. R1E six native/DACL cases remain unrun; POSIX support is not implied. |
| Historical unreadable fixture equivalence | Separately authorized provenance/security review; lstat metadata cannot prove unreadable contents, owner or custom DACL equivalence. |
| Broader acceptance and security barrier | R5R1 strict guard/selection/56-case/JUnit/startup checks passed with the exact reviewed R1D two-address call table. Independent adjudication, legacy regression, native/ACL and hostile-filesystem evidence remain required. No native permission expansion is authorized. |

## Immutable source hashes for this map

| Target relative source | SHA-256 |
|---|---|
| `src/runevidence/__init__.py` | `c31d0732b3cec915405789ebe15b976ec37b91c4acc863e6cb4a664ca66c75ad` |
| `src/runevidence/__main__.py` | `482b8693e2fe6dbd732e3d7961e03827d3efa187bad11d29da188be580ebbeea` |
| `src/runevidence/_experimental_versioned_manifest.py` | `4296131d0d2b9412e70f53d302abcb347ad0675559872e036092cb81b201ef91` |
| `src/runevidence/adapters/__init__.py` | `79557565e848993bb4164467858aa13d041dc53912a52eb6ebe9d8f0006916e4` |
| `src/runevidence/adapters/base.py` | `7ae7799c0f7d8af18f7045a2db21c487c636a3057207b7e82100d7035fc0868e` |
| `src/runevidence/adapters/generic.py` | `132a31b9d75e26abe0082e87b3176c50a074b9032ff12456ece7e2251bdfeab7` |
| `src/runevidence/checks/__init__.py` | `1d1c51319fdee0c7e7226048415dd41e664bb492006b3ec20deb7ef5a80cf536` |
| `src/runevidence/checks/directory_policy.py` | `50506491e2e13529a7b0d059806ad2592837f9f1a3d532b48df3d1b02a14ef8f` |
| `src/runevidence/checks/files.py` | `cff58f0f9ef139cc22e1e13174b7132c610d577abdeb8ba8f58016496a571a65` |
| `src/runevidence/checks/graph.py` | `6320bcf9c486aba435618637c4c9391f0d166a7be462ab7bb59193f59328df62` |
| `src/runevidence/checks/results.py` | `6952364cc85a86c81fc1b94bc375b09b8ea1cef831040d2cc9888857fb94f512` |
| `src/runevidence/cli.py` | `e45b9d696d1fa63730b223bb6c7d43d5093f9aee42fb84cb498dad4112096fe1` |
| `src/runevidence/directory_apply.py` | `76ac35bd917ea01f30f2be2bc9deaae9c3e3211f458491177c65bba58b7fc7d0` |
| `src/runevidence/directory_audit.py` | `4fb5f0b2e1d5aa5f34466da1a1ae124a630f8dde7b4aa8da5dd781443341ba73` |
| `src/runevidence/directory_authoring.py` | `d93e390a3dbdd3495000fa0682e37f943a1857c63cc1b09c67d51244d5ef6533` |
| `src/runevidence/directory_batch.py` | `1bc5fd18c579858535a7f76cf90f6e17eada3b427a5c927540efc788cdbed619` |
| `src/runevidence/directory_cooperative_apply.py` | `bd72c2a8395c5101e36dff89969dc14c46ba6eeceb80241425c2cf66fc578b4d` |
| `src/runevidence/directory_hashing.py` | `ac3dd094415cbca9bcaa9aea03121af920bce2a2a565e5be03847ce38b32f350` |
| `src/runevidence/directory_plan.py` | `69b506155819a33c36b11f95a789787935eecc513da8e92d15ce5fc1108c45f7` |
| `src/runevidence/directory_present.py` | `b2fcaa5aba4eeccb4abcf47531187c090adc92eb04d19ef1cf0c14b762dfe212` |
| `src/runevidence/directory_preview.py` | `bc13dcdadabab617baefced450dbb4b08ef4623df030ff0b0e9a78d4419e78f4` |
| `src/runevidence/directory_review.py` | `ba6a430a167d334917cf31d446582ae7f055d087bf15e15193028395cc06f1e0` |
| `src/runevidence/directory_review_path.py` | `cdd2259abcc367ef39a2accc66bdb31dd368aa0a9eb22dd1d6101e87453d7dc3` |
| `src/runevidence/directory_scanner.py` | `596175288346fc0902801d89455e135fb2eab56735da485bdabb8852b49f4178` |
| `src/runevidence/edit.py` | `fd0e82cda7eece6065d9405202f2989ab753396fa375abd5f3eb3993e98f74d5` |
| `src/runevidence/findings.py` | `b172e46d9799972aa881400f2b4f3918d62afb3ae01db7063ea5f20e249b45ea` |
| `src/runevidence/hashing.py` | `1232c1ac8cf118d5b140e5f3b1cac7bc65ee956f7bee1559ac3b94d18aa0e2c1` |
| `src/runevidence/manifest.py` | `ad065582c2dac53785fdd239218f25662d41826209747eafea057caeca92123c` |
| `src/runevidence/models.py` | `1f6fb49ea91e332e00182c10af2ba02293e20d72854da45161743336b7cb5903` |
| `src/runevidence/report.py` | `13510a24d6779a44085f485a1410730140f65de7ae5e3f227031182919c71426` |
| `packages/runevidence-hydra/src/runevidence_hydra/__init__.py` | `06023392c2e4ee99617d8abb01c531bc0eb4873ecadb1c974bd8c5ffc0c4d57e` |
| `packages/runevidence-hydra/src/runevidence_hydra/adapter.py` | `038eb779afedc66114845fc8d911dabf081eace03c41f18fe31efcfb0fc1f647` |
| `packages/runevidence-mlflow/src/runevidence_mlflow/__init__.py` | `c70c8dd77122a5031a9a13920c1b2dfd00eeee2f801ba2a5e0f1b5824001009e` |
| `packages/runevidence-mlflow/src/runevidence_mlflow/adapter.py` | `d3d09e934c70433dc421260cae4388596f3b106454d52c208c1fb0116e8c1788` |

## Four actionable product cutover gates

1. **Private staging contract, this R5R1, PARTIAL / HOLD for independent review.**
   The guarded actual-model/exact-byte, scratch-only CAS/receipt, bounds and
   corruption checks passed 56 isolated cases. Source mapping and this repository
   document are complete within the bounded scope. Preserve all failed attempts
   and obtain independent review of the external evidence-copy permission-route
   deviation as well as the product findings. No real writes or activation.
2. **Production reader compatibility in explicit opt-in test mode.** Exercise
   every route in the inventory, including CLI validate/inspect, edits' loaded
   snapshot contract, internal directory review chain and adapter extraction/
   metadata paths. Preserve raw bytes/unknown metadata/defaults and refuse
   foreign/corrupt/missing stores. No fallback authority or real writes. Obtain
   positive per-caller tests; a top-level import test alone is insufficient.
3. **Exclusive migration/cutover rehearsal in disposable cloned experiments.**
   Separately authorize and security-review a complete writer-stop rule covering
   save_manifest/save_raw, init/add/rehash, direct/cooperative apply, adapter init,
   scripts and external editors. Capture verified exact JSON and consistent DB
   backups; admit one authoritative DB, with JSON explicitly export-only.
   Simultaneous JSON/SQLite authority is NO. Rehearse failures and rollback with
   receipt retention, unknown acknowledgements and a reviewed epoch/restore
   policy; prove refusal if writer freeze, backup or authority validation fails.
4. **Owner-controlled production activation.** Require DACL/owner/sidecar and
   companion-path review, nonparticipating-writer enforcement, safe controlled
   restore/data-loss decisions, resolved native scanner/platform limitations,
   broad safe regression, retention/backup/durability evidence and external
   independent approval. Activate only by explicit owner action. This gate
   authorizes no automatic migration, dual authority or public activation.

## Coverage and remaining boundaries

The source fixture contains project, runs, artifacts, results, evidence and
relationship records, opaque metadata, CRLF/Unicode, uppercase hashes and omitted
default/schema fields. File and directory/volatile variants roundtrip exact
source bytes even when canonical dumps differ. Validation refuses malformed
UTF-8/JSON, extra fields, unknown schema and invalid directory formats before a
connection. Windows ordinary Temp paths pass while unsafe layout/relative/dotdot
paths refuse; no link or ACL fixture was constructed.

Tests verify real sqlite_schema.sql text, column and committed-receipt integrity,
exclusive targets, revision/operation limits, exact 256 KiB source acceptance,
oversize source/16 MiB DB refusal, 32-receipt capacity and historic replay at
capacity. Independent reopening, bounded SQLITE_BUSY without retry, identical-byte
revision advancement, source before/after freshness refusal, missing/corrupt/extra
schema/sidecar refusal and no JSON fallback also passed. The physical 16 MiB
growth boundary was not stress-filled; oversized DB refusal was tested.

Separate-connection sequential contention is not a live two-process race.
Injected receipt/source/overflow failures are not crash or power-loss evidence.
Original synthetic JSON stays unchanged except in tests explicitly injecting
source mutation/deletion. Quiescent raw readbacks include those deliberately
tampered stores and are not admission/recovery evidence. A source path convention
does not prove freshness against a hostile caller or filesystem.

The guard is a source-constrained Python audit harness, not an OS sandbox.
Original guard native-operation records and complete allowed-call counters are
retained per process, with supplemental post-plugin native event records. The
original pre-hook resolution and allowed startup call arguments are not a native
syscall trace. Scanner/DACL read symbols remain noncallable. Six native/DACL
cases, broad legacy suite, cooperative ACL/copytree/developer-link workflows and
POSIX behavior remain unrun. Historical unreadable fixture contents, 90 Git
directory size observations and 15 recurring object timestamps remain qualified;
no owner/custom-DACL equivalence is inferred.

No dual-write period is acceptable. JSON remains the sole current production
authority until a separately approved exclusive cutover. Optional editors must
be stopped or admitted under that future protocol; a SQLite transaction cannot
protect JSON from nonparticipating writers. Backups, retained operation receipts,
unknown acknowledgements, epochs, restore/loss consent, external directory scan
consistency and Windows/POSIX companion security remain explicit phase barriers.

### D17-S1R6 reconciliation, 2026-11-08 (append-only; static assessment)
Earlier `cli.py` hash `e45b9d696d1fa63730b223bb6c7d43d5093f9aee42fb84cb498dad4112096fe1` is historical; current LF SHA-256 is `a4cd86ac0903b4827bf7a846949a253d3d8e1aff9a02602e12ea95512d3a03e6`. The historical `report.py` pin `13510a24...` is also stale; current `src/runevidence/report.py` SHA-256 is `1eb3fdc9236018c3d2c4ff02ee9c968cfbd602b085bdfe4592eb5cb6fa0df063` (adapter-unreadable presentation changes, no new built-in writer found in static review). Current CLI locations: `_load_or_report` 55-70; `cmd_validate` 73-83; `cmd_inspect` 86-98; `cmd_init` 543-596; `_finish_edit` 631-642; `cmd_rehash` 645-694; `cmd_add` 697-732; `_backup_existing` 735-765; `_newest_backup` 768-775; `_same_bytes` 778-782; `_write_new_file` 785-799; `cmd_schema` 846-854; `cmd_adapters` 857-860; `build_parser` 863-1068; `main` 1071-1081. The public parser now registers **22**, not seven, commands. The fifteen additional routes are `compare`, `leaderboard`, `matrix`, `run`, `claim`, `lineage`, `impact`, `coverage`, `coverage-gate`, `fingerprint`, `fingerprint-compare`, `coverage-diff`, `review-diff`, `impact-diff`, `review-gate`. Their `cli.py` handlers occupy lines 102-540 and use the `run_*.py` / `run_fingerprint_explain.py` in-memory projection modules, statically reviewed in R5; add these to the current reader-route inventory. The legacy seven routes, plugin entry points and private experimental code retain their own distinct authority limits. This supplements rather than changes historical gate evidence. No SQLite activation or runtime/plugin proof; JSON authority and P7 HOLD remain.

## Dated note — 2026-11-11 (overnight session): current source identities

- Current `src/runevidence/cli.py` LF SHA-256 is `500148e36b7b08f2e24910c7dce6df8d6704322dff511d44618d8dbc60521ba2` with 23 parser routes including `validate-batch`; the exact tested bytes are covered by `tests/test_experimental_cli_parser_contract.py`.
- Current `src/runevidence/manifest.py` SHA-256 is `a47494717308b5a73b65a161b81418e287b6e7c6e39c71bf4d6dc2f7b0e374fa`; duplicate-key and numeric-overflow rejection are retained, legacy `NaN`/`Infinity`/`-Infinity` are accepted only at `results[*].value`, and schema findings use privacy-safe pointers.
- `src/runevidence/report.py` remains `1eb3fdc9236018c3d2c4ff02ee9c968cfbd602b085bdfe4592eb5cb6fa0df063` and `README.md` remains `692c007d83219aaee49048d10b2f17793560bf4379475f0dbcde8f6c32c655eb`.
- This is an append-only dated current-state note, not a pin refresh; all historical text and prior gates remain as written, and no SQLite activation, B4 qualification, CLI runtime/plugin qualification or cutover is implied.

## Dated note - 2026-11-11 (Stage 2b): comparability CLI and pins

- Current `src/runevidence/cli.py` LF SHA-256 is `a4a4b921921f5e8fe1b04ca79d40e5dcffb2678e28ad5a0313ff6c3f25c3f147` (CRLF `83c2dbfa6cccbd9e5c505d2cef6d244901179caf67ba70054dc2dfde6b2d2d3a`) with 24 parser routes; `comparability` is registered directly after `compare`, and removing it recovers the exact prior 23-route sequence.
- Reader test pin: `READER_HASH` is `582a27bca1dfe64e1d7aaf2c18376044511431b2c08cc270bbb6581cea604ecc`; the superseded 23-route reader digest `cb614ec86203f3845fb54e0c1f92f5f2af0d0e3ea1b294d77392b49284e0dde3` is preserved in the historical tuple.
- `README.md` SHA-256 is `164e01cb976ca3770cd90521542e03dfb947c0fe2067288011f0d814bb606a06`. The controlled refresh and its proof packet are documented in `docs/comparability-stage2b-pin-refresh.md`.
- Append-only note. No SQLite activation, B4 qualification, plugin/runtime qualification or cutover is implied; JSON remains production authority and P7 HOLD remains.
