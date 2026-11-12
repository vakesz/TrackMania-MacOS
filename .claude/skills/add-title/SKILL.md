---
name: add-title
description: Checklist for adding a game title to the TrackMania Mac hub or changing an existing one's registry entry (config/titles/*.json), such as its installer URL or hash, prefix, app name, bundle ID, executables or save directories. Use it whenever a title JSON or assets/titles/ is touched, or the user asks to support another TrackMania (or similar) game, even if they only mention one field.
---

`config/titles/*.json` is the only source of title data. The shell reads it through `python -m trackmania_mac.hub.registry export`, and Swift only sees it through `trackmania-mac hub snapshot`. So a title change should be a data change, never a hard-coded path in shell or Swift.

1. **Entry.** Copy a similar title (e.g. `config/titles/tmu.json`) to `config/titles/<id>.json` and fill in its fields. `src/python/trackmania_mac/hub/registry.py` (`_FIELDS`) is the schema. It rejects unknown keys, so a misspelt field fails instead of being ignored.
   - `user_subdir` is the folder under UserData that the game reads (its `Nadeo.ini` `UserSubDir`). It must be one of `save_dirs`, so uninstall guards always cover it.
   - `needs_tmloader` is true for titles that Play starts through Competition Patch and CoreMod. It requires `game_exe` `TmForever.exe`, the only program ModLoader targets here. Other titles launch their own executable.
   - Do not add a prefix path. The prefix, saves, launcher and icon locations all derive from the title id (`games/<id>/prefix`, `saves/<id>`, `launchers/<id>`, `icons/<id>.icns` under `~/Library/TrackManiaMacOS`), so ids keep titles apart.
   - `bundle_id_game` and `bundle_id_settings` are required. By convention they are `local.trackmania-mac.<id>.game` and `local.trackmania-mac.<id>.settings` (Nations Forever, the first title, has `local.trackmania-mac.game` and `.settings`).
   - Leave `legacy_prefix_subdir`, `legacy_bundle_id_game` and `legacy_bundle_id_settings` out. They record where releases before the shared data folder kept an existing title and what they called its apps, and only the migration reads them. A new title has no earlier install.
   - The installer's `source_url`, `source_sha256` and `source_size` go here, not in `config/pins.json`, which holds only shared downloads.
   - Optional release pins, only when the exact build is known: `game_version` with `game_exe_sha256` (doctor then verifies the executable; the `online` installer kind requires them), and `creative_openal_sha256` (the Creative `OpenAL32.dll` the release ships, which install replaces with OpenAL Soft). Leave them out otherwise.
   - New titles start with `status` provisional. Only `tmnf` is supported.
   - Don't add `prefix_marker` or `needs_directx`. The marker is derived from the id, and every title gets DirectX.
2. **Components are derived, not configured.** `Title.components` decides the optional install steps, and the shell asks `title_has`:
   - `competition-patch` comes from `needs_tmloader`;
   - `openal` comes from `creative_openal_sha256`;
   - `system-config` (seeding the runtime's Forever-format `Default.SystemConfig.Gbx` into `user_subdir`) comes from `game_exe` `TmForever.exe`.

   Never branch on a title id in shell or Python. `tests/test_titles.py::TitleConditionalTests` fails on `TRACKMANIA_MAC_TITLE = ...`, `title.id == ...` and similar checks outside the legacy shims it lists. Add a registry fact or a component instead.
3. **Icon.** Add `assets/titles/<id>.svg`. The PNGs are generated and gitignored, and `tests/test_title_assets.py` fails without the SVG.
4. **New installer kind (only if no existing `installer` value fits):**
   - Update `INSTALLERS`/`ONLINE_INSTALLERS` in `src/python/trackmania_mac/hub/registry.py` and the label in `src/python/trackmania_mac/hub/snapshot.py`.
   - Add the install flow in `src/shell/lib/install/game.sh` and `src/shell/lib/commands/import.sh`.
5. **Docs and inventory.** Update the title table in `docs/hub-app.md`, "Which games work?" in `README.md`, the hard-coded games list in `website/index.html`, the game dropdown in `.github/ISSUE_TEMPLATE/bug_report.yml`, and the title id set in `tests/test_titles.py::test_allTitle_json_valid` (the one deliberate inventory).
6. **Verify.** Run `uv run --frozen --group test pytest -q tests/test_titles.py tests/test_title_assets.py tests/test_hub_backend.py`, then `make check`. `test_titles.py` checks that the shell's computed paths, components and exported values match the registry for every title, which catches a missing `TRACKMANIA_MAC_TITLE` or a hard-coded path.
