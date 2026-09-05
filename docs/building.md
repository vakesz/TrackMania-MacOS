# Building and releasing

## Source ownership

`make build` owns `.build/release`. It never patches a checkout elsewhere on the
machine. `config/sources.json` pins every top-level runtime source by archive
SHA-256 or Git commit. D9VK's Git submodules and MoltenVK's `ExternalRevisions`
pin their nested dependencies. Homebrew provides build tools, not bundled Wine
libraries. Apple supplies the SDK, system frameworks and Rosetta.

The public commands are `make build`, `make install` and `make package`.
Maintainers can run `tools/build.sh --only fetch|deps|wine|d9vk|sidecar|bundle|package`
while diagnosing a build. Run the full build before packaging a release.

The layout under `.build/release` is:

- `sources/`: downloaded source with local patches applied.
- `work/`: dependency and compiler output.
- `runtime/`: the relocatable installation tree.
- `logs/build.log`: compiler commands and full build output.

The build compiles x86_64 Unix Wine and both i386 and x86_64 Windows DLLs.
x87sidecar is native arm64. MoltenVK uses Xcode; a command-line-tools-only
installation is insufficient to build it. The current development host is an
M1 Pro with Xcode 27 and macOS 27. The macOS 14 deployment target does not by
itself prove runtime compatibility on macOS 14, 15 or 26.

Wine includes the macOS graphics, Vulkan, CoreAudio, font and TLS paths. Linux
backends, GStreamer, SDL, USB, printing and packet capture are disabled.
This is a TMNF runtime, not a general replacement for a full Wine distribution.

## Validation

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

The probes cover shader compiler profiles, alertable waits and APC delivery,
x87 precision, stereo/positional DirectSound playback, and HTTPS certificate
verification against the game's download server. The source-only
x87sidecar probes additionally exercise multiple translation modes.

## Release files

Update `VERSION` when the runtime or default installation changes. Then:

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
the same GitHub Release. Do not ship game installers, game profiles, DirectX
redistributables, local diagnostics, caches or your Wine prefix.

Before publishing, install from the packaged runtime into an empty test folder,
verify that the apps still work after moving the checkout, and build from an
extracted source archive. Check that the archive licenses match the libraries
actually bundled. This also catches missing source and accidental local paths.

Create a GitHub Release in `vakesz/TrackMania-MacOS`, tagged `vVERSION`, and attach
all four files. Keep it a draft until the installation and visual checks pass.
No command in this repository creates a remote, commits, pushes or publishes.
The installer downloads the exact version's checksum and archive over HTTPS;
SHA-256 verifies integrity, not an independent publisher signature.
