# Schema 0.2 stabilization (S6): design and contract (v0.4)

Status: **CLOSED (2026-11-12): accepted, implemented through slices 1-4, panel-reviewed, and
owner-approved.** Schema 0.2, `migrate`, `conditions` and the read-only preflight are stable;
the comparison verdict CLI remains experimental. The owner accepted the recorded hashes/test
log in place of a commit; nothing is committed or published. Defines what "schema 0.2 is stable" means, as recommended by both reviewers on
2026-11-12, and is the prerequisite for the accepted locator design. Each slice had its own
review; the stabilization gate closes only when the panel accepts the evidence package.
Two items additionally need explicit owner sign-off regardless of the standing rule:
removing "experimental" from the README for the schema, and whether the work must be
committed before it can be called stable (no Git operations are performed under the current
rule).

## 1. Problem

Schema 0.2, `migrate`, `conditions` and `comparability` are marked experimental and
"evolving", but nothing defines what would make them stable. The locator RFC depends on a
frozen 0.2. Dogfood also shows the practical barrier is preparing and discovering
comparable results, not the comparison itself.

## 2. What "stable" means

**Stable is a compatibility promise:** later changes may not change the accepted manifest
interpretation, the documented outcomes, the authoring behavior or the refusal behavior
without a new schema version and an explicit migration. It is backed by:

- one accepted behavior matrix and clause-to-test traceability;
- automated evidence for every clause, including CLI-level paths;
- frozen 0.1 and 0.2 schema artifacts and a frozen runtime accept/reject example set;
- documented supported authoring paths;
- a clean regression gate on the qualified environment;
- explicit reviewer closure.

"Stable" does not mean all comparability features are done. Same-run comparison and
multi-dataset-as-one-context stay out of scope and explicitly experimental.

## 3. Scope

In scope: the matrix, preflight, the `init --force` refusal, the authoring decision, schema
freezing, dogfood closure, README wording, regression evidence, and the CLI parser-pin
refresh that adding `--preflight` requires (`tests/test_experimental_cli_parser_contract.py`).

Out of scope: locators/0.3, adapters, YAML, Windows parity, batch, S5 Linux R5, same-run
comparison, comparing multiple datasets as one evaluation context, release/publication.

## 4. Readiness mode (`comparability --preflight`)

Route and shape (no new route):

```text
runevidence comparability BASELINE TARGET --preflight
runevidence comparability BASELINE TARGET --preflight --baseline-result ID --target-result ID
```

Rules:

- **Selectors are both-or-neither.** Zero IDs lists both sides; exactly one is
  `INVALID_ARGUMENTS`, exit `2`. Ordinary comparison keeps its required selectors and its
  JSON behavior unchanged.
- **List mode** lists candidates in manifest order, capped at 20 per side, with a fixed
  `N more not shown` line when truncated. Exit `0` whenever the listing itself works, even
  if some candidates are not ready; discovery must be scriptable.
- **Pair mode** (both IDs) reports exactly those two results. Exit `2` only for a manifest
  load failure, an unknown ID or invalid arguments. A bridge mapping/bound error is
  **not-ready, exit `1`**, with its fixed mapping code as the reason; the bridge stops at
  the first error per side, so a failed side shows only that code and no per-dimension
  states. It reports `SAME_RUN_ID` as a not-ready reason, so a pair is never "ready" and
  then rejected.
- **Readiness uses the bridge's own resolution and bound checks plus the core's own
  validation**, not a copy. A value is **usable** only when the core's side state is
  `KNOWN`; `AMBIGUOUS` and `CONFLICTING` are not ready. Pair readiness runs the full bridge
  pipeline, including the pair-only combined-size bound, and the core `validate()`,
  including `NUMBER_LIMIT` and `SAME_RUN_ID` checks that the bridge deliberately leaves to
  the core.
- **Three states** per mandatory dimension: `present`, `recorded-unknown` (an explicit null
  where the contract permits one) and `missing`. `recorded-unknown` is **not ready**.
- Report dataset attachment separately from the five dimensions as
  `mappable`/`not-mappable`; readiness requires all five usable and the attachment
  mappable.
- **Agreement:** a reported-ready pair makes the comparison exit `0` or `1`. The one
  documented exception is the case/whitespace-difference path, which is `INSUFFICIENT_EVIDENCE`
  even when both sides are `KNOWN`; the footer names it. A reported-not-ready pair must have
  a fixed reason. This is a matrix row with its own fixture set.
- Text only. `--preflight --json` is a fixed text refusal, exit `2`; no new machine
  document before its own versioning/privacy review.
- Output: fixed lines on stdout, value-free. **Result IDs appear only in successful
  preflight output** (a recorded privacy decision; other errors stay ID-free) and are always
  taken from the loaded manifest, never echoed from the command line. The ID pattern blocks
  control characters; an ID longer than 1024 characters is shown as a fixed placeholder and
  counted, not printed in full.
- **Duplicate IDs on a side:** the bridge rejects the whole side with `DUPLICATE_ID`. List
  mode still exits `0` because the listing itself worked, and shows that side as
  `unavailable: DUPLICATE_ID` without candidates.
- Footer states that readiness does not establish matching values, artifact integrity, or a
  successful comparison, and that omitted candidates and pairwise compatibility were not
  assessed.
- Nothing is written.

## 5. `init --force`

Today `init --force` already prints a fixed 0.2 warning and keeps a backup; it does not
silently drop fields. S6 changes the behavior:

- **Refuse whenever the raw JSON parses and its version field is `"0.2"`, valid or not**,
  before adapter discovery, backup creation or saving, under the existing writer lock. This
  covers first-class data, the downgrade case (0.2 with no first-class fields would
  otherwise regenerate as 0.1) and a 0.2-declared manifest that fails validation, which is
  exactly where hand-entered data is most at risk. Nothing is written.
- The refusal is a fixed, value-free message with exit `2`; no IDs. It tells the user to
  move the manifest aside, run `init`, then `migrate --to 0.2` and reapply the first-class
  values from the moved file.
- **If the raw JSON does not parse**, keep today's behavior: back it up, then regenerate
  (0.1 path unchanged). This is the only 0.1-risk area and is tested.
- 0.1 manifests, explicit or omitted version: unchanged.
- The existing tests that pin the warning and backup behavior (`test_init_backups.py`) are
  rewritten as part of this slice; new tests cover explicit/omitted 0.1, unloadable
  manifests, existing backups and symlinks.

## 6. Supported authoring paths (recorded decision)

- `conditions` remains the supported command for the **six** run-level condition fields.
- `dataset_id` (artifact) and `metric_definition` (result) are hand-edited, with an
  **executable** documented example: migrate, take a backup or scratch copy, make both
  edits, validate, preflight, compare. A test extracts the example and runs it.
- Document that schema-valid is not the same as ready or comparable, and how equal/unequal
  legacy duplicates resolve.

## 7. Schema freeze

- Fix the stale directory description first: both schema files and `models.py` currently say
  "'directory' is development-only: no command checks or records it yet", which is false
  after S4. Replace it with an accurate sentence.
- Pin the SHA-256 of **both** `schema/runevidence.schema.json` and
  `schema/runevidence-0.2.schema.json` in tests. Today neither has a byte pin; both are only
  checked for generator equality.
- Keep the generator/schema/runtime agreement tests.
- Add `.gitattributes` `schema/** -text` so a CRLF checkout cannot change the bytes (or pin
  both LF and CRLF hashes; choose one and test it).
- Add a frozen runtime accept/reject example set (valid and invalid manifests) covering
  version gating, nonblank strings, selector ownership, null branches and unknown keys,
  because a frozen JSON Schema does not freeze runtime acceptance.
- Do not pin README or guide bytes. Test only the README stable/experimental split sentence.
- Separate artifact freeze from semantic versioning: any incompatible change needs a new
  schema version; a formatting-only artifact change needs an explicit reviewed policy.

## 8. Behavior matrix (v0.2)

| # | Area | Documented behavior |
| --- | --- | --- |
| 1 | Version | Omitted version is 0.1; `0.2` opts in; unknown future versions reject |
| 2 | 0.1 purity | A 0.1 manifest with any 0.2-only key (even `null`) is a schema error |
| 3 | Presence/null | Absent, `null` and empty are distinct; `conditions: {}` valid; `conditions: null` invalid; permitted explicit nulls recorded as recorded-unknown |
| 4 | Serialization | Reading never rewrites; 0.1 output omits 0.2 keys; 0.2 preserves omission/null/empty and round-trips |
| 5 | Resolution | Equal legacy duplicates collapse; unequal or malformed duplicates reject; a first-class explicit null alongside a non-null legacy value rejects; whole protocol/metric-definition/environment values never merge; first-class wins only when the legacy value is absent |
| 6 | Selectors | Dataset/checkpoint selectors resolve and are uniquely owned; dangling/conflicting/duplicate IDs reject before comparison |
| 7 | Bounds | Shape/value bounds apply before canonical equality/conflict; `INPUT_BOUND_EXCEEDED` refuses |
| 8 | Outcomes | `COMPARABLE_RECORDED_CONDITIONS` (0), `NOT_COMPARABLE_RECORDED_CONDITIONS` (1), `INSUFFICIENT_EVIDENCE` (2); input rejection uses `input_rejected` plus error codes and exit 2. `MANIFEST_LOAD_FAILED`, `MAPPING_FAILED` (with bridge sub-code), `INVALID_ARGUMENTS` and `INTERNAL_ERROR` also exit 2 |
| 9 | Precedence | Full order: baseline load, target load, baseline mapping, target mapping, pair combined-size (reported as target), then the core. A baseline failure is reported before the corresponding target failure |
| 10 | Migration | Additive, preserves values, fresh backup generation, dry-run writes nothing, refusals leave the manifest unchanged |
| 11 | Conditions | Set/clear/`null`/no-op, patch file and stdin, first-wins before no-op, backup-backed write, sanitized errors, dry-run no write |
| 12 | Absorption (CLI) | `init --force` refuses declared 0.2; `add`/`rehash` preserve first-class 0.2 data, tested through the real CLI on a 0.2 manifest |
| 13 | Directory records (CLI) | `add --directory`/`rehash --directory` work on 0.2; migrate/conditions/compare preserve directory records |
| 14 | Privacy (per command) | Comparison/preflight carry no raw values, metadata or paths. `migrate` prints the manifest and backup paths. `conditions` prints the manifest and backup paths, the run ID and the names of fields set/cleared, never their values; dry-run and no-op print the run ID |
| 15a | Pre-write refusals | Shape/argument/migration/conditions/init refusals create nothing and change nothing |
| 15b | Post-write failures | Failures after a backup/replace use the documented per-step message; a completed backup may remain |
| 16 | Platform | Qualified: Linux, Python 3.12.3 and 3.10.20, Pydantic 2.14.3. Windows is not claimed and not evidenced |
| 17 | Preflight | §4 semantics: both-or-neither, three states, attachment, caps/truncation, exits, privacy |
| 18 | Preflight/verdict agreement | Readiness is positive: all five dimensions usable (core state `KNOWN`), attachment mappable, pair input checks (mapping, bounds, `SAME_RUN_ID`, core `NUMBER_LIMIT`, combined size) satisfied. Ready => comparison exits 0 or 1, except the documented case/whitespace exception named in the footer |
| 19 | Compatibility outside comparison | The `HEAD` 0.1 schema (without the uncommitted directory fields) accepts file-only 0.1 writer output, because those keys are dropped; a 0.1 manifest with directory records does not pass it. 0.1 fingerprints unchanged; fingerprint v1 refuses 0.2 |
| 20 | Dependencies | Supported range Python >=3.10, Pydantic >=2.6,<3; Python 3.12.3 and 3.10.20 with Pydantic 2.14.3 are evidenced; other combinations are not |

## 9. Evidence and traceability

- A traceability table maps every matrix clause to named tests and observed results. One
  test per broad row is insufficient.
- Record the reviewed source snapshot (file hashes), dependency versions, skip categories,
  packaging results, and an installed-wheel check run **outside** the checkout so local
  imports cannot hide packaging errors.
- Evidence limits on this host: row 16 Windows half, CRLF byte behavior, Pydantic <2.11,
  Python 3.11, directory records on non-ext4 filesystems, and the writer path without
  POSIX directory flock are not evidenced here and are recorded as such.
- The 0.2 schema file and example folders are currently untracked/uncommitted; the closure
  note records their hashes and the test log. Whether a commit is required before calling
  the format stable is an owner decision.

## 10. Dogfood closure

Re-run `examples/comparability-recorded`, `examples/comparability-recorded-0.2`,
`examples/dogfood-mlp-sweep`, `examples/hydra-sweep` and `examples/mlflow-file-store`
(migrate, conditions, preflight, compare) into a new dated note.

- Gap 1 (no discovery/preflight): **closed** by §4.
- Gap 3 (conventions not validatable): **closed** by 0.2 first-class fields.
- Gap 4 (dataset attachment can be silently absent): **mitigated** by preflight reporting
  `not-mappable`; `validate` still accepts a missing link.
- Gap 6 (privacy-minimal error vocabulary): **accepted limitation**, by design.
- Gap 7 (validate clean does not imply readiness): **closed** by preflight.
- Gap 2 (same-run comparison) and gap 5 (multiple datasets as one evaluation context):
  **deferred** with a one-line rationale; one explicitly selected evaluation dataset
  already works.
- `hydra-sweep` and `mlflow-file-store` are expected to remain blocked (no results or no
  manifest); record observed outcomes and adapter limits, and distinguish manually prepared
  fixtures from adapter output.

## 11. Gates

1. This RFC accepted by the review panel.
2. Traceability table complete; every clause has named evidence, including CLI-level rows
   12 and 13 and the preflight/verdict agreement row.
3. Preflight golden output, caps/truncation, both-or-neither, unknown IDs, exit matrix
   (including bridge-error => exit 1 and load/unknown-ID/invalid-arguments => exit 2),
   `SAME_RUN_ID`, core `NUMBER_LIMIT` and combined-size rejection, a duplicate-ID side shown
   as unavailable with exit 0, a >1024-character ID placeholder, agreement fixtures
   (including the case/whitespace exception), parser pin refresh, and privacy canaries in
   stdout and stderr including argument failures.
4. `init --force` refusal tests (raw 0.2 declaration valid and invalid, with and without
   fields, explicit/omitted 0.1, unparseable manifest keeping backup-then-regenerate,
   backups, symlinks, lock held) and rewritten existing tests.
5. Schema fixes, both hash pins, line-ending policy, runtime accept/reject set, and the
   README split sentence test.
6. Executable authoring example test.
7. Dogfood note with outcomes and dispositions.
8. Full suite on the qualified environment, categorized skips, packaging, installed-wheel
   smoke outside the checkout.
9. Both reviewers accept the evidence package and explicitly close the stabilization gate.
10. Owner sign-off on the README de-experimentalization and on the commit/stability
    question.

## 12. Risks and open questions

- Scope creep: preflight must not grow verbose errors, same-run comparison or multiple
  datasets.
- The stale schema description must be fixed before pinning; otherwise the freeze locks in a
  false statement.
- The unloadable-manifest path is the only 0.1 behavior risk; keep it and test it.
- Open question 1: is `.gitattributes` `schema/** -text` acceptable, or should both LF and
  CRLF hashes be pinned?
- Open question 2: does the owner require a commit before the format is called stable?
