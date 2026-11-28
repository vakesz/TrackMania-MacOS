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
- **TrackMania Settings** opens the original settings launcher. For a title
  with Competition Patch, the launcher's Play button starts the game the same
  way as the Game app (see `docs/runtime.md`).

The hub opens those app wrappers through macOS; it does not replace them.
For titles that need a serial (`requiresSerial`), the hub replaces the Settings button
with "Register player key", which opens the same Settings app, and notes that the key is
entered there before pressing Play. Once a title's `cdKey` item (below) is ready, the
key is registered and the plain Settings button comes back.

A title with `cd_key_in_nadeo_ini` in its registry entry (United Forever)
also carries a `cdKey` item in `state` once its game files are installed. It is
ready when the game launcher has registered a key, which writes a non-empty
`Config/pkey.dat` in the title's user folder, or when the `[TmForever]` section
of the game folder's `Nadeo.ini` has an `InstallKey` that is not empty,
`%CDKEY%` or `Ask` (Steam copies). The snapshot never includes the key. While it
is not ready, the hub shows Play disabled with "CD key required" and a link to
[tmuf-cd-key.md](tmuf-cd-key.md), which explains how Steam owners get the key.
Titles without the item, such as Nations Forever, are never gated. The gate is
in the hub only; the Game app and `trackmania-mac play` don't check the key.

A title with `cd_key_in_system_config` (Sunrise eXtreme) carries the same
`cdKey` item. Its launcher stores the registered key in the text
`Default.SystemConfig.Gbx` in the game folder, as the third value of the
settings chunk 0x0B005012, which is empty until a key is registered. The item
is ready when that value is not empty. The hub reads the file only to check
this, never through a symlink, and the snapshot never includes the key. No
installer step writes this file for the title, so the key is never replaced.

## Backend contract

The Swift interface is an adapter over four shell commands:

```sh
trackmania-mac hub snapshot
trackmania-mac hub run TITLE ACTION [--source ABSOLUTE_PATH] [--runtime download|build] [--include-saves]
trackmania-mac hub run TITLE set-defaults [--window-size fullscreen|WxH] [--audio on|off] [--x87 on|off]
trackmania-mac hub run TITLE rename-apps --app-name=NAME | --reset-app-name
trackmania-mac hub migrate
trackmania-mac hub clean downloads|runtimes|logs
```

`snapshot` is read-only and returns one versioned JSON document describing every
registered title, its install state, app paths and currently available actions.
It never starts Wine. `run` validates the title, action and optional source path,
then starts the normal CLI/installer in a fresh process with `TRACKMANIA_MAC_TITLE` set
before title-dependent paths are calculated. This prevents one title from using
another title's prefix or game path. Only the `install` and `install-platform`
actions accept `--runtime`; it is the only way `run` sets `TRACKMANIA_MAC_RUNTIME_SOURCE`,
which is never inherited from the hub's environment. Only the `uninstall`
action accepts `--include-saves`, and only `set-defaults` accepts
`--window-size`, `--audio` and `--x87` (see [Settings](#settings)), and only
`rename-apps` accepts `--app-name` or `--reset-app-name`, exactly one of them
(see [App names](#app-names)). `migrate` and
`clean` are the commands that are not per title.

`run`, `migrate` and `clean` print plain progress lines on stdout. They exit
with status 0 when done, 75 while a game or another TrackMania task is running,
and 1 when they refuse or fail; a refusal ends with one stderr line that starts
with `error: ` followed by a sentence that says why.

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
directory, its cached installer, its logs and diagnostics reports, its
extracted icon and, last, its `app-name` file (see [App names](#app-names)). It keeps the saves folder, `saves/<id>` in the data
folder, which is the prefix's Windows Documents folder. `--include-saves` removes
only that folder, and only at its default location; save folder names overlap
between titles, so a custom or shared folder is never removed.

A title whose game writes saves inside its game folder lists those folders in
its registry `game_save_dirs` (Nations ESWC). They are kept in the saves folder
and linked from the game folder (see
[Saves in the game folder](runtime.md#saves-in-the-game-folder)), so removing
the prefix removes only the links. An uninstall that keeps saves lists any
files still in those folders as moved to the saves folder first, moves them
only once the removal is confirmed (`--yes` counts), and then refuses ("Saves
are still in the game folder") while files remain there. Files the prefix would
take with it count wherever a symlink inside the prefix leads, and in every
case-spelling of a folder name. `--include-saves` removes them with the prefix.
The snapshot does not change.

An uninstall never removes the Wine runtime, the shared downloads (DirectX,
TMLoader, OpenAL Soft; [Settings](#settings) can clear them), a title root (`games/<id>`, which holds the title's
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
| United Forever (`tmu`) | pinned official retail installer; asks for a serial key | Competition Patch and CoreMod | supported |
| Original (`tm-original`) | user-owned retail disk image or setup executable | its own game executable | unsupported |
| Sunrise (`tm-sunrise`) | user-owned retail disk image or setup executable | its own game executable | unsupported |
| Sunrise eXtreme (`tm-sunrise-extreme`) | user-owned retail Sunrise disk image or setup executable, then the pinned official eXtreme 1.5.0 update and 1.5.1 patch and a community StarForce-free build; asks for a serial key | its own game executable | provisional |
| TrackMania 2 Canyon (`tm-canyon`) | pinned official ManiaPlanet trial | its own game executable | unsupported |
| Turbo (`tm-turbo`) | existing Steam game folder | its own game executable | unsupported |
| Nations ESWC (`tmn-eswc`) | pinned official free installer (you click through its setup), official 1.7.9 update and community StarForce-free 1.8.0 build | its own game executable | provisional |
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

Each title entry's `sourceSize` is the total size in bytes of the title's pinned
downloads: the installer, its updates and its overlay.

`config/titles/*.json` is the sole authority for title paths, app names and
bundle identifiers; each title's prefix marker is derived from its id. The shell
reads the registry; Swift only renders the machine-readable snapshot.

## Retail import

The native app uses macOS's file picker, then passes the chosen absolute path
to the backend. A retail title takes its disc image (`.iso` or `.dmg`), a
folder with the disc's files (a copy, or the mounted disc itself) or the
setup program. The backend mounts an image read-only for the whole import and
runs the one setup program (`*setup*.exe`, any case) at the top of the disc;
it stops when it finds none or more than one, so choose the `.exe` then. The
retail installer runs interactively in that title's managed prefix; choose its
game install location and the hub checks for the expected executable when
setup closes. A Steam copy takes its game folder. Steam copies are copied into the managed prefix
without following symlinks. Serial keys are entered in the game and are never
stored by this project.

A retail title with pinned official updates in its registry entry (Sunrise
eXtreme) downloads and verifies them before the setup starts, so a failed
download costs no clicks. When the setup closes, the updates run silently in
order, and each one that finishes is recorded in
`drive_c/trackmania-mac-updates.done` in the prefix. If an update stops, the
game state says the updates are not finished and the hub offers the retail
installer again: choose the same disc or setup file, and the import skips the setup
and runs only the updates that are not recorded yet.

A retail title may also have an overlay and files to mark with the memory
protection flag (Sunrise eXtreme: a community build without StarForce, then six
files, see `docs/runtime.md`). The import runs them after the updates and then
checks the game executable against the registry's pinned build. Until the
executable is that build, the game state says the remaining install steps are
not finished and the hub offers the retail installer again; that import skips
the setup too and repeats only the steps after it.

The hub keeps the sequence explicit: it offers shared-platform setup until the
runtime, prefix, DirectX and renderer are ready; it then offers import. Once
the game files are present, installing the two macOS app wrappers becomes the
next primary action.

## Diagnostics

The action **Create diagnostics report** (`diagnostics`, in the **⋯** menu) is
offered once a title's prefix exists. It writes one zip,
`diagnostics-<id>-<stamp>.zip`, to the logs folder, and the report is meant to
be attached to a bug report. It holds:

- `summary.txt`: the project version, the runtime the title's apps use, the
  title id, macOS version, machine architecture, chip and whether Rosetta is
  present.
- `doctor.txt`: the output of `trackmania-mac doctor` for that title, with its exit
  status. A doctor that fails or runs longer than two minutes is recorded and
  does not stop the report.
- `snapshot.json`: that title's entry from the hub snapshot.
- `logs/`: up to the five most recent logs for that title only, each cut to its
  last 2 MiB.

Every file has the home folder replaced by `~`, the user name by `<user>` and
the Mac's serial number by `<serial>` before it is zipped.

On success the action prints two lines, the second always last:

```text
Report form: https://github.com/vakesz/TrackMania-MacOS/issues/new?template=bug_report.yml&version=…&runtime=…&system=…&doctor=…
Diagnostics report: /Users/me/Library/TrackManiaMacOS/logs/diagnostics-tmnf-20260101-120000.zip
```

The report form link opens the bug report form
(`.github/ISSUE_TEMPLATE/bug_report.yml`) with these fields filled in, each
redacted the same way as the zip:

- `version`: the project version.
- `runtime`: the runtime the title's installed apps use, or `apps not
  installed`.
- `system`: the macOS version and build and the chip.
- `doctor`: the title, then only the doctor's `warning:`, `error:` and
  `Result:` lines and its exit status: at most 15 lines, each cut to 200
  characters. Lines are dropped from the end, with a note that the rest is in
  the report, until the whole link fits in 6,000 characters.

### Report a problem

**Help › Report a Problem…**, and the **Report a Problem…** button on the card
of a failed action, run the `diagnostics` action for that title. When it
finishes, the hub shows the zip in Finder and opens the report form link in
the browser. GitHub can't attach a file through a link, so the form asks the
reporter to drag the zip in. The hub opens only a link to this repository's
`issues/new`. The Help menu item uses the selected title. When that title
has no `diagnostics` action yet (no prefix), it opens the blank bug report
form instead. **Save diagnostics report…** in Settings writes the same zip
without opening the form.

## Settings

The hub's Settings window (the gear at the bottom left of the sidebar, or
Cmd-comma) shows the space the hub can free and each game's launch defaults.

### Storage

The snapshot carries a top-level `storage` object, or `null` when it cannot be
computed. Sizes are in bytes; a size is `null` when it could not be measured
quickly (a large source-build workspace, say).

```json
"storage": {
  "downloads": {"bytes": 1982400580, "items": [{"label": "DirectX June 2010 redistributable", "path": "/Users/me/Library/TrackManiaMacOS/cache/directx_Jun2010_redist.exe", "bytes": 100275120}]},
  "runtimes": {"current": "0.5.0", "items": [{"version": "0.4.3", "path": "/Users/me/Library/TrackManiaMacOS/runtime/0.4.3", "bytes": 639007885}], "inUse": ["0.5.0"], "blocked": null},
  "logs": {"bytes": 294534, "path": "/Users/me/Library/TrackManiaMacOS/logs"},
  "error": null
}
```

`downloads.items` and `runtimes.items` list exactly what the matching `clean`
removes. `error` names a part that could not be listed. `trackmania-mac hub
clean KIND` runs `trackmania-mac clean KIND`, which removes:

- `downloads`: only files the hub knows by name in the downloads folder
  (`cache`): each game's installer with its unfinished downloads and unpacked
  copy, the DirectX, TMLoader and OpenAL Soft downloads in any version, runtime
  archives, runtime source-build workspaces (`cache/build`) and unpacked
  downloads left for more than an hour. Lock files, the session helper
  (`game-state-*.exe`), disk images being imported and any file it does not
  know stay. Everything removed is downloaded again when an install needs it.
- `runtimes`: folders in `runtime/` that carry a runtime marker and that
  neither this release nor any installed launcher uses. A launcher uses the
  runtime its `config/installation.sh` records, else the one of its own release;
  `inUse` lists them. When a launcher's settings cannot be read, nothing is
  removed and `blocked` says which launcher to reinstall.
- `logs`: every launch log and its rotated copies that no game is writing (its
  `.lock` can be locked), and every diagnostics report. Lock files stay.

Downloads and runtimes are removed holding the app lock and every game's
prefix lock with Wine inactive, so they wait for no install, update or game:
while one runs the command exits with status 75 and removes nothing. Removal
uses the same checks as an uninstall: a folder is renamed to a tombstone and
deleted only while its device and inode still match, a mount point is never
entered, and the data folder, the downloads, runtime, launcher and logs
folders themselves, every game's folders and every runtime in use are never
removed. No `clean` runs while files of an earlier release wait to move.

### Game defaults

Each title entry carries `launchOptions`, the defaults its installed launcher
records in `launchers/<id>/config/installation.sh`, or `null` when the title
has no installed apps:

```json
"launchOptions": {"windowSize": "fullscreen", "audio": "on", "x87": "on", "renderer": "d9vk", "editable": true, "reason": null}
```

`windowSize` is `"fullscreen"` or `"<width>x<height>"` with each side from 1 to
65535; `audio` and `x87` are `"on"` or `"off"`. `renderer` is shown only: the
hub does not change it. A setting the file does not record reads as its
default. The file is read without running it, and only lines in the exact form
the installer writes are accepted; otherwise `editable` is `false` and `reason`
says to reinstall the apps. A recorded value that is not valid reads as its
default, with `reason` naming it, and the next change replaces it.

`trackmania-mac hub run TITLE set-defaults` with at least one of the flags runs
`trackmania-mac defaults` for that title, which checks the values the way
`play` does and rewrites only those lines. It holds the title's prefix lock and
the app lock, so it exits with status 75 while the game runs. The change
applies the next time the game starts. Installing or updating the apps keeps
the recorded defaults; a `TRACKMANIA_MAC_WINDOW_SIZE`, `_AUDIO`, `_X87` or
`_RENDERER` set in the environment of that install wins.

### App names

Each title entry carries `appNames`, the names of its installed Game and
Settings apps, or `null` unless both apps are installed:

```json
"appNames": {"name": "TMU", "default": "TrackMania United Forever", "custom": true,
             "game": "TMU.app", "settings": "TMU Settings.app", "editable": true, "reason": null}
```

`name` is the Game app's name without `.app` and `default` the registry's.
`custom` is `true` when the title's `app-name` file sets the names, including a
file that is not valid. `editable` follows the same rule as
`launchOptions.editable`, and `reason` says why the apps can't be renamed, or
that a recorded name is not valid and should be reset. The snapshot does not
say whether the game is running; the hub disables renaming while it is.

`trackmania-mac hub run TITLE rename-apps --app-name=NAME` runs
`trackmania-mac rename-apps --name=NAME` for that title, which names the apps
`NAME.app` and `NAME Settings.app`. `--reset-app-name` runs
`trackmania-mac rename-apps --reset`, which gives them the registry's names
again; a name equal to the registry's (ignoring case) counts as a reset. A name
is trimmed and must be at most 64 characters, must not contain `/`, `:` or
control characters, must not start with a dot and must not end in `.app`. It
can't be a name another title's apps have or would have, or the name of
anything else already in the apps folder. The command holds the title's prefix
lock and the app lock, so it exits with status 75 while the game runs.

The apps are moved, not written again, so a Dock item usually keeps working;
then their bundle names and the paths the launcher records are updated. The
name is kept in `games/<id>/app-name` in the data folder, one line. Reinstalling
the apps or Update writes them under that name, and uninstalling the title
removes the file after the apps. A `TRACKMANIA_MAC_APP` or
`TRACKMANIA_MAC_SETTINGS_APP` set in the environment ignores the file, and
`rename-apps` then refuses to run.

## Packaging and signing

Building, signing, notarizing and releasing the hub app are covered in
[building.md](building.md).
