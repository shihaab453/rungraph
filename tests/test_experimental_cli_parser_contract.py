"""Parser grammar checks; these never dispatch a CLI command."""
import ast
import hashlib
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
# ---- HISTORICAL RECORDS (preserved evidence; never accepted as current pins) ----
# Seven-route CLI pin (CRLF), valid for that source snapshot only.
HISTORICAL_SEVEN_ROUTE_CLI_HASH = "e45b9d696d1fa63730b223bb6c7d43d5093f9aee42fb84cb498dad4112096fe1"
HISTORICAL_SEVEN_ROUTES = ("validate", "inspect", "init", "rehash", "add", "schema", "adapters")
# Exact reviewed 22-route CLI bytes in LF and CRLF checkout forms.
# D17-S1R5 static writer-route review, independently adjudicated in D17-S1R6.
HISTORICAL_22_ROUTE_CLI_HASH = frozenset({
    "a4cd86ac0903b4827bf7a846949a253d3d8e1aff9a02602e12ea95512d3a03e6",  # LF
    "ba6bd580723f1b1ad7bccda0a0379a09d210d5cf46e43b9800e54383f56338a6",  # CRLF
})
HISTORICAL_22_ROUTES = ("validate", "inspect", "compare", "leaderboard", "matrix", "run", "claim", "lineage",
                        "impact", "coverage", "coverage-gate", "fingerprint", "fingerprint-compare",
                        "coverage-diff", "review-diff", "impact-diff", "review-gate",
                        "init", "rehash", "add", "schema", "adapters")
# Exact reviewed 23-route CLI bytes in LF and CRLF checkout forms: the 22-route
# snapshot plus validate-batch, registered directly after validate.
# HISTORICAL RECORD for RG-IND11-PC4 (2026-11-09), valid for that source snapshot only.
HISTORICAL_23_ROUTE_CLI_HASH = frozenset({
    "500148e36b7b08f2e24910c7dce6df8d6704322dff511d44618d8dbc60521ba2",  # LF
    "63886e751054cb49e92341bed976e1b4ac684f95f98e6166081eaf407a83b7a5",  # CRLF
})
HISTORICAL_23_ROUTES = ("validate", "validate-batch", "inspect", "compare", "leaderboard", "matrix", "run", "claim",
                        "lineage", "impact", "coverage", "coverage-gate", "fingerprint", "fingerprint-compare",
                        "coverage-diff", "review-diff", "impact-diff", "review-gate",
                        "init", "rehash", "add", "schema", "adapters")
# Exact reviewed 24-route CLI bytes in LF and CRLF checkout forms: the historical
# 23 plus the experimental comparability route, registered directly after compare.
# M9-COMPARABILITY-STAGE2B-R1 (2026-11-10); HISTORICAL RECORD for that snapshot only.
HISTORICAL_24_ROUTE_CLI_HASH = frozenset({
    "a4a4b921921f5e8fe1b04ca79d40e5dcffb2678e28ad5a0313ff6c3f25c3f147",  # LF
    "83c2dbfa6cccbd9e5c505d2cef6d244901179caf67ba70054dc2dfde6b2d2d3a",  # CRLF
})
HISTORICAL_24_ROUTES = ("validate", "validate-batch", "inspect", "compare", "comparability", "leaderboard", "matrix", "run",
                        "claim", "lineage", "impact", "coverage", "coverage-gate", "fingerprint",
                        "fingerprint-compare", "coverage-diff", "review-diff", "impact-diff", "review-gate",
                        "init", "rehash", "add", "schema", "adapters")
# Exact reviewed 25-route CLI bytes in LF and CRLF checkout forms: the historical
# 24 plus the explicit migrate route, registered directly after init.
# M9-COMPARABILITY-STAGE3B-2C1 (2026-11-11); HISTORICAL RECORD for that snapshot only.
HISTORICAL_25_ROUTE_CLI_HASH = frozenset({
    "749a6a7270216417d278524453a3e582767e4c48e4111c2debd0ce5261abcffd",  # LF
    "08361a545733bf1ea8dbf31efcb69229defe23039af2491bf5e1b109f46e105f",  # CRLF
})
HISTORICAL_25_ROUTES = ("validate", "validate-batch", "inspect", "compare", "comparability", "leaderboard", "matrix", "run",
                        "claim", "lineage", "impact", "coverage", "coverage-gate", "fingerprint",
                        "fingerprint-compare", "coverage-diff", "review-diff", "impact-diff", "review-gate",
                        "init", "migrate", "rehash", "add", "schema", "adapters")
# Exact reviewed 26-route CLI bytes in LF and CRLF checkout forms: the historical
# conditions snapshot, before the public read-only directory flag.
# M42-SCANNER-S3B (2026-11-12) superseded these; HISTORICAL RECORD only.
HISTORICAL_26_ROUTE_CLI_HASH = frozenset({
    "59e42297c6387012a33e73e008f376766871a9235e37f58982c92cd5c46ee340",  # LF
    "cbfc3de10aa22996eaa66646516116ec4ea0dd37e2f6d86e7144f05b857e1a7d",  # CRLF
    # M47-S4-2A G4-2a 3rd review fix (2026-11-12): superseded by the 4th review
    # fix. Historical record only; must never be accepted as the current pin.
    "139c374f7fb19476b94918f33aa5bff482230eb0d585fa5f839b6cd18a796c38",  # LF
    "df4be14124ce6f93f883e1a9585e47d501efd53fe98bd1c393b564ca95922e75",  # CRLF
    # M48-S4-2B first review bytes (2026-11-12): superseded after G4-2b review
    # (phase-3 output and preparation sanitizer fixes). Historical record only.
    "3f2a17f9fdee7e5499dc6c63478a91062265849b8fd2722042422403150d09b4",  # LF
    "460778025e53f74815d07019040be0dcfaf5976d0c9c6b2d45de9aab12b2ffcb",  # CRLF
    # M48-S4-2B second review bytes (2026-11-12): superseded by the sanitized
    # manifest-path fallback and single-emission rendering. Historical only.
    "d39d2a242883d203919e5448b4a882d4d1ca618ae95d7575d70f1fbe3ae066cb",  # LF
    "abc1b8e9af88fe2a9703f2c46a9d6fbb3fb7f0fb1ed16cd2cc970373cf83d673",  # CRLF
    # M48-S4-2B third review bytes (2026-11-12): superseded by the dry-run
    # sanitizer, classifier precision and rehash-wrapper pins. Historical only.
    "742b9f3f389d71ed75784c42c9000b960f52ffa7cd9395f170f8c902d15188c5",  # LF
    "5518aa4bf78f627023eeeb534cd8715f61683809747681efbaeead052f4ef0bf",  # CRLF
    # M48-S4-2B delivered bytes (2026-11-12): superseded by the truthful
    # manifest reason pre-check and schema-0.2 coverage. Historical only.
    "ee77af201d548805a20697ee347a31cac538fcc7332cd1c669f8b020439725e3",  # LF
    "c9fd41148e982d6437ec2d67fd84a583114ea5c4210b09d4701080c336e95534",  # CRLF
    # M48-S4-2B polish bytes (2026-11-12): superseded by the FIFO-safe
    # pre-check and filesystem-first order. Historical only.
    "e6badf77cc8c5baa857930b155f014c87917bb5ea1783424d70515fd74045610",  # LF
    "6954e76deea8ed66c2ec9ef63bff8cbc7f5ad2e3efcfd01945e75dc30f70d9a2",  # CRLF
    # M48-S4-2B FIFO-safe bytes (2026-11-12): superseded by the dangling-alias
    # classifier correction. Historical only.
    "a5a5fdc306fec88c643401dfa6d2061cf63902cdeec061ae6465917d77b57cd4",  # LF
    "955edbf9b814bbea6d0a5b8e112da7b1186886dae2b9fa1490716caa79146cb6",  # CRLF
    # M48-S4-2B dangling-fixed bytes (2026-11-12): superseded by the non-blocking
    # dry-run and held-descriptor loads. Historical only.
    "11102f575dcb9f0d8d3824f48234ceff9277eec41504e035b71ecf830005cd12",  # LF
    "f684ab09adcaa96820fad8070a50535a2fb348d725cada5ac685122cfc1a1b1e",  # CRLF
    # M48-S4-2B FIFO-completion bytes (2026-11-12): superseded by the
    # shared-loader FIFO fix and the dry-run docstring correction. Historical only.
    "6204f4600a6a88f30eb2974dd8a5e700b202625d7234077ee8ae7017fe88479d",  # LF
    "3e058e266265e8a0f3c5e4dd63c8b51b931f693237c17b9799cd6341439ed25c",  # CRLF
    # S4 prelude bytes (2026-11-12): superseded by the S6 init 0.2 refusal.
    "cd717cb6d942437dddecbe07a7494c64d7486196a95a2c08cad3365bfce736c7",  # LF
    "00d4a0f2751175d185661df68aebcfcd2db11a243a432a5f909985cdb602db50",  # CRLF
    # Slice D phase 4a (2026-11-14): migrate --to 0.5, the init 0.5 refusal and
    # version-keyed conditions; superseded by the phase 4b volatile hook.
    "acfb857e1e1b0af5963036c4e848e356449f2b7f8fe97e38209675af559e9084",  # LF
    "67ddbd0d38b65038163c107ec5e82cb4666f0788ed769af54bbd16d889ca283b",  # CRLF
})
HISTORICAL_READER_HASHES = (
    "d1d0bfe3d854ba44e42db8b92b292b49bb408fe8c7bd0442ebbb616c0e0e67d5",  # original reader test
    "8fb6e0fd75b8617f968439c05e108da20eb6089c96310ccd08504b72aa34edfe",  # PC3R2 corrected loader
    # PC4 23-route reader-set assertion (2026-11-09); superseded by the Stage 2b
    # 24-route reader test on 2026-11-11.
    "cb614ec86203f3845fb54e0c1f92f5f2af0d0e3ea1b294d77392b49284e0dde3",
    # M9-COMPARABILITY-STAGE2B-R1 (2026-11-10) 24-route reader-set assertion;
    # superseded by the Stage 3d-2c1 25-route reader test on 2026-11-11.
    "582a27bca1dfe64e1d7aaf2c18376044511431b2c08cc270bbb6581cea604ecc",
    # M9-COMPARABILITY-STAGE3B-2C1 (2026-11-11) 25-route reader-set assertion;
    # superseded by the Stage 3d-3 conditions 26-route reader test on 2026-11-11.
    "aa7eeedadb7495b80a8c551683d759c3195a62d39a3a22025450d2d763bee360",
    # Slice C phase 4a (2026-11-14): the schema 0.4 dispatcher wiring updates
    # this reader test's import assertion; superseded by the current pin.
    "b08c530cb51befc74d23a049f46a4d22a924fbb146233086408b5d5642d9c9a1",
)
HISTORICAL_READER_DIGEST_23 = (
    "cb614ec86203f3845fb54e0c1f92f5f2af0d0e3ea1b294d77392b49284e0dde3"
)
# ---- CURRENT PROSPECTIVE PINS: M51 locator slice A, 2026-11-12 ----
# Exact reviewed 26-route CLI bytes in LF and CRLF checkout forms. Slice D
# phase 4a (2026-11-14) added migrate --to 0.5, the init 0.5 refusal and the
# version-keyed conditions 0.5 text; phase 4b adds the adapter volatile-target
# hook. The 26-route surface is unchanged. Each
# pin is an exact byte SHA-256. Nothing is normalized during checking. Static
# != plugin safety.
CLI_HASH = frozenset({
    "37ca37bf0f32fd45ffe9a10acf24ef5bae5ef3a2244b5dab8d40fcd117c22980",  # LF
    "97a89251f71f5255ee4dd68b47d3c401abb4b8e352385140c3ce143255740d19",  # CRLF
})
# PC3R2 corrected loader plus the PC4, Stage 2b, Stage 3d-2c1 and Stage 3d-3
# route-set reconciliations.
# Exactly one LF digest string: never a collection, no CRLF or historical alternative.
READER_HASH = "e02e60c2f2a1c893c73d32e1d59b3f3ea566ffd2e577f56d060239691c6ee5c4"
# Exact registration order. Any missing, extra, duplicated or reordered route fails.
ROUTES = ("validate", "validate-batch", "inspect", "compare", "comparability", "leaderboard", "matrix", "run", "claim",
          "lineage", "impact", "coverage", "coverage-gate", "fingerprint", "fingerprint-compare",
          "coverage-diff", "review-diff", "impact-diff", "review-gate",
          "init", "migrate", "conditions", "rehash", "add", "schema", "adapters")
PRIVATE_ROUTE_MARKERS = ("experimental", "sqlite", "stage", "shadow", "witness")


def source(path, *, source_kind):
    # Accepted digests come only from the fixed current pins for a named source
    # category. Callers cannot supply, widen or relabel them; unknown kinds fail closed.
    if source_kind == "cli":
        # Exactly the two reviewed CLI representations (LF, CRLF); fails closed here, not only in the constant test.
        assert type(CLI_HASH) is frozenset and len(CLI_HASH) == 2 and all(type(h) is str for h in CLI_HASH), \
            "CLI pins must be exactly two digest strings"
        bound, accepted = "src/runevidence/cli.py", CLI_HASH
    elif source_kind == "reader":
        assert type(READER_HASH) is str, "reader pin must be exactly one digest string"
        bound, accepted = "tests/test_experimental_staged_reader.py", frozenset({READER_HASH})
    else:
        raise AssertionError(f"unknown source kind: {source_kind!r}")
    assert path == bound, f"{path!r} is not the pinned {source_kind} source"
    assert type(accepted) is frozenset and all(type(h) is str for h in accepted)
    historical = (HISTORICAL_26_ROUTE_CLI_HASH
                  | HISTORICAL_25_ROUTE_CLI_HASH | HISTORICAL_24_ROUTE_CLI_HASH
                  | HISTORICAL_23_ROUTE_CLI_HASH | HISTORICAL_22_ROUTE_CLI_HASH
                  | {HISTORICAL_SEVEN_ROUTE_CLI_HASH, *HISTORICAL_READER_HASHES})
    assert accepted.isdisjoint(historical), "historical digest offered as a current pin"
    data = (ROOT / path).read_bytes()
    digests = {hashlib.sha256(data).hexdigest()}
    if b"\r\n" in data:
        # Git may check the reviewed LF source out as CRLF on Windows. The pin
        # still names the reviewed LF bytes, so accept that canonical form of
        # the same checkout and nothing else.
        digests.add(hashlib.sha256(data.replace(b"\r\n", b"\n")).hexdigest())
    assert digests & accepted
    return ast.parse(data)


def test_pinned_parser_structure():
    tree = source("src/runevidence/cli.py", source_kind="cli")
    build = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "build_parser")
    calls = [ast.unparse(n.func) for n in ast.walk(build) if isinstance(n, ast.Call)]
    assert set(calls) == {"argparse.ArgumentParser", "parser.add_argument", "parser.add_subparsers",
                          "sub.add_parser", "p.add_argument", "p.set_defaults", "p.add_mutually_exclusive_group",
                          "vol.add_argument", "add_check_args", "add_edit_args", "Path"}
    assert not any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id.startswith("cmd_")
                   for n in ast.walk(build))
    found = [n for n in ast.walk(build) if isinstance(n, ast.Call) and ast.unparse(n.func) == "sub.add_parser"]
    routes = [n.args[0].value for n in sorted(found, key=lambda n: (n.lineno, n.col_offset))]
    assert sorted(routes) == sorted(ROUTES) and len(routes) == len(set(routes))
    assert routes == list(ROUTES)
    assert not [r for r in routes if r.startswith("_") or any(m in r.lower() for m in PRIVATE_ROUTE_MARKERS)]


def test_current_contract_preserves_historical_baselines():
    # Constant-only: records how the current contract relates to preserved history.
    assert len(ROUTES) == len(set(ROUTES)) == 26
    assert set(HISTORICAL_SEVEN_ROUTES) <= set(ROUTES)
    assert [r for r in HISTORICAL_24_ROUTES if r != "comparability"] == list(HISTORICAL_23_ROUTES)
    assert [r for r in HISTORICAL_24_ROUTES if r not in ("validate-batch", "comparability")] == list(HISTORICAL_22_ROUTES)
    assert [r for r in HISTORICAL_25_ROUTES if r != "migrate"] == list(HISTORICAL_24_ROUTES)
    assert [r for r in ROUTES if r not in ("migrate", "conditions")] == list(HISTORICAL_24_ROUTES)
    assert [r for r in HISTORICAL_25_ROUTES if r not in ("validate-batch", "comparability", "migrate")] == list(HISTORICAL_22_ROUTES)
    assert [r for r in ROUTES if r != "conditions"] == list(HISTORICAL_25_ROUTES)
    assert ROUTES[ROUTES.index("validate") + 1] == "validate-batch"
    assert ROUTES[ROUTES.index("compare") + 1] == "comparability"
    assert ROUTES[ROUTES.index("init") + 1] == "migrate"
    assert ROUTES[ROUTES.index("migrate") + 1] == "conditions"
    assert type(CLI_HASH) is frozenset and len(CLI_HASH) == 2 and all(type(h) is str for h in CLI_HASH)
    assert CLI_HASH.isdisjoint(HISTORICAL_26_ROUTE_CLI_HASH
                               | HISTORICAL_25_ROUTE_CLI_HASH | HISTORICAL_24_ROUTE_CLI_HASH
                               | HISTORICAL_23_ROUTE_CLI_HASH | HISTORICAL_22_ROUTE_CLI_HASH
                               | {HISTORICAL_SEVEN_ROUTE_CLI_HASH})
    assert type(READER_HASH) is str
    assert HISTORICAL_READER_DIGEST_23 in HISTORICAL_READER_HASHES
    assert READER_HASH != HISTORICAL_READER_DIGEST_23
    assert READER_HASH not in HISTORICAL_READER_HASHES


def test_reader_comparison_argv_tripwire():
    tree = source("tests/test_experimental_staged_reader.py", source_kind="reader")
    f = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "test_cli_with_test_only_injection_matches_unmodified_json")
    assigns = [n.value for n in f.body if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "argvs" for t in n.targets)]
    assert len(assigns) == 1 and isinstance(assigns[0], ast.List)
    cases = []
    for row in assigns[0].elts:
        assert isinstance(row, ast.List) and len(row.elts) in (2, 3)
        assert isinstance(row.elts[1], ast.Name) and row.elts[1].id == "p"
        assert all(isinstance(x, ast.Constant) and isinstance(x.value, str) for x in [row.elts[0], *row.elts[2:]])
        cases.append((row.elts[0].value, *(x.value for x in row.elts[2:])))
    assert cases == [("validate",), ("validate", "--json"), ("validate", "--strict"), ("inspect",), ("inspect", "--json")]


def parse_without_dispatch(argv):
    from runevidence import cli
    # Historical seven-handler dispatch denial, unchanged. Only routes inside this
    # set are parsed below; any other route fails closed on ns.func is denied.
    names = ("validate", "inspect", "init", "rehash", "add", "schema", "adapters")
    saved = {name: getattr(cli, "cmd_" + name) for name in names}
    def denied(*args, **kwargs):
        raise AssertionError("parser dispatched a handler")
    try:
        for name in names:
            setattr(cli, "cmd_" + name, denied)
        ns = cli.build_parser().parse_args(argv)
        assert ns.func is denied
        return ns
    finally:
        for name, handler in saved.items():
            setattr(cli, "cmd_" + name, handler)


def test_edit_route_grammar_only():
    path = "Z:/__r14_impossible_sentinel__/manifest.json"
    rehash = parse_without_dispatch(["rehash", path, "--all", "--dry-run"])
    assert (rehash.command, rehash.path, rehash.all, rehash.targets, rehash.volatile, rehash.dry_run) == ("rehash", Path(path), True, [], None, True)
    file = "Z:/__r14_impossible_sentinel__/file"
    add = parse_without_dispatch(["add", path, file, "--dry-run", "--no-run"])
    assert (add.command, add.path, add.files, add.dry_run, add.no_run) == ("add", Path(path), [Path(file)], True, True)


def test_read_only_route_grammar_only():
    path = "__r14_impossible_sentinel__/manifest.json"
    for route in ("validate", "inspect"):
        ns = parse_without_dispatch([route, path, "--json", "--strict", "--directory-artifacts"])
        assert (ns.command, ns.path, ns.json, ns.strict, ns.directory_artifacts) == (
            route, Path(path), True, True, True)
        ns = parse_without_dispatch([route, path])
        assert ns.directory_artifacts is False


def test_directory_flag_is_registered_only_on_read_only_routes():
    # Grammar only: inspect the parser objects; no handler is dispatched.
    import argparse

    from runevidence.cli import build_parser

    parser = build_parser()
    sub = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
    assert set(sub.choices) == set(ROUTES)
    for name, route in sub.choices.items():
        options = {option for action in route._actions for option in action.option_strings}
        assert ("--directory-artifacts" in options) is (name in ("validate", "inspect"))


def test_comparability_route_grammar_only():
    # Grammar only: the handler is replaced before parsing, so nothing dispatches.
    from runevidence import cli

    baseline = "__r14_impossible_sentinel__/baseline.json"
    target = "__r14_impossible_sentinel__/target.json"

    def denied(*args, **kwargs):
        raise AssertionError("parser dispatched a handler")

    saved = cli.cmd_comparability
    try:
        cli.cmd_comparability = denied
        ns = cli.build_parser().parse_args([
            "comparability", baseline, target,
            "--baseline-result", "res-b", "--target-result", "res-t", "--json",
        ])
        assert ns.func is denied
        assert (ns.command, ns.baseline, ns.target, ns.baseline_result,
                ns.target_result, ns.json, ns.preflight) == (
            "comparability", Path(baseline), Path(target), "res-b", "res-t", True, False)
        # Selectors are optional in the grammar now: ordinary comparison
        # enforces both, while --preflight allows neither.
        bare = cli.build_parser().parse_args(["comparability", baseline, target])
        assert (bare.baseline_result, bare.target_result, bare.preflight) == (None, None, False)
        preflight = cli.build_parser().parse_args([
            "comparability", baseline, target, "--preflight", "--json",
        ])
        assert (preflight.preflight, preflight.json) == (True, True)
        # No strict/redaction/summary-only surface is registered for this route.
        assert not hasattr(ns, "strict")
        assert not hasattr(ns, "redact_manifest_path")
        assert not hasattr(ns, "summary_only")
        with pytest.raises(SystemExit) as caught:
            cli.build_parser().parse_args([
                "comparability", baseline, target, "--baseline-result",
            ])
        assert caught.value.code == 2
    finally:
        cli.cmd_comparability = saved


def test_migrate_route_grammar_only():
    # Grammar only: the handler is replaced before parsing, so nothing dispatches.
    # The historical seven-handler parse_without_dispatch helper does not admit
    # migrate; this uses the reviewed comparability handler-replacement pattern.
    from runevidence import cli

    path = "__r14_impossible_sentinel__/manifest.json"

    def denied(*args, **kwargs):
        raise AssertionError("parser dispatched a handler")

    saved = cli.cmd_migrate
    try:
        cli.cmd_migrate = denied
        ns = cli.build_parser().parse_args([
            "migrate", path, "--to", "0.2", "--dry-run", "--json",
        ])
        assert ns.func is denied
        assert (ns.command, ns.path, ns.to, ns.dry_run, ns.json) == (
            "migrate", Path(path), "0.2", True, True)
        # --to is validated after parse, so an unsupported value still parses.
        unsupported = cli.build_parser().parse_args(["migrate", path, "--to", "0.3"])
        assert unsupported.to == "0.3"
        # No strict/redaction/summary-only surface is registered for this route.
        assert not hasattr(ns, "strict")
        assert not hasattr(ns, "redact_manifest_path")
        assert not hasattr(ns, "summary_only")
        with pytest.raises(SystemExit) as caught:
            cli.build_parser().parse_args(["migrate", path])
        assert caught.value.code == 2
    finally:
        cli.cmd_migrate = saved


def test_directory_authoring_route_grammar_only():
    # Grammar only: add/rehash handlers are replaced before parsing.
    path = "Z:/__r14_impossible_sentinel__/manifest.json"
    directory = "Z:/__r14_impossible_sentinel__/data"
    add = parse_without_dispatch([
        "add", path, directory, "--directory", "--id", "data",
        "--kind", "dataset", "--no-run", "--dry-run",
    ])
    assert (
        add.command, add.path, add.directory, add.id, add.files, add.kind,
        add.no_run, add.dry_run,
    ) == (
        "add", Path(path), True, "data", [Path(directory)], "dataset", True, True,
    )
    rehash = parse_without_dispatch([
        "rehash", path, "id:data", "--directory", "--dry-run",
    ])
    assert rehash.command == "rehash" and rehash.directory is True
    assert rehash.targets == ["id:data"] and rehash.dry_run is True
    # Without --directory the existing file grammar is unchanged.
    add_file = parse_without_dispatch(["add", path, directory, "--id", "x"])
    assert add_file.directory is False and add_file.files == [Path(directory)]

    from runevidence import cli

    # S4-v1 is text-only and has no override flags.
    for argv in (
        ["add", path, "--directory", "--id", "data"],
        ["add", path, directory, "--directory", "--id", "data", "--json"],
        ["rehash", path, "data", "--directory", "--json"],
        ["add", path, directory, "--directory", "--id", "data", "--force"],
        ["rehash", path, "data", "--directory", "--sha256", "x"],
    ):
        with pytest.raises(SystemExit) as caught:
            cli.build_parser().parse_args(argv)
        assert caught.value.code == 2


def test_directory_authoring_flag_is_only_on_edit_routes():
    import argparse

    from runevidence.cli import build_parser

    parser = build_parser()
    sub = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
    assert set(sub.choices) == set(ROUTES)
    for name, route in sub.choices.items():
        options = {option for action in route._actions for option in action.option_strings}
        assert ("--directory" in options) is (name in ("add", "rehash"))


def test_conditions_route_grammar_only():
    # Grammar only: the handler is replaced before parsing, so nothing dispatches.
    # The historical seven-handler parse_without_dispatch helper does not admit
    # conditions; this uses the reviewed comparability handler-replacement pattern.
    from runevidence import cli

    path = "__r14_impossible_sentinel__/manifest.json"

    def denied(*args, **kwargs):
        raise AssertionError("parser dispatched a handler")

    saved = cli.cmd_conditions
    try:
        cli.cmd_conditions = denied
        ns = cli.build_parser().parse_args([
            "conditions", path, "--run", "run-1", "--patch", "-",
            "--clear", "environment", "--dry-run", "--json",
        ])
        assert ns.func is denied
        assert (ns.command, ns.path, ns.run, ns.patch, ns.clear, ns.dry_run, ns.json) == (
            "conditions", Path(path), "run-1", ["-"], ["environment"], True, True)
        # --patch stays a raw string: exactly "-" is stdin, "./-" is a file.
        dotted = cli.build_parser().parse_args([
            "conditions", path, "--run", "run-1", "--patch", "./-",
        ])
        assert dotted.patch == ["./-"]
        # No strict/redaction/summary-only surface and no value option exists.
        assert not hasattr(ns, "strict")
        assert not hasattr(ns, "redact_manifest_path")
        assert not hasattr(ns, "summary_only")
        assert not hasattr(ns, "set")
        assert not hasattr(ns, "value")
        with pytest.raises(SystemExit) as caught:
            cli.build_parser().parse_args(["conditions", path])
        assert caught.value.code == 2
    finally:
        cli.cmd_conditions = saved
