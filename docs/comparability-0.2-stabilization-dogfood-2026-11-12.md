# Schema 0.2 stabilization: dogfood re-run and traceability (2026-11-12)

Status: **evidence note for S6 slices 3+4; not a stability closure.**
Acceptance and closure are the independent reviewers' decision. No Git
operation, version bump or publication was performed.

Environment: Linux, CPython 3.12.3 / 3.10.20, Pydantic 2.14.3. Commands below
used the checkout's `runevidence` entry point. Every example was copied to a
scratch folder before migrate/conditions; no example file was written. The
installed-wheel smoke used a separate venv outside the checkout (see
[Evidence](#evidence)).

## 1. Dogfood commands and outcomes

Each example was copied with `cp -r` to a scratch `$WORK` directory. The
conditions patch used above contained exactly
`{"evaluation_split": "test"}`; the commands below ran from the copied example
folder with the checkout's `runevidence` entry point.

| Example | Command | Exit | Observed result |
| --- | --- | ---: | --- |
| `examples/comparability-recorded` (0.1) | `migrate baseline.json --to 0.2` | 0 | Wrote 0.2; fresh backup |
| | `migrate target-comparable.json --to 0.2` | 0 | Wrote 0.2; next backup generation |
| | `conditions baseline.json --run baseline-run --patch patch.json` | 0 | Set `evaluation_split`; backup-backed |
| | `comparability baseline.json target-comparable.json --preflight` | 0 | Listed both candidate sides |
| | `comparability baseline.json target-comparable.json --preflight --baseline-result baseline-result --target-result target-comparable-result` | 0 | **ready**; five present; attachment mappable |
| | `comparability baseline.json target-comparable.json --baseline-result baseline-result --target-result target-comparable-result --json` | 0 | `COMPARABLE_RECORDED_CONDITIONS` |
| `examples/comparability-recorded-0.2` | `comparability baseline.json target-comparable.json --preflight` | 0 | Listed both sides |
| | `comparability baseline.json target-comparable.json --preflight --baseline-result baseline-run-result --target-result target-comparable-run-result` | 0 | **ready** |
| | `comparability baseline.json target-comparable.json --baseline-result baseline-run-result --target-result target-comparable-run-result --json` | 0 | `COMPARABLE_RECORDED_CONDITIONS` |
| | `comparability baseline.json target-drifted.json --baseline-result baseline-run-result --target-result target-drifted-run-result --json` | 1 | `NOT_COMPARABLE_RECORDED_CONDITIONS` (`evaluation_split`) |
| | `comparability baseline.json target-insufficient.json --baseline-result baseline-run-result --target-result target-insufficient-run-result --json` | 2 | `INSUFFICIENT_EVIDENCE` (`evaluation_split`) |
| `examples/dogfood-mlp-sweep` | `migrate runevidence.json --to 0.2` | 0 | Wrote 0.2; fresh backup |
| | `conditions runevidence.json --run job0 --patch patch.json` | 0 | Set `evaluation_split`; backup-backed |
| | `validate runevidence.json` | 0 | `OK: 0 errors, 0 warnings` |
| | `comparability runevidence.json runevidence.json --preflight` | 0 | Listed six result candidates per side |
| | `comparability runevidence.json runevidence.json --preflight --baseline-result job0-val-rmse --target-result job1-val-rmse` | 1 | not ready: `baseline: INPUT_SHAPE_UNSUPPORTED` |
| | `comparability runevidence.json runevidence.json --baseline-result job0-val-rmse --target-result job1-val-rmse --json` | 2 | `MAPPING_FAILED` / `INPUT_SHAPE_UNSUPPORTED` (baseline) |
| `examples/hydra-sweep` | `migrate runevidence.json --to 0.2` | 0 | Wrote 0.2; fresh backup |
| | `validate runevidence.json` | 0 | Clean 0.1-shaped manifest after migration |
| | `comparability runevidence.json runevidence.json --preflight` | 0 | Both sides `(none)` (no results) |
| | `comparability runevidence.json runevidence.json --preflight --baseline-result missing --target-result missing` | 2 | `RESULT_NOT_FOUND` (baseline) |
| | `comparability runevidence.json runevidence.json --baseline-result missing --target-result missing --json` | 2 | `MAPPING_FAILED` / `RESULT_NOT_FOUND` (baseline) |
| `examples/mlflow-file-store` | `migrate runevidence.json --to 0.2` | 2 | `MANIFEST_LOAD_FAILED` (no manifest) |
| | `comparability runevidence.json runevidence.json --preflight` | 2 | `MANIFEST_LOAD_FAILED` (baseline) |
| | `comparability runevidence.json runevidence.json --baseline-result missing --target-result missing --json` | 2 | `MANIFEST_LOAD_FAILED` (baseline) |

`examples/dogfood-mlp-sweep` and `examples/hydra-sweep` are `validate`-clean
but not comparison-ready. That is the intended distinction: validate is not a
readiness certificate. The MLP example has multiple attached datasets without
an explicit selector, so the bridge refuses the shape; the preflight reports
the refusal rather than inventing a comparison. Hydra has no results. MLflow
has no manifest. These are recorded as blocked, not passes.

## 2. Gap dispositions

| Gap | Disposition | Evidence |
| --- | --- | --- |
| 1. No discovery/preflight | **Closed** | List and pair preflight above; `tests/test_cli_comparability_preflight.py` |
| 2. Same-run comparison | **Deferred** — out of S6 scope; `SAME_RUN_ID` is deliberately not-ready and the comparison rejects it | Preflight `test_same_run_id_is_not_ready`; core tests |
| 3. Conventions not validatable | **Closed** — first-class 0.2 fields and the schema-0.2 example validate cleanly | `tests/test_manifest_0_2.py`, `tests/test_schema_0_2.py`, `tests/test_writer_schema_compat.py` |
| 4. Dataset attachment silently absent | **Mitigated** — preflight reports `not-mappable`; validate still accepts a missing link (not silently fixed) | `test_attachment_not_mappable_is_reported_separately`; MLP bridge refusal above |
| 5. Multiple datasets as one evaluation context | **Deferred** — one explicitly selected evaluation dataset works; combining several remains unsupported | Preflight/guide; MLP shape refusal |
| 6. Privacy-minimal error vocabulary | **Accepted limitation** — fixed codes and text, no raw values/paths/IDs in errors | `tests/test_cli_comparability.py`, preflight privacy test |
| 7. Validate clean does not imply readiness | **Closed** — preflight distinguishes the two explicitly | MLP/hydra: validate `0`, preflight not-ready/blocked |

## 3. Traceability (behavior matrix v0.2)

| # | Clause | Named evidence | Result |
| ---: | --- | --- | --- |
| 1 | Version | `test_manifest_0_2.py::test_omitted_schema_version_is_0_1`, `::test_unknown_or_nonstring_schema_version_is_rejected`; `test_schema_0_2.py::test_0_2_schema_has_its_own_identity_and_version` | pass |
| 2 | 0.1 purity | `test_manifest_0_2.py::test_0_1_rejects_every_0_2_only_key`, `::test_omitted_schema_version_with_0_2_key_is_rejected`, `::test_0_1_accepts_its_own_shape_without_0_2_keys`; `test_schema_0_2.py::test_0_1_schema_rejects_every_0_2_only_key` | pass |
| 3 | Presence/null | `test_manifest_0_2.py::test_conditions_omitted_empty_and_present_are_distinct`, `::test_conditions_explicit_null_is_rejected`; `test_stage3b2a_first_class.py::test_explicit_null_condition_is_unknown_without_fallback`, `::test_dump_reload_preserves_omitted_null_and_empty_presence`; preflight `test_explicit_null_is_recorded_unknown_and_not_ready`, `test_missing_dimension_is_not_ready` | pass |
| 4 | Serialization | `test_stage3b2a_first_class.py::test_dump_reload_preserves_omitted_null_and_empty_presence`; `test_writer_schema_compat.py::test_committed_0_2_schema_accepts_actual_0_2_writer_output`, `::test_frozen_0_1_reader_rejects_actual_0_2_writer_output` | pass |
| 5 | Resolution | `test_stage3b2a_first_class.py::test_equal_first_class_and_legacy_collapse_to_one_observation`, `::test_unequal_first_class_and_legacy_fail`, `::test_protocol_and_environment_are_whole_values_never_merged`, `::test_conditions_absent_falls_back_to_legacy_metadata` | pass |
| 6 | Selectors | `test_conditions_check.py::test_missing_artifact_is_reported_with_fixed_text`, `::test_wrong_kind_artifact_is_reported`, `::test_artifact_not_consumed_by_the_run_is_reported`; `test_stage3b2a_first_class.py::test_unattached_explicit_selector_fails` | pass |
| 7 | Bounds | `test_stage3b2a_first_class.py::test_baseline_bound_beats_target_selector_failure`, `::test_baseline_bound_beats_target_shape`, `::test_conflict_candidate_bound_wins_over_conflict`, `::test_aggregate_record_bound_precedes_source_revision_conflict`; preflight `::test_core_number_limit_is_not_ready`, `::test_combined_size_bound_is_exit_one_reason_target` | pass |
| 8 | Outcomes | `test_cli_comparability.py::test_comparable_json_envelope_exit_zero`, `::test_not_comparable_json_envelope_exit_one`, `::test_insufficient_json_envelope_exit_two`, `::test_core_rejected_json_envelope_exit_two` | pass |
| 9 | Precedence | `test_cli_comparability.py::test_unknown_result_selector_is_fixed_mapping_failure`, `::test_mapping_failure_text_shape`, `::test_load_failures_are_fixed_and_leak_free`, `::test_target_load_failure_text_shape` | pass |
| 10 | Migration | `test_cli_migrate.py::test_real_0_1_json_success_is_closed_and_path_free`, `::test_already_0_2_is_a_success_no_op`, `::test_0_1_dry_run_json_and_text_write_nothing`, `::test_second_invocation_is_idempotent`, `::test_unsupported_target_refused_without_touching_anything` | pass |
| 11 | Conditions | `test_cli_conditions.py::test_real_changed_json_is_closed_path_free_and_value_free`, `::test_dry_run_changed_writes_nothing_and_reports_none`, `::test_patch_only_creates_conditions_and_writes_the_value`, `::test_clear_only_removes_the_child_and_keeps_others`; `test_conditions_authoring.py::test_duplicate_keys_are_rejected_at_every_depth`, `::test_patch_file_size_bound` | pass |
| 12 | Absorption (CLI) | `test_init_backups.py::test_forced_init_over_0_2_refuses_and_writes_nothing`, `::test_forced_init_over_empty_0_2_refuses_the_downgrade`, `::test_forced_init_refuses_an_invalid_0_2_declaration`; `test_edit.py::test_add_on_a_0_2_manifest_preserves_first_class_fields`, `::test_rehash_on_a_0_2_manifest_preserves_first_class_fields` | pass |
| 13 | Directory records (CLI) | `test_cli_directory_authoring.py::test_directory_authoring_works_on_a_schema_0_2_manifest`; `test_cli_migrate.py::test_migrate_preserves_directory_artifacts`; `test_cli_conditions.py::test_conditions_preserves_directory_artifacts`; `test_cli_comparability_preflight.py::test_comparison_preserves_directory_records` | pass |
| 14 | Privacy (per command) | `test_cli_comparability.py::test_success_output_has_no_canaries`; preflight `::test_privacy_canaries_never_reach_output`; `test_cli_migrate.py::test_real_0_1_text_success_shows_only_resolved_paths`; `test_cli_conditions.py::test_real_changed_text_shows_only_resolved_paths` | pass |
| 15a | Pre-write refusals | `test_cli_migrate.py::test_0_1_dry_run_json_and_text_write_nothing`; `test_cli_conditions.py::test_dry_run_changed_writes_nothing_and_reports_none`; `test_init_backups.py::test_forced_init_over_0_2_refuses_and_writes_nothing`; preflight `::test_invalid_arguments_do_not_write`, `::test_preflight_writes_nothing` | pass |
| 15b | Post-write failures | `test_cli_migrate.py::test_late_stale_observation_is_sanitized_and_keeps_backup`, `::test_write_failure_is_sanitized_and_keeps_backup`; `test_cli_conditions.py::test_save_raw_oserror_with_unchanged_bytes_is_write_failed`; `test_directory_publication.py::test_unexpected_at_step_11_final_guard_is_phase_two`, `::test_unexpected_just_before_replace_is_phase_three` | pass |
| 16 | Platform | Qualified run: Linux/Python 3.12.3 and 3.10.20/Pydantic 2.14.3; 138 categorized skips (R5 119, Windows-only 9 + 6, case-sensitive 1, second-user 1, SELinux 1, mount privilege 1) | qualified; Windows not evidenced |
| 17 | Preflight | `test_cli_comparability_preflight.py::test_list_golden_using_the_ready_example`, `::test_ready_pair_golden_using_the_ready_example`, `::test_not_ready_pair_golden_using_the_insufficient_example`, `::test_ambiguous_values_are_present_and_reason_uses_usability`, `::test_conflicting_state_is_present_but_not_ready` (synthetic projection; the bridge cannot emit CONFLICTING), `::test_combined_size_bound_is_exit_one_reason_target`, `::test_duplicate_side_list_is_unavailable_and_exit_zero`, `::test_cap_truncation_and_long_id_placeholder`, `::test_list_reports_recorded_unknown_and_missing_states` | pass |
| 18 | Preflight/verdict agreement | `test_cli_comparability_preflight.py::test_ready_pairs_compare_zero_or_one`, `::test_case_whitespace_ready_but_comparison_insufficient` | pass; case/whitespace exception is ready preflight + `INSUFFICIENT_EVIDENCE` compare |
| 19 | Compatibility outside comparison | `test_writer_schema_compat.py::test_frozen_0_1_reader_accepts_actual_0_1_writer_output`, `::test_frozen_0_1_reader_rejects_actual_0_2_writer_output`; `test_cli_fingerprint_v1_fail_closed.py::test_live_rich_golden_and_default_projection_unchanged` | pass |
| 20 | Dependencies | `pyproject.toml` (`>=3.10`, Pydantic `>=2.6,<3`); qualified full suite below | supported; only 3.10/3.12 + Pydantic 2.14.3 evidenced here |

## 4. Evidence

Source snapshot hashes (SHA-256) for the reviewed code and evidence:

| File | SHA-256 |
| --- | --- |
| `src/runevidence/models.py` | `3ff18f192fab28d8bf593b0f8e31b0772f2c3db372513e42aec55da7a9c5d6c3` |
| `schema/runevidence.schema.json` | `cc0727d4693216d2580ae8b2343661ad9ab64f15129ffa492a270c8697644ab2` |
| `schema/runevidence-0.2.schema.json` | `67658ebfd9b55ee1a9d2b659facdd5e7dc22b9ee507e2892a67b6733f9280d46` |
| `src/runevidence/cli.py` | `1079003d83d95b30e1683f61f6691e0cc7c7e504b6289f872ed2002ecb059eaf` |
| `src/runevidence/run_comparability.py` | `ad6cd5e30c1a614137811bf299539b3d84224655880dcf3edc54b3fcb7c74a5f` |
| `src/runevidence/_experimental_comparability_bridge.py` | `76e7d335ed93e9827ad31a453198d74b0674f7902fedb83c5e862ed95d12c80e` |
| `tests/test_cli_comparability_preflight.py` | `5bdb59aca5fc6a5c055480734a8b2f4fe85e07cc2ab6a13292784f7f1370bb91` |
| `tests/test_edit.py` | `320c01947f6e6dcac43c19090e87eb49505d1882194ab63420d59b74627778ed` |
| `tests/test_cli_migrate.py` | `d041c62d8792fcf879b4401ec7f2a1f641527b91483a62107a6e530f9446e31b` |
| `tests/test_cli_conditions.py` | `1c26ec49fe50835bf5b75834ea89822136525b195721b87da0bf4c118ed270f6` |
| `tests/test_comparability_authoring_example.py` | `699c4178e026265e68ba00df6eeb35c94d0ef2ea2c3736e56b76e8ddf933682f` |
| `tests/test_comparability_stabilization_docs.py` | `0a32f4305300b22ee4234b984d17174526fe4c63ee8856d4b160ea4930bc263b` |
| `tests/test_experimental_cli_parser_contract.py` | `89d248347d55e15469fe8d84de558608458b05b262ab008bf829f7c2b798d387` |
| `README.md` | `69aa91b67285fd15402ce7ac766cf1519016c2955677d5f460a8409b24f90803` |
| `docs/comparability-schema-0.2.md` | `0e18c5d47714da062a63f41b90f4f53b938dfd1580337c1f3eba1c5d825610ed` |

- Accepted pre-correction suite: 4102 passed, 138 skipped, 7 subtests (the
  reviewer-corrected count, not 4104).
- Final suite after the correction and traceability regressions: 4111 passed,
  138 skipped, 7 subtests on Python 3.12 and on Python 3.10 (`PYTHONPATH=src`).
- New preflight file: 28 tests (including the ambiguity, conflicting and
  combined-size regressions). New executable-authoring-example test: 1 test.
  README/guide stability-split tests: 2 tests.
- Skip accounting: 138 total (on a sandbox with a long `TMPDIR`, the native unix-socket test
  can additionally skip with `AF_UNIX path too long`, giving 4110 passed / 139 skipped; the
  qualified run above used a short temp path). R5 evidence root not configured = 119;
  `winerror` Windows-only = 9; other Windows-only/DACL/case behavior = 6;
  case-sensitive filesystem = 1; missing second-user access = 1; missing
  SELinux label = 1; no mount privilege for real non-ext4 evidence = 1. Skips
  are missing evidence, not passes.
- Packaging: wheel `fedfd80d9b538c7fcdef2a5debb0811c3c9d4bc994d1ce72f8f644fd34c48ddb`,
  sdist `6f36ed5118aca18a91fe5df638e5f1bd66107b033c4ad8762c401747257747a0`
  (built into a scratch directory).
- Installed-wheel smoke outside the checkout: `runevidence 0.1.1` imported
  from the venv's site-packages; the executable authoring example ran to
  `COMPARABLE_RECORDED_CONDITIONS`; the 0.2 example preflight listed and
  returned ready, and the pair comparison exited `0`.

## 5. Limitations

- Readiness is not matching, artifact integrity or a successful comparison.
  Omitted candidates and pairwise compatibility are not assessed.
- A ready pair can still compare as `INSUFFICIENT_EVIDENCE` for a
  case/whitespace-only difference; the preflight footer names it.
- The comparison verdict CLI remains experimental; schema 0.2, `migrate` and
  `conditions` are the stable surfaces.
- No same-run comparison, multi-dataset-as-one-context, Windows/macOS parity,
  tree freeze, CAS or release qualification is claimed.
- Hydra has no results and MLflow has no manifest; both remain blocked rather
  than silently passed.
