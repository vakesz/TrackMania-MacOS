# Building and releasing

The repository produces two deliberately separate artifacts:

- `tmnf-<version>-runtime.tar.xz`: the pinned, patched Wine runtime.
- `TrackMania-Mac-<version>.app.zip`: one native Swift hub app that downloads
  and manages the runtime and game.

The distributed zip contains exactly one `.app`. After a TMNF install, that hub
creates the separately installed Game and Settings apps; they are not bundled
inside the download.

## Source layout

- `patches/` and `config/sources.json` are the source-build contract. Keep both
  in every source release.
- `src/c/` holds the Windows session helper source.
- `src/python/tmnf/build/` holds runtime-build tooling; `src/python/tmnf/probes/`
  holds maintainer probe tooling.
- `src/shell/` holds the CLI, installer modules and package scripts.
- `src/swift/` holds the native hub UI. It calls `tmnf hub snapshot` and
  `tmnf hub run`; it does not duplicate install rules.

Generated runtime output belongs under `.build/` and is never a source input.

## Validate a checkout

```sh
make check
```

This runs Bash syntax checks, ShellCheck, Ruff, Python tests, the shell CLI
integration test and `swift test`.

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
tmnf-<version>-runtime.tar.xz
tmnf-<version>-runtime.tar.xz.sha256
tmnf-<version>-source.tar.xz
tmnf-<version>-source.tar.xz.sha256
```

The source archive expands into a single `tmnf/` directory containing the
repository inputs and the corresponding patched upstream source snapshots.

Run `make check` again before publishing. The thin hub expects the matching
runtime archive and checksum to exist in the same GitHub Release tag.

## Build the native hub

```sh
make appbundle
make zip
```

`make appbundle` compiles the release `TrackManiaHub` executable, places it in
`.build/app/TrackMania Mac.app`, embeds the shell launcher payload and ad-hoc
signs the result. It uses `uv` to stage the exact portable arm64 CPython 3.12.14
runtime inside the app, so the hub and later Game/Settings app wrappers do not
depend on a host Python installation. Set `TMNF_BUNDLED_PYTHON_ROOT` to an
already installed compatible uv-managed Python root for an offline package
build. `make zip` rebuilds that app, signs it and writes the zip and SHA-256
checksum under `.build/dist/`.

For Developer ID distribution, set `TMNF_SIGN_IDENTITY` and optionally enable
notarization with `TMNF_NOTARIZE=1`, `APPLE_ID`, `TMNF_TEAM_ID` and
`TMNF_APP_PASSWORD`.

## Release order

1. Build and verify the runtime/source archives.
2. Upload all four runtime/source artifacts to the release tag.
3. Build, sign and notarize the native hub zip for that exact version.
4. Upload the hub zip and its checksum.

Do not publish a hub whose versioned runtime archive is unavailable: first-run
installation verifies and downloads that exact asset. The tag workflow enforces
the matching `VERSION`, runtime asset, checksum and Developer ID notarization
before it attaches a hub zip; use a new version and coherent runtime build for
any archive-format change.
