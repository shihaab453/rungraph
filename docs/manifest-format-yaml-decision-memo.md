# Manifest format: YAML decision memo (v0.1 draft)

Status: **DESIGN INPUT ONLY. NOTHING IMPLEMENTED, NOTHING AUTHORIZED BY THIS DOCUMENT.**
Owner standing rule: implementation may proceed when the independent panel accepts it.
This memo exists to give that panel a concrete, risk-scoped proposal for the roadmap item
"YAML manifests" instead of an open-ended format change. Accepting a direction here does
**not** authorize implementation of that direction; a separate design RFC and gate are
required first.

## 1. Why this memo

The README roadmap originally listed, in order: directory artifacts, **YAML manifests**,
more tool adapters, then lineage. Directory artifacts are delivered (S2 scanner, S4-1 core,
S4-2a admission lock, S4-2b public authoring). This memo was the decision point for the
next item. The only evidence of demand is the roadmap entry itself; no user request for
YAML authoring is recorded in this repository. The project also carries standing invariants
that YAML can weaken:

- **JSON is the production authority.** Every writer, checker, digest, locator and backup
  path assumes one unambiguous document.
- **No new core runtime dependency** has been added for these features. PyYAML is already
  a dependency of the separate Hydra adapter package; making it a core dependency would be
  a new commitment for the main package.
- **Strict parsing is a feature.** Duplicate keys, ambiguous scalars and unknown fields
  are rejected rather than silently normalized. One deliberate legacy exception remains:
  JSON `NaN`/`Infinity`/`-Infinity` tokens are accepted only at `results[*].value` for
  backward compatibility and rejected everywhere else. A YAML import may choose to reject
  them entirely rather than inherit the exception.
- **26 public routes, no format flag creep.** `migrate` and `conditions` already carry
  versioned JSON envelopes.

YAML is attractive for hand-editing claims, relationships and annotations (comments,
multiline strings, less punctuation). It is also the highest-risk format to add: implicit
typing, anchors/aliases (expansion bombs), merge keys, tags, non-string keys, duplicate
keys, encodings and multi-document streams all interact with a validator that must remain
unambiguous.

## 2. Options

### A. YAML as a parallel authority (`runevidence.yaml`)

Commands discover either `runevidence.json` or `runevidence.yaml` and save back in the
same format. Comments and layout survive an edit.

- Pros: comments and layout survive an edit; no conversion step.
- Cons: doubles every load/save/backup/resolve path; `runevidence.json` and `.yaml`
  collisions need precedence rules; backup generations must follow the chosen name;
  determinism of saved YAML; every reader (adapters, comparability, directory core,
  checks) must accept it. Highest review surface and highest chance of a format-dependent
  bug in the evidence path.

### B. YAML as an import-only surface

A separate, explicit conversion (for example `init --from-yaml FILE`, or a new
`convert` route) parses YAML once into the exact document model and writes a normal JSON
manifest. JSON remains the only authority; no reader changes.

- Pros: bounded blast radius (one parser, one route or flag); the security review is
  local; no dual-format ambiguity.
- Cons: not what "YAML manifests" usually means; round-tripping loses comments; adds a
  route or a flag and a new public contract.

### C. Strict YAML subset with no dependency (vendored parser)

Implement a small block-YAML reader/writer limited to: block mappings and sequences,
quoted and plain scalars restricted to JSON-compatible types, `null`/`true`/`false`,
JSON numbers, comments, and literal/folded strings. Reject anchors, aliases, tags, merge
keys, flow style, multi-document streams, non-string keys and duplicate keys.

- Pros: no PyYAML; the accepted subset is testable and deterministic; can be used either
  as a parallel authority or import-only.
- Cons: a hand-written parser is a security-sensitive component; Unicode, indentation and
  scalar-grammar edge cases are easy to get wrong; users will hit the unsupported subset
  and read it as a bug; maintenance burden for a format that is not the authority.

### D. Defer YAML; keep JSON authority

Document JSON as the authority, note why YAML is deferred, and spend the next milestone
on a lower-risk roadmap item (an adapter or lineage). Revisit if users ask for YAML.

- Pros: zero new risk in the evidence path; honest about the invariant.
- Cons: ignores a roadmap item; the ergonomic complaint remains.

## 3. Recommendation

**Recommend D for now, with B as the only acceptable future shape.** Do not make YAML a
parallel authority (A) and do not vendor a YAML parser (C) without a separate security
gate. If the owner wants the ergonomic win, the smallest safe increment is an explicit,
one-way import that produces a canonical JSON manifest and never writes YAML:

1. Parse only through an isolated subclass of the **pure-Python** `SafeLoader` (never the
   global loader, never `CSafeLoader`, which differs on errors and depth). The subclass
   provides the duplicate-rejecting constructor and does not modify PyYAML's global
   behavior.
2. Check scalar spelling **before** conversion and reject anything outside the JSON
   grammar. Default `safe_load` turns `010` into `8`, `0123456` into `42798`, `1:20` into
   `80`, `on` into `True`, `1.0e+999` into infinity, `1e3` into the string `'1e3'`, and an
   empty value into `None`. Inspecting the resulting Python value cannot recover what the
   author wrote, and a string where a number was intended is a silent type change the
   schema may not catch (for example a free-form param). Only a literal `null` is accepted
   as null; `~`, `Null` and empty values are rejected.
3. Enforce limits during acquisition and on the parser's **event stream** (`yaml.parse`):
   byte cap, one mapping document only, nesting depth, node count, scalar length and
   numeric length, before any document is constructed. Depth 20,000 already makes
   `safe_load` raise `RecursionError`, so post-construction checks are too late. Reject
   aliases and merge keys before composition/expansion (merge keys work without any
   anchor: `<<: {b: 1}`); reject empty and multiple documents.
4. Reject duplicate keys at every depth using decoded keys, so `id` and `"\u0069d"` are
   the same key. Reject **every** explicit tag (`!!str`, `!!float`, `!!set` and the bare
   `!`), not only unknown ones, and reject YAML directives. Reject non-string keys
   (`null: a` produces a `None` key).
5. Define block scalars by their decoded YAML value (folding and chomping included); do not
   claim source-byte preservation. Multi-line quoted/plain scalars fold silently (`"a\n b"`
   becomes `'a b'`; NEL, U+2028 and U+2029 fold too), so either reject multi-line flow
   scalars or define their meaning. State the flow-style (`{}`, `[]`) policy explicitly.
6. Specify the public contract before code: whether the YAML is a full manifest (with
   hand-written digests) or an overlay that a scan then digests; quoting for string fields
   such as `schema_version: "0.1"`; the destination folder for relative artifact paths;
   overwrite/backup behavior; and conflicts with existing command flags. A second import
   should refuse when the destination exists, with no `--force`; the import is one-shot.
7. Convert to the exact JSON document and validate it through the existing loader before
   any save; then use the existing save/backup/publication path. The imported file is an
   input, never an authority.
8. Fixed, value-free errors for scanner and constructor failures. PyYAML error messages
   include the offending source line, so the boundary must replace them, with a test that
   asserts no input bytes appear in the message.
9. Add no route: the import lives on an existing explicit command (for example
   `init --from-yaml`), so the public route set stays at 26.

If the panel prefers A or C, it should say so with the guarantee it accepts: which file is
the authority, what happens when both names exist, how backups are named, what the saved
bytes are, and which readers are in scope.

## 4. What this memo does not decide

- No PyYAML or parser dependency is added.
- No route, flag, file name, precedence rule or save format is authorized.
- No claim that YAML is safe for untrusted input.
- No release/version/bump implication.

## 5. Questions for the panel

1. Is a one-way import (B) worth a public flag, or should YAML stay deferred (D)?
2. If B: is PyYAML `safe_load` plus a duplicate-key-rejecting constructor acceptable, or
   does the project require a dependency-free subset?
3. If YAML ever becomes an authority (A): what is the collision rule with
   `runevidence.json`, and which components must be re-reviewed?

## Appendix A — security test matrix for any YAML import (option B)

Any accepted import must reject or normalize the following before the model sees a value;
each row becomes a test in its own file with a fixed, value-free error:

| Input | Required behavior |
| --- | --- |
| Duplicate mapping keys (`id: a` twice, or `id` vs `"\u0069d"`) | Reject at every depth; never last-wins |
| Non-string mapping keys (`1: x`, `true: x`) | Reject |
| Anchors/aliases (`&a`, `*a`) | Reject the document, no expansion |
| Merge keys (`<<: {b: 1}`), with or without an anchor | Reject |
| Multi-line quoted/plain scalars, NEL/U+2028/U+2029 folding | Reject or define; never silent folding |
| Flow style (`{}`, `[]`) | Explicit policy; reject unless the subset defines it |
| Explicit tags (`!!python/object`, `!!binary`, custom) | Reject; only the accepted scalar tags |
| Implicit timestamps (`2026-01-01`) | Reject or keep as a string, never a datetime |
| `yes`/`no`/`on`/`off`/`y`/`n` | Treated as strings or rejected, never booleans |
| `nan`/`inf`/-`inf`/hex/octal/`.5`/`1_000`/`010` | Reject; only JSON number grammar |
| `1:20` (sexagesimal), `1e3` (valid JSON but a YAML string) | Reject; never a number and never a silent string |
| `key:` with no value, `~`, `Null`, `NULL` | Reject; only a literal `null` is null |
| Explicit tags: `!!str 1`, `!!float 1`, `!!set`, bare `!` | Reject every explicit tag |
| Huge nesting / node count / scalar length | Fixed caps counted on the event stream before construction |
| Multi-document stream (`---` twice) | Reject |
| Tabs used for indentation / inconsistent indent | Reject |
| BOM and non-UTF-8 bytes | Reject with a fixed encoding error |
| Deep nesting / very large document | Fixed byte and depth caps before parsing |
| Comments and blank lines | Ignored, never part of a value |
| Literal/folded block scalars | Preserved as strings exactly, including trailing newlines |
| CRLF vs LF | Same document; no semantic difference |

## Appendix B — compatibility with the delivered surface

Any accepted import must produce a document that the existing validator accepts for:

- schema 0.1 and 0.2 (including runs, conditions, relationships);
- file, directory and volatile artifacts;
- `add`/`rehash`/`migrate`/`conditions` outputs, including backup generations;
- the comparability bridge (0.1/0.2 conditions and selectors).

It must not create a manifest that the JSON loader would reject, and it must not alter any
existing `runevidence.json` reading or writing path.
