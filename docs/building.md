# Building and releasing

The repository produces two deliberately separate artifacts:

- `trackmania-mac-<version>-runtime.tar.xz`: the pinned, patched Wine runtime.
- `TrackMania-Mac-<version>.app.zip`: one native Swift hub app that downloads
  and manages the runtime and game.

The distributed zip contains exactly one `.app`. After installing a title, that hub
creates the separately installed Game and Settings apps for it; they are not
bundled inside the download.

## Source layout

- `patches/` and `config/sources.json` are the source-build contract. Keep both
  in every source release.
- `config/titles/*.json` is the title registry; `config/pins.json` pins
  upstream downloads.
- `assets/titles/*.svg` is hand-authored title artwork, rasterized to PNG only
  inside the app bundle at package time; `assets/branding/` holds the hub icon.
- `src/c/` holds the Windows session helper source.
- `src/python/trackmania_mac/` holds the Python packages: `app` (icon extraction,
  app launcher), `build` (runtime build), `guard` (locks, uninstall and the move
  out of earlier releases' folders), `hub` (snapshot, run and migrate backend),
  `install`, `probes` and `session`.
- `src/shell/cli/` holds the `trackmania-mac` entry point and installer;
  `src/shell/lib/commands/` implements its verbs, `src/shell/lib/apps/` builds
  app wrappers, icons and the launcher payload, and `src/shell/lib/install/`
  holds installer steps. `src/shell/tools/` holds build and package scripts.
- `src/swift/` holds the native hub UI. It calls `trackmania-mac hub snapshot`,
  `trackmania-mac hub run` and `trackmania-mac hub migrate`; it does not duplicate install rules.

Generated runtime output belongs under `.build/` and is never a source input.

## Validate a checkout

```sh
make check
```

This runs Bash syntax checks, ShellCheck, Ruff (lint and format check), mypy,
Python tests, the shell CLI integration test, SwiftLint and `swift test`.
`make check-scripts` and `make check-swift` run each half on its own.

CI runs only the jobs whose files a change touches.
`.github/scripts/ci-areas.sh` maps changed paths to jobs: docs, the website and
GitHub forms run nothing, Swift runs only the Swift jobs, and any path it has
no rule for runs the scripts check.

Run `make smoke` only from a checkout with a built runtime and initialized
prefix, with Rosetta 2, Python 3 and network access for the HTTPS probe. Close
the game and Settings app first; audio checks need a working output device.

## Build a runtime from source

Use an Apple Silicon Mac, Rosetta 2, full Xcode, Homebrew and a checkout path
without spaces. The first build needs substantial time and disk space.

```sh
sudo xcode-select --switch /Applications/Xcode.app/Contents/Developer
make build
make package
```

`make build` writes the verified runtime under `.build/release/runtime`.
`make package` writes these release inputs under `.build/dist/`:

```text
trackmania-mac-<version>-runtime.tar.xz
trackmania-mac-<version>-runtime.tar.xz.sha256
trackmania-mac-<version>-source.tar.xz
trackmania-mac-<version>-source.tar.xz.sha256
```

The source archive expands into a single `trackmania-mac/` directory containing the
repository inputs and the corresponding patched upstream source snapshots. It is
the corresponding source for the runtime archive; the hub does not build from it.

Run `make check` again before publishing. The hub expects the matching
runtime archive to exist in the same GitHub Release tag.

### Release order

The release version is `[project] version` in `pyproject.toml`; `uv.lock`
mirrors it, and `tests/test_project.py` checks that the two match. The tag
workflow trusts `config/release-assets.sha256`, committed in the tagged commit,
not the `.sha256` files beside the release assets (anyone who can replace an
asset could replace those too). So the hashes go through review before the tag
exists:

1. Bump the version in `pyproject.toml`, run `uv lock`, then `make build` and
   `make package`. Editing anything else in `pyproject.toml` does not
   invalidate a compiled runtime.
2. Replace `config/release-assets.sha256` with the two lines from the
   `.sha256` files under `.build/dist/` (`shasum` format, runtime then source).
   `tests/test_hub_package.py` fails until the file names the current version.
   The file is left out of the build inputs and the source archive, so editing
   it doesn't invalidate the build.
3. Merge the hash file through a PR.
4. Attach the runtime and source archives to the release for the tag. Do not
   publish a hub whose versioned runtime archive is unavailable, because first-run
   installation downloads it. The source archive is published as the runtime's
   corresponding source.
5. Push the tag from the merged commit. The workflow checks that the tag equals
   `v` plus the `pyproject.toml` version and verifies both assets against
   `config/release-assets.sha256`. It then builds the hub for that exact
   version, signs it and uploads the hub zip and its checksum. The committed
   runtime digest is what the hub pins. The workflow downloads the archives;
   re-run it if it started before they were attached.

Build the hub from a clean, pushed commit so its build mode works. Use a new
version and a coherent runtime build for any archive-format change.

The workflow builds without secrets, signs in a separate job behind the
`release` environment, and publishes from a third job without `--clobber`.
Publishing never overwrites: if a run fails after attaching only part of the
hub assets, delete the partial asset from the release by hand before re-running.
If the repository has no signing secrets, it publishes an ad-hoc signed,
unnotarized hub with a workflow warning; users must allow it once under System
Settings > Privacy & Security. If any signing secret is set, the workflow
requires the complete Developer ID and notarization configuration and fails
otherwise.

### Runtime source modes

The installer (`make install`, `make setup`, `src/shell/cli/install.sh` and the
hub) takes the runtime from `TRACKMANIA_MAC_RUNTIME_SOURCE`, or `--runtime=<mode>` on
`install.sh`, or `RUNTIME=<mode>` on `make`:

- `auto` (default): install `.build/release/runtime` when it exists, else
  download the release runtime.
- `download`: always download the release runtime, even if a local build exists.
- `build`: compile the runtime, then install it. In a checkout this runs
  `src/shell/tools/build-runtime.sh` in place and keeps `.build`. A packaged hub
  has no build script, so it clones the exact commit the hub was built from
  (recorded in `config/build-revision` by `make appbundle`) with git, builds under
  `build/<commit>` in the download cache (`~/Library/TrackManiaMacOS/cache`) (the path must not contain spaces) and
  removes that folder after a successful install. After a failure the folder is
  kept and the next run resumes in it. It needs git (full Xcode), network access
  and a commit that has been pushed to GitHub. A hub built from uncommitted
  changes records `dirty` and cannot build; use a checkout instead.

The installer trusts a release runtime only when its SHA-256 is pinned in the hub:
first `config/runtime-pin` (written when the app is packaged), then the entry for
the runtime archive in `config/release-assets.sha256`. With neither, the install
stops instead of trusting a checksum published next to the archive. Set
`TRACKMANIA_MAC_RUNTIME_ALLOW_UNPINNED=1` to accept that checksum anyway; the installer then
warns that a compromised release could replace both files.

An installed runtime is never replaced; remove it while the game is closed to
install again with another mode. `build` needs the prerequisites above and
cannot be combined with `TRACKMANIA_MAC_RUNTIME_ARCHIVE`.

### Compare x87sidecar builds

Two tools compare x87sidecar builds. Like `make smoke`, they run in the selected
prefix and need the i686 MinGW compiler; close the game first. Run them from the
repository root and name each build with `NAME=PATH`. The physics gate works on
`TmForever.exe`, so it applies to the Forever games (Nations and United
Forever); `x87_bench` uses no game files:

```sh
PYTHONPATH=src/python python3 -m trackmania_mac.probes.x87_bench --sidecar old=PATH --sidecar new=PATH
PYTHONPATH=src/python python3 -m trackmania_mac.probes.x87_physics_gate --sidecar old=PATH --sidecar new=PATH
```

`x87_bench` times `tests/x87-bench.c` (a 4x4 matrix chain in PC24 and PC53,
particle-sprite vertices in PC24, and an integer control) under stock Rosetta and
each build, and prints nanoseconds per iteration. It uses no game files.

`x87_physics_gate` needs an installed Forever game. It builds a call table for about
230 `Gm*` math functions from `TmForever.map`, maps `TmForever.exe` without running
it, and calls each function with deterministic random and edge-case inputs in PC24
with all four rounding modes and in PC53. Every later build must give the same
hash per function and mode as the first, or the gate fails. Each build is also
compared with stock Rosetta, for information. The numbers in this paragraph are
historical measurements taken against runtime 0.3.0, not results of the current
build. The differences from stock predate
the fast paths: runtime 0.3.0's translator (4e9c738 with the old patch) differs in
125 of 1150 results, 0b7c5e5 with patch 0001 in 113, and patches 0002-0004 change
none of them. No result that matched stock in 0.3.0 differs now. All remaining
differences need inputs a race does not produce: angles of 2.7e9 radians and more
(fsin, fcos and fptan match stock below that, checked with 30 million random
angles), overflowing squares and degenerate camera frustums.

`--explain NAME` prints the values one function computes differently, with their
inputs. Functions that need game state the harness does not set up are listed in
`tests/x87-gamecode-skip.txt`.

## Build the native hub

```sh
make appbundle
make zip
```

`make appbundle` compiles the release `TrackManiaHub` executable, places it in
`.build/app/TrackMania Mac.app`, embeds the shell launcher payload and ad-hoc
signs the result. It uses `uv` to stage the exact portable arm64 CPython 3.12.14
runtime inside the app, so the hub and later Game/Settings app wrappers do not
depend on a host Python installation. Set `TRACKMANIA_MAC_BUNDLED_PYTHON_ROOT` to an
already installed compatible uv-managed Python root for an offline package
build. `make zip` rebuilds that app, signs it and writes the zip and SHA-256
checksum under `.build/dist/`. The release workflow uses the two halves
separately: `package-app.sh build-unsigned` builds the unsigned app, and
`package-app.sh sign` signs an existing app, notarizes and zips it.

The executable at `Contents/MacOS/TrackMania Mac` is a signed arm64 Mach-O
binary, not a shell wrapper; launcher resources sit below
`Contents/Resources/launcher`. The packager signs only after the binary and
resources are final, verifies the bundle, then uses `ditto -c -k --keepParent`
to preserve the signature. The UI needs no JIT, DYLD or unsigned-executable-memory
entitlement.

For Developer ID distribution, set `TRACKMANIA_MAC_SIGN_IDENTITY="Developer ID Application:
Name (TEAMID)"`. Notarization is opt-in: set `TRACKMANIA_MAC_NOTARIZE=1` and
`TRACKMANIA_MAC_NOTARY_PROFILE` to a keychain profile created with
`xcrun notarytool store-credentials`; no password is passed on a command line.
The release workflow (`.github/workflows/release-hub.yml`) creates that profile
from the `APPLE_ID`, `TRACKMANIA_MAC_TEAM_ID` and `TRACKMANIA_MAC_APP_PASSWORD`
repository secrets, and reads `TRACKMANIA_MAC_SIGN_IDENTITY` and
`TRACKMANIA_MAC_NOTARIZE` from secrets of the same names. The signing
certificate comes from `APPLE_CERT_P12` and `APPLE_CERT_PASSWORD`.
