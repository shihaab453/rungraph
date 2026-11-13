# Tool adapters: CSV and TensorBoard decision memo (v0.3 draft)

Status: **DESIGN INPUT ONLY. NOTHING IMPLEMENTED. NOTHING AUTHORIZED BY THIS DOCUMENT.**
This memo gives the independent panel a concrete proposal for the roadmap item "more tool
adapters (TensorBoard logs, plain CSV logs)" after directory artifacts (delivered) and YAML
(deferred). Accepting a direction here does not authorize implementation; a design RFC and
its own gate are required first.

## 1. What exists today

- `Adapter` is a two-step read-only interface: `discover(root)` then `extract(root,
  discovery)`. The `generic` adapter is built in and extracts **no results**; it records
  files with digest and size. Hydra and MLflow are separate packages.
- A `Result` has `value`, an optional `artifact_id` and an optional string `locator`,
  documented as an RFC 6901 JSON Pointer into a `.json` artifact.
- A locator on a non-JSON artifact produces a `result-unverifiable` **warning** and
  `validate` exits `0` by default (dogfood scenario S7); `--strict` turns that warning into
  exit `1`. The type rule is a case-insensitive file-extension check
  (`path.suffix.lower() == ".json"`). A locator on a directory artifact is an error only
  when the public experimental `--directory-artifacts` flag is on.
- A result without a locator is skipped **silently** by `check_results`: a clean validation
  result does not establish that every recorded value was checked.
- The MLflow adapter already records metric summaries with no locator; `edit.py` has
  special handling (RE-10) because the recorded summary can stay stale. Unverified results
  are therefore current behavior, not a new proposal.
- The adapter guide overpromises: it says adapters record "results with locators" for
  "plain JSON/CSV" and lists CSV as a parse target. An adapter can parse CSV today; what
  does not exist is **core verification of CSV-cell locators** (or any non-JSON locator).

## 2. Options

### A. CSV adapter, files only

Discover CSV files and record each as a file artifact.

- Pros: dependency-free, no schema/locator change, no new content parser.
- Cons: `generic` already records every file with digest and size. This adds only
  naming/run conventions, not evidence value. The roadmap item is either "covered by
  generic" or must mean verified cells.

### B. CSV adapter with verified single-cell locators

Record results only for values that sit in one cell (for example "val_rmse at data row 40")
and add a CSV locator that re-reads and compares that cell.

- Pros: real, dependency-free verification of tabular cells.
- Cons: needs a schema/locator extension (admitted only in the schema version that ships
  the verifier), strict RFC 4180 profiles, number grammar, privacy and caps. It cannot verify "mean" or "maximum" of a column: those are
  computations, not cells. It also needs a rule for what "one run" means in a CSV.

### C. TensorBoard scalar summaries

Read `tfevents` files and map scalar summaries to results with an occurrence-aware locator.

- Pros: common in ML training logs.
- Cons: the largest surface: protobuf/event framing/CRC, duplicate `(tag, step)` pairs from
  restarts and multiple files, TF2 `tensor` summaries versus legacy `simple_value`, and a
  dependency/security gate. Also see §4: the checker code lives in core.

### D. JSONL locator (line number plus JSON Pointer within that line)

Verify `history.jsonl` values by line and pointer. Reuses the existing JSON number and
pointer rules.

- Pros: the smallest useful step; covers the recorded dogfood request (G11, below) while
  reusing JSON syntax and pointer resolution under an explicit parsing profile (artifact
  JSON parsing and manifest parsing already differ); no new number grammar and no RFC 4180
  dialect work.
- Cons: still needs schema 0.3 and a new locator kind; line numbering and trailing-newline
  semantics must be pinned.

### E. Defer both adapters; record the reason

- Pros: no new dependency, locator contract or adapter package; keeps the evidence path
  small.
- Cons: the roadmap item stays open; CSV/TensorBoard files are still recorded by
  `generic`, without results.

## 3. Recommendation

**Defer the CSV and TensorBoard adapters this cycle; start a design-only locator RFC now.**
The locator contract is the first gate, not parsing. The RFC should define a general
structured locator and rank **JSONL first**, because it is the smallest useful step and it
answers the only recorded user request: dogfood gap **G11**
(`docs/dogfood-mlp-sweep-20261025.md`) asks for JSONL and CSV locators for per-epoch
training histories. That satisfies the "recorded workflow" condition; CSV cells and
TensorBoard come after the scheme exists.

**Do not ship A.** It duplicates `generic`. G11 supports doing locator work; it does not
establish that JSONL is preferred over CSV.

**Why a structured object, not a `csv:`/`jsonl:` prefix string.** `locator` is an
unconstrained string in the 0.1 and 0.2 schemas, so a prefixed string is valid in both
today. An older tool would accept it, treat it as non-JSON, warn
`result-unverifiable`, and still exit `0`: a failure that reports success. An
**object-valued locator in a new opt-in schema 0.3** is rejected outright by existing
readers, which accept only `"0.1"` and `"0.2"`. Inside 0.3, a string still means a JSON
Pointer, so existing manifests keep working.

**Locator shape.** A discriminated object, for example
`{kind: "jsonl", line: 40, pointer: "/val_rmse"}` or
`{kind: "csv-cell", row: 40, column: 3, header: "val_rmse"}`. The bytes always come from
`Result.artifact_id`; the locator carries **no file path**. A kind that does not match the
artifact type is an error, not a warning.

**Compatibility surface.** 0.1/0.2 are unchanged; an omitted version stays 0.1; 0.3 is
explicit. New readers read 0.1/0.2 without rewriting them; existing readers reject 0.3.
Unknown kinds, a missing verifier, or a kind that cannot represent the value are an
explicit **error**, never a pass. Migration is explicit, and downgrade refuses locators it
cannot represent. Several components assume a string locator and must be in scope:
`run_fingerprint.py` hashes the locator (needs a canonical object form, and fingerprint v1
accepts only schema 0.1, so a canonical form alone does not enable 0.3), `edit.py`
re-reads values through JSON only, `run_details.py` prints the locator as a string, and the
`directory_authoring` message hard-codes "JSON locator".

**TensorBoard specifics.** It belongs in a separate optional package, loaded through the
existing adapter entry point. The harder problem is that **locator checking lives in core**
(`checks/results.py`): either core reads event files without a dependency (hand-written
framing, CRC, protobuf decoding) or core calls plugin code, which makes `validate` results
depend on which plugins a machine has installed. That decision must precede the dependency
decision. The dependency gate: pinned versions with an upper bound, a vulnerability audit
in CI, prefer `protobuf` with checked-in generated code over the full `tensorboard`
package, no import when core is imported, size caps before parsing, every record CRC
verified, truncated trailing records refused, and only scalar summaries decoded.

**Unverified results.** Acceptable only as a visible limitation, following the MLflow
precedent: a machine-readable marking (today "unverified" is only "no locator"), no report
or gate may count an unverified result as verified, and a claim backed only by unverified
results is flagged. Making `validate` stop being silent for existing 0.1/0.2 manifests is a
**separate decision**, not part of this direction: it would change current exit behavior.
Any new adapter must use a checkable locator wherever a kind exists and record unverified
only where none does.

## 4. Risks and required decisions

- **Summary semantics.** A cell locator verifies one cell. A minimum, mean or latest value
  needs a defined selection/computation rule that verification also checks.
- **Number parsing.** `float()`/`Decimal()` accept `"1_000"`, surrounding whitespace,
  `"nan"`, `"inf"` and `"+1"`; some CSVs use decimal commas with `;`. Define a number
  grammar checked before parsing and reconcile it with `values_match`
  (`rel_tol=1e-9`, `abs_tol=1e-12`) and with `json.loads` accepting `NaN`.
- **Encodings.** UTF-8 only, one BOM rule, CRLF handling, NUL refused, UTF-16 refused.
- **Determinism.** Never guess a delimiter (`csv.Sniffer`); refuse duplicate/empty
  headers; pin row indexing (one-based data rows, header excluded); define file ordering,
  ID collisions and duplicate tie-breaking.
- **Resource limits.** The `csv` module's default field limit is 131072. Cap rows, cells
  and line length before parsing, stream the file, cache per artifact like the JSON check,
  and set an output-result cap.
- **Read-after-hash race.** Logs can change between hashing and reading. Hash and parse the
  same in-memory bytes, or refuse a detected change.
- **Privacy.** Current JSON mismatch findings print both recorded and actual values. The
  RFC must decide whether a no-values rule applies to new locator kinds only or changes
  JSON too. Headers and tags become result names, so they can leak as well.
- **Output injection.** Cells beginning with `=` (spreadsheet formula injection) or
  containing terminal escapes must not reach report/CSV/terminal output unsafely.
- **Run definition.** For CSV, whether one file is one run is undefined.

## 5. What this memo does not decide

- No dependency, locator kind, adapter package, route or schema change is authorized.
- No claim that CSV/TensorBoard values are verifiable until the locator checks exist.
- No release/version implication.

## 6. Questions for the panel

1. Is "defer adapters, start the locator RFC, JSONL first" right?
2. Is schema 0.3 with an object-valued locator the right vehicle, and what schema version
   gate applies?
3. Where should non-JSON locator checking live: core or the adapter package?
4. Is the unverified-result policy above sufficient, or must new adapters refuse to record
   unverified values?
5. Anything missing from §4?
