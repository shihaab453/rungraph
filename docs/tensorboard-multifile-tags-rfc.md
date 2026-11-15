# TensorBoard multi-file runs and tag filtering — design RFC (v1.14)

Status: **DESIGN PROPOSAL. NOTHING IMPLEMENTED. NOTHING AUTHORIZED BY THIS DOCUMENT.**
Slice D delivered schema 0.5, the scalar reader and the optional
`runevidence-tensorboard` adapter. Its acceptance explicitly does not authorize multi-file
logs, other summary kinds, or tag filtering. This RFC covers the first and third of those: grouping
several event files into one logical run, and selecting tags at `init` time.

Changelog entries below are historical. Where an old entry conflicts with sections 1-9, the
current sections win.

## Changes in v1.14 (round-8 findings, second seat)

- **Directory-volatile vector corrected**: `init` records only regular files, so
  `--volatile 'x.tfevents'` cannot match a directory artifact at the CLI; the vector now expects
  the delivered no-match warning and a successful write, and non-protection is asserted at the
  predicate level with a synthetic `Manifest05` directory artifact at `x.tfevents`.

## Changes in v1.13 (round-7 final seat)

- **Anchored-segment charge is conditional**: an anchored segment charges `len(seg)` only once
  it is reached; evaluation stops at the first segment that finds no match, so later segments
  are not charged (this is what keeps `*ab*b` vs `xxxx` at 4).
- **Directory-volatile vector corrected**: `init` records only regular files, so the CLI vector
  expects the delivered no-match warning and a successful write; non-protection is tested at the
  predicate level with a synthetic `Manifest05` directory artifact at `x.tfevents`.
- Process: v1.12 was applied before the fourth seat reported; v1.13 is re-frozen and confirmed
  in a fresh round against one hash.

## Changes in v1.12 (round-7 findings, first seat)

- **Window vector corrected**: `*ab*b` against `xxxx` has window 3 and two candidate positions
  (charge still 4).
- **Cache proof made real**: the 15-tag run adds `--tags '*'`, which selects all 15 tags at zero
  matching cost so the warning pass actually revisits the expensive pairs from the cache.
- **Editorials**: tag evaluation runs until the budget is exhausted; §5 quotes the event-backslash
  text; a lone-surrogate pattern is charged like any other; the directory-volatile vector uses
  `--volatile 'x.tfevents'` so nested event files cannot trigger it.

## Changes in v1.11 (round-6 findings, all four seats)

- **Budget exhaustion stops work**: after `TagPatternTooExpensive`, no further `(tag, pattern)`
  pair is evaluated; remaining files are still profile-checked.
- **Charge vector** with a non-empty last segment: `*ab*b` against `xxxx` charges exactly 4.
- **Forced-digest wording** limited to different files' single occurrences of the *same*
  `(tag, step)`; the crafted same-prefix pair is an additional refusal vector.
- **Precedence vectors**: surrogate+backslash (surrogate wins), backslash+corrupt (path wins, no
  read), cap/budget on file 1 + truncated file 2 (profile wins).
- Proof/injectivity wording: occurrence <= 9,999 is the 10,000-cap assumption; same-run
  injectivity is up to the 2^-64 truncation.
- Third-party cache-miss contract; §5 refusal scoped to the TensorBoard adapter; migration copy
  wording; path refusals added to the Discovery amendment list.
- **Volatile is regular-file only**: a directory artifact named `x.tfevents/` is not protected,
  with a vector.
- **Profile wording is per file**: each file's profile runs before any of *its* tags is selected.
- **Budget vectors**: 7 tags × 2 expensive patterns = 43,997,100 pass; 8 × 2 = 50,282,400
  refuse; 15 tags × 1 pattern still passes with `--tags '*'` added and the warning pass reading
  cached pairs (re-charging would reach 94,279,500).

## Changes in v1.10 (round-6 findings, second seat)

- **Backslash scope settled to event paths** (the adapter must parse them); generic paths are
  left to the delivered grammar. The single fixed text is
  `an event path contains a backslash; nothing was written`, used in §3.2/§5/§6/§7 and the
  vectors. Surrogate refusal remains for any path.
- **Window formula exact**: `window = len(tag) - sum(len(later segments)) - current_position`,
  and both search methods try `max(0, window - len(seg) + 1)` positions.
- **Two-pattern vector**: tag `xloss` against `*loss` and `x*` charges 4 + 1 = 5 units, both
  pairs once, and counts once toward the result caps.
- File-artifact volatile wording and per-`(tag, pattern)` D9 confirmed.

## Changes in v1.9 (round-6 findings, first seat)

- **Backslash scope widened to every discovered path** on POSIX; superseded by v1.10, which
  restored the event-only scope and its fixed text.
- **Budget wording**: "each evaluated `(tag, pattern)` pair" (stray "matching" removed); §7 adds
  the two-pattern vector; D9 says per-`(tag, pattern)` caching and "after escape processing".
- Volatile wording says every **file** artifact.

## Changes in v1.8 (round-5 findings, fourth seat)

- **Budget accounting stated exactly**: every evaluated `(tag, pattern)` pair is charged at most
  once, match or not; repeated records never re-charge; a tag matching two patterns is charged
  twice; the two-pattern vector is added and D9 says per-`(tag, pattern)` caching.
- **Name parsing removed as unnecessary**: names are opaque; uniqueness comes from the digest.
- **Forced-collision vector corrected**: two occurrences of the same `(tag, step)` in ONE file
  (occurrence 0 and 1) with a sibling file making the group multi-file, then equal forced
  digests collide; forcing two different files' single occurrences does not.
- **Extraction order**: every failure (profile, unrepresentable, too-expensive, caps, fixed-id,
  name collision) is recorded and the highest-precedence one reported after all files; volatile
  runs after extraction.
- Editorials: §7 backslash scope says **event**; volatile protects **file** artifacts only;
  pattern cap says after escape processing; keep the migration copy outside the folder; the
  `--tags '*' --tags loss` example is qualified to a present `loss`.

## Changes in v1.7 (round-5 findings, third seat)

- **Path refusal precedence/scope completed**: a surrogate in any discovered path refuses before
  any read with its fixed text and precedes the POSIX event-backslash check; the compatibility
  section lists the new refusal for a non-UTF-8 filename that slice D accepted; §6 and the
  extraction-precedence list carry it.
- **Matcher accounting unified**: `window` is the searchable span after reserving all later
  segments; both `str.find` and `?` segments try `max(0, window - len(seg) + 1)` positions and
  each position charges `len(seg)`.
- **Proof/accounting wording**: bare vs qualified names distinguished by `#` + 16 hex vs a bare
  ending in `str(step)` or ` #` + <=4 digits; result caps vs matching work separated; each pair
  (matching or not) is charged once; warning pass only reads the cache; no duplicated budget
  bullet; `TagFilter` wording; 2^-64 caveat in the "prevented" sentence; capable-adapter
  warning behavior.

## Changes in v1.6 (round-5 findings, second seat)

- **Middle-segment window defined for every segment**, not only 3-segment patterns: segment
  `S_i` (0 < i < k) searches from the current position up to
  `len(tag) - sum(len(S_j) for j > i)`, reserving every later segment. The charge uses that same
  window. Added `*a*b*c` and `*ab*b` vectors.

## Changes in v1.5 (round-5 findings, first seat)

- **Two path refusals with their own texts**: a POSIX **event** path containing `\` refuses with
  the event-path text; any discovered path containing a surrogate refuses with
  `a path is not valid UTF-8; nothing was written`. Both are pre-read and normative, not
  changelog-only.
- **`--volatile` dialect stated exactly**: `_norm` rewrites `\` to `/` in both paths and
  patterns before `fnmatchcase`, which supports `[...]`.
- Removed the leftover matcher sentence that still said "splits the pattern on `*`".

## Changes in v1.4 (round-4 findings, fourth seat)

- **Intro scope fixed** ("first and third" of the unauthorized items).
- **Name proof corrected**: the parsing argument no longer relies on the last ` [`, which a
  basename can contain; the final `#<16 hex>` identifies the artifact/occurrence and the prefix's
  last ` @ ` separates the integer step. The 2^-64 cross-run digest-collision caveat is stated.
- **Path policy**: any discovered path containing a surrogate is refused with
  `a path is not valid UTF-8; nothing was written`; §7 asserts that refusal, not "does not
  raise."
- **Migration/artifact-id wording**: leaving the standard manifest in place blocks `init`; a
  copy under another name is recorded. Event artifact ids are unchanged; a generic slug can be
  re-suffixed on a new fixed-id collision.
- **Dialect/accounting wording**: `--volatile` uses `fnmatchcase` with `_norm` rewriting `\` to
  `/`; `--tags` treats `\` as an escape. Budgeting is per `(tag, pattern)` once; repeated
  records never re-charge. Budget exhaustion is selection-only (the warning pass reads the
  cache).
- Vectors added: delete-then-add, cross-run basename/different-path vs same-layout alignment,
  forced digest collision, surrogate path refusal.

## Changes in v1.3 (round-4 findings, third seat)

- **Deterministic budget precedence**: `extract` evaluates every pattern against every distinct
  representable tag (no OR short-circuit); the CLI warning pass only reads the cache and never
  charges. Budget exhaustion therefore always happens during extraction, before caps/volatile,
  and `cmd_init` prints the fixed line.
- **Matcher window defined**: a middle segment searches only up to the suffix reserved for the
  last segment; `*ab*b` does not match `ab`.
- **Path handling tightened**: `os.fsencode` everywhere, with Windows surrogatepass; a path
  containing a surrogate or (on POSIX) a backslash is refused with a fixed error before any read.
- **Name proof and wording**: bare names cannot equal qualified ones (occurrence <= 9,999, digest
  exactly 16 hex); exact budget numbers (3,142,650 per pair, N=16); `impact-diff` "reports";
  `a\\*b` examples; stale matcher wording removed; D9 cap wording corrected.

## Changes in v1.2 (round-4 findings, second seat)

- **Normalization vector corrected**: `\` -> `/` happens on every platform; the POSIX event-path
  refusal happens before any rewritten discovery spelling is used.
- **`_nearest_run` rule stated exactly**: inference is `R` only when the nearest populated
  ancestor's complete owner set is exactly `{R}` with `R` non-None; `{R, None}` and `{R, S}`
  return `None`.
- **Short-window charge clamped**: `max(0, window - len(seg) + 1) * len(seg)`; an insufficient
  window charges zero positions and is not searched.
- **Concrete budget vector** (15 x 3,142,650 = 47,139,750 pass; 16 = 50,282,400 refuse) and
  exact `a\\*b` examples.
- Cross-run alignment requires artifact path, occurrence and name; matching basenames alone is
  insufficient. "Artifact ids unchanged" is qualified to previously supported paths.

## Changes in v1.1 (round-4 findings)

- **Path id encoding made consistent**: run and artifact ids hash `os.fsencode(path)`
  (`utf-8` with `surrogateescape`), matching §7 and never raising on a POSIX surrogateescaped
  filename.
- **Digestless name text removed from §5, §8 and D3**; the crafted collision vector is now a
  synthetic 16-hex digest collision (monkeypatch the digest helper), with the non-colliding
  crafted tag/basename pair asserted to produce distinct digest names.
- Cross-run multi-file alignment is stated as matching artifact **paths/digests**, not basenames.
- `impact-diff` wording pinned to `modified [name]` with no `removed`/`added`.

## Changes in v1.0 (round-3 findings, fourth seat)

- **Escaped wildcards are tokenized correctly**: the pattern is scanned once; `\` escapes the
  next code point, an unescaped `*` separates segments, and escaped `*`/`?` stay literal inside a
  segment. Vectors: `a\?b` matches only `a?b`, `a\*b` only `a*b`, `a\\*b` matches a literal
  backslash followed by anything.
- **POSIX backslash rule made honest**: the delivered path grammar normalizes `\` to `/` on
  every platform. Event files whose discovery path contains `\` on POSIX are refused with a
  fixed `AdapterError` instead of being silently regrouped or misread. The checker stays
  unchanged.
- **One shared tag-filter budget**: the CLI creates a core `TagFilter` (patterns, matcher cache,
  50M-unit budget) and passes it to `extract`; selection and CLI warning evaluation share the
  same counter and cache. Exhaustion raises a core `TagPatternTooExpensive`, caught by
  `cmd_init` before `AdapterError`, so the fixed text prints. The parameter is `tag_filter`, not
  `tag_patterns`.
- Fixed: Discovery collision wording (distinct directories/paths, run reserved once), exact
  `_nearest_run` owner qualification, 3.12 local vs CI 3.10/3.14, `os.fsencode` for path-derived
  ids (surrogateescape), fixed-vs-generic id caveat, "no representable supported scalar tag",
  `add` surface vs inference, capable-adapter-no-locator contract, and save-failure wording.

## Changes in v0.9 (round-3 findings, third seat)

- **Name-family property and reuse scope**: bare and qualified families cannot collide; under
  sibling insertion with existing files unchanged, no name is reused. Deleting a file and adding
  another with the same tag/step can move a bare name to the new observation (id-based tools see
  removal/addition; name-based tools see continuity). The crafted collision vector now gives
  both files both tags so each group is multi-file.
- **Matcher accounting completed**: a `str.find` segment charges
  `(window - len(seg) + 1) * len(seg)`; anchored segments charge `len(seg)`; matching is cached
  per distinct tag and pattern; the warning pass runs over distinct selected tags on the same
  budget. A core `TagPatternBudgetError` is caught by `cmd_init` before `AdapterError`, so the
  fixed text is printed. Anchoring vectors and a pinned exhaustion N are added.
- **Extraction order**: selection and charging stream per file; profile/cap/budget failures are
  held until the remaining files pass the strict profile. Full order: profile, unrepresentable,
  too-expensive, caps, id collision, name collision, volatile.
- Fixed: Windows determinism scoped to portable names; `impact-diff` wording; D7 adds
  §14/§15; unrepresentable no-match vector; `inspect.signature` failure maps to the contract
  error; package text replaced with the exact matched-checkout sentence.

## Changes in v0.8 (round-3 findings, second seat)

- **Matcher accounting made deterministic**: matching is evaluated once per unique decoded tag
  (results are cached for every pattern), a `str.find` call charges the searched tag length, a
  `?`-segment position charges the segment length, and warning re-evaluation reuses the cache.
  The budget is therefore consumed by unique tags, not by scalar records.
- **Precedence pinned**: existing-manifest refusal, adapter lookup, capability flag, `extract`
  signature contract, pattern syntax/cap, then discovery/selection.
- Verification adds a many-unique-tags budget-exhaustion vector and a cache/no-double-charge
  vector.

## Changes in v0.7 (round-3 findings)

- **Injective qualified names**: a multi-file group name becomes
  `tag @ step [basename] #<16-hex digest>` where the digest hashes the artifact id and
  occurrence. The suffix is parsed from the right and cannot be forged by a tag or basename, so
  two different observations in different runs can no longer share a name and produce a false
  `compare` change.
- **Insertion expectation corrected**: the existing id, value and locator stay unchanged, but a
  bare name can become qualified; `impact-diff` may report a `name` modification for review.
  That is the documented consequence, not "no change".
- **Package coordination**: this unreleased gate requires core and adapter from matched source
  checkouts; dependency-floor/version changes are deferred to an authorized release.
- **Matcher accounting**: the work budget charges each `str.find` call and each `?` comparison,
  including repeated warning evaluation; boundary and cross-file accumulation vectors added.
- Fixed: slice D §9 **Discovery** added to the amendment list; `impact` -> `impact-diff`
  consistently in §7/§8.

## Changes in v0.6 (round-2 findings)

- **Conditional names**: a bare `tag @ step` (+ ` #occurrence`) is kept unless the same
  `(tag, step)` appears in two or more files of one run; only then every observation in that
  group gains ` [basename]`. Cross-run `compare`/`leaderboard`/`matrix` keep aligning ordinary
  one-file logdirs; adding a sibling file can make a bare name disappear but never reassigns it
  to a different value.
- **Name uniqueness is checked**: a crafted basename/tag pair that still collides refuses with a
  fixed `AdapterError`, so names never duplicate within a run.
- **Matcher bound is honest and budgeted**: segment-based matching, worst case O(tag × pattern),
  pattern cap 1,024 code points (after escape processing), tag matching limited to at most 4,096
  UTF-8 bytes, and a fixed total work budget. Overlong/empty tags are skipped when a filter is
  active; without a filter they refuse as today.
- Fixed: §5/§7 stale ordinal text, `impact-diff` naming, Windows-only determinism wording,
  failed-run warning wording, pattern-cap unit, capability signature semantics, `inspect` in the
  privacy list, slice D §9 Discovery amendment, move the old manifest outside the folder, and
  the `_nearest_run` qualifier.

## Changes in v0.5 (round-1 findings, fourth seat)

- **Stable names**: an artifact-stable basename qualifier replaces the run-level ordinal, so
  inserting an event file ahead of an existing one cannot reassign a metric name.
  `loss @ 1 [events.out.tfevents.1] #1` is stable under sibling insertion.
- **Result-id encoding** is explicit: UTF-8 tag, ASCII decimal step/occurrence.
- **Warnings are buffered until the manifest is saved**, so a backup/save failure prints no
  `--tags` warning; capability is checked by signature introspection, not by catching
  `TypeError`; the capability contract is TensorBoard-locator-scoped.
- **POSIX backslashes are literal** (no unconditional `\ -> /` normalization); Windows-style
  discovery is normalized only on Windows.
- **Matcher bound stated**: greedy last-star, O(tag + pattern) time, pattern capped at 4,096
  code points; `*a*b`/`aab` and adversarial vectors added.
- Texts unified (`no supported scalar tag`, `{adapter.name!r}`, "Nothing was written.").
- Privacy lists every recorded-name display; the slice D amendment enumerates §9/§13/§14/§15.
- Verification adds the insertion vector, non-ASCII/` @ `/`#`/NUL names, per-run state,
  real-fixture pins, privacy across compare/matrix/leaderboard/details, `_nearest_run`, claims
  with a broken file, old manifest + impact, internal `TypeError`, POSIX backslash, and the
  adapter/core requirement.

## Changes in v0.4 (round-1 findings, third seat)

- **Names resolve the consumer ambiguity**: the run-level ordinal makes every result name unique
  within a run, so `conflicting-evidence`, `compare`, `leaderboard` and `matrix` never see a
  duplicate name; the series appear as distinct names and need no consumer change.
- **Pattern matching is full-string and linear**: `loss` does not match `train/loss`; `?` is one
  code point; `*` includes newline; patterns are evaluated independently (no OR short-circuit
  false warning) and a scalar is budgeted once; the matcher is a linear glob, not a regex.
- **Warning text uses `ascii(pattern)`**: one escaped line; quotes, backslashes, newlines and ESC
  cannot forge output.
- **Strict profile and caps always apply**: every event file is read and parsed before tag
  selection; filtering never hides trailing corruption or an over-cap file.
- **Identity/collision and compatibility**: truncated hashes remain conditional on global
  collision checks; a run id is reserved once; regeneration changes run ids even at one file
  per directory, and old manifests are kept as regression fixtures. `impact` sees identity
  changes as removals/additions.
- **Privacy wording** restores slice D's recorded-name exception and names the pattern warning
  as the intentional exception.
- Verification adds budget-across-files vectors, corrupted all-filtered, shuffled discovery,
  Windows/absolute-root determinism, synthetic collisions, long-pattern linearity, third-party
  capable/legacy adapters, and warning-escaping cases.

## Changes in v0.3 (round-1 findings, second seat)

- **Event volatile refusal is by path, not by selected results**: every event artifact
  (`basename` contains `.tfevents`) is protected from `--volatile`, even when it is empty,
  header-only, or fully filtered out.
- **Warning contract pinned**: `cmd_init` emits `--tags` no-match warnings after extraction and
  after the event-volatile refusal, before `_mark_volatile`, in argument order including
  repeats, and only on the successful write path. No warning channel is added to adapters.
- **Path normalization and ordering** are explicit: `extract` normalizes every `rel` and sorts
  event files itself, so caller-supplied discovery order cannot change ids, names or runs.
- **CLI byte pin and parser-contract update** added to the implementation surface; the `add`
  run-inference change (one run per directory) is recorded.
- Verification adds `compare`/`leaderboard`/`matrix`, filtered/empty volatile, escape and
  regex-metacharacter vectors, the 16 MiB selected boundary, and pinned real multi-file
  fixtures.

## Changes in v0.2 (round-1 findings, first seat)

- **Unique names within a run**: results that share `(tag, step)` across files get a run-level
  ordinal in the **name**, so `conflicting-evidence` cannot fire; the locator `occurrence`
  stays per file.
- **Migration path corrected**: `init --force` refuses an existing 0.5 manifest. §5 now gives
  the real move-aside/regenerate path and lists every id class that changes.
- **Filter grammar vs unrepresentable tags**: every UTF-8-decodable tag is matchable, including
  empty and over-4,096-byte tags; a selected unrepresentable tag keeps the delivered refusal.
  Only invalid-UTF-8 tags are skipped when a filter is active. `--tags '*'` is no longer called
  equivalent to no filter.
- **Slice D amendment stated**: this RFC amends slice D §2, §9 ("Interface"), §13 and §16;
  the matcher and pattern validation live in core, and the CLI derives no-match warnings from
  the returned manifest (no warning channel).
- Clarified: run ordering, the `--volatile` interaction, two glob dialects, code-point matching,
  lone surrogates, terminal-safe pattern quoting, error precedence, privacy wording, third-party
  adapter contract, hidden consumers, and the expanded verification matrix.

## 1. Problem and evidence

The delivered adapter maps **one `.tfevents` file to one run**. TensorBoard groups by **log
directory**: a trainer that restarts, or several workers writing into the same logdir, produces
several event files for one logical run. A user comparing "the run" then sees one run per file,
split series, and duplicated names. Grouping fixes the run identity and colocation; it does not
merge or deduplicate values, and it does not reproduce TensorBoard's out-of-order-step
discarding. A series split across files remains several results, one per file.

The adapter also records **every** supported scalar. A real training log carries hundreds of
tags; a manifest usually needs a handful. There is no `--tags` filter, so users must edit the
generated manifest or accept a 10,000-result cap that unrelated tags can consume.

The schema does not change: a `Run` already groups artifacts and results, a result already names
its artifact, and `TensorboardLocator` already carries tag/step/occurrence. Both features are
adapter/CLI behavior.

## 2. Scope and non-goals

In scope: group event files by log directory into one run; `init --tags` for the TensorBoard
adapter; stable identities, names, ordering, budgets and fixed texts; compatibility and
verification.

Out of scope: schema changes, checker changes, new value kinds, value merging/deduplication
across files, run-level occurrence, plugin filtering, TensorBoard UI/database access, YAML,
parity claims.

## 3. Run grouping

### 3.1 Directory rule

An **event file** is a regular file whose basename contains `.tfevents` (case-insensitive) —
the delivered slice D §4.4 predicate. Each directory that directly contains at least one event
file becomes **one run**. Event files in nested directories belong to their own directory's
run; a subdirectory never joins its parent's run. A directory named `x.tfevents/` is still a
directory: only event files inside it form its run; the directory itself is not a file artifact.

The run's recorded name is the directory's relative POSIX path (`PurePosixPath(rel).parent`),
`.` for the experiment root. Run names are recorded data; a result's name is derived below.

Non-event files remain ordinary artifacts with generic ids and no `run_id`. A directory with no
event file produces no run. TensorBoard's own predicate is `"tfevents" in basename`,
case-sensitive; ours stays the slice D predicate, and this difference is recorded.

### 3.2 Identity and ordering

- `dir_rel` is `.` for root, otherwise the relative directory (`a/b`).
- Run id: `tb-run-<16 hex of SHA-256(os.fsencode(dir_rel))>` (`utf-8` with
  `surrogateescape` on POSIX, `surrogatepass` on Windows).
- Artifact id: `tb-art-<16 hex of SHA-256(os.fsencode(file path))>` (unchanged).
- Each event artifact gets `run_id` = its directory's run id.
- `iter_files` already yields POSIX `/` paths. `extract` derives
  `dir_rel = PurePosixPath(path).parent.as_posix()`, sorts all paths, and processes them in
  that order for runs, artifacts, results and generic slug suffixing. Caller-supplied
  `Discovery.files` order cannot change ids, names or output order.
- The delivered path grammar normalizes `\` to `/` on every platform before matching the
  `.tfevents` predicate and deriving paths; the checker and predicate are unchanged. On POSIX a
  filename may legally contain `\`, but an **event** path containing `\` cannot be addressed
  unambiguously by this grammar. On POSIX an event discovery path containing `\` is refused
  before any read with `an event path contains a backslash; nothing was written` (exit 2).
  Generic paths containing `\` are left to the delivered grammar. A discovered path containing a
  surrogate (undecodable bytes) is refused before any read with
  `a path is not valid UTF-8; nothing was written`. The adapter raises `AdapterError` with the
  inner text; the CLI's existing adapter-error wrapper prefixes it. On Windows, discovery paths
  are already `/` or are normalized by the same grammar.
- One `Run` per directory with events, ordered by the directory's first event-file path using
  code-point string order of POSIX paths (`a-b/` sorts before `a/`).
- A fixed-vs-fixed id collision (two different paths hashing to the same id, or two run ids
  colliding) remains the single `AdapterError`:
  `a generated id collides with an existing id; nothing was written`.
- Linux permits `Run/` and `run/` as two directories (two runs); Windows/macOS cannot. This is
  recorded, not normalized.

### 3.3 Result identity, names and occurrence

Two files in one run can hold the same `(tag, step, occurrence)`, so:

- **Result id** includes the artifact. The payload is
  `UTF-8(run_id) + 0x00 + UTF-8(artifact_id) + 0x00 + UTF-8(tag) + 0x00 + ASCII(str(step)) +
  0x00 + ASCII(str(occurrence))`, hashed with SHA-256 and truncated to 16 hex:
  `tb-res-<16 hex>`.
- `occurrence` is **per artifact (file)**: the zero-based position among supported scalars with
  the exact `(tag, step)` in that file, in record order then `Summary.Value` order.
- **Result name** is run-unique and preserves cross-run alignment where it can:
  - group a run's observations by `(tag, step)`;
  - if the group comes from exactly one file, name each observation
    `f"{tag} @ {step}"` plus `f" #{occurrence}"` when `occurrence > 0` (slice D's name);
  - if the group comes from two or more files, name every observation in that group
    `f"{tag} @ {step} [{basename}] #<digest>"`, where `digest` is the first 16 hex of
    `SHA-256(UTF-8(artifact_id) + 0x00 + ASCII(str(occurrence)))`.
  - `basename` is the event file's name in the run directory, unique within the run. Names are
    opaque; nothing parses them back. The digest makes the suffix injective up to the 2^-64
    truncation: a tag or basename can contain ` @ `, `[`, `]`, `#` or a hex-looking substring, but
    two different `(tag, step, artifact, occurrence)` tuples cannot produce the same name unless
    their 16-hex digests collide. A qualified name ends with `#` + 16 hex; a bare name ends with
    `str(step)` (which never contains `#`) or ` #` + at most four digits, since the 10,000-result
    cap keeps occurrence <= 9,999; the two string families cannot meet. Within a run the name
    check catches an actual duplicate; truncated-digest collisions across runs remain possible at
    2^-64 and the per-run check does not catch them.
  Adding a sibling file can make a formerly bare name disappear and become a qualified name, but
  it never reassigns a bare name to a different observation. Result ids do not change. A
  single-file logdir (the Keras `train/`/`validation/` case) keeps alignable bare names across
  runs; multi-file groups align across re-inits only when the artifact path (and therefore the
  digest) is the same.
- After names are built, `extract` checks `(run_id, name)` uniqueness as a safety net for a
  truncated-digest collision. A duplicate refuses with
  `a generated result name collides within a run; nothing was written`; it is never written
  with duplicate names. Cross-run false alignment is prevented up to the 2^-64 truncated-digest caveat, not by
  this check.
- Results are emitted in discovery order: event files sorted by path, then record order, then
  `Summary.Value` order.
- The max-length argument for the id payload: artifact id and run id are fixed-length and
  contain no NUL; step/occurrence are decimal; a tag may contain NUL but adding it cannot make
  two distinct tuples encode identically. This is asserted by tests, not assumed.
- Because names are unique per run, the graph's `conflicting-evidence` check and the
  `compare`/`leaderboard`/`matrix` duplicate-name refusals never fire on a restart fixture. A
  multi-file `(tag, step)` group appears as distinct qualified names
  (`loss @ 1 [a.tfevents] #<digest>`, `loss @ 1 [z.tfevents] #<digest>`); a single-file group
  keeps the bare name.
  This RFC does not merge observations and does not change those consumers. Under sibling
  insertion with existing files unchanged, no name is reused for a different observation; at
  worst a bare name disappears when a second file joins its group. Deleting an existing file and
  adding another with the same `(tag, step)` can move a bare name to the new observation under a
  new id: id-based tools (`impact-diff`, `review-diff`) see a removal plus an addition, while
  name-based tools (`compare`, `matrix`, `leaderboard`) see the series as continuous.

Every grouping fixture must end with `init` then `validate` exiting `0`.

## 4. Tag filtering

### 4.1 CLI surface

`runevidence init ... --tags PATTERN` (repeatable). The flag is valid only with an adapter that
declares tag-filter support; otherwise a fixed error, exit 2, nothing written.

No `--tags` means every supported scalar, exactly today's behavior. `init`-only; `add`,
`rehash`, `migrate`, `conditions` and `validate` are unchanged. A pattern starting with `-`
must be written `--tags=-x`; the help text says to quote patterns so the shell does not expand
`*`, `?` or `\`.

### 4.2 Adapter API and ownership

- `Adapter` gains `supports_tag_filters: bool = False`.
- `TensorboardAdapter` sets it true; its `extract` accepts keyword-only
  `tag_filter: TagFilter | None = None`. `TagFilter` is a core object holding the patterns, the
  per-`(tag, pattern)` result cache and the 50M matching-work budget. Hydra/MLflow signatures are
  unchanged.
- The **pattern grammar and validation live in core** (a small shared module used by the CLI and
  the adapter), so `init` validates patterns before discovery and before any read.
- The CLI passes patterns only when the adapter declares support.
- The CLI checks capability by inspecting the bound `extract` signature for an explicit
  `tag_filter` parameter (keyword-only or positional-or-keyword). If `inspect.signature`
  itself raises `ValueError`/`TypeError`, that maps to the same fixed contract error. This is
  not done by catching `TypeError`
  (a `TypeError` raised inside a valid adapter must surface as its own failure, not as "cannot
  apply"). A bare `**kwargs` does not count. A declared capability without the explicit
  parameter prints
  `ERROR: adapter {adapter.name!r} cannot apply --tags. Nothing was written.` (exit 2).
- The capability contract is scoped to adapters that emit schema 0.5 `TensorboardLocator`
  results: the CLI derives no-match warnings from those locator tags. An adapter that opts in
  without TensorBoard locators is out of contract. A capable third-party adapter that does not
  call `TagFilter.matches` for every pattern/tag pair is likewise out of contract; the CLI never
  performs an uncharged hidden match.
- No warning channel is added to `extract`. No warning prints when nothing is written: warnings
  are buffered and printed only after `save_manifest` succeeds.

This **amends slice D §2, §9 and §13 and supersedes §16** for multi-file runs and tag filtering
only. The affected §9 bullets are **Interface** (new keyword, signature introspection, no
warning channel), **Discovery** (the "does not create folder runs" paragraph, the fixed-id
reservation rule and the new path refusals), **Identity** (one run per directory, run id/name, artifact-bearing result
id/name), **Selection v1** (all scalars became a filter), **Budget** (selected scalars only) and
**Volatile event targets** (event path, not targeted results). §14's "one-file-one-run and no tag filtering" risk and §15 D7's "all scalars in v1" are
stale after this gate. Other summary kinds and parity claims remain unauthorized.

### 4.3 Pattern grammar

A pattern is a Unicode string matched against the **decoded exact tag**:

- `*` matches zero or more Unicode **code points** (including `/` and the empty string);
- `?` matches exactly one code point;
- the pattern is scanned once: `\` escapes the next code point; a trailing `\` is invalid; an
  **unescaped** `*` is a wildcard; an escaped `*` or `?` is a literal code point;
- every other code point is literal; matching is case-sensitive; no NFC/NFD normalization;
- no character classes, no brace expansion, no path semantics (this differs from `--volatile`,
  which runs its delivered `_norm` — rewriting `\` to `/` in both path and pattern — before
  `fnmatchcase`, and therefore supports `[...]`; the help text says so);
- the matcher is core code, not `fnmatch` and not a regex. It matches the segments (split on
  unescaped `*`) in order, with the first segment anchored at the
  start and the last at the end. Segment `S_i` (0 < i < k) searches from the current position up
  to `len(tag) - sum(len(S_j) for j > i)`, reserving every later segment; escaped `*`/`?` live
  inside segments as literals. `extract` evaluates every
  pattern against every distinct representable tag until the budget is exhausted (§4.4), with no
  OR short-circuit, so the CLI warning pass is a cache read and never charges. A `?`-free segment uses `str.find`; a segment with `?` has a fixed length and is
  compared position by position. The worst case is O(len(tag) × len(pattern)); the design bounds
  it: the pattern is capped at 1,024 Unicode code points counted **after** escape processing, a
  tag is matched only when it is 1..4,096 UTF-8 bytes, and `init` has a fixed total
  matching-work budget (50,000,000 cost units).
  A `?`-free segment means no unescaped `?`. For a middle segment starting at current position
  `p`, `window = len(tag) - sum(len(later segments)) - p`; both `str.find` and `?` segments try
  `max(0, window - len(seg) + 1)` positions and each tried position charges `len(seg)`; an
  insufficient window charges zero positions and is not searched. An anchored segment charges
  `len(seg)` once it is reached; evaluation stops at the first segment that finds no match, so
  later segments are not charged. Matching is evaluated **once per distinct decoded `(tag, pattern)` pair** and
  cached by that key; every evaluated `(tag, pattern)` pair is charged at most once, match or not;
  scalar records that repeat a tag never re-charge it; a tag matching two patterns is charged
  twice. The CLI warning pass
  runs over distinct selected tags and shares the cache and budget. The budget is therefore
  consumed by distinct tags and patterns, not result count. The adapter raises a core
  `TagPatternTooExpensive`; `cmd_init` catches it before `except AdapterError` and prints
  `ERROR: --tags pattern is too expensive. Nothing was written.` This is a documented bounded
  cost, not a strict linearity claim.
- matching is full-string: `loss` does not match `train/loss`; to match the suffix use
  `*loss` or `train/*`.

An empty pattern (`--tags ""`) is invalid. A pattern containing a lone surrogate (possible from
`argv` on POSIX) never matches and must not crash; it is evaluated and charged against each
distinct tag like any other pattern. Patterns are ORed for selection; a tag is
selected when at least one matches. Warnings are computed by evaluating **every** pattern
independently against all selected candidates; OR short-circuiting must not suppress a
per-pattern warning. `--tags '*' --tags loss` warns for neither when a `loss` tag exists; a
matching scalar counts once toward the 10,000-result/16 MiB caps, while matching work charges
each evaluated `(tag, pattern)` pair once.

Filtering is decided per tag; it never changes occurrence, because occurrence counts only
supported scalars and selection is on the tag. A selected scalar is emitted; an unselected one
is ignored entirely (no result, no tag bytes, no unrepresentable-tag refusal).

**Volatile protection is per event artifact, not per selected result.** Every **regular-file**
artifact whose basename contains `.tfevents` is protected: `init --volatile` matching one refuses with
`an event artifact cannot be volatile; nothing was written`, regardless of whether the file is
empty, header-only, or fully filtered out. The delivered `_event_artifact_is_volatile` predicate
changes from "artifact targeted by a TensorBoard result" to "event artifact in a `Manifest05`"
and must keep working for a run with zero results.

A pattern that selects no scalar in the whole `init` (across all directories, not per run)
produces one warning per pattern occurrence, in argument order including repeats, using
`ascii(pattern)` (`f"WARNING: --tags pattern {ascii(pattern)} matches no representable supported scalar tag"`):
quotes, backslashes, newlines, ESC and non-ASCII code points are escaped into one line and
cannot forge output. It is not an
error. If every pattern selects nothing, the manifest is still written with runs/artifacts and
zero results.

Warnings are buffered by `cmd_init` after extraction and after the event-artifact volatile
refusal, then printed only after `save_manifest` succeeds, in argv order, just before the
`Wrote …` summary. A refused `init` (adapter, pattern, volatile or backup failure) and a failed
save print **no `--tags` warning**; their existing output lines are unchanged (a backup failure
prints two lines, a save failure can add a backup note). `Adapter.extract` never prints, and a
successful write with no unmatched pattern prints no warning.

### 4.4 Unrepresentable tags and budgets

Tags are UTF-8-decoded to be matched. A tag is **representable** when it is valid UTF-8,
non-empty and at most 4,096 UTF-8 bytes.

- **Without a filter**, any unrepresentable event tag keeps today's refusal:
  `a scalar tag cannot be represented in schema 0.5; nothing was written`.
- **With a filter**, matching is defined only over representable tags. An invalid-UTF-8, empty
  or over-4,096-byte tag cannot be selected by any pattern and is skipped (no result, no
  refusal, no tag bytes). This keeps matching bounded and is deliberate: a filter selects among
  values schema 0.5 can represent. `--tags '*'` is therefore **not** equivalent to no filter; it
  selects only representable tags. A filtered log with an unrepresentable tag silently omits
  that tag; the alternative (matching arbitrary-length tags) has unbounded cost.

The per-manifest budget (10,000 results, 16 MiB of tag UTF-8 bytes) counts only **selected**
scalars, so filtering can bring a large log under the cap. Budget failure text is unchanged.

**Filtering never weakens the file profile.** Every event file is read through the protected
snapshot and parsed under the strict TensorBoard profile and 128 MiB cap before any of its tags
is selected. A truncated file, CRC failure, trailing bytes, over-cap file, or malformed protobuf
refuses the whole `init` even when every tag would be filtered out. Selection and budget charging
stream per file; the reader does not retain every supported scalar from every file. Extraction
records **every** failure (profile, unrepresentable, too-expensive, caps, fixed-id collision,
result-name collision) and continues with the remaining files; after all files it reports the
highest-precedence one. Once the matching budget is exhausted, no further `(tag, pattern)` pair
is evaluated, but every remaining file is still profile-checked. Volatile is checked after
extraction. The precedence is: path refusal (a surrogate on any path, then a POSIX event
backslash, before any read), strict profile, unrepresentable tag, too-expensive pattern, caps,
fixed-id collision, result-name collision, volatile. This keeps the profile authoritative even
when an earlier file already exhausted the result cap.

### 4.5 Privacy

A filter pattern is user input and can itself be a tag. It is echoed only in the no-match
warning (`ascii`-escaped), never in findings. The generated manifest records only selected tags.

Slice D's privacy promise is unchanged and has the same exceptions: findings never print a tag
read from the log, but a recorded `Result.name` is recorded data and may contain the tag. That
name is printed wherever results are displayed: `run`, `claim`, `lineage`, `compare`, `matrix`,
`leaderboard`, `details`, `inspect`, `rehash` and `impact`. The `--tags` warning echoes the
user-supplied pattern,
`ascii`-escaped. No other human path prints a tag read from the log.

## 5. Compatibility and migration

- Schema 0.5, the reader and the checker are unchanged; there is no schema migration.
- Existing generated manifests remain **valid** (run names and ids are recorded data), but their
  semantics differ after regeneration.
- **The regeneration path is not `init --force`**: the delivered CLI refuses an existing 0.5
  manifest with `INIT_0_5_REFUSAL`. The real path is: move the manifest **outside the
  experiment folder** (keep that copy outside the folder). Leaving the standard `runevidence.json` in place blocks
  `init`; a copy under another name would be recorded as an ordinary artifact. Then run `init`
  again and reapply hand-written evidence, claims, relationships, conditions and first-class
  values. There is no id remapping tool.
- Changed id classes on regeneration: **run id** (file hash -> directory hash, so it changes
  even when the folder has one file per directory), **run name** (file path -> directory path),
  **result id** (adds the artifact component), and **result name** (`tag @ step` becomes
  `tag @ step [basename] #<16-hex digest>` when a `(tag, step)` group spans several files;
  locator `occurrence` stays per-file and is hashed into the digest. **Event** artifact ids are
  unchanged for previously supported paths; a path newly refused under the backslash/surrogate
  rules has no id, and a **generic** slug can be re-suffixed if a new fixed id collides with it. Evidence/claims/relationships that cite
  old `tb-run-…` or `tb-res-…` ids will dangle; `impact-diff`/`review-diff` report the changed
  identities as removals/additions.
- Truncated 16-hex ids remain conditional on the existing global collision checks: all fixed
  ids are reserved once against project ids, other fixed ids and generic slugs; a collision is
  the fixed `AdapterError`, never a silent duplicate. The same directory run is reserved once,
  no matter how many event files it holds.
- Keep an old-manifest regression fixture (a slice D one-file-per-directory manifest) and assert
  it still loads and validates after the schema/reader/checker stay unchanged.
- Hidden consumers that must be updated in the implementation gate: the adapter `description`
  and module docstring, `docs/adapters.md`, `docs/schema-0.5-tensorboard.md`, the adapter README,
  and tests that pin file-hash run ids / file-path run names.
- `edit.py::_nearest_run` infers a run only when the nearest populated ancestor's **complete**
  owner set is exactly `{R}` with `R` non-`None`; `{R, None}` and `{R, S}` both return `None`.
  With two event files now sharing one run owner, `runevidence add` may infer that run where the
  old two-owner set returned `None`. The `add` command surface is unchanged; only this inference
  can differ.
- Adding `--tags` to `build_parser` changes `src/runevidence/cli.py` bytes. The implementation
  gate rotates `CLI_HASH` (current pair moved into `HISTORICAL_26_ROUTE_CLI_HASH`), keeps the
  `build_parser` AST call-set constraint, and updates the parser help tests.
- Third-party adapters are unaffected unless they opt into `supports_tag_filters`.
- A folder containing a non-UTF-8 (surrogate) filename, which slice D could record as a generic
  artifact, now refuses `init` **with the TensorBoard adapter** with
  `a path is not valid UTF-8; nothing was written`. A folder whose event path contains `\` on
  POSIX refuses too with `an event path contains a backslash; nothing was written`. Both are new
  refusals, listed here as compatibility changes.

## 6. Fixed texts and precedence

- Unknown adapter: existing message, unchanged.
- Existing-manifest refusal: existing `INIT_0_2/0_3/0_4/0_5_REFUSAL`, before `--tags` handling.
- Unsupported adapter: `ERROR: adapter {adapter.name!r} does not support --tags. Nothing was written.`
- Invalid pattern (including a cap overrun): `ERROR: invalid --tags pattern. Nothing was written.`
- Pattern work budget exhausted: `ERROR: --tags pattern is too expensive. Nothing was written.`
- Adapter contract failure: `ERROR: adapter {adapter.name!r} cannot apply --tags. Nothing was written.`
- Result-name collision: `a generated result name collides within a run; nothing was written`
- POSIX event path with a backslash: adapter error `an event path contains a backslash; nothing was written`
  (the CLI's adapter wrapper prefixes it)
- Path with a surrogate: adapter error `a path is not valid UTF-8; nothing was written` (the
  CLI's adapter wrapper prefixes it)
- No-match warning: `WARNING: --tags pattern {ascii(pattern)} matches no representable supported scalar tag` (one escaped line).
- Existing profile/tag/budget/volatile texts are unchanged.

Precedence: existing-manifest refusal, then adapter lookup, then the `supports_tag_filters`
flag, then the `extract` signature contract, then pattern syntax/cap validation, then
discovery/selection. Nothing is written for any argument, adapter or pattern error; a save
failure follows the existing backup-note behavior.

## 7. Verification plan

- **Grouping**: two files in one directory -> one run/ two artifacts; two directories -> two
  runs; nested directories separate; root files -> run `.`; a non-event directory -> no run; a
  directory named `x.tfevents/`; non-ASCII directory names; run ids/names stable across
  discovery order, and across Linux/Windows for portable names (no literal backslash and no
  case-only sibling directories), where CI compares manifests byte-for-byte. The restart
  fixture also passes `compare`, `leaderboard` and `matrix` (no ambiguity error).
- **Restart vector (B1)**: two files in one logdir where the resumed writer re-logs step 40 with
  a different value; names are run-unique, result ids distinct, `init` then `validate` exits 0.
- **Identity**: same `(tag, step, occurrence)` in two files -> two ids; id-payload uniqueness
  including a NUL in a tag; non-ASCII tags; per-file occurrence; deterministic re-init. Tags
  containing ` @ `, `#` and `]` never duplicate within a run: names are unique or refused. Two
  different runs may both use a bare name. A crafted pair — both files carry `(loss, 1)` and
  `(loss @ 1 [q.tfevents], 5)`, with basenames `q.tfevents] @ 5 [r.tfevents` and `r.tfevents` —
  produces distinct `#<digest>` names because the artifacts differ. The forced-collision test
  uses two occurrences of the same `(tag, step)` in ONE file (occurrence 0 and 1) plus a sibling
  file making the group multi-file, then forces those two digests equal; the common
  `tag @ step [basename]` prefix plus equal digest triggers the `(run_id, name)` refusal and
  writes nothing. Forcing two different files' single occurrences **of the same `(tag, step)`**
  to the same digest does not collide (different basename prefixes); forcing the crafted
  same-prefix pair is an additional refusal vector. Property tests cover the bare-vs-qualified families and
  insertion with existing files unchanged.
- **Insertion stability**: baseline with `logs/z.tfevents`, then add `logs/a.tfevents` and
  re-init. While the group is single-file, `z` is `loss @ 1`; after the addition the bare name
  disappears and the group becomes `loss @ 1 [a.tfevents] #<digest>` /
  `loss @ 1 [z.tfevents] #<digest>`. Under sibling insertion with existing files unchanged, no
  name is reused for a different observation; `z`'s id, value and locator are unchanged.
  `impact-diff` reports `modified [name]` with no `removed` or `added` result for `z`; that is
  expected and documented, and the claim is flagged for review rather than silently accepted. A
  single-file cross-run fixture (`train/`, `validation/`) keeps alignable bare names for
  `compare`/`matrix`/`leaderboard`.
- **Delete-then-add**: baseline `logs/z.tfevents`; delete it and add `logs/a.tfevents` with the
  same `(tag, step)`; the old result id is removed, the new one added, the former bare name is
  continuous, `impact-diff` shows a removal and an addition, and `validate` exits 0.
- **Cross-run alignment**: `trainA/{e1,e2}.tfevents` and `trainB/{e1,e2}.tfevents` share
  basenames but not relative paths, so qualified names do not align; two checkouts with the same
  relative layout (different absolute roots) do align.
- **Budget/caching**: one pattern `*` + `a`×1020 + `?b*`, distinct tags `a`×4095 + one character
  from `c`..`r`; each costs 3,142,650 units. 15 tags (47,139,750) pass; 16 (50,282,400) exhaust
  the shared counter and refuse with the too-expensive text. Tags are spread across files to
  verify accumulation; the adapter selection and CLI warning pass share the counter/cache;
  repeating the same tag across thousands of scalar records charges once; tag `xloss` against
  `*loss` and `x*` charges 4 + 1 = 5 units (each pair once) and counts once toward the
  result/byte caps. Two distinct expensive patterns (`...?b*` and `...?c*`) over 7 tags cost
  7 × 2 × 3,142,650 = 43,997,100 and pass; 8 tags cost 50,282,400 and refuse. The 15-tag
  single-pattern run (47,139,750) still passes when `--tags '*'` is added: `*` selects all 15
  tags at zero matching cost, so the warning pass revisits the expensive pairs from the cache;
  re-charging them would reach 94,279,500 and refuse, which proves the cache is shared.
- **Full-string/bounded**: `loss` does not match `train/loss`; `*loss` and `train/*` do; `?`
  counts one code point (including a multibyte character); `*` matches newline. Anchoring:
  `*ab` matches `abab`, `a*a` does not match `a`, `ab*` does not match `xab`, and `*a*b` matches
  `aab`. The middle-`?` worst case `*` + `a`×1020 + `?b*` (1,024 code points) against
  `a`×4,096 is measured; the pinned N is in the budget bullet. A middle segment's window
  excludes the suffix reserved for later segments, so `*ab*b` does not match `ab` and `*a*b*c`
  reserves `b` and `c` at each step. `*ab*b` against `xxxx` has a middle-segment window of 3,
  two candidate positions, and charges exactly 4 units. A pattern over 1,024 code points after
  escape processing is invalid.
  A tag over 4,096 bytes is skipped under a filter and refused without one, and a pattern that
  would only match such a tag produces the no-match warning.
- **Escaped wildcards**: `a\?b` matches only `a?b` (not `axb`); `a\*b` matches only `a*b` (not
  `axb`); `a\\*b` matches `a`, a literal backslash, any sequence, then a final `b`
  (`a\b`, `a\xb`, `a\XYZb` match; `ab`, `a\XYZc` do not); a trailing `\` is invalid.
- **Signature edge**: an `inspect.signature` failure (ValueError/TypeError) maps to the fixed
  contract error, not a traceback.
- **Warning evaluation**: `--tags '*' --tags loss` warns for neither when a `loss` tag exists; with only
  `train/loss`, both `loss` and `eval` warn, while `--tags '*loss' --tags eval` warns for `eval`
  only;
  repeats warn once per occurrence; a pattern containing quotes/backslash/newline/ESC is
  `ascii`-escaped on one line.
- **Strict profile under filtering**: an all-filtered truncated, trailing-byte, CRC-failing or
  over-128-MiB file still refuses `init` with the slice D text; no manifest or backup is
  written.
- **Discovery/root determinism**: shuffled `Discovery.files` and different absolute roots
  produce byte-identical manifests and ids on POSIX. The delivered grammar normalizes `\` to `/`
  on every platform; on POSIX an **event** path containing `\` refuses with
  `an event path contains a backslash; nothing was written`, and any path containing a surrogate
  refuses with `a path is not valid UTF-8; nothing was written`, both before any read. The cross-platform byte-identical check
  uses portable names. Adding a new event file leaves every existing **fixed** `tb-` id
  unchanged; a generic slug can still be re-suffixed if the new fixed id collides with it
  (delivered `_unique` behavior). Path-derived ids hash `os.fsencode(path)`; a path with a
  surrogate is refused, never hashed.
- **Collisions**: a synthetic fixed-id collision refuses; one directory run is reserved once
  regardless of file count.
- **Filtering**: exact, `*`, `?`, escaped metacharacters, `[]` treated literally (vs
  `--volatile`), case sensitivity, OR, `--tags ""`/trailing `\` errors, escaped backslash and
  a literal backslash before `*`, regex metacharacters (`.+()[]{}^$|`) as literals, leading `-`
  via `=`, lone surrogate, unsupported-adapter error, contract `TypeError`, no-match warning
  with ascii quoting, repeated patterns, all-filtered -> zero results, `--tags '*'` with
  empty/over-long/invalid-UTF-8 tags (skipped under a filter, refused without one), selected-only
  10,000 and 16 MiB boundaries (16 MiB + 1 byte fails), occurrence unchanged by filtering.
- **Precedence/path**: a surrogate path plus a POSIX event backslash prints the surrogate text;
  an event path with a backslash plus a corrupt file prints the path text with no read; file 1 over the
  result/budget limit plus file 2 truncated prints the profile text.
- **Volatile interaction**: `init --tags no_match --volatile '*.tfevents*'` still refuses with
  the event-artifact text and writes nothing; the same holds for an empty/header-only event
  file. `init --volatile 'x.tfevents'` writes with the delivered no-match warning: `init`
  records only regular files, so no directory artifact exists to protect. The predicate is
  unit-tested instead: a `Manifest05` carrying an `artifact_type="directory"` artifact at
  `x.tfevents` is not protected by `['x.tfevents']`. A filtered file that is written (no
  `--volatile`) still yields its run with zero results.
- **Regression**: adapter tests updated for directory runs/artifact-bearing ids; slice D
  checker/reader tests unchanged; an old one-file-per-directory manifest fixture still loads and
  validates; full suites locally on 3.12 and in CI on 3.10/3.14, plus pydantic 2.6.
- **Adapters**: a third-party adapter declaring the capability receives the keyword and works;
  a legacy adapter receives no new keyword and still works; declaring support without accepting
  it produces the fixed contract error, not a traceback.
- **Real fixtures**: checked-in TensorBoard bytes with SHA-256, byte-size and provenance pins
  (same discipline as slice D): an overlapping-step two-file logdir, concurrent writers
  differing by host/pid suffix, Keras `train/`/`validation/`, and a `*.profile-empty` file.
- **Privacy/consumers**: a canary tag in a recorded result name across `run`, `claim`,
  `lineage`, `compare`, `matrix`, `leaderboard`, `details`, `inspect`, `rehash` and `impact`;
  `edit._nearest_run`
  with one event and one non-event file in a directory; a claim resting on a run with one broken
  event file; an old manifest with claims/relationships, regenerated, with `impact-diff`
  showing removals/additions (not `impact`).
- **API edge**: a capable adapter that raises `TypeError` internally surfaces its own failure,
  not "cannot apply --tags"; a legacy adapter receives no keyword; a capable adapter without
  TensorBoard locators is out of contract — the CLI would see no locator tags and warn for every
  pattern, and the adapter must document that it emits TensorboardLocator results.
- **Normalization/backslash**: `\` -> `/` normalization happens on every platform exactly as
  the delivered grammar does; on POSIX an **event** path containing `\` (directory part or
  basename) refuses with `an event path contains a backslash; nothing was written` **before** any
  rewritten spelling is opened; generic paths are left to the delivered grammar.
- **Package coordination**: this gate is unreleased: core and adapter must be installed from
  matched source checkouts of the same commit. The adapter keeps
  `runevidence>=0.1.1,<0.2.0`; that range alone would admit today's 0.1.1 core, which lacks the
  shared matcher, and loading the adapter would fail even without `--tags`. Raising the floor is
  deferred to an authorized release. `test_entry_point_version_and_dependencies` keeps its
  current pin assertions unchanged; an explicit mismatched-install test states that loading the
  adapter against pre-gate core fails rather than silently disabling filters.
- **Fuzz/mutation**: pattern matcher property tests, adapter id determinism, Windows paths.

## 8. Risks and open points

- Regeneration changes run names/ids and result ids even at one file per directory; hand-written
  references dangle and `impact-diff`/`review-diff` report removals/additions. Documented, no remapping tool.
- Restart observations with the same `(tag, step)` become distinct qualified names, not one
  merged series; consumers see two observations.
- Cross-run `compare`/`leaderboard`/`matrix` align multi-file groups only when the event
  artifact path, occurrence and resulting name all match, as across a re-init; matching
  basenames alone is insufficient (`train/a.tfevents` and `validation/a.tfevents` differ).
  Single-file logdirs keep bare names and align normally.
- Grouping colocated series does not deduplicate out-of-order steps (TensorBoard discards them).
- Run-level occurrence would need a run-level verification pass and a file component in the
  locator; out of scope.
- Directories of many event files still hit the 10,000-result budget unless filtered.
- The `--tags` and `--volatile` glob dialects differ deliberately; the help text must say so.
- Matching has a fixed total work budget; a pathological tag/pattern set refuses with the
  too-expensive text instead of running unbounded.
- A fully filtered event file has no results; its run remains with zero results. Volatile
  protection is still by event path, so it cannot be marked volatile.

## 9. Decisions requested

- **D1:** one run per log directory; nested directories separate; root `.`.
- **D2:** path-derived run ids/names; code-point path order.
- **D3:** artifact-scoped occurrence, artifact-bearing result ids, and conditional names
  (bare `tag @ step` plus ` #occurrence` when nonzero for single-file groups;
  `tag @ step [basename] #<16-hex digest>` when a group spans files), with a duplicate-name
  refusal.
- **D4:** `init --tags` grammar/OR/no-match warning; core-owned matcher, cache and budget; the
  CLI passes one `TagFilter` to `extract`.
- **D5:** adapter capability flag and the fixed unsupported/contract errors.
- **D6:** selected-only budgets; unrepresentable tags refuse without a filter and are
  unmatchable/skipped with a filter; strict profile and caps always run.
- **D7:** no schema, checker, migration or dependency change; this RFC amends slice D §2/§9/§13
  (including Discovery), marks §14 and §15 D7 stale, and supersedes §16 for these two features
  only. Core and adapter must come from matched source checkouts for this unreleased gate;
  dependency-floor/version changes wait for an authorized release.
- **D8:** volatile protection is per event artifact (path predicate), independent of filtering;
  no-match warnings are buffered and printed only after a successful save, in argv order.
- **D9:** full-string segment glob matching with a stated O(tag × pattern) worst case, a
  1,024-code-point pattern cap after escape processing, per-`(tag, pattern)` caching
  and a fixed work budget; the warning
  pass reads the cache and never charges; `ascii`-escaped warning text; a core
  `TagPatternTooExpensive` maps to
  the fixed text; the 128 MiB file cap and strict profile always run before filtering, while the
  result/tag-byte caps count selected scalars.
