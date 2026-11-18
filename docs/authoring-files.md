# Authoring JSON files

The authoring commands accept one small JSON object from a local file. They validate it against
the manifest schema and the existing references before writing; a refusal writes nothing. Use
`--dry-run` to inspect the planned change first.

These files are inputs to `runevidence result`, `runevidence evidence`, `runevidence
relationship`, `rungraph claim` and `rungraph relationship`. A file contains **one object**, not
an array and not a patch envelope.

## Result (`runevidence result add|edit --file RESULT.json`)

For `add`, required: `id`, `run_id`, `name`. Optional: `value` (default `null`),
`artifact_id`, `locator`, `metadata`, and schema-0.2+ fields such as `metric_definition`.
For `edit`, only `id` is required: the supplied fields are merged into the existing result, so
omitted fields are preserved.

```json
{
  "id": "tuned-accuracy",
  "run_id": "run-tuned",
  "name": "test_accuracy",
  "value": 0.91,
  "artifact_id": "tuned-metrics",
  "locator": "/eval/accuracy"
}
```

`locator` is a JSON Pointer string for a `.json` artifact, or an object such as
`{"kind": "jsonl", "line": 1, "pointer": "/accuracy"}` (schema 0.3+) or
`{"kind": "csv-cell", "row": 2, "column": 1, "header": "accuracy"}` (schema 0.4+). From
schema 0.3 onward a locator needs `artifact_id`; 0.1/0.2 accept the legacy string form without
one. A targeted artifact without a recorded digest is a warning in 0.1/0.2 and a
`locator-no-digest` error from 0.3 onward. `remove --id ID` refuses while another result,
claim or relationship cites it.

## Evidence / claim (`runevidence evidence add|edit --file CLAIM.json`)

For `add`, required: `id`, `claim`. Optional: `supports` (default `[]`), `contradicts`
(default `[]`), `source`, `metadata`. For `edit`, only `id` is required and supplied fields are
merged. IDs name results, artifacts, runs or other evidence in the same manifest.

```json
{
  "id": "claim-tuning-helps",
  "claim": "Tuning improves test accuracy over the baseline.",
  "supports": ["tuned-accuracy", "baseline-accuracy"],
  "contradicts": [],
  "source": "REPORT.md#C2"
}
```

## Relationship (`runevidence relationship add --file RELATIONSHIP.json`)

Required: `source`, `target`, `type`. Optional: `note`. Relationships have no ID; remove one by
its zero-based `--index` in the recorded array.

```json
{
  "source": "run-tuned",
  "target": "run-baseline",
  "type": "derived_from",
  "note": "same setup, lower learning rate"
}
```

`type` is one of the manifest relationship types (`supports`, `contradicts`, `derived_from`,
`reported_in`, `result_of`, `produced_by`, `input_to`). Workspace relationships have the
stricter rule: they refuse `supports`/`contradicts` because workspace claims are the only
claim-citation mechanism.

## Workspace claim (`rungraph claim add|edit --file CLAIM.json`)

For `add`, required: `id`, `claim`. Optional: `supports`, `contradicts`, `source`,
`metadata`. For `edit`, only `id` is required and supplied fields are merged. A reference is
either `{"member": "exp-a", "id": "acc-a"}` or `{"claim": "another-workspace-claim"}`.

```json
{
  "id": "a-beats-b",
  "claim": "Method A beats B on the shared split.",
  "supports": [{"member": "exp-a", "id": "acc-a"}],
  "contradicts": [{"member": "exp-b", "id": "acc-b"}]
}
```

## Workspace relationship (`rungraph relationship add --file RELATIONSHIP.json`)

Required: `id`, `type`, `source`, `target`. Optional: `note`. Source and target use the same
qualified `{"member": "id", "id": "local-id"}` or `{"claim": "workspace-claim-id"}` forms; a
claim reference is one key, not `{"claim", "id"}`.

```json
{
  "id": "a-and-b-share-data",
  "type": "derived_from",
  "source": {"member": "exp-b", "id": "run-b"},
  "target": {"member": "exp-a", "id": "run-a"}
}
```

## Editing and removal

- `add` refuses an ID that already exists; `edit` requires the ID to exist.
- `remove --id ID` refuses while another object cites it. Remove or edit the citing claim or
  relationship first.
- A link must use an endpoint kind its relationship allows: claims support or contradict
  results, artifacts or runs; `result_of` links a result to a run; `reported_in` links a result
  to an artifact; `produced_by` and `input_to` link an artifact to a run; `derived_from` links
  artifact-to-artifact or run-to-run.
- Every command takes the manifest/workspace stale-byte check; a concurrent edit is refused
  rather than overwritten. `--json` returns a machine-readable success or error document.

## Artifact metadata and removal (`runevidence artifact edit|remove`)

Required: `id`. Editable fields: `kind`, `dataset_id`, `run_id`, `metadata`, `volatile`.
Supplied fields are merged; omitted fields are preserved.

```json
{
  "id": "tuned-metrics",
  "kind": "metrics",
  "dataset_id": "ds-shared"
}
```

`id`, `path`, `sha256`, `size_bytes`, `artifact_type` and `digest_format` are refused here:
identity, path and bytes belong to `add`/`rehash`, and directory format belongs to
`add --directory`. This is the CLI route for setting `dataset_id` on schema 0.2+.

`remove --id ARTIFACT_ID` untracks one artifact and leaves its file on disk. It refuses while
a result names it through `artifact_id`, a run condition selects it as the evaluation dataset
or model checkpoint, or a claim or relationship cites it. Remove or edit those references
first, then remove the artifact.


## TensorBoard scalar byte refresh (`runevidence rehash PATH ID --refresh-locators`)

On a schema 0.5 manifest, this verifies every recorded TensorBoard scalar
locator from one protected read and updates only the artifact `sha256` and
`size_bytes`. Result values must already match; record an intentional change
with `result edit` first. Only explicit exact IDs are accepted, and `--all`,
volatile, directory, mixed-locator and schema 0.6+ targets are refused.

## Run (`runevidence run PATH --add|--edit --file RUN.json`)

Required for `add`: `id`. Optional: `name`, `started_at` (ISO 8601), `params`, `metadata`,
and schema-0.2+ `conditions` (the same six children as the `conditions` command). `edit` merges
the supplied fields; `id` is immutable. `--remove --id RUN_ID` refuses while an artifact or
result names the run, a relationship uses it, or a claim cites it.

```json
{
  "id": "run-tuned",
  "name": "Tuned",
  "params": {"learning_rate": 0.01},
  "conditions": {"evaluation_split": "test", "source_revision": "9f2c1ab"}
}
```
