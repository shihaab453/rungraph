"""Private, unintegrated shadow-observation policy (R17 static candidate).

This module performs NO I/O. Inputs are untrusted caller assertions, not
filesystem observations. An eligible outcome is never permission to read,
write, repair, migrate, activate SQLite or supersede JSON authority.
"""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Observation:
    json_digest_before: str | None
    json_digest_after: str | None
    json_identity_before: str | None
    json_identity_after: str | None
    shadow_source_digest: str | None
    stage_revision: int | None
    stage_identity_before: str | None
    stage_identity_after: str | None
    stage_receipt_digest: str | None
    stage_verified: bool | None
    artifact_digest_before: str | None
    artifact_digest_after: str | None


@dataclass(frozen=True, slots=True)
class Decision:
    status: str
    reason: str


REFUSE = "REFUSE"
REVIEW_ONLY = "ELIGIBLE_FOR_SEPARATE_VALIDATION"


def classify(observation: Observation) -> Decision:
    """Classify claimed observations without touching source or shadow bytes.

    REVIEW_ONLY means that a distinct native-aware, handle/receipt-aware
    validator would still be required. There is no grant of authority here.
    """
    values = (
        observation.json_digest_before,
        observation.json_digest_after,
        observation.json_identity_before,
        observation.json_identity_after,
        observation.shadow_source_digest,
        observation.stage_identity_before,
        observation.stage_identity_after,
        observation.stage_receipt_digest,
        observation.artifact_digest_before,
        observation.artifact_digest_after,
    )
    if not all(isinstance(value, str) and bool(value.strip()) for value in values):
        return Decision(REFUSE, "MISSING_OR_INVALID_OBSERVATION")
    if type(observation.stage_revision) is not int or observation.stage_revision != 1:
        return Decision(REFUSE, "UNSUPPORTED_STAGE_REVISION")
    if observation.stage_verified is not True:
        return Decision(REFUSE, "STAGE_NOT_VERIFIED")
    if observation.json_digest_before != observation.json_digest_after:
        return Decision(REFUSE, "JSON_BYTES_CHANGED")
    if observation.json_identity_before != observation.json_identity_after:
        return Decision(REFUSE, "JSON_IDENTITY_CHANGED")
    if observation.stage_identity_before != observation.stage_identity_after:
        return Decision(REFUSE, "STAGE_IDENTITY_CHANGED")
    if observation.shadow_source_digest != observation.json_digest_after:
        return Decision(REFUSE, "SHADOW_SOURCE_DIVERGED")
    if observation.artifact_digest_before != observation.artifact_digest_after:
        return Decision(REFUSE, "ARTIFACT_OBSERVATIONS_DIVERGED")
    return Decision(REVIEW_ONLY, "PROVISIONAL_CONSISTENCY_ONLY")
