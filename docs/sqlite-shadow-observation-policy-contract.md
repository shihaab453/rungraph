# Private shadow-observation policy v0

Status: static private policy draft, untested and unintegrated. P7 persistence safety hold remains in force. This is a claim classifier, not an authority or a persistence guard. JSON remains the sole authority; the shadow is disposable, private and synthetic. No silent fallback or destructive recovery is authorized.

`Observation` contains caller-supplied abstract strings for JSON digests and identities before and after, shadow source digest, stage identities before and after, stage receipt digest, and artifact digests before and after; it also contains stage revision and verification claims. No claim is acquired or validated by this module. Nonempty strings are checked with whitespace stripping only for emptiness; comparisons use the original strings. No digest format or authenticity is established. `Decision` is immutable and contains status and reason.

The first applicable reason wins:

1. Missing, non-string, empty or whitespace-only digest, identity or receipt: `REFUSE / MISSING_OR_INVALID_OBSERVATION`.
2. Revision other than exact integer 1, including boolean or missing: `REFUSE / UNSUPPORTED_STAGE_REVISION`.
3. Verification claim other than the singleton true: `REFUSE / STAGE_NOT_VERIFIED`.
4. Unequal JSON digest claims: `REFUSE / JSON_BYTES_CHANGED`.
5. Unequal JSON identity claims: `REFUSE / JSON_IDENTITY_CHANGED`.
6. Unequal stage identity claims: `REFUSE / STAGE_IDENTITY_CHANGED`.
7. Shadow source digest differs from current JSON digest claim: `REFUSE / SHADOW_SOURCE_DIVERGED`.
8. Unequal artifact digest claims: `REFUSE / ARTIFACT_OBSERVATIONS_DIVERGED`.
9. Otherwise: `ELIGIBLE_FOR_SEPARATE_VALIDATION / PROVISIONAL_CONSISTENCY_ONLY`.

Only the defined `Observation` interface is in scope; arbitrary objects, hostile string subclasses and malformed invocation are unsupported. The receipt is required but is not verified or compared to other claims. A true verification claim is not proof of native validation. Inputs can be false, stale, incomplete or non-atomic. R9 ABA can restore equal values after an intervening change, including artifact ABA; equal claims cannot prove no change. The ABA vector intentionally looks eligible despite that limitation.

R12 authority and conflict boundaries remain separate: this classifier does not resolve source authority, reconcile conflicts, permit fallback or authorize stage reads. Separate source review, runtime admission and owner authorization are required before any import or use; a later native-aware, handle/receipt-aware validation and independent activation decision remain necessary. No result permits production use, JSON overwrite, SQLite mutation, repair, migration, rollback or cutover. Native races, filesystem identity, permissions and crash durability remain unproven.

The accompanying 15 vectors are data-only specifications, not executed tests. They apply each case's changes to the base observation and specify the first expected status and reason. The static review is not a functional pass.