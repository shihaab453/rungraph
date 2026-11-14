# Private claimed-observation trace policy v0

**Proposal only. Static, untested, unintegrated. S3P3P7 PERSISTENCE SAFETY HOLD remains in force.** JSON is the sole production authority. SQLite stays private, synthetic, disposable and non-authoritative. The policy is neither a guard nor a native or filesystem validator.

The public surface of this *private module* comprises frozen/slotted `ClaimedPoint`, frozen/slotted `TraceDecision`, and `classify_claimed_trace(tuple[ClaimedPoint, ...])`. It deliberately does not import or call R17, RunGraph, SQLite, files, handles, or anything beyond the standard library's `dataclasses`. It accepts only an exact tuple of 3 through 32 exact `ClaimedPoint` objects, each assigned exact consecutive `int` ordinals 0..n-1 (bool is invalid). The tuples and ordinals are caller assertions and do not authenticate temporal order, captures, or gaps between captures.

Each point claims JSON digest and identity, shadow-source digest, stage identity, stage receipt digest, stage revision, stage-verified flag, and artifact digest. Digests/identities are *abstract nonempty strings*, not validated cryptographic digests. The receipt is checked only for presence and equality among observations, **not authenticity, binding, contents, format, or signature**. `stage_verified is True` is a statement, not proof of native validation. Outside scope: arbitrary objects, hostile string subclasses, reentrant/mutated objects, callers that forge claims, and a manifest I/O acquisition mechanism.

Return the first applicable decision, **in this exact precedence**:

1. Input is not an exact tuple of 3..32 exact `ClaimedPoint`s: `REFUSE / INVALID_TRACE_SHAPE`.
2. Ordinals are not exact consecutive integers 0..n-1: `REFUSE / INVALID_ORDINAL_SEQUENCE`.
3. Any digest/identity/receipt is non-string or empty/whitespace-only: `REFUSE / MISSING_OR_INVALID_OBSERVATION`.
4. Any stage revision is not the exact integer 1: `REFUSE / UNSUPPORTED_STAGE_REVISION`.
5. Any stage-verified claim is not the exact singleton `True`: `REFUSE / STAGE_NOT_VERIFIED`.
6. Claimed JSON digest differs from the first point at any later point: `REFUSE / OBSERVED_JSON_BYTES_TRANSITION`.
7. Claimed JSON identity differs from the first point: `REFUSE / OBSERVED_JSON_IDENTITY_TRANSITION`.
8. Claimed stage identity differs: `REFUSE / OBSERVED_STAGE_IDENTITY_TRANSITION`.
9. Claimed stage receipt digest differs: `REFUSE / OBSERVED_STAGE_RECEIPT_TRANSITION`.
10. Claimed artifact digest differs: `REFUSE / OBSERVED_ARTIFACT_TRANSITION`.
11. A point's shadow-source digest differs from **that point's** claimed JSON digest: `REFUSE / SHADOW_SOURCE_DIVERGED`.
12. Otherwise: `ELIGIBLE_FOR_SEPARATE_VALIDATION / PROVISIONAL_CLAIMED_TRACE_STABILITY_ONLY`.

Unlike the R17 endpoint-only comparison, this contract detects a **supplied** A-B-A sequence when the intermediate B appears in claimed JSON, identity, stage receipt or artifact observations. It cannot detect unobserved A-B-A changes, claims forged by a caller, races between separate observations, hidden filesystem aliases, or an untrusted native source. An ordinal sequence is not a monotonic/transactional counter; the observable sequence may omit arbitrarily many real mutations. No number of same-valued snapshots establishes atomicity, trusted provenance, durability, stage-read authorization or safe JSON/SQLite cutover.

The companion vectors are specification data, not executed tests. They supply per-point field edits relative to a base tuple and explicitly label an indistinguishable unobserved ABA case. R17 and R12 remain independent. Do not import, integrate, execute or publish this proposal under the static-only scope. Future activation, full validation, testing, writer changes, real manifests, SQLite state changes and any Git mutation require new authorization.

Historical deviations remain: R11 eight unauthorized `rehash/add --dry-run` CLI route starts; R14 premature 69-node test execution. R15's two test cleanups remain applied **but untested**; R16B remains unadmitted. Native SQLite, host ACL/owner, hardlink and Git-object preservation, power-loss, crash durability, hostile concurrency, and historical directory findings remain unresolved. This contract makes no guarantees about those areas.
