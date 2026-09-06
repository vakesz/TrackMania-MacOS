# Runtime fixes

All active patches live under `patches/`. The build applies them in filename
order to the exact revisions in `config/sources.json`.

| Patch | Purpose | Regression coverage |
| --- | --- | --- |
| Wine 0001 | Lower legacy `texm3x3` instructions in the shader compiler | Shader probes |
| Wine 0002 | Separate the multimedia timer lock to avoid contention and startup deadlocks | Audio playback and timer-dependent waits |
| Wine 0005 | Avoid a wineserver round trip for empty alertable yields on Darwin while preserving APC delivery | 32-bit and 64-bit alertable-wait probes |
| Wine 0006 | Accept legacy HLSL function targets used by TMNF materials | Shader probes |
| Wine 0007 | Honor the legacy D3DX compiler flag | Shader probes and visual material check |
| Wine 0008 | Start x87sidecar through a cooperative per-process handshake | 32-bit and 64-bit precision probes |
| Wine 0009 | Report no CoreAudio capture devices, so the game never opens a microphone | Audio probes |
| D9VK 0001 and 0002 | Build this MoltenVK-compatible fork with current MinGW headers | Source build |
| MoltenVK 0001 | Reuse the dependency sources supplied in a source release | Source archive preparation |
| x87sidecar 0001 | Honor x87 precision-control and rounding modes, including 24-bit arithmetic | Arithmetic, host-state and translation-mode probes |

The filename gaps are intentional. Rejected experiments are not part of the
runtime or source releases.

## Settings that matter

`config/dxvk.conf` enables asynchronous shader compilation, dynamic-buffer
caching, direct buffer mapping and device-local constants. The frame queue is
one frame, with no forced presentation interval. Shader caches persist in the
Wine prefix. Diagnostics are off during ordinary play.

With the D9VK renderer, wined3d runs with `renderer=gdi` (its no-3D adapter).
TMNF and its launcher call `DirectDrawCreateEx` during startup, and Wine's
ddraw would otherwise create a wined3d OpenGL adapter that nothing renders
with. That GL context work, spread over the main thread and the network-check
splash thread, crashed about half of all launches under Rosetta. The 32-bit
thread resumed in 64-bit mode at a 32-bit function entry, or the reverse in
Wine's syscall thunk, always right after the GL vendor probe. Ten launches with
the no-3D adapter showed no crash, and the game only needs ddraw for its 2D
startup windows. Set `WINE_D3D_CONFIG` to add other wined3d options; the
renderer setting is prepended.

The launcher uses Wine's patched `d3dx9_30` and `d3dx9_43` compilers, including
when CoreMod redirects shader compilation from one to the other. Microsoft's
legacy DirectX installer can replace Wine DLLs, so installation restores the
Wine-owned files afterwards.

Prefix setup skips optional Mono and Gecko downloads. DirectX setup treats
.NET as unavailable because TMNF uses the native components. Setup also points
the Windows profile folders (Desktop, Downloads, Music, Pictures, Videos) into
the prefix instead of the real macOS home, and keeps Documents on the game's
own UserData, so the game never trips a macOS privacy prompt for folders it does
not use. The game's old installer can hang while exiting under WoW64/Rosetta.
Installation waits for its log to confirm successful installation and
deinitialization, then allows ten seconds for shutdown before stopping that
prefix. A partial installation does not qualify for this workaround and is
retried on the next install.

The same shutdown weakness can leave a game process alive after you quit,
holding the Wine virtual desktop on screen as a black window. Each launch runs a
small watchdog that waits for the game's own process to exit, then stops the
prefix, so the window closes and the app quits. The watchdog matches the game
process only, never the desktop that hosts it, so it never acts mid-game.

CoreAudio is enabled. The audio probes check both device enumeration and actual
DirectSound playback progress for stereo and positional sound. x87sidecar runs
with FMA contraction and fast rounding disabled. It uses cooperative attachment;
no system Rosetta files, privileged service or debugger entitlement is needed.

The original graphics launcher receives a known Nvidia PCI identity (GeForce
8800 GT) for that executable only. The wined3d renderers read it from the
launcher's per-application registry keys; D9VK reads it from the
`[TmForeverLauncher.exe]` section of `config/dxvk.conf`. The launcher looks the
GPU up in `GfxDevicePerfs.txt` by PCI ID. Apple GPUs are not listed, so without
the spoof it starts `TmForever.exe /bench`, which does not complete under
Rosetta, and its older adapter probing could crash. The game keeps its actual
renderer identity. The launcher runs with the same environment as the game, so
a benchmark or game started from it inherits the D9VK settings and x87sidecar.

The first launch shows TMNF's own "Network check" dialog, which confirms the
peer-to-peer firewall test, followed by the account screen. Both are the
game's normal first-run behavior and are remembered in the SystemConfig.
Windows opened by the game are ordinary macOS windows; check behind other
windows if a launch seems silent.

The two apps carry TMNF's own icon, extracted at install time from the
installed `TmForever.exe` by `lib/app_icon.py` using only sips and iconutil.
Nothing of Nadeo's is stored in this repository.

`config/game-settings.json` describes the initial graphics preset.
`tools/game_profile.py` generates only display and opponent chunks in a GBX
SystemConfig file, with no player profile, account or network fields. Existing
profiles are never replaced. The chunk formats are documented by
[GBX.NET](https://github.com/BigBang1112/gbx-net/tree/master/Src/GBX.NET/Engines/System).

## Troubleshooting

Start with `make doctor`. The app logs are in
`~/Library/Logs/MacTMNF/play.log` and `configure.log`.

- If settings need changing, close the game and open **TrackMania Settings**.
- If a macOS update makes x87sidecar's compatibility probe fail, rebuild or use
  `MACTMNF_X87=off make play` temporarily. Stock Rosetta can be substantially
  slower on this game's math workload.
- If sound is missing, check the game's audio settings and macOS output device.
  Run `./tools/smoke-audio.sh` from a source checkout to test Wine playback.
- If textures are wrong after installing other DLLs, rerun `make install`.
  Keep the bundled D9VK and patched Wine D3DX compilers together.
- For frame-time observation, use `DXVK_HUD=fps,frametimes make play`. Compare
  warmed laps on the same track and resolution. Loading time and menu FPS are
  not race benchmarks.
- Avoid verbose Wine traces and MoltenVK performance logging during ordinary
  play. Earlier diagnostic runs reduced menu performance severely.

The local investigation established correct textures and audible game sound.
The user reported smoother gameplay with the patched translator. The probes
verify arithmetic and API behavior; they are not whole-game FPS benchmarks.
Performance also depends on the Mac, Rosetta build, track and graphics settings.
