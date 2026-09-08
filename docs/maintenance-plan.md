# Maintenance and multi-title implementation

This is the local implementation ledger for the approved codebase review. No
commits, pushes, PRs, or tracker changes are authorized. Preserve unrelated work,
existing TMNF paths, app identities, settings, saves, and replays.

## Decisions

- One supported bundled runtime; keep developer builds on the same contract.
- All older titles eventually, United Forever first. Local owned installers and
  official downloads only. Exact installer inventory is still required.
- Balanced configurable graphics, with game-controlled vsync.
- Refuse mutations and settings changes while the selected prefix is active.
- Quiet normal quits; report detected crashes without claiming uncertain exits
  are crashes.
- Reuse an explicit archive only after checksum and installed-content verification.
- Require campaign, settings, saves, replays, editor, tested controllers, online
  where services exist, and two-machine LAN acceptance for supported titles.
- A macOS 14 deployment target is not proof of macOS 14+ release compatibility.
- Full80 numeric state is a separate architecture project, by user decision.
  The current binary64-backed translator must not be advertised as full x87 emulation.

## Current checkpoint

Core safety, build integrity, session supervision, bounded rounding, TLS policy
and renderer fixes are implemented. The combined 0.3.0 runtime and source
archives have now been built, and the runtime archive installed and exercised
in scratch locations. Changes remain uncommitted. The normal game installation
and original 0.2.0 reference runtime are untouched.

Remaining work, in order:

1. Finish remaining qualification gaps: custom registry save locations, broader
   installer interruption cases, handshake failure coverage, Wine GPU lowering
   and existing FX2 effect-state emission limitations.
2. Rebuild the complete runtime independently from the extracted source archive;
   source preparation, offline checks and helper compilation already pass.
3. Qualify actual TMNF/TMLoader/settings behavior and benchmark fixed tracks.
4. Extract title policy and add United Forever after installer inventory, then
   qualify the remaining editions, macOS versions, controllers and LAN.

Do not expand this checkpoint into further open-ended upstream audits. New work
must address the approved list or a concrete failure blocking its verification.

## Status rules

Unchecked work is not complete. Fixture tests do not establish real-game
compatibility, native numerical correctness, frame-time improvements, or release
qualification. Record verification and outstanding evidence below.

## Safe tests and ownership

- [x] Hermetic CLI environment, temporary HOME and all writable paths; hostile inherited overrides.
- [x] Complete fake runtime, successful modded launch, clean relocated launcher, per-call logs.
- [x] Per-file Bash syntax validation and visible optional ShellCheck skip.
- [x] Physical-path validation, protected roots and home ancestors, symlinks, trailing separators, overlap rejection.
- [x] Refuse nonempty unmanaged prefixes before creating ownership markers.
- [ ] Safe legacy TMNF recognition and title-aware installation metadata.
- [x] Refuse uninstall when known profile/alias locations contain data inside the prefix.
- [ ] Resolve arbitrary custom Windows Shell Folders registry locations; explicit backup warning remains.
- [x] Serialize cooperating prefix operations; refuse active Wine server locks without starting Wine.
- [x] Serialize app/support replacement across prefixes with a stable per-user app-install lock.

## Runtime and installation

- [x] Complete runtime contract including wineboot PE payload and both compiler architectures.
- [x] Strict hashes, complete manifests, contained links, safe archive extraction.
- [x] Unique staging/download files, atomic no-replace runtime publication, identical archive retry.
- [x] One complete post-DirectX DLL repair path, including marker reuse.
- [x] Separate verified mod payload from configured launch readiness.
- [ ] Retry interrupted setup without replacing existing profiles or user source media.
- [x] Persist pre-Wineboot profile provenance; retry fresh profile setup without copying or deleting host data.
- [x] Validate commands/options before mutation; idempotently sourceable installer functions.
- [x] Non-mutating missing-prefix doctor; diagnose effective translator/configuration.
- [ ] Reduce redundant equality hashing and registry setup without stale-state caches.
- [x] Remove renderer environment print/reparse helpers; construct argument arrays directly.

## Lifecycle

- [x] Establish prefix-local ownership and handoff using real Wine with controlled launchers.
- [ ] Qualify handoff with actual TMNF, TMLoader, and the original Settings Play button.
- [x] Replace global executable-name supervision with owned-session tracking.
- [x] Startup/cleanup deadlines including registry preparation, interruptions, duplicate-launch refusal.
- [x] Distinguish normal quit, launch failure, timeout, known exception status, and uncertain exit.
- [x] Controlled settings-to-game handoff, bounded stop, session logging and three-backup retention.
- [x] Strict positive WIDTHxHEIGHT validation before Wine calls.
- [x] Recognize Wine's own explorer desktop for fullscreen (no virtual desktop) sessions and close it after the game exits; window-owner, handoff, reference-mismatch and namesake cases run against the 0.3.0 qualification runtime.

## Build and release

- [x] Ordered patch-set source identity; stale prepared trees are re-prepared automatically.
- [x] Invalidate completion before full/partial/fetch mutation; finalize only successful full builds.
- [x] Verify runtime/source snapshots and manifests before packaging, including source changes during compilation.
- [x] Include root LICENSE, shared verifier and recursive config inputs; track toolchain/SDK identity.
- [x] Clean dependency staging and exclude stale/diagnostic libraries.
- [x] Package/help independent of build bootstrap; clean job-count validation.
- [x] Per-file atomic artifact publication, fail-closed path scanning and inventory verification.
- [ ] Final manual source/license audit before publication.
- [ ] Reduce per-file classification overhead after correctness validation.
- [x] Serialize concurrent builds/packages through publication.
- [x] Build and package the combined 0.3.0 runtime; verify real archive installation and identical retry.
- [x] Extract source archive, run offline checks, prepare all bundled sources and compile its own session helper.
- [ ] Complete the independent Wine/dependency rebuild from the extracted source archive.
- [x] Checkpoint compiler cache identity before work, without marking release completion; interruption preserves valid partial work.

## Native correctness and probes

- [x] Correct eye-vector normalization in Wine and D9VK; separate Wine translation and D9VK GPU regressions.
- [ ] Qualify Wine's separate GPU rendering path for legacy shader lowering.
- [x] Pass explicit effect-compile target into function selection; native SDK comparison confirms intended selection.
- [ ] Existing Wine FX2 PixelShader state emission is incomplete; full effect-output qualification remains blocked.
- [x] Implement bounded instruction-aware binary shader-token parser and compile both architectures.
- [x] Execute the shader parser self-checks under real Wine, both architectures.
- [ ] PC24 extreme-boundary/full80 retention: deferred to the separate architecture project.
- [x] Discriminating PC53 rounding and float-store guest-rounding regressions/fixes.
- [x] Scoped FPCR, sticky FPSR, NZCV and borrowed-register checks; signal-storm stability gate.
- [ ] Broader directed-RC signal/context, unmasked trap and x87 status-word qualification.
- [x] Real Wine multimedia-timer callback/reentry/set/kill/synchronous-cancellation tests; corrected rationale.
- [x] Fast-wait acquisition/EOF/error fallback and per-thread disablement after terminal failure; actual 32/64-bit APC probes pass with the shortcut on and off.
- [ ] Cooperative handshake envelope/error/startup assumptions and positive hook-execution evidence.
- [x] Force translation/hook in named tests; explicit source-only skips.
- [x] Bound all probe invocations; terminate only owned process groups.
- [x] Aggregate independent probes; run the external-CDN check last.
- [x] Implement and compile actual 3D audio state operations; retain capture guards.
- [ ] Run actual-runtime audio state tests and manual audible spatialization.
- [x] Controlled sync/async negative TLS tests with private-prefix trust and positive controls.
- [x] WinHTTP certificate error/callback reporting and failed-handshake resource cleanup (0010).
- [x] Unignored combined errors, fail-closed policy calls, changed-policy connection reuse (0011).
- [x] Certificate-query failure ownership: fault injection proves no second connection release.
- [ ] Callback-driven close/retry/cancel, policy-API/allocation failure injection, further certificate categories.
- [ ] Native Windows TLS comparison, client-certificate/proxy pool identity, real login/socket/LAN qualification.

## Performance and dependencies

- [x] Correct dying-input FPR reuse estimate and eliminate unused FSqrt temporary.
- [x] Real restricted-allocator emission tests, including identical operands: 68 native cases.
- [ ] Evaluate bounded JIT fixup storage, precision hoisting, proven-f32 propagation.
- [x] Game-controlled vsync; remove redundant option and inaccurate memory claims.
- [ ] Measure async on/off cold visuals, buffer overrides, frame latency, filtering.
- [ ] Measure startup/hashing/registry overhead and compilation queue/worker contention.
- [x] Advance D9VK to f229921c after source/build/GPU verification and retire two superseded build patches.
- [x] Fix introduced promoted-volume slice stride (0003); prove GPU red/green and packed-format readback.
- [ ] Review upstream/security by applicable need; refresh toolchain notices when tools change.
- [ ] Record alternating cold/warm runs, p95/p99, long frames, memory, audio, visual/physics checks.

## Title support and host qualification

- [ ] Extract inert title policy without changing TMNF behavior; no plugins or arbitrary hooks.
- [ ] Resolve title before persisted config; isolate prefixes, userdata, apps, support, logs.
- [ ] Move title assets out of runtime contract; carry all assets in installed launchers.
- [ ] Separate official game updates/runtime maintenance/mod repair; unknown builds are not TMNF replacements.
- [ ] Inventory legitimate installers/editions/patch chains/activation without storing credentials.
- [ ] Qualify TMUF all environments, then United, retail/Original upgrade, Sunrise, Nations ESWC.
- [ ] Validate packaged artifact on every claimed macOS/Rosetta generation, including macOS 14.
- [ ] Manual Finder/TCC/controllers/audio/LAN evidence; service-unavailable states explicit.

## Nits and documentation

- [x] Exact legacy app identifier matching; no regex wildcard dots; preflight before launcher replacement.
- [x] Version-correct archive examples and existing smoke-command references in patch headers.
- [ ] Accurate Wine redistribution/security scope and contributor multi-title scope when implemented.
- [ ] Coverage wording matches assertions; deployment target, async/vsync/memory claims accurate.
- [ ] Thread count described as a maximum; smoke prerequisites and generated-vs-personal profiles clear.
- [x] GBX known/reserved field annotations, primitive validation, byte-identical structural tests.
- [x] Safe PE resource handling; synthetic icon success/failure tests; shared icon generation/resizes.
- [x] Remove redundant cp subprocess and impossible escaped-argument test alternative.
- [x] App log retention, compiler hints and early option validation; no broad formatting churn.

## Verification record

### Combined runtime candidate

Latest working-tree `make check`: 315 discovered tests, 283 passed and 32
explicitly skipped, plus the CLI suite, Bash syntax and ShellCheck. The final
bounded safety review found no new P1/P2 regression; custom registry save paths
remain outside automatic verification. `git diff --check` is clean.

The complete build succeeded in `.build/qualification-0.3.0-r3`, using the
verified sources in `.build/qualification-0.3.0/sources`. The original release
workspace was not replaced. A first attempt identified one libunistring scratch
file; its exact exclusion is regression-tested. A second attempt hit the tool's
30-minute limit. The successful build used a larger deadline, and compiler-cache
checkpointing now prevents future interruptions from discarding valid work.

Artifacts:

- `.build/qualification-0.3.0-r3/dist/mactmnf-0.3.0-runtime.tar.xz`
- Runtime SHA-256: `03374a707f7282389536a0c20d1cc5e0221fcace0ca59af9c5c090501fd639ea`
- `.build/qualification-0.3.0-r3/dist/mactmnf-0.3.0-source.tar.xz`
- Source SHA-256: `dbf8b03bfb6fe9035295f452ded00e252d32d51b06a8280b490c172d7a171a47`

The runtime archive installed successfully outside the checkout and a second
identical installation reused it. Against that installed copy, with no component
overlays, these passed on the current M1 Pro/macOS 27 host:

- 24 session/integration cases, including the packaged helper, actual CLI,
  fresh-profile privacy setup, and 32/64-bit alertable waits in both modes.
- Full sync/async TLS policy and pool matrix plus importer/cleanup checks.
- Ten GPU texture/volume/readback cases and six legacy reflection cases.
- All 88 numeric cases in each of four translator modes and both architectures,
  using the packaged sidecar.

The extracted source archive passed its own `make check` (311 discovered tests,
278 passed, 33 skipped), offline preparation of all ten bundled components,
nested-dependency checks, 26 patch reverse checks and independent session-helper
compilation. This is not a full independent Wine rebuild or game qualification.

The earlier component evidence below remains useful but is superseded by the
combined-runtime checks where the same behavior is covered.

### Earlier component checks

Integrated checkpoint: `make check` passed shell syntax, ShellCheck, the CLI
fixture suite and 269 discovered Python tests: 240 passed, 29 explicitly skipped.
Skipped tests require an opted-in runtime/build or native Windows. Component
suites were also exercised with Python 3.9.6 and 3.14.7.

Real component verification, performed in disposable locations:

- Session: 22 real Wine cases passed, including the CLI, handoffs, translator-on,
  cross-prefix isolation, timeout survival, multimedia timers and shader parsing.
- x87: 68 allocator cases, two isolated-store cases, and 64 native ARM numeric/state
  cases passed. Windows 32/64-bit probes passed 88 cases in all four translator
  modes; cooperative probes passed 7,504 PC24 cases per mode.
- Signals: 504 storm cases passed. This proves interruption stability against the
  sample's baseline, not complete directed-RC or full x87-context equivalence.
- TLS: both architectures and sync/async modes passed 288 fixture/flag cases,
  20 connection-policy sequences, 12 no-subscription checks, ownership guards
  and interruption cleanup. Original behavior failed 28 combined/pool checks.
- TLS lifetime: four fault-injected lookup failures detected the old double
  release; all four passed after clearing the failed connection pointer.
- Renderer: ten actual M1 Pro/MoltenVK GPU cases passed, including distinct volume
  slices and poisoned, row-distinct packed readbacks. The unpatched new D9VK
  candidate failed volume sampling; the one-line slice fix made it pass.
- The final patch whitespace cleanup preserved application to the pristine pin
  and passed the 68 + 2 allocator and 64 ARM numeric/state cases.

These tests used the existing 0.2.0 Wine runtime with newly compiled scratch
helpers, sidecar or DLL overlays. Existing prepared source/work directories,
installed runtimes, normal game prefixes, user data and macOS Keychain were not
modified. Test CA material is public and trusted only within disposable prefixes.

Actual-game benchmarks, older-macOS qualification and a public release remain
outstanding. Subsequent test/documentation changes do not alter the preserved
candidate archives; regenerate artifacts through the normal completion gates
before publishing a final release.

## External prerequisites

Installer inventory, native Windows numerical/reference rendering results,
multiple macOS/Rosetta hosts, controller models, a second LAN peer, and real-game
cold/warm performance runs remain necessary. Do not mark these gates complete
using mocks or compilation alone.
