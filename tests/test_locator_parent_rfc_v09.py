"""The parent locator contract carries the slice D errata as v0.9.

`docs/verified-value-locators-rfc.md` is the contract both locator slices amend.
Slice D adds 27 exact errata (D9); this test pins their shipped text so a later
edit cannot silently drop or reflow one back to the v0.8 wording.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DOC = REPO / "docs" / "verified-value-locators-rfc.md"


def _text() -> str:
    return re.sub(r"\s+", " ", DOC.read_text(encoding="utf-8"))


def test_header_and_status_are_v0_9():
    text = _text()
    assert text.startswith("# Verified value locators: design RFC (v0.9)")
    assert "Status: **v0.9 ERRATA FOR SLICE C AND SLICE D.**" in text
    assert "v0.8 ERRATA FOR SLICE C.**" not in text


def test_scope_names_the_tensorboard_kind_and_drops_the_exclusion():
    text = _text()
    assert "the `tensorboard` kind specified for slice D" in text
    assert "Out of scope: computed/aggregate values, YAML manifests," in text
    assert "Out of scope: TensorBoard (separate RFC)" not in text
    assert (
        "TensorBoard scalar locators are specified by the slice D design RFC; "
        "other adapters remain later slices."
    ) in text


def test_kind_rule_and_absent_locator_cover_0_5():
    text = _text()
    assert (
        "`kind` must match the artifact, case-insensitively: `jsonl` on `.jsonl`, "
        "`csv-cell` on `.csv`, and `tensorboard` on a basename containing `.tfevents`."
    ) in text
    assert "An absent locator yields `result-unverified` (warning, §4.3) in 0.3, 0.4 and 0.5." in text


def test_snapshot_scope_memory_and_cap_wording():
    text = _text()
    assert (
        "Only artifacts targeted by a 0.3, 0.4 or 0.5 locator (string, `jsonl`, "
        "`csv-cell` or `tensorboard`) get a retained in-memory snapshot."
    ) in text
    assert (
        "capped at 50 MiB, or at 128 MiB when the artifact is targeted by a "
        "kind-eligible TensorBoard locator."
    ) in text
    assert (
        "a capped artifact emits one cap finding per capped kind, one per eligible "
        "artifact and kind (including over-cap artifacts)."
    ) in text
    assert (
        "except for a locator kind whose artifact is capped for that kind, where no "
        "value is compared"
    ) in text
    assert (
        "There is one cap finding per eligible artifact and kind in state `ok` or "
        "`over-cap`."
    ) in text
    assert (
        "The TensorBoard-only size-race rows also classify a file whose read ends "
        "past its cap as over-cap."
    ) in text


def test_outcome_table_has_the_tensorboard_rows():
    text = _text()
    assert "| Line/row/column/occurrence out of range |" in text
    assert "| Recorded type / unsupported scalar / non-scalar |" in text
    assert "| TensorBoard scalar cap | `locator-unreadable` (new) | error | 1 |" in text


def test_caps_and_comparator_cover_tensorboard():
    text = _text()
    assert (
        "A TensorBoard target uses a 128 MiB file cap and a 16 MiB merged summary "
        "payload cap per Event"
    ) in text
    assert (
        "TensorBoard results and tag metadata are capped at 10,000 entries and 16 "
        "MiB per manifest"
    ) in text
    assert (
        "**Comparator:** string, `jsonl` and `csv-cell` locators (0.3, 0.4 and 0.5) "
        "use a new comparator:"
    ) in text
    assert (
        "TensorBoard locators use `compare_tensorboard`: `rel_tol=1e-6, "
        "abs_tol=1e-12` for float32-origin values; `rel_tol=1e-9, abs_tol=1e-12` "
        "for float64"
    ) in text
    assert (
        "a recorded string, boolean or null is `locator-unsupported-value`."
    ) in text


def test_display_and_privacy_cover_tensorboard():
    text = _text()
    assert "`tensorboard:step=40,occurrence=0`" in text
    assert "a `tensorboard` finding never names the tag." in text


def test_version_matrix_and_migration_cover_0_5():
    text = _text()
    assert (
        "New readers read 0.1-0.5 without rewriting them; old readers reject 0.3, "
        "0.4 and 0.5."
    ) in text
    assert (
        "A 0.3/0.4 manifest migrates to 0.5 as a version-only bump; 0.5 has no "
        "downgrade."
    ) in text
    assert (
        "Fingerprint v1 is frozen: it accepts schema **0.1 only** and refuses 0.2, "
        "0.3, 0.4 and 0.5."
    ) in text
    assert "v1 refuses 0.2, 0.3, 0.4 and 0.5" in text
    assert "old readers reject 0.3, 0.4 and 0.5;" in text


def test_gates_cover_the_0_5_matrix_and_tensorboard_vectors():
    text = _text()
    assert (
        "extension matching (`.json`/`.jsonl`/`.csv`, or a TensorBoard basename "
        "containing `.tfevents`; `.ndjson`/`.tsv` refused)"
    ) in text
    assert "absent locator (0.3, 0.4 and 0.5)" in text
    assert (
        "a 129 MiB fixture for TensorBoard targets"
    ) in text
    assert (
        "8. TensorBoard adds framing/CRC/protobuf vectors: duplicate singular "
        "fields, message merge, oneof interleaving, packed/unpacked values, the "
        "merged summary cap, and the 50/128 MiB overlap."
    ) in text


def test_the_0_5_matrix_ends_with_one_period():
    text = _text()
    assert (
        "carry-over, and the 0.5 `artifact_id` cross-field rule). 2. Outcome table:"
    ) in text
    # The two `..` in the document are the pre-existing ellipses (`jsonl:...`
    # and `EMFILE`, ...); the errata must not add punctuation artifacts.
    for bad in (".;", ";.", ",.", ".,", ";;", ".)."):
        assert bad not in text, bad
