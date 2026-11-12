# TrackMania Mac hub app

The GitHub Release download is one self-contained native application:
`TrackMania-Mac-<version>.app.zip` containing `TrackMania Mac.app`. It embeds a
SwiftUI user interface, shell/Python launcher payload, a relocatable CPython
runtime and the title registry. It does not embed Wine, DirectX, game files or
the two app wrappers.

After a full TMNF installation, the hub creates two separate installed apps:

- **TrackMania Nations Forever** launches the game with Competition Patch and
  CoreMod. United Forever does the same.
- **TrackMania Settings** opens the original settings launcher.

The hub opens those app wrappers through macOS; it does not replace them.

## Backend contract

The Swift interface is an adapter over exactly two shell commands:

```sh
tmnf hub snapshot
tmnf hub run TITLE ACTION [--source ABSOLUTE_PATH] [--runtime download|build] [--include-saves]
```

`snapshot` is read-only and returns one versioned JSON document describing every
registered title, its install state, app paths and currently available actions.
It never starts Wine. `run` validates the title, action and optional source path,
then starts the normal CLI/installer in a fresh process with `TMNF_TITLE` set
before title-dependent paths are calculated. This prevents one title from using
another title's prefix or game path. Only the `install` and `install-platform`
actions accept `--runtime`; it is the only way `run` sets `TMNF_RUNTIME_SOURCE`,
which is never inherited from the hub's environment. Only the `uninstall`
action accepts `--include-saves`.

Each title entry carries `icon` (an absolute path or `null`) and `iconSource`
(`"game"`, `"bundled"` or `null`). The icon is chosen in this order: the `.icns`
extracted from the locally installed game executable
(`~/Library/Application Support/TMNF/icons/<id>.icns`, never redistributed),
then the project's own artwork as a PNG, then as an SVG (`assets/titles/<id>`).
Clients should load either format; `iconSource` only says whether the art is the
game's own. Each title also installs its own launcher support directory,
`~/Library/Application Support/TMNF/launchers/<id>`, so installing one title's
apps never changes what another title's apps launch.

Nations Forever and United Forever start with Competition Patch and CoreMod;
the other titles start their own game executable. Nations Forever exposes
install and update actions; install also writes the app wrappers. United
Forever and Canyon can also use their pinned official installers. Original can
be installed from user-owned retail media, while Turbo can copy an existing
Steam game folder. These other titles remain provisional: installation does not
mean their renderer, DRM, online services or complete gameplay have been
qualified under Wine.

## Uninstalling a title

The snapshot lists an `uninstall` action for every title that left something
behind: its prefix, its Game or Settings app, its launcher support directory,
or only its saves folder. Each title entry also carries `removal`, the paths an
uninstall would remove, so the confirmation dialog can name them:

```json
"removal": {
  "items": [{"kind": "prefix", "label": "Wine prefix and game files", "path": "/Users/me/Games/TMNF/prefix"}],
  "saves": "/Users/me/Games/TMNF/UserData"
}
```

`kind` is one of `prefix`, `game-app`, `settings-app`, `launcher`, `cache`,
`logs` and `icon`; `saves` is `null` when the title has no saves folder.
Error entries carry `"removal": null`. The preview only checks what exists; the
full checks run when the action does.

`tmnf hub run TITLE uninstall` runs `tmnf uninstall --yes` for that title. It
removes the title's prefix, its two app wrappers (only our own, marked with
`.tmnf-app` and carrying the title's bundle identifier), its launcher support
directory, its cached installer, its logs and diagnostics reports, and its
extracted icon. It keeps the saves folder, `~/Games/<title>/UserData`, which
is the prefix's Windows Documents folder. `--include-saves` removes that folder
too, but only at its default location; save folder names overlap between
titles, so a custom or shared folder is never removed.

An uninstall never removes the Wine runtime, the shared downloads (DirectX,
TMLoader, OpenAL Soft), a title root, or another title's files. Nations
Forever's root `~/Games/TMNF` also holds the other titles' roots, so only its
`prefix` and `UserData` folders are removed, never the root. The action refuses
while the game is running or another operation holds the prefix, and an app
that is not ours, a symlink or an older launcher app is reported and kept. When
it finishes, the snapshot shows the title as not installed.

## Titles

| Title | Install source | Status |
| --- | --- | --- |
| Nations Forever (`tmnf`) | free Nadeo download | supported |
| United Forever (`tmu`) | pinned official retail installer; asks for a serial key; starts with Competition Patch and CoreMod | provisional |
| Original (`tm-original`) | user-owned retail disk image or setup executable | provisional |
| TrackMania 2 Canyon (`tm-canyon`) | pinned official ManiaPlanet trial | provisional |
| Turbo (`tm-turbo`) | existing Steam game folder | provisional |

`config/titles/*.json` is the sole authority for title paths, app names and
bundle identifiers; each title's prefix marker is derived from its id. The shell
reads the registry; Swift only renders the machine-readable snapshot.

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

Building, signing, notarizing and releasing the hub app are covered in
[building.md](building.md).
