# Building and releasing

## Source ownership

`make build` owns `.build/release`. It never patches a checkout elsewhere on the
machine. `config/sources.json` pins every top-level runtime source by archive
SHA-256 or Git commit. D9VK's Git submodules and MoltenVK's `ExternalRevisions`
pin their nested dependencies. Homebrew provides build tools, not bundled Wine
libraries. Apple supplies the SDK, system frameworks and Rosetta.

The public commands are `make build`, `make install` and `make package`.
Maintainers can run `tools/build.sh --only fetch|deps|wine|d9vk|sidecar|session|bundle|package`
while diagnosing a build. Run the full build before packaging a release.

Prepared sources record the ordered patch set as well as upstream identity.
An old stamp or changed patch set makes `make build` delete and re-prepare that
source directory; prepared trees under `.build/release/sources` are build
products, so keep source edits in `patches/`, never in the prepared tree. Full
builds use clean dependency staging. Toolchain/SDK changes invalidate compiler
work; do not keep unique source edits in compiler output.

Build and packaging operations share a lock. A partial or failed build cannot
reuse the previous completion stamp. Packaging checks the complete runtime and
corresponding-source snapshots before publishing archives and checksums. Help
and packaging do not provision the native build toolchain.

Compiler-cache identity is checkpointed before compilation, separately from
release completion. Interrupted work can resume when its inputs, toolchain and
prepared sources still match; an incomplete build cannot be packaged. The exact
libunistring scratch file `lib/libunistring.sym-t1` is excluded from source
inventories, while the actual symbol list and source remain checked. Source-drift
errors now identify the changed paths instead of requiring blind rebuilds.

The `session` stage builds the 64-bit Windows supervisor at
`runtime/libexec/mactmnf-session.exe`. It also supervises 32-bit games.
Helper-only builds invalidate only helper compiler work and release completion,
not Wine or dependency output. The runtime contract includes the helper and
the native conhost image used to recognize console infrastructure.

The layout under `.build/release` is:

- `sources/`: downloaded source with local patches applied.
- `work/`: dependency and compiler output.
- `runtime/`: the relocatable installation tree.
- `logs/build.log`: compiler commands and full build output.

The terminal shows one status line per step (configure, make, hashing, and so
on) with the elapsed time and the last line the command wrote to `build.log`;
when output is not a terminal it prints a line per minute instead. A second
`make build` or `make package` waits for the running one and says which
process holds the lock. Compiler output under `work/` is reused when the build
inputs, toolchain and prepared sources are unchanged; otherwise the build says
what changed before discarding it.

The build compiles x86_64 Unix Wine and both i386 and x86_64 Windows DLLs.
x87sidecar is native arm64. MoltenVK uses Xcode; a command-line-tools-only
installation is insufficient to build it. The current development host is an
M1 Pro with Xcode 27 and macOS 27. The macOS 14 deployment target does not by
itself prove runtime compatibility on macOS 14, 15 or 26.

Wine includes the macOS graphics, Vulkan, CoreAudio, font and TLS paths. Linux
backends, GStreamer, SDL, USB, printing and packet capture are disabled.
This is a TMNF runtime, not a general replacement for a full Wine distribution.

## Validation

`make check` is offline. Runtime smoke tests need an Apple Silicon host with
Rosetta 2, Python 3, an installed runtime, and an initialized Wine prefix.
Both `i686-w64-mingw32-gcc` and `x86_64-w64-mingw32-gcc` must be on `PATH`;
Homebrew's `mingw-w64` supplies them. The HTTPS probe needs network access to
the game's CDN, and audio checks need a working macOS output device. Close
the game and Settings app first. `make smoke` uses the selected prefix and can
change its audio configuration; it does not create an isolated prefix for you.

```sh
make check
make build
make install
make smoke
```

For a fresh installation test, set `MACTMNF_ROOT`, `MACTMNF_SUPPORT` and
`MACTMNF_APPS_DIR` to empty test folders below your home directory. Keep those
variables set for every command in the test. This avoids altering your usual
installation. The game must reach a menu, start a race, render materials
correctly and play audio. Open both generated apps from Finder as well. A
successful `wine --version` or a live process alone is insufficient.

The probes cover shader compiler profiles and legacy flags, alertable waits and
APC delivery, x87 precision, and DirectSound device enumeration and playback
cursor progress. The current shader probe does not exercise `texm3x3` lowering.
Audio tests exercise stereo/mono buffers and 3D buffer/listener state, but do not
prove audible output or positional mixing. The HTTPS probe is a positive request to the game's
download server with normal certificate verification; it does not test rejection
of invalid certificates. The source-only x87sidecar probes additionally exercise
multiple translation modes. Without a source build, that script falls back to
the installed Wine integration probes. None of these checks proves compatibility
on an untested macOS release or measures whole-game frame times.

Each probe has a host-side deadline. Cleanup targets only its subprocess group,
not every Wine process sharing a prefix. Detached processes may need explicit
cleanup in the dedicated test prefix. The aggregate smoke target collects
independent failures and runs the external CDN check last.

The opt-in suites create their own temporary homes and prefixes. They never
reuse the game prefix, and the session suite compiles controlled executables
instead of installing game content:

```sh
MACTMNF_TEST_RUNTIME="$PWD/.build/release/runtime" \
  python3 -m unittest discover -s tests -p test_session_runtime.py -v
MACTMNF_TEST_RUNTIME="$PWD/.build/release/runtime" \
  python3 -m unittest discover -s tests -p test_network_runtime.py -v
```

Set `TMPDIR` to an existing temporary directory if the environment does not
already provide one. The session suite may briefly show a test-only virtual
desktop. It includes host/CLI handoff, prefix isolation, translator-enabled
launch, image-identity, multimedia-timer and shader-token checks. It reports
the tested runtime version separately from the current source version.

The local TLS suite trusts a public test CA only inside disposable prefixes.
It requires a valid control and exact WinHTTP rejection diagnostics. Existing
runtime 0.2.0 rejects the three invalid certificates but returns error 12157
without secure-failure callback flags, so those strict compatibility assertions
remain failing on the unmodified runtime. Patch 0010 corrects that reporting;
isolated rebuilt DLL overlays pass both synchronous and asynchronous cases on
both architectures. Patch 0011 additionally fixes combined-error ignore handling,
changed-policy connection reuse and failed-certificate-query ownership. The
expanded matrix and fault-injected lookup tests pass. Callback-driven close/retry,
further certificate categories, native Windows comparison and complete-runtime
qualification remain separate gates.

With the existing pinned, unmodified WinHTTP source and completed Wine work
available, the diagnostic builder can rebuild the affected WinHTTP module without
touching that work tree or the installed runtime:

```sh
scratch="$(mktemp -d)"
python3 tools/build-winhttp-probe.py --output "$scratch/winhttp"
MACTMNF_TEST_RUNTIME="$PWD/.build/release/runtime" \
MACTMNF_TEST_WINHTTP_DLLS="$scratch/winhttp/native" \
  python3 -m unittest discover -s tests -p test_network_runtime.py -v
```

The native-test DLLs load only beside the scratch probes. They are not release
artifacts. See `tests/fixtures/tls/README.md`; do not install its test CA in a
real prefix or macOS Keychain.

## Release files

Update `VERSION` before the full build used for release qualification when the
runtime or default installation changes. The installer reuses a verified
same-version installed runtime, so testing an old version does not qualify new
source changes. Then:

```sh
make check
make build
make package
```

`.build/dist` receives:

- `mactmnf-VERSION-runtime.tar.xz` and its `.sha256` file.
- `mactmnf-VERSION-source.tar.xz` and its `.sha256` file.

The runtime contains Wine, dependency dylibs, D9VK, x87sidecar, generated game
settings, renderer configuration, source pins, per-file hashes and licenses.
Mach-O library references are made relative, and changed binaries are ad-hoc
signed. These artifacts are not Developer ID signed or notarized.

The source archive contains these scripts and patches plus the corresponding
upstream source, including nested dependencies. Keep both archives together in
the same GitHub Release. Generated account-free default settings are included;
personal profiles and account data are not. Do not ship game installers, personal
game profiles, DirectX redistributables, local diagnostics, caches or your Wine
prefix.

Before publishing, install from the packaged runtime into an empty test folder,
verify that the apps still work after moving the checkout, and build from an
extracted source archive. Check that the archive licenses match the libraries
actually bundled. This also catches missing source and accidental local paths.

Create a GitHub Release in `vakesz/TrackMania-MacOS`, tagged `vVERSION`, and attach
all four files. Keep it a draft until the installation and visual checks pass.
No command in this repository creates a remote, commits, pushes or publishes.
The installer downloads the exact version's checksum and archive over HTTPS;
SHA-256 verifies integrity, not an independent publisher signature.
