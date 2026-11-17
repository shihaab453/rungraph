# Authoring JSON files

The authoring commands accept one small JSON object from a local file. They validate it against
the manifest schema and the existing references before writing; a refusal writes nothing. Use
`--dry-run` to inspect the planned change first.

These files are inputs to `runevidence result`, `runevidence evidence`, `runevidence
relationship`, `rungraph claim` and `rungraph relationship`. A file contains **one object**, not
an array and not a patch envelope.

## Result (`runevidence result add|edit --file RESULT.json`)

Required: `id`, `run_id`, `name`. Optional: `value` (default `null`), `artifact_id`, `locator`,
`metadata`, and schema-0.2+ fields such as `metric_definition`.

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
`{"kind": "jsonl", "line": 1, "pointer": "/accuracy"}` or
`{"kind": "csv-cell", "row": 2, "column": 1, "header": "accuracy"}`. A locator needs
`artifact_id`; a targeted artifact without a hash is an error in `validate`, not silently
accepted. `edit` replaces the object with that ID; `remove --id ID` refuses while another
result, claim or relationship cites it.

## Evidence / claim (`runevidence evidence add|edit --file CLAIM.json`)

Required: `id`, `claim`. Optional: `supports` (default `[]`), `contradicts` (default `[]`),
`source`, `metadata`. IDs name results, artifacts, runs or other evidence in the same manifest.

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

`type` is one of `derived_from`, `reported_in`, `result_of`, `produced_by` or `input_to`.
`supports`/`contradicts` are refused because claims are the only claim-citation mechanism.

## Workspace claim (`rungraph claim add|edit --file CLAIM.json`)

Required: `id`, `claim`. Optional: `supports`, `contradicts`, `source`, `metadata`. A reference
is either `{"member": "exp-a", "id": "acc-a"}` or `{"claim": "another-workspace-claim"}`.

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
qualified `{"member", "id"}` or `{"claim", "id"}` forms.

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
- Every command takes the manifest/workspace stale-byte check; a concurrent edit is refused
  rather than overwritten. `--json` returns a machine-readable success or error document.
