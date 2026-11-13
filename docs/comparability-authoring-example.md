# Authoring a comparable schema 0.2 pair (executable example)

Schema-valid is not the same as comparison-ready. This example starts from a
minimal schema 0.1 manifest, migrates a baseline and a scratch target copy to
0.2, hand-edits the two first-class fields that no command authors
(`artifact.dataset_id` and `result.metric_definition`), validates both files,
runs the read-only preflight, and compares the pair.

The script is self-contained and runs entirely in a scratch folder. It uses
`PYTHON` (default `python3`) to invoke `python -m runevidence`; set `WORK` to
choose the scratch directory. Nothing in this repository is modified. The
example is extracted and executed by
`tests/test_comparability_authoring_example.py`, so it cannot silently rot.

```bash
#!/usr/bin/env bash
set -euo pipefail
PY="${PYTHON:-python3}"
WORK="${WORK:-$(mktemp -d)}"
mkdir -p "$WORK"
cd "$WORK"

"$PY" - <<'PY'
import hashlib
import json
from pathlib import Path

data = b"x,y\n1,2\n"
Path("data.csv").write_bytes(data)
doc = {
    "schema_version": "0.1",
    "project": {"id": "authoring-demo", "name": "Authoring demo"},
    "runs": [
        {
            "id": "baseline-run",
            "name": "baseline run",
            "metadata": {
                "evaluation": {
                    "split": "test",
                    "protocol": {"protocol_id": "holdout-v1", "version": 1},
                },
                "source_revision": "rev-1",
                "environment": {"python": "3.12"},
            },
        }
    ],
    "artifacts": [
        {
            "id": "data",
            "path": "data.csv",
            "kind": "dataset",
            "sha256": hashlib.sha256(data).hexdigest(),
            "size_bytes": len(data),
        }
    ],
    "results": [
        {
            "id": "baseline-result",
            "run_id": "baseline-run",
            "name": "accuracy",
            "value": 0.9,
            "artifact_id": "data",
        }
    ],
    "relationships": [
        {"source": "data", "target": "baseline-run", "type": "input_to"}
    ],
    "evidence": [],
}
Path("baseline.json").write_text(json.dumps(doc, indent=2) + "\n")
doc["runs"][0]["id"] = "target-run"
doc["runs"][0]["name"] = "target run"
doc["results"][0]["id"] = "target-result"
doc["results"][0]["run_id"] = "target-run"
doc["relationships"][0]["target"] = "target-run"
Path("target.json").write_text(json.dumps(doc, indent=2) + "\n")
PY

"$PY" -m runevidence migrate baseline.json --to 0.2
"$PY" -m runevidence migrate target.json --to 0.2

"$PY" - <<'PY'
import json
from pathlib import Path

def edit(name):
    doc = json.loads(Path(name).read_text(encoding="utf-8"))
    doc["artifacts"][0]["dataset_id"] = "toy-dataset-v1"
    doc["results"][0]["metric_definition"] = {
        "definition_id": "accuracy-top1",
        "higher_is_better": True,
    }
    Path(name).write_text(json.dumps(doc, indent=2) + "\n")

edit("baseline.json")
edit("target.json")
PY

"$PY" -m runevidence validate baseline.json
"$PY" -m runevidence validate target.json
"$PY" -m runevidence comparability baseline.json target.json --preflight
"$PY" -m runevidence comparability baseline.json target.json --preflight \
  --baseline-result baseline-result --target-result target-result
"$PY" -m runevidence comparability baseline.json target.json \
  --baseline-result baseline-result --target-result target-result
```

The final preflight exits `0` only because every mandatory dimension is
present, the dataset attachment is mappable, the bridge bounds pass and the
core accepts the pair. The comparison then reports
`COMPARABLE_RECORDED_CONDITIONS`. Readiness alone does not prove matching
values, artifact integrity or a successful comparison; the footer says so.
