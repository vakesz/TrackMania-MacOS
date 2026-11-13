# TrackMania Mac hub app

The hub installs and launches a collection of TrackMania titles on Apple
Silicon; the [Titles](#titles) table lists them. The GitHub Release download is
one self-contained native application: `TrackMania-Mac-<version>.app.zip`
containing `TrackMania Mac.app`. It embeds a SwiftUI user interface,
shell/Python launcher payload, a relocatable CPython runtime and the title
registry. It does not embed Wine, DirectX, game files or the app wrappers.

After installing a title, the hub creates two separate installed apps for it,
named after the game. For Nations Forever they are:

- **TrackMania Nations Forever** launches the game with Competition Patch and
  CoreMod.
- **TrackMania Settings** opens the original settings launcher.

The hub opens those app wrappers through macOS; it does not replace them.
For titles that need a serial (`requiresSerial`), the hub replaces the Settings button
with "Register player key", which opens the same Settings app, and notes that the key is
entered there before pressing Play. Once a title's `cdKey` item (below) is ready, the
key is registered and the plain Settings button comes back.

A title with `cd_key_in_nadeo_ini` in its registry entry (United Forever)
also carries a `cdKey` item in `state` once its game files are installed. It is
ready when the
`[TmForever]` section of the game folder's `Nadeo.ini` has an `InstallKey` that
is not empty, `%CDKEY%` or `Ask`; the snapshot never includes the key. While it
is not ready, the hub shows Play disabled with "CD key required" and a link to
[tmuf-cd-key.md](tmuf-cd-key.md), which explains how Steam owners get the key.
Titles without the item, such as Nations Forever, are never gated. The gate is
in the hub only; the Game app and `trackmania-mac play` don't check the key.

## Backend contract

The Swift interface is an adapter over three shell commands:

```sh
trackmania-mac hub snapshot
trackmania-mac hub run TITLE ACTION [--source ABSOLUTE_PATH] [--runtime download|build] [--include-saves]
trackmania-mac hub migrate
```

`snapshot` is read-only and returns one versioned JSON document describing every
registered title, its install state, app paths and currently available actions.
It never starts Wine. `run` validates the title, action and optional source path,
then starts the normal CLI/installer in a fresh process with `TRACKMANIA_MAC_TITLE` set
before title-dependent paths are calculated. This prevents one title from using
another title's prefix or game path. Only the `install` and `install-platform`
actions accept `--runtime`; it is the only way `run` sets `TRACKMANIA_MAC_RUNTIME_SOURCE`,
which is never inherited from the hub's environment. Only the `uninstall`
action accepts `--include-saves`.

Each title entry carries `icon` (an absolute path or `null`) and `iconSource`
(`"game"`, `"bundled"` or `null`). The icon is chosen in this order: the `.icns`
extracted from the locally installed game executable
(`icons/<id>.icns` in the data folder, `~/Library/TrackManiaMacOS`; never
redistributed),
then the project's own artwork as a PNG, then as an SVG (`assets/titles/<id>`).
Clients should load either format; `iconSource` only says whether the art is the
game's own. Each title also installs its own launcher support directory,
`launchers/<id>` in the data folder, so installing one title's apps never
changes what another title's apps launch.

Each title entry also carries `locations`, the folders the hub can show or
open. `userdata` is the title's saves folder:

```json
"locations": {
  "root": "/Users/me/Library/TrackManiaMacOS/games/tmnf",
  "prefix": "/Users/me/Library/TrackManiaMacOS/games/tmnf/prefix",
  "gameDir": "/Users/me/Library/TrackManiaMacOS/games/tmnf/prefix/drive_c/Program Files (x86)/TmNationsForever",
  "userdata": "/Users/me/Library/TrackManiaMacOS/saves/tmnf",
  "logs": "/Users/me/Library/TrackManiaMacOS/logs"
}
```

The hub's **Show in Finder** item opens the game folder (`gameDir`, else
`prefix`, else `root`) and **Open Logs Folder** opens `logs`. Nothing in the hub
opens the saves folder.

## Uninstalling a title

The snapshot lists an `uninstall` action for every title that left something
behind: its prefix, its Game or Settings app, its launcher support directory,
or only its saves folder. Each title entry also carries `removal`, the paths an
uninstall would remove, so the confirmation dialog can name them:

```json
"removal": {
  "items": [{"kind": "prefix", "label": "Wine prefix and game files", "path": "/Users/me/Library/TrackManiaMacOS/games/tmnf/prefix"}],
  "saves": "/Users/me/Library/TrackManiaMacOS/saves/tmnf"
}
```

`kind` is one of `prefix`, `game-app`, `settings-app`, `launcher`, `leftover`,
`cache`, `logs` and `icon`; `saves` is `null` when the title has no saves
folder. `leftover` is a folder an interrupted uninstall renamed but did not
finish deleting.
Error entries carry `"removal": null`. The preview only checks what exists; the
full checks run when the action does.

`trackmania-mac hub run TITLE uninstall` runs `trackmania-mac uninstall --yes` for
that title. It
removes the title's prefix, its two app wrappers (only our own, marked with
`.trackmania-mac-app` or an earlier release's marker, and carrying the title's
current or earlier bundle identifier), its launcher support
directory, its cached installer, its logs and diagnostics reports, and its
extracted icon. It keeps the saves folder, `saves/<id>` in the data
folder, which is the prefix's Windows Documents folder. `--include-saves` removes
only that folder, and only at its default location; save folder names overlap
between titles, so a custom or shared folder is never removed.

An uninstall never removes the Wine runtime, the shared downloads (DirectX,
TMLoader, OpenAL Soft), a title root (`games/<id>`, which holds the title's
operation lock), or another title's files. Every title has its own root and
saves folder, and none holds another's. The action refuses
while the game is running or another operation holds the prefix, and an app
that is not ours, a symlink or an older launcher app is reported and kept.

The guard plans and removes in one Python process; the shell only confirms and
prints, and never reads a plan back. Each folder is first renamed to a tombstone
(`.<name>.trackmania-mac-removing.<hex>`) in its own parent folder, so a removal
that stops halfway is finished by the next uninstall. Tombstones are only
recognised where the original folder would be accepted: prefix tombstones in the
title's own root, saves tombstones as `saves/.<id>.trackmania-mac-removing.<hex>`,
launcher tombstones as `launchers/<id>`. Tombstones that an earlier release
left, named `.tmnf-removing`, are recognised and finished the same way. Keeping
saves never deletes a saves tombstone: it is renamed back to `saves/<id>` when
that name is free, and kept otherwise; the snapshot then reports it as `saves`. A planned
folder that is itself a mount point is kept ("is a mounted volume; unmount it
first") and left out of `removal`; a prefix or saves folder with a volume
mounted inside it is kept too, naming the mount point, before anything is
renamed. Paths are compared by device and
inode, not by spelling, because APFS is case-insensitive; removal refuses a
path or parent that changed after it was checked, a folder with another volume
mounted inside it, and any path containing a control character. For titles
other than Nations Forever, the download folder `cache/games/<id>` holds no
ownership marker, so only the pinned installer, its partial downloads and its unpacked
staging are removed, and the folder only once it is empty. When
it finishes, the snapshot shows the title as not installed.

## Moving from an earlier release

Earlier releases kept files in `~/Games/TMNF` and in `TMNF` folders under
`~/Library`; everything now lives in `~/Library/TrackManiaMacOS`. The snapshot
is schema 3 and carries a top-level `migration` object that says whether files
still wait to move:

```json
"migration": {
  "state": "pending",
  "reason": null,
  "from": ["/Users/me/Games/TMNF", "/Users/me/Library/Application Support/TMNF"],
  "to": "/Users/me/Library/TrackManiaMacOS",
  "partial": false
}
```

`state` is `none` (nothing to move), `pending` (the hub moves the files now) or
`blocked` (`reason` says why and what to do; nothing was moved). `from` lists
the old folders that still hold something. `partial` is `true` when an earlier
move stopped halfway: the installed apps may not work until it finishes, and the
hub and the command line say so. While `state` is not `none`, every
title has `"actions": []` and `trackmania-mac hub run` refuses, so no action can
install a second copy beside the old one.

The hub runs `trackmania-mac hub migrate` once per launch when the state is
`pending` and shows a full-window progress view in place of the title list.
When the state is `blocked` it shows the reason and a **Try Again** button.
On the command line, `trackmania-mac migrate` does the same move and exits with
status 0 when it is done or nothing needed to move, 75 when a game or another
TrackMania task is running, and 1 when the move is blocked or failed. Title
commands (`install`, `play`, `uninstall` and the others) run the move first and
refuse to continue when it is blocked. The move is skipped only when
`TRACKMANIA_MAC_ROOT`, `TRACKMANIA_MAC_PREFIX` or `TRACKMANIA_MAC_USERDATA` is
set, because those are a location you chose.

The move only renames whole folders, so nothing is copied, overwritten or
deleted inside a saves folder, and every folder exists under exactly one name at
any moment. It checks everything before the first rename: that all folders are
on the same disk, that no game is running, that the target names are free and
that the prefixes carry the project's ownership marker, current or earlier. A
journal in the data folder (`.migration.json`) records each planned rename; a
failure renames everything back, and an interrupted move resumes the next time
the hub or a command runs. Old folders are removed only once they are empty.
Locations you set with `TRACKMANIA_MAC_SUPPORT` or `TRACKMANIA_MAC_CACHE`, and a
prefix without our marker, stay where they are. The move also converts the
Game and Settings apps: their markers and bundle identifiers (`local.tmnf.*`)
become `local.trackmania-mac.*`, and their executable (the process name) becomes
`TrackManiaMac` in place of `TMNF`; the old ones are still recognised until
then. The apps keep using their launcher's code and runtime version until the
next Update; only the paths they record change. The hub's own bundle identifier
is now `local.trackmania-mac.hub`.

## Titles

| Title | Install source | Starts with | Status |
| --- | --- | --- | --- |
| Nations Forever (`tmnf`) | free Nadeo download | Competition Patch and CoreMod | supported |
| United Forever (`tmu`) | pinned official retail installer; asks for a serial key | Competition Patch and CoreMod | provisional |
| Original (`tm-original`) | user-owned retail disk image or setup executable | its own game executable | unsupported |
| TrackMania 2 Canyon (`tm-canyon`) | pinned official ManiaPlanet trial | its own game executable | unsupported |
| Turbo (`tm-turbo`) | existing Steam game folder | its own game executable | unsupported |
| Nations ESWC (`tmn-eswc`) | pinned official free installer | its own game executable | unsupported |
| TrackMania 2 Stadium (`tm-stadium`) | pinned official ManiaPlanet trial | its own game executable | unsupported |
| TrackMania 2 Valley (`tm-valley`) | pinned official ManiaPlanet trial | its own game executable | unsupported |
| TrackMania 2 Lagoon (`tm-lagoon`) | pinned official ManiaPlanet trial | its own game executable | unsupported |
| Trackmania 2020 (`tm-2020`) | existing Trackmania game folder | its own game executable | unsupported |

Nations Forever exposes install and update actions; install also writes the app
wrappers. Each title entry's `status` is one of:

- `supported`: installs and plays; changes are developed and checked against it.
- `provisional`: installs, but its renderer, DRM, online services and complete
  gameplay have not been qualified under Wine.
- `unsupported`: listed for completeness; not expected to run yet.

Clients treat any other value as `unsupported`, and so does the snapshot for an
entry it cannot read. On an unsupported title the hub adds a short note: please
don't open an issue just to say it doesn't work; issues and pull requests that
help it run are welcome.

Each title entry also carries `link`, a related page shown under its notes, as
`{"label": "...", "url": "https://..."}`, or `null`. It comes from the
registry's optional `link_url` (https only) and `link_label` (one line, at most
60 characters), which are set together or not at all. Trackmania 2020 uses it
for the separate Trackmania2020-MacOS project. The hub opens only https links.

Each title entry also carries `releaseDate`, the title's first release as
`YYYY-MM-DD` (the registry's required `release_date`; `null` only for an entry
that could not be read). The snapshot lists titles oldest release first, ties
broken by id, followed by entries that could not be read. The hub keeps that
order inside its "installed" and "available" sidebar sections and does not
re-sort by status; with nothing selected it selects the first installed title,
else the first supported one.

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

## Diagnostics

The action **Create diagnostics report** (`diagnostics`, in the **⋯** menu) is
offered once a title's prefix exists. It writes one zip,
`diagnostics-<id>-<stamp>.zip`, to the logs folder, and the report is meant to
be attached to a bug report. It holds:

- `summary.txt`: the project version, the title id, macOS version, machine
  architecture and whether Rosetta is present.
- `doctor.txt`: the output of `trackmania-mac doctor` for that title, with its exit
  status. A doctor that fails or runs longer than two minutes is recorded and
  does not stop the report.
- `snapshot.json`: that title's entry from the hub snapshot.
- `logs/`: up to the five most recent logs for that title only, each cut to its
  last 2 MiB.

Every file has the home folder replaced by `~` and the user name by `<user>`
before it is zipped.

## Packaging and signing

Building, signing, notarizing and releasing the hub app are covered in
[building.md](building.md).
