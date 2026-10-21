# TrackMania Mac hub app

The GitHub Release download is one self-contained native application:
`TrackMania-Mac-<version>.app.zip` containing `TrackMania Mac.app`. It embeds a
SwiftUI user interface, shell/Python launcher payload, a relocatable CPython
runtime and the title registry. It does not embed Wine, DirectX, game files or
the two app wrappers.

After a full TMNF installation, the hub creates two separate installed apps:

- **TrackMania Nations Forever** launches the game with Competition Patch.
- **TrackMania Settings** opens the original settings launcher.

The hub opens those app wrappers through macOS; it does not replace them.

## Backend contract

The Swift interface is an adapter over exactly two shell commands:

```sh
tmnf hub snapshot
tmnf hub run TITLE ACTION [--source ABSOLUTE_PATH]
```

`snapshot` is read-only and returns one versioned JSON document describing every
registered title, its install state, app paths and currently available actions.
It never starts Wine. `run` validates the title, action and optional source path,
then starts the normal CLI/installer in a fresh process with `TMNF_TITLE` set
before title-dependent paths are calculated. This prevents one title from using
another title's prefix or game path.

The supported TMNF actions are install/repair, update and installing the two app
wrappers. Provisional retail titles expose shared-platform setup and, once the
prefix exists, a local disk-image/setup import action. Their installer chain and
game qualification remain intentionally incomplete.

## Titles

| Title | Install | Status |
| --- | --- | --- |
| Nations Forever (`tmnf`) | free Nadeo download | supported |
| United Forever (`tmu`) | user disk image | provisional |
| Original (`tm-original`) | user disk image | provisional |
| TrackMania 2 Canyon (`tm-canyon`) | user disk image | provisional |
| Turbo (`tm-turbo`) | user disk image | provisional |

`config/titles/*.json` is the sole authority for title paths, app names, bundle
identifiers and prefix markers. The shell reads the registry; Swift only renders
the machine-readable snapshot.

## Retail import

The native app uses macOS's file picker for a `.iso`, `.dmg` or `.exe`, then
passes its absolute path to the backend. Disk images mount read-only for
inventory. Serial keys are entered interactively by the retail installer and are
never stored by this project.

## Packaging and signing

```sh
make appbundle
make zip
```

The executable at `Contents/MacOS/TrackMania Mac` is a signed arm64 Mach-O
binary, not a shell wrapper. Its launcher resources sit below
`Contents/Resources/launcher`. The packager signs only after the binary and
resources are final, verifies the bundle, then uses `ditto -c -k --keepParent`
to preserve the signature in the zip.

Set `TMNF_SIGN_IDENTITY="Developer ID Application: Name (TEAMID)"` for
Developer ID signing. Set `TMNF_NOTARIZE=1` with the documented Apple credential
environment variables to notarize and staple the release. The native UI needs no
JIT, DYLD or unsigned-executable-memory entitlement.
