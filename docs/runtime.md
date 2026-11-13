# Runtime fixes

The runtime and its patches are shared by every title the hub installs. Some
sections below apply only to Nations and United Forever, because they depend on
`TmForever.exe`, the Competition Patch or a pinned Creative OpenAL library; each
of those says so on an "Applies to" line. The Wine, D9VK and x87sidecar patches,
the renderer settings and the session helper apply to all titles.

All active patches live under `patches/`. The build applies them in filename
order to the exact revisions in `config/sources.json`.

| Patch | Purpose | Regression coverage |
| --- | --- | --- |
| Wine 0001 | Lower legacy `texm3x3` instructions without changing eye-vector magnitude | Separate SPIR-V translation regression; Wine GPU path still needs qualification |
| Wine 0002 | Isolate multimedia timer state from the shared winmm lock | Direct multimedia-timer concurrency, reentry, cancellation and restart probe |
| Wine 0005 | Avoid a wineserver round trip for empty alertable yields on Darwin while preserving APC delivery | 32-bit and 64-bit alertable-wait probes |
| Wine 0006 | Accept legacy HLSL function targets used by Nations Forever materials | Shader probes |
| Wine 0007 | Honor the legacy D3DX compiler flag | Shader probes; material appearance requires a manual game check |
| Wine 0008 | Start x87sidecar through a cooperative per-process handshake | 32-bit and 64-bit precision probes |
| Wine 0009 | Disable audio capture on macOS, so the game never opens a microphone | Audio probes |
| Wine 0010 | Preserve certificate rejection errors and report WinHTTP secure-failure callbacks | Sync/async local TLS controls and rejection cases in isolated DLL overlays, both architectures |
| Wine 0011 | Preserve unignored certificate errors and prevent reuse under a different validation policy | Combined-error, pool-policy and fault-injected lifetime checks, both modes/architectures |
| D9VK 0003 | Correct promoted volume-texture destination slice stride | Manual GPU volume red/green and packed-format readback probe; no automated test |
| D9VK 0004 | Preserve the eye vector in `texm3x3vspec` | Manual GPU volume/cube reflection check; no automated test |
| MoltenVK 0001 | Reuse the dependency sources supplied in a source release | Source archive preparation |
| x87sidecar 0001 | Scoped PC24/PC53 arithmetic and float-store rounding within a binary64-backed implementation, ported to 0b7c5e5 | Bit-exact numeric, allocator, host-state and interruption probes |
| x87sidecar 0002 | Skip the scoped FPCR write when the guest rounds to nearest and the host FPCR already gives the same results | The same probes in all translator modes |
| x87sidecar 0003 | Skip the f32 operand check for values loaded from a 32-bit float | The same probes in all translator modes |
| x87sidecar 0004 | Run PC24 IR runs in native single precision, falling back to the exact body on any out-of-range value | The same probes with the fast bodies on and off (`X87_PC24_RUNS=0`) |

The filename gaps are intentional. D9VK's two build patches were superseded by
the pinned upstream update. Rejected experiments are not included in releases.

The shader-profile probe also checks VS 1.1 declaration lengths; the former two
failures were in the test parser. All 37 profile cases pass against the tested
Wine compilers and native SDK 30. Explicit effect-target selection is corrected,
but Wine's existing FX2 PixelShader assignment emission still blocks full effect
output. That limitation is not hidden by the profile or GPU tests.

## Numeric limits

Applies to: every title, because x87sidecar is on by default for all of them
(`TRACKMANIA_MAC_X87=off` turns it off). The physics checks use `TmForever.exe`, so they
cover the Forever games only.

Since 0b7c5e5 the translator keeps x87 registers in their native 80-bit layout
between translated runs and converts a slot to binary64 only where an
instruction reads it as binary64. Arithmetic inside a run is binary64-backed.
The verified PC24/PC53 rounding and float-store fixes do not provide general
80-bit arithmetic or exponent retention. Patch 0004's single-precision bodies
only run when every value is exactly an f32 and every result stays in the f32
range; anything else continues in the exact binary64 body, so they compute
the same results.
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
Nations Forever and its launcher call `DirectDrawCreateEx` during startup, and Wine's
ddraw would otherwise create a wined3d OpenGL adapter that nothing renders
with. That GL context work, spread over the main thread and the network-check
splash thread, crashed about half of all launches under Rosetta. The 32-bit
thread resumed in 64-bit mode at a 32-bit function entry, or the reverse in
Wine's syscall thunk, always right after the GL vendor probe. Ten launches with
the no-3D adapter showed no crash, and the game only needs ddraw for its 2D
startup windows. Set `WINE_D3D_CONFIG` to add other wined3d options; the
renderer setting is prepended.

The game and launcher use Wine's patched `d3dx9_30`, `d3dx9_31` and `d3dx9_43`
compilers, including when CoreMod redirects shader compilation from one to
another. `d3dx9_43` loads `d3dx9_31` for the legacy-compiler flag CoreMod uses,
and Microsoft's DirectX installer replaces `d3dx9_31` with a native copy. That
installer can replace Wine DLLs, and `wineboot --update` does not put them back,
so installation copies the three files from the runtime into the prefix
explicitly and Play overrides them to the Wine copies. Other d3dx9 versions stay
as the installer left them.

Prefix setup skips optional Mono and Gecko downloads. DirectX setup treats
.NET as unavailable because the games use the native components. Setup also points
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

After the last watched process exits, the helper waits two seconds for a
handoff before reporting. It skips that wait only when the launch root has
exited, the job has no active process, a fresh job accounting read matches every
process it observed, and nothing is unrecognized. A failed accounting query,
desktop close, or live job process keeps the full wait.

For a title with the Competition Patch, a settings session also names the
game's own `TmForever.exe` for redirection. When the launcher starts that file,
the helper reads the new process's command line from its PEB (Wine has no
`ProcessCommandLineInformation`). Unless an argument starts with `/bench` or
`/list_benchs`, the launcher's benchmark switches, it stops the process, gives
the launcher the two-second window to exit, and reports `redirected` if every
job process is still recognized. The supervisor then shuts the prefix down as
after a normal exit, and `configure`, still holding the prefix lock, runs `play`.
So the launcher's Play gets Competition Patch and CoreMod as the Game app does.
A command line that cannot be read leaves the game running as before. Wine does
not honour Image File Execution Options `Debugger`, and ModLoader starts its own
copy of `TmForever.exe`, a different file, so it is never redirected.

Wine's console host is recognized only by native System32 path/file identity,
byte equality with the bundled reference, and a retained parent in the same
job. Only its completed zero-status lifecycle is exempt from unknown-child
handling; a namesake or modified image is not trusted as infrastructure.

`src/python/trackmania_mac/session/supervisor.py` preserves the reported unsigned Windows exit status before
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

### OpenAL

Applies to: Nations and United Forever. Only a title whose registry entry sets
`creative_openal_sha256` gets this replacement.

Both games play their sound through `OpenAL32.dll` in the game directory. They ship
Creative's OpenAL router with its `wrap_oal.dll` software mixer, which on macOS 27
keeps a real-time thread busy for much of a core and lowers race frame rates.
Both ship the same Creative `OpenAL32.dll`, so both entries pin the same hash.
`make install` replaces it with OpenAL Soft's official 32-bit `soft_oal.dll`
(pinned version and SHA-256), keeps the original as `OpenAL32.creative.dll` and
puts OpenAL Soft's license beside it as `OpenAL32.COPYING.txt`. OpenAL Soft runs
with its defaults: its resampler and HRTF settings change its own mixing cost by
a few percent of one core at most, on its own thread. An `%AppData%\alsoft.ini` in
the prefix still applies. Wine's CoreAudio driver reports every output device as
speakers, so OpenAL Soft's automatic HRTF never turns on for headphones; for
binaural sound on headphones, put `stereo-encoding = hrtf` under `[general]` in
the prefix user's `AppData/Roaming/alsoft.ini`.

`TRACKMANIA_MAC_OPENAL=creative make install` restores Creative's library; later installs
and updates keep that choice until `TRACKMANIA_MAC_OPENAL=soft make install`. A different
`OpenAL32.dll` placed there by the user is left unchanged. Only a library
whose hash the title registry pins (`creative_openal_sha256`) is replaced; other
titles keep the OpenAL they ship.

### Launcher and first launch

Applies to: Nations and United Forever. Both start `TmForeverLauncher.exe` and
`TmForever.exe`.

The original graphics launcher receives a known Nvidia PCI identity (GeForce
8800 GT) for that executable only. The wined3d renderers read it from the
launcher's per-application registry keys; D9VK reads it from the
`[TmForeverLauncher.exe]` section of `config/dxvk.conf`. The launcher looks the
GPU up in `GfxDevicePerfs.txt` by PCI ID. Apple GPUs are not listed, so without
the spoof it starts `TmForever.exe /bench`, which does not complete under
Rosetta, and its older adapter probing could crash. The game keeps its actual
renderer identity. The launcher runs with the same environment as the game, so
a benchmark started from it inherits the D9VK settings and x87sidecar. Its Play
button is redirected to Play (see the session section above).

The first launch shows the game's own "Network check" dialog, which confirms the
peer-to-peer firewall test, followed by the account screen. Both are the
game's normal first-run behavior and are remembered in the SystemConfig.
Windows opened by the game are ordinary macOS windows; check behind other
windows if a launch seems silent.

### Display mode, icons and initial settings

Applies to: every title, except that the initial settings file
(`config/game-settings.json` and `Default.SystemConfig.Gbx`) is seeded only for
Nations and United Forever, the titles with `TmForever.exe` as `game_exe`.

By default the launcher runs the game directly, so it uses the display mode set
in TrackMania Settings (fullscreen or windowed, at the chosen resolution).
`TRACKMANIA_MAC_WINDOW_SIZE=WxH` instead runs it inside an explorer virtual desktop of
that size; the desktop's title bar shrinks the drawable below the requested
height, which clips the bottom of the game's fixed-size menu, so the direct
launch is the default. Fullscreen uses the primary display; on multiple monitors
set the game's resolution to the primary's.

The two apps carry the game's own icon, extracted at install time from the
installed `TmForever.exe` by `src/python/trackmania_mac/app/icon.py` using Python's standard library,
sips and iconutil. Malformed or unsupported icons fall back to generic app artwork
without blocking installation. Nothing of Nadeo's is stored in this repository.

`config/game-settings.json` describes the initial graphics preset.
`src/python/trackmania_mac/build/game_profile.py` generates only display and opponent chunks in a GBX
SystemConfig file, with no player profile, account or network fields. Existing
profiles are never replaced. The chunk formats are documented by
[GBX.NET](https://github.com/BigBang1112/gbx-net/tree/master/Src/GBX.NET/Engines/System).
The generator names documented fields and labels unknown ones; the TMF shader
quality field is inferred from the later display layout. It validates required
types, integer encoding limits and preset enum ranges, not every setting the
game may accept. Offline tests check chunk structure and unchanged default bytes.

## Account creation over TLS 1.0

Applies to: Nations and United Forever.

TrackMania Nations and United Forever create accounts through WinINet.
The game calls `HttpOpenRequestA` with `INTERNET_FLAG_SECURE` and the
ignore-certificate flags `0x80F000`. On certificate errors 12045, 12037 or
12038 it adds `0xF380` to `INTERNET_OPTION_SECURITY_FLAGS` and retries. The
requests go to `nations.trackmaniaforever.com` and `united.trackmaniaforever.com`
under `/online_game/request.php`.

Those servers accept TLS 1.0 only, with AES-128/256-CBC-SHA and DHE-RSA-AES-SHA
ciphers. Their certificates are self-signed, RSA-1024, SHA-1 and expired. Windows
turns TLS 1.0 off by default, so players there must re-enable it. The runtime
needs no workaround because Wine's WinINet uses GnuTLS, which still negotiates
TLS 1.0 and SHA-1 certificates here.

An offline test keeps it that way. It serves the expired, untrusted test
certificate on loopback with TLS 1.0 and those ciphers only, then runs a WinINet
probe (`tests/inet_legacy.c`) that repeats the game's flags and retry. The probe
must get HTTP 200, and the server must have negotiated TLS 1.0:

```sh
TRACKMANIA_MAC_TEST_RUNTIME=/absolute/path/to/runtime \
  uv run --frozen --group test pytest -q tests/test_network_runtime.py
```

`make smoke` runs the same check in the selected prefix. Both skip with a reason
when the local OpenSSL cannot serve TLS 1.0.

When you bump Wine or GnuTLS, run this test before release. A failure means the
new build dropped TLS 1.0, SHA-1 signatures or one of the ciphers, which breaks
account creation. Check the Wine `secur32` and `schannel` GnuTLS priority string
and the GnuTLS build options first.

## Troubleshooting

Start with `make doctor`, which checks the title in `TRACKMANIA_MAC_TITLE` (default
`tmnf`); for example, `TRACKMANIA_MAC_TITLE=tmu make doctor`. In the hub, **⋯ > Create
diagnostics report** bundles the same checks with recent logs for the selected
game. The app logs are in the `logs` folder of `~/Library/TrackManiaMacOS`:
`play.log` and `configure.log` for Nations Forever. Other titles write
`<title>-play.log` and `<title>-configure.log` there, so titles never share a log.

`src/python/trackmania_mac/app/launch.py` serializes app log writers and rotates completed logs at
launch, keeping three backups. Active or surviving writers retain direct file
descriptors and can exceed the 2 MiB rotation threshold.

Prefix-changing commands and launches take an installation lock and refuse
an already-active selected prefix. Close the game and Settings before running
diagnostics that execute a Windows probe. A missing or unmanaged prefix is
reported without initializing it. These guards serialize commands from this
launcher; external Wine invocations do not participate in its operation lock.

- If settings need changing, close the game and open **TrackMania Settings**.
- If x87sidecar's compatibility probe fails, the game still runs on stock
  Rosetta, which can be substantially slower on this game's math workload.
  x87sidecar supports Rosetta from macOS 15.4 on; macOS 14 through 15.3 ship
  an older Rosetta it can't hook, so update macOS. If a newer macOS update
  breaks the probe, rebuild against a newer x87sidecar. `TRACKMANIA_MAC_X87=off` skips
  the sidecar entirely.
- If sound is missing, check the game's audio settings and macOS output device.
  Run `./src/shell/tools/probes/smoke-audio.sh` from a source checkout to test Wine playback.
- If textures are wrong after installing other DLLs, rerun `make install`.
  Keep the bundled D9VK and patched Wine D3DX compilers together.
- For frame-time observation, use `DXVK_HUD=fps,frametimes make play`. Compare
  warmed laps on the same track and resolution. Loading time and menu FPS are
  not race benchmarks.
- For a frame-rate report by game state, play with `make diag` and read the
  report it prints when the game exits (`make report` shows it again,
  `PYTHONPATH=src/python python3 -m trackmania_mac.session.report --list` lists earlier
  sessions). The session goes to `play.log` in the logs folder with Wine's
  `+fps` channel, about one line a second, and `trackmania_mac.session.monitor` adds window
  focus, power state and the busiest threads every five seconds. A small helper,
  `src/c/game-state.c`, reads the game's own state
  (menu, race, finish, replay and others) and the current map's name from its
  memory, so the report also groups races by map; MinGW builds it on
  first use (`brew install mingw-w64`). It knows the layout of Nations Forever 2.11.26,
  which the Competition Patch build shares; with another build, or without
  MinGW, the state is reported as unknown. `TRACKMANIA_MAC_DIAG=on` enables the same
  logging for any launch from a checkout.
- Avoid verbose Wine traces and MoltenVK performance logging during ordinary
  play. Earlier diagnostic runs reduced menu performance severely.

The local investigation established correct textures and audible game sound.
The user reported smoother gameplay with the patched translator. The probes
verify arithmetic and API behavior; they are not whole-game FPS benchmarks.
Performance also depends on the Mac, Rosetta build, track and graphics settings.
