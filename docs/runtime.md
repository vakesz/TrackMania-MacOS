# Runtime fixes

All active patches live under `patches/`. The build applies them in filename
order to the exact revisions in `config/sources.json`.

| Patch | Purpose | Regression coverage |
| --- | --- | --- |
| Wine 0001 | Lower legacy `texm3x3` instructions without changing eye-vector magnitude | Separate SPIR-V translation regression; Wine GPU path still needs qualification |
| Wine 0002 | Isolate multimedia timer state from the shared winmm lock | Direct multimedia-timer concurrency, reentry, cancellation and restart probe |
| Wine 0005 | Avoid a wineserver round trip for empty alertable yields on Darwin while preserving APC delivery | 32-bit and 64-bit alertable-wait probes |
| Wine 0006 | Accept legacy HLSL function targets used by TMNF materials | Shader probes |
| Wine 0007 | Honor the legacy D3DX compiler flag | Shader probes; material appearance requires a manual game check |
| Wine 0008 | Start x87sidecar through a cooperative per-process handshake | 32-bit and 64-bit precision probes |
| Wine 0009 | Disable audio capture on macOS, so the game never opens a microphone | Audio probes |
| Wine 0010 | Preserve certificate rejection errors and report WinHTTP secure-failure callbacks | Sync/async local TLS controls and rejection cases in isolated DLL overlays, both architectures |
| Wine 0011 | Preserve unignored certificate errors and prevent reuse under a different validation policy | Combined-error, pool-policy and fault-injected lifetime checks, both modes/architectures |
| D9VK 0003 | Correct promoted volume-texture destination slice stride | GPU volume red/green and packed-format readback probes |
| D9VK 0004 | Preserve the eye vector in `texm3x3vspec` | GPU volume/cube reflection regressions |
| MoltenVK 0001 | Reuse the dependency sources supplied in a source release | Source archive preparation |
| x87sidecar 0001 | Scoped PC24/PC53 arithmetic and float-store rounding within a binary64-backed implementation | Bit-exact numeric, allocator, host-state and interruption probes |

The filename gaps are intentional. D9VK's two build patches were superseded by
the pinned upstream update. Rejected experiments are not included in releases.

The shader-profile probe also checks VS 1.1 declaration lengths; the former two
failures were in the test parser. All 37 profile cases pass against the tested
Wine compilers and native SDK 30. Explicit effect-target selection is corrected,
but Wine's existing FX2 PixelShader assignment emission still blocks full effect
output. That limitation is not hidden by the profile or GPU tests.

## Numeric limits

The translator stores eight binary64 values. The verified PC24/PC53 rounding
and float-store fixes do not provide general 80-bit state or exponent retention.
PC64 remains an approximation; extreme underflow/overflow chains, full x87
status-word exceptions and unmasked traps are not qualified. A value lost from
the internal representation cannot be recovered by rounding its final store.

Full80 state is a separate architecture project. Each additional game still
needs physics/replay validation; stock Rosetta is a comparison, not an assumed
full80 correctness fallback. Scoped helper tests preserve FPCR and NZCV and
retain/accumulate FPSR sticky flags. Signal-storm results establish stability
against that sample's unstressed baseline, not complete context equivalence.

## Settings that matter

`config/dxvk.conf` enables asynchronous shader compilation, dynamic-buffer
caching and device-local constants, and limits the frame queue to one frame.
Direct buffer mapping is already enabled by the pinned D9VK default. There is
no forced presentation interval, so the game's vsync setting applies. Shader
caches persist in the Wine prefix. Diagnostics are off during ordinary play.

Async compilation can skip draws whose pipelines are not ready, temporarily
omitting geometry or effects instead of blocking the render thread. Dynamic-buffer
caching requests host-cached memory for host-visible buffers and drops their
device-local requirement. Device-local constants request a Vulkan memory property,
not dedicated VRAM on Apple Silicon's unified memory. Neither flag establishes
what memory type MoltenVK will choose or proves that this game runs faster.
The remaining tuning overrides are retained pending controlled frame-time and
visual comparisons, including the latency/throughput tradeoff of a one-frame queue.

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
holding the Wine virtual desktop on screen as a black window. `src/c/session.c`
runs inside the selected prefix and tracks retained Windows process handles,
exact executable file identities, job accounting and prefix-local snapshots.
It does not search the host process list by executable name.

Audio and launcher-specific registry setup share the host's 300-second startup
budget. Once a watched process is observed, there is no race-duration deadline.
Settings sessions watch both launcher and game paths. Virtual desktops have
unique session names. Without a virtual desktop, Wine starts its own
`explorer.exe /desktop` for the first game window inside the session's job; the
helper accepts only the runtime's exact explorer image started by a watched
process, and asks it to close after the game exits. Unknown descendants,
incomplete observations or a stuck desktop produce an uncertain result rather
than a job-wide kill.

Wine's console host is recognized only by native System32 path/file identity,
byte equality with the bundled reference, and a retained parent in the same
job. Only its completed zero-status lifecycle is exempt from unknown-child
handling; a namesake or modified image is not trusted as infrastructure.

`src/python/tmnf/session/supervisor.py` preserves the reported unsigned Windows exit status before
cleanup. A validated completed session permits bounded, prefix-specific
wineserver shutdown; `stop` is the explicit user-authorized version of that
operation. Timeout or interruption before completion terminates only the host
helper, leaving a possible game alive. Use `make stop` if the window remains.
Known exception-status exits are reported as such, not inferred from a timeout.

Controlled Wine fixtures exercise these handoffs and cleanup rules. They do not
replace qualification with the real TMLoader, original launcher and game.

CoreAudio is enabled. The audio probes check device enumeration and
DirectSound playback cursor progress for stereo and mono buffers, plus 3D
buffer/listener state updates and deferred commits.
These are API and timing checks, not proof of audible output or correct positional
mixing. Listen in game to verify those. x87sidecar runs
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

By default the launcher runs the game directly, so it uses the display mode set
in TrackMania Settings (fullscreen or windowed, at the chosen resolution).
`TMNF_WINDOW_SIZE=WxH` instead runs it inside an explorer virtual desktop of
that size; the desktop's title bar shrinks the drawable below the requested
height, which clips the bottom of the game's fixed-size menu, so the direct
launch is the default. Fullscreen uses the primary display; on multiple monitors
set the game's resolution to the primary's.

The two apps carry TMNF's own icon, extracted at install time from the
installed `TmForever.exe` by `src/python/tmnf/app/icon.py` using Python's standard library,
sips and iconutil. Malformed or unsupported icons fall back to generic app artwork
without blocking installation. Nothing of Nadeo's is stored in this repository.

`config/game-settings.json` describes the initial graphics preset.
`src/python/tmnf/build/game_profile.py` generates only display and opponent chunks in a GBX
SystemConfig file, with no player profile, account or network fields. Existing
profiles are never replaced. The chunk formats are documented by
[GBX.NET](https://github.com/BigBang1112/gbx-net/tree/master/Src/GBX.NET/Engines/System).
The generator names documented fields and labels unknown ones; the TMF shader
quality field is inferred from the later display layout. It validates required
types, integer encoding limits and preset enum ranges, not every setting the
game may accept. Offline tests check chunk structure and unchanged default bytes.

## Troubleshooting

Start with `make doctor`. The app logs are in
`~/Library/Logs/TMNF/play.log` and `configure.log`.

`src/python/tmnf/app/launch.py` serializes app log writers and rotates completed logs at
launch, keeping three backups. Active or surviving writers retain direct file
descriptors and can exceed the 2 MiB rotation threshold.

Prefix-changing commands and launches take an installation lock and refuse
an already-active selected prefix. Close the game and Settings before running
diagnostics that execute a Windows probe. A missing or unmanaged prefix is
reported without initializing it. These guards serialize commands from this
launcher; external Wine invocations do not participate in its operation lock.

- If settings need changing, close the game and open **TrackMania Settings**.
- If a macOS update makes x87sidecar's compatibility probe fail, rebuild or use
  `TMNF_X87=off make play` temporarily. Stock Rosetta can be substantially
  slower on this game's math workload.
- If sound is missing, check the game's audio settings and macOS output device.
  Run `./src/shell/tools/probes/smoke-audio.sh` from a source checkout to test Wine playback.
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
