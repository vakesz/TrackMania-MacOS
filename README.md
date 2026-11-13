# TrackMania Mac

**Play TrackMania games on an Apple Silicon Mac.** One free app, the TrackMania
Mac hub, installs and launches a collection of TrackMania titles and sets up
what they need: a patched Windows compatibility layer (Wine), DirectX and, for
Nations and United Forever, the Competition Patch. You don't need Windows, a
virtual machine or developer tools. Nations Forever is supported today, United
Forever is provisional and the other games are listed but unsupported (see
[Which games work?](#which-games-work)).

**Website:** <https://tmmac.vakesz.com/> ·
**Download:** [latest release](https://github.com/vakesz/TrackMania-MacOS/releases/latest)

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/images/hub-dark.png">
  <img src="docs/images/hub.png" alt="The TrackMania Mac hub app with Nations Forever selected, showing the Install button and setup checklist" width="900">
</picture>

This is an independent community project, not affiliated with Nadeo or Ubisoft.
Games and Microsoft DirectX are downloaded from their original hosts or supplied
by you. They are not redistributed here.

> [!NOTE]
> Looking for Trackmania 2020? [Trackmania2020-MacOS](https://github.com/V-Roman-V/Trackmania2020-MacOS) is a separate community project, inspired by this one.  
> It isn't part of this hub for now, but it could be integrated later.

## What you need

- A Mac with Apple Silicon (M1 or later) running macOS 14 Sonoma or later.
  Intel Macs are not supported.
- macOS 15.4 or later for the fastest physics. On older versions the
  x87sidecar accelerator can't hook Rosetta, so the game runs on stock Rosetta
  and is slower. The installer warns when this happens.
- An internet connection and a few gigabytes of free disk space.
- Rosetta 2. If it's missing, the installer adds it for you.

## Quick start

1. **Download** `TrackMania-Mac-<version>.app.zip` from the
   [latest release](https://github.com/vakesz/TrackMania-MacOS/releases/latest).
2. **Unzip it** and drag **TrackMania Mac.app** into your **Applications** folder.
3. **Open it.** If macOS says it can't verify the app, open
   **System Settings > Privacy & Security**, scroll down and click
   **Open Anyway**. You only have to do this once.
4. **Pick a game** in the sidebar. If you're not sure, choose TrackMania
   Nations Forever, the only supported title, and click **Install or repair**.
   Choose **Download prebuilt (recommended)** when asked how to get the
   runtime. Downloading takes a few minutes; building it yourself takes hours.
   The other games ask for what they need: a retail installer, a disc image or
   a Steam folder.
5. **Play.** When the setup checklist is all green, click **Play** in the hub,
   or open the game's app from your Applications folder.

Each installed game adds a Game app and a Settings app named after the game to
your Applications folder. For Nations Forever they are:

| App | What it does |
| --- | --- |
| **TrackMania Nations Forever** | Starts the game with Competition Patch. Use this to play. |
| **TrackMania Settings** | Opens the game's own launcher to change graphics, resolution and audio. Close the game first, then save and close Settings before you play. |

### Your first launch on Nations or United Forever

- Starting the game takes a while before a window appears. The window can open
  **behind other windows**, so check Mission Control or the Dock.
- The game shows its own **"Network check"** dialog. Click **OK**. The account
  screen comes next.
- The game opens in the display mode chosen in **TrackMania Settings**
  (fullscreen or windowed, at the resolution you pick). With more than one
  monitor, fullscreen uses the primary display.

| In game | Main menu |
| --- | --- |
| ![Nations Forever racing on an M1 Pro with the DXVK FPS overlay](docs/images/ingame.png) | ![Nations Forever main menu with Competition Patch and CoreMod loaded](docs/images/menu.png) |

Both screenshots are from an M1 Pro in a 1280x720 window with the DXVK frame
rate overlay enabled (`DXVK_HUD=fps,frametimes`).

## Which games work?

| Title | Where the game comes from | Status |
| --- | --- | --- |
| Nations Forever (`tmnf`) | free download from Nadeo | supported |
| United Forever (`tmu`) | official retail installer (needs your serial key); starts with Competition Patch | provisional |
| Original (`tm-original`) | your own retail disc image or setup file | unsupported |
| TrackMania 2 Canyon (`tm-canyon`) | official ManiaPlanet trial | unsupported |
| Turbo (`tm-turbo`) | your existing Steam game folder | unsupported |
| Nations ESWC (`tmn-eswc`) | official free installer | unsupported |
| TrackMania 2 Stadium (`tm-stadium`) | official ManiaPlanet trial | unsupported |
| TrackMania 2 Valley (`tm-valley`) | official ManiaPlanet trial | unsupported |
| TrackMania 2 Lagoon (`tm-lagoon`) | official ManiaPlanet trial | unsupported |
| Trackmania 2020 (`tm-2020`) | your existing Trackmania game folder | unsupported |

**Supported** means the game installs and plays, and it is the title changes
are developed and checked against. **Provisional** means the title installs,
but graphics, copy protection, online play and full gameplay have not been
tested enough under Wine to call it supported. **Unsupported** means the title
is listed for completeness but is not expected to run yet. See [the hub
guide](docs/hub-app.md) for details. If you try a provisional title, reports of
what works and what doesn't are welcome as
[issues](https://github.com/vakesz/TrackMania-MacOS/issues).

For unsupported titles, please don't open an issue just to say the title
doesn't work. Issues that help make it work or bring in a proper runtime for
it, and pull requests that add support, are welcome.

## Common questions

**Is it free?** Yes. The hub and this project are free and open source.
Nations Forever is a free game; the other games need your own copy.

**The game window doesn't show up.** Give it a minute, then look in Mission
Control or click the game in the Dock. Windows can open behind others.

**How do I change graphics settings?** Close the game, open
**TrackMania Settings**, change what you want, save, close it, then start the
game again. A new install starts with conservative settings (Nice shaders,
post-processing and stadium water geometry off, at most six opponents) so it
runs well on any Apple Silicon Mac.

**The first lap on a new track stutters.** The renderer builds its shader cache
the first time it sees a track, and later visits are smoother. See
[runtime fixes and troubleshooting](docs/runtime.md).

**Where are my saves and replays?** For Nations Forever, in
`~/Library/TrackManiaMacOS/saves/tmnf/TmForever`. Other titles keep theirs in
`saves/<id>` in the same folder. Finder hides your `Library` folder: choose
**Go > Go to Folder** in Finder and paste the path. See
[where files go](#where-files-go) for the layout and everything else.

**How do I uninstall?** In the hub, select the game, click the **⋯** button and
choose **Uninstall…**. The dialog lists what it removes for that game: its Wine
prefix, its Game and Settings apps, its launcher files, its downloaded
installer and its logs. Choose **Remove game, keep saves** to keep your saves,
or **Remove everything including saves** to also delete the game's saves
folder. The runtime and the downloads other games share are always kept.

**Something went wrong.** In the hub, select the game, click **⋯ > Create
diagnostics report** and attach the zip, which is written to the logs folder
(**⋯ > Open Logs Folder**), when you
[open an issue](https://github.com/vakesz/TrackMania-MacOS/issues). It holds
the checks, your setup state and recent logs for that game, with your home
folder and user name removed. The report is offered once the game's prefix
exists.

---

## For advanced users

Everything below is optional. The hub app above is all you need to play.

You'll need the Xcode command-line tools for `make`. Close any running
TrackMania session before you install or update.

### Install from a source checkout

From the repository root, this installs a game with the runtime chosen by
`RUNTIME`. The `make` targets act on the title in `TRACKMANIA_MAC_TITLE`
(default `tmnf`, Nations Forever); `./src/shell/cli/trackmania-mac titles` lists
the titles. From a checkout with your real home folder, any title command
moves an existing install from an earlier release first, unless
`TRACKMANIA_MAC_ROOT`, `TRACKMANIA_MAC_PREFIX` or `TRACKMANIA_MAC_USERDATA` is
set (see [moving from an earlier release](#moving-from-an-earlier-release)):

```sh
make install                  # auto: a local build if present, else download
make install RUNTIME=download # always the prebuilt release runtime
make install RUNTIME=build    # compile the runtime here, then install it
```

`make setup` takes the same `RUNTIME` choice. `build` has the prerequisites
listed under [Build the runtime yourself](#build-the-runtime-yourself). An
already installed runtime is never replaced.

To install from an archive you already downloaded instead, point the installer
at it and give the checksum from the release:

```sh
version="$(. src/shell/lib/version.sh && trackmania_mac_project_version .)"
TRACKMANIA_MAC_RUNTIME_ARCHIVE="$HOME/Downloads/trackmania-mac-${version}-runtime.tar.xz" \
TRACKMANIA_MAC_RUNTIME_SHA256="<checksum from the release>" make install
```

If macOS refuses to open the game after a manual download, the archive picked up
a quarantine flag in the browser. Clear it, then install:

```sh
version="$(. src/shell/lib/version.sh && trackmania_mac_project_version .)"
xattr -dr com.apple.quarantine "$HOME/Downloads/trackmania-mac-${version}-runtime.tar.xz"
```

Archives that `make install` fetches with `curl` are not quarantined, so this
step is only for a browser download.

The installed apps go in `/Applications` when it is writable, otherwise in
`~/Applications`. They use their installed support files, not the hub bundle or
a source checkout.

### Build the runtime yourself

This compiles the whole runtime from the pinned sources instead of downloading
it, then installs the game. Install [Homebrew](https://brew.sh) and the full
Xcode app, then select Xcode:

```sh
sudo xcode-select --switch /Applications/Xcode.app/Contents/Developer
make build
make install
```

Use a checkout path without spaces for source builds, as required by Wine's
makefiles. Installed games and runtime folders support spaces.

`make build` downloads pinned sources, verifies archive hashes and Git revisions,
applies the repository patches, then compiles Wine, D9VK, x87sidecar and the
session helper. It writes the complete runtime to `.build/release/runtime`.
Afterwards `make install` detects and installs that local runtime instead of
downloading one. `make install RUNTIME=build` does both steps, and in a hub
install it clones the commit that hub was built from (git comes with Xcode) into
the `build` folder of the download cache, builds there and deletes that folder after a
successful install. You need Apple Silicon, Rosetta 2, full Xcode and Homebrew
(missing formulae are installed with `brew install`). Allow several gigabytes
of disk space and a substantial first build. Builds use every CPU core by
default; use `TRACKMANIA_MAC_JOBS=4 make build` to leave some free.

The deployment target is macOS 14. x87sidecar also depends on the installed
Rosetta version: it supports Rosetta from macOS 15.4 on, and on anything older
the game runs without it. See
[building and releasing](docs/building.md).

### Everyday commands

The `make` targets use the title in `TRACKMANIA_MAC_TITLE` (default `tmnf`). For
example, `TRACKMANIA_MAC_TITLE=tmu make doctor` checks United Forever.

| Command | Action |
| --- | --- |
| `make play` | Start the same game as the app |
| `make diag` | Play with session diagnostics, then report frame rate by game state |
| `make report` | Show the report of the last diagnostic session again |
| `make configure` | Open the original settings launcher |
| `make doctor` | Check runtime, game, renderer, audio and mod installation |
| `make stop` | Stop Wine processes in this game's prefix |
| `make install` | Install missing files and refresh the app launchers |
| `./src/shell/cli/trackmania-mac play-vanilla` | Run the official game without Competition Patch |
| `./src/shell/cli/trackmania-mac migrate` | Move an install from an earlier release into the data folder (see [moving from an earlier release](#moving-from-an-earlier-release)) |
| `./src/shell/cli/trackmania-mac uninstall` | Remove this game's prefix, apps, launcher, installer and logs after confirmation; add `--include-saves` to remove its saves too |

To force a fixed windowed size, set `TRACKMANIA_MAC_WINDOW_SIZE=1280x720` (any `WxH`).
That runs the game inside a separate Wine virtual desktop for this session.
Each dimension must be between 1 and 65535.

The session supervisor tracks both settings and game executables, so a launcher
exit alone does not authorize cleanup. Normal quits are quiet; failed or
uncertain sessions show an alert with the log location. Uncertainty does not
authorize killing a game that may still be running. Use `make stop` when you
explicitly want to stop the selected prefix.

### Where files go

Everything lives in one folder, `~/Library/TrackManiaMacOS`. Finder hides your
`Library` folder: choose **Go > Go to Folder** and paste the path to open it.

| Files | Location in that folder |
| --- | --- |
| Game and Wine prefix | `games/<id>/prefix` |
| Saves, settings and replays | `saves/<id>`; for Nations Forever `saves/tmnf/TmForever` |
| Runtime | `runtime/<version>` |
| Installed launcher scripts and game icons | `launchers/<id>` and `icons/<id>.icns` |
| Download cache and runtime build clones | `cache` |
| App launch logs and diagnostics reports | `logs` |

`<id>` is the title id from the table above, such as `tmnf` or `tmu`. Older
local installations may still save to `~/Documents/TmForever`; the installer
keeps that existing arrangement. Uninstalling keeps the runtime, the shared
downloads and the saves folder unless you choose to remove saves; saves outside
`saves/<id>`, such as `~/Documents/TmForever`, are never removed.

To choose another game or app folder, set it when installing:

```sh
TRACKMANIA_MAC_ROOT="$HOME/Games/TrackMania" \
TRACKMANIA_MAC_APPS_DIR="$HOME/Applications" make install
```

With `TRACKMANIA_MAC_ROOT` set, saves default to `$TRACKMANIA_MAC_ROOT/UserData`.
`TRACKMANIA_MAC_PREFIX` and `TRACKMANIA_MAC_USERDATA` set those two folders
directly, and `TRACKMANIA_MAC_SUPPORT` and `TRACKMANIA_MAC_CACHE` move the
runtime, launchers and icons, and the download cache. The apps remember these
installation paths. When using commands from the checkout with a custom game
folder, keep the same environment variables set.

Earlier releases named every setting `TMNF_<NAME>`. Those names still work:
each is mapped to `TRACKMANIA_MAC_<NAME>` with a one-line warning on stderr, and
the new name wins when both are set.

### Moving from an earlier release

TrackMania Mac now keeps everything in one folder, `~/Library/TrackManiaMacOS`.
The first time you open the new hub, or run a command, it moves your games,
saves, runtime, downloads and logs there from `~/Games/TMNF` and the `TMNF`
folders in `~/Library`. It also updates your Game and Settings apps so they keep
working until your next Update. Folders are moved whole, never copied or
overwritten. If a game is running, a folder is on another disk, or a folder
already exists in both places, nothing is moved: the hub says why and what to
do, and your current apps keep working. If the move is interrupted, the hub
finishes it the next time it opens. Old folders are removed only once they are
empty. Saves that an older install keeps in `~/Documents/TmForever` stay where
they are, and so do folders you chose yourself.

You may notice a few side effects of the new app identity: macOS asks for
notification permission for the hub again, Launchpad may briefly show two
TrackMania Mac entries, and a converted Game or Settings app may ask for privacy
permissions again. The hub's own settings carry over. From a checkout,
`./src/shell/cli/trackmania-mac migrate` does the same move.

App logs rotate before launch when they reach 2 MiB, retaining three numbered
backups. A currently running verbose session can exceed that size because game
output goes directly to a file, not a pipe that could interrupt a surviving game.

### Diagnostic overrides

`TRACKMANIA_MAC_X87=off make play` uses stock Rosetta math for a comparison.
`TRACKMANIA_MAC_AUDIO=off make play` disables sound. Normal installs enable both audio
and the patched translator. These temporary overrides do not change saved game
settings. `make install` replaces the game's Creative OpenAL with OpenAL Soft;
`TRACKMANIA_MAC_OPENAL=creative make install` puts the original back and keeps it on
later installs.

### How it works

The runtime is a patched Wine build with D9VK (Direct3D 9 to Vulkan), MoltenVK
(Vulkan to Metal) and x87sidecar, an x87 translator that preserves the game's
24-bit floating-point precision under Rosetta. The native hub bundles its own
Python runtime, so it needs no developer tools. See
[runtime fixes and troubleshooting](docs/runtime.md) for the patches.

### Development

`make check` runs shell checks, Ruff, mypy, Python tests, the CLI integration
test, SwiftLint and the Swift hub tests. Build, packaging, signing and release steps are in
[docs/building.md](docs/building.md); patch details are in
[docs/runtime.md](docs/runtime.md); official installer and server links with
hashes are in [docs/download-links.md](docs/download-links.md); contribution rules are in
[.github/CONTRIBUTING.md](.github/CONTRIBUTING.md). The project website lives in
[`website/`](website) and deploys to GitHub Pages on every push to `main` that
changes it.

## License

This project's own scripts, tests and build tooling use the MIT license; see
[LICENSE](LICENSE). Patches to Wine, D9VK and x87sidecar and bundled third-party
code retain their upstream licenses, and each runtime archive carries their
notices in its `licenses` directory next to the corresponding source archive
(see [licenses/README.md](licenses/README.md)). The license does not cover the
TrackMania games, the Competition Patch bundle, DirectX or Apple software.
OpenAL Soft (LGPL 2.1 or later) is downloaded from its official release during
installation, not bundled; its license is installed next to it in the game
directory.

## Acknowledgements

This runtime is thin glue over other people's hard work. With gratitude to:

- [Wine](https://www.winehq.org) for the Windows compatibility layer everything here is built on.
- [DXVK](https://github.com/doitsujin/dxvk) and the [d9vk fork](https://github.com/Sikarugir-App/d9vk) that turn the game's Direct3D 9 calls into Vulkan.
- [MoltenVK](https://github.com/KhronosGroup/MoltenVK) for running Vulkan on Apple's Metal.
- [OpenAL Soft](https://openal-soft.org) for the game's audio mixing.
- Apple's Rosetta 2 for translating the game's x86 code on Apple Silicon.
- [athei](https://github.com/athei)'s [x87sidecar](https://github.com/athei/x87sidecar) and cooperative Wine handshake, which keep the Forever games' 24-bit x87 math intact under Rosetta.
- [tomashu](https://tomashu.pages.dev)'s TMLoader, and the TrackMania Competition Patch and CoreMod that make online play current.
- GnuTLS, Nettle, GMP, libtasn1, libunistring, FreeType and mingw-w64, which the runtime links or is built with.
- Nadeo and Ubisoft for the TrackMania games.

Trademarks and game content belong to their owners. This is an unofficial community project, not affiliated with Nadeo or Ubisoft.
