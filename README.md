# TrackMania Nations Forever for Apple Silicon

Install TMNF with Competition Patch, working textures and audio, and a patched
Wine runtime tuned for this game. The runtime includes D9VK, MoltenVK and an
x87 translator that preserves the game's 24-bit floating-point precision.

This is an independent community project. The game and Microsoft DirectX are
downloaded from their original hosts during installation, not redistributed here.

| In game | Main menu |
| --- | --- |
| ![TMNF racing on an M1 Pro with the DXVK FPS overlay](docs/images/ingame.png) | ![TMNF main menu with Competition Patch and CoreMod loaded](docs/images/menu.png) |

Both screenshots are from an M1 Pro in a 1280x720 window with the DXVK frame
rate overlay enabled (`DXVK_HUD=fps,frametimes`).

## Requirements

An Apple Silicon Mac, an internet connection, and Apple's command line tools for
`make` (run `xcode-select --install` if they are missing). Rosetta 2 is also
required; the installer adds it if necessary.

Close any existing TMNF session before installing or updating, then download this
repository and open Terminal in its folder. Pick one of the two options below.

## Option 1: install the prebuilt runtime

This downloads a ready-built runtime. Run:

```sh
make install
```

The installer downloads the versioned runtime from
[GitHub Releases](https://github.com/vakesz/TrackMania-MacOS/releases), verifies
its checksum, installs the game and creates two apps:

- **TrackMania Nations Forever** starts the game with Competition Patch.
- **TrackMania Settings** opens the original launcher to change graphics and
  audio settings. Close the game before changing these settings.

Save and close the settings launcher, then use the game app to play with
Competition Patch.

On the first launch the game shows its own "Network check" dialog for the
peer-to-peer firewall test; click OK. The account screen follows. A launch
takes a while to show a window; the game's windows may open behind others.

The apps go in `/Applications` when it is writable, otherwise in
`~/Applications`. You can remove the downloaded repository after installation.
The apps use their installed support files, not the checkout.

To install from an archive you already downloaded instead, point the installer
at it and give the checksum from the release:

```sh
MACTMNF_RUNTIME_ARCHIVE="$HOME/Downloads/mactmnf-0.1.0-runtime.tar.xz" \
MACTMNF_RUNTIME_SHA256="<checksum from the release>" make install
```

If macOS refuses to open the game after a manual download, the archive picked up
a quarantine flag in the browser. Clear it, then install:

```sh
xattr -dr com.apple.quarantine "$HOME/Downloads/mactmnf-0.1.0-runtime.tar.xz"
```

Archives that `make install` fetches with `curl` are not quarantined, so this
step is only for a browser download.

## Option 2: build it yourself

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

`make build` installs missing build tools through Homebrew. It downloads pinned
sources, checks archive hashes and Git revisions, applies this repository's
patches, then compiles Wine, D9VK, x87sidecar and the runtime libraries locally.
It does not require another Wine installation or a separate source checkout.

The complete runtime is written to `.build/release/runtime`. Downloads, sources,
build logs and intermediate files stay under `.build`. Allow several gigabytes
of disk space and a substantial first build. Builds use three compiler jobs by
default; use `MACTMNF_JOBS=6 make build` to change that.

The deployment target is macOS 14. x87sidecar also depends on the installed
Rosetta version; its compatibility probe must pass. See
[building and releasing](docs/building.md) for the tested host and limitations.

## Everyday commands

| Command | Action |
| --- | --- |
| `make play` | Start the same game as the app |
| `make configure` | Open the original settings launcher |
| `make doctor` | Check runtime, game, renderer, audio and mod installation |
| `make stop` | Stop Wine processes in this game's prefix |
| `make install` | Install missing files and refresh the app launchers |
| `./bin/mactmnf play-vanilla` | Run the official game without Competition Patch |
| `./bin/mactmnf uninstall` | Remove the Wine prefix after confirmation |

A new installation starts at 1280×720 with Nice shaders, post-processing and
stadium water geometry disabled, four game threads and at most six opponents.
Existing settings are preserved. Use the Settings app to adjust them.

The renderer keeps a shader cache between launches. A first visit to a track can
still stutter. The patches improve specific problems; they do not guarantee a
particular frame rate. See [runtime fixes and troubleshooting](docs/runtime.md).

## Where files go

| Files | Default location |
| --- | --- |
| Game and Wine prefix | `~/Games/TMNF/prefix` |
| Saves, settings and replays for a new install | `~/Games/TMNF/UserData/TmForever` |
| Runtime and installed launcher scripts | `~/Library/Application Support/MacTMNF` |
| Download cache | `~/Library/Caches/MacTMNF` |
| App launch logs | `~/Library/Logs/MacTMNF` |

Older local installations may still save to `~/Documents/TmForever`; the
installer keeps that existing arrangement. Uninstalling the prefix keeps the
runtime, download cache and separate user data.

To choose another game or app folder, set it when installing:

```sh
MACTMNF_ROOT="$HOME/Games/TrackMania" \
MACTMNF_APPS_DIR="$HOME/Applications" make install
```

The apps remember these installation paths. When using commands from the
checkout with a custom game folder, keep the same environment variables set.

For a diagnostic comparison, `MACTMNF_X87=off make play` uses stock Rosetta math.
`MACTMNF_AUDIO=off make play` disables sound. Normal installs enable both audio
and the patched translator. These temporary overrides do not change saved game
settings.

## Development

`make check` runs shell checks and offline tests. `make smoke` runs Windows
regression probes against the selected Wine prefix. `make package` creates the
runtime archive, matching source archive, and SHA-256 files for a release.
See [docs/building.md](docs/building.md) before distributing them.

This project's own scripts and tests use the MIT license. Patches and bundled third-party
code retain their upstream licenses. See [LICENSE](LICENSE) and the `licenses`
directory in each runtime archive.

## Acknowledgements

This runtime is thin glue over other people's hard work. With gratitude to:

- [Wine](https://www.winehq.org) for the Windows compatibility layer everything here is built on.
- [DXVK](https://github.com/doitsujin/dxvk) and the [d9vk fork](https://github.com/Sikarugir-App/d9vk) that turn the game's Direct3D 9 calls into Vulkan.
- [MoltenVK](https://github.com/KhronosGroup/MoltenVK) for running Vulkan on Apple's Metal.
- Apple's Rosetta 2 for translating the game's x86 code on Apple Silicon.
- [athei](https://github.com/athei)'s [x87sidecar](https://github.com/athei/x87sidecar) and cooperative Wine handshake, which keep TMNF's 24-bit x87 math intact under Rosetta.
- [tomashu](https://tomashu.pages.dev)'s TMLoader, and the TrackMania Competition Patch and CoreMod that make online play current.
- GnuTLS, Nettle, GMP, libtasn1, libunistring, FreeType and mingw-w64, which the runtime links or is built with.
- Nadeo and Ubisoft for TrackMania Nations Forever itself.

Trademarks and game content belong to their owners. This is an unofficial community project, not affiliated with Nadeo or Ubisoft.
