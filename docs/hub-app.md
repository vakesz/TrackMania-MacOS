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

Nations Forever exposes install/repair, update and app-wrapper actions. United
Forever and Canyon can also use their pinned official installers. Original can
be installed from user-owned retail media, while Turbo can copy an existing
Steam game folder. These other titles remain provisional: installation does not
mean their renderer, DRM, online services or complete gameplay have been
qualified under Wine.

## Titles

| Title | Install source | Status |
| --- | --- | --- |
| Nations Forever (`tmnf`) | free Nadeo download | supported |
| United Forever (`tmu`) | pinned official retail installer; asks for a serial key | provisional |
| Original (`tm-original`) | user-owned retail disk image or setup executable | provisional |
| TrackMania 2 Canyon (`tm-canyon`) | pinned official ManiaPlanet trial | provisional |
| Turbo (`tm-turbo`) | existing Steam game folder | provisional |

`config/titles/*.json` is the sole authority for title paths, app names, bundle
identifiers and prefix markers. The shell reads the registry; Swift only renders
the machine-readable snapshot.

## Retail import

The native app uses macOS's file picker for a retail `Setup.exe`, or a folder
for a Steam copy, then passes its absolute path to the backend. The retail
installer runs interactively in that title's managed prefix; choose its game
install location and the hub checks for the expected executable when setup
closes. To use a `.iso` or `.dmg`, run the CLI import command to mount it
read-only and inspect its contents, copy `Setup.exe` to a stable location, then
select that file in the hub. Steam copies are copied into the managed prefix
without following symlinks. Serial keys are entered in the game and are never
stored by this project.

The hub keeps the sequence explicit: it offers shared-platform setup until the
runtime, prefix, DirectX and renderer are ready; it then offers import. Once
the game files are present, installing the two macOS app wrappers becomes the
next primary action.

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
