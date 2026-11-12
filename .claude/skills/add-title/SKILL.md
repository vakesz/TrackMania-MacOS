---
name: add-title
description: Checklist for adding a Forever-era game title to MacTMNF or changing an existing one's registry entry (config/titles/*.json), such as its installer URL or hash, prefix, app name, bundle ID, executables or save directories. Use it whenever a title JSON or assets/titles/ is touched, or the user asks to support another TrackMania (or similar) game, even if they only mention one field.
---

`config/titles/*.json` is the only source of title data. The shell reads it through `python -m tmnf.hub.registry export`, and Swift only sees it through `tmnf hub snapshot`. So a title change should be a data change, never a hard-coded path in shell or Swift.

1. **Entry.** Copy a similar title (e.g. `config/titles/tmu.json`) to `config/titles/<id>.json` and fill in every field:
   - `user_subdir` is the folder under UserData that the game reads (its `Nadeo.ini` `UserSubDir`). It must be one of `save_dirs`, so uninstall guards always cover it. Setup seeds the default graphics settings there.
   - `needs_tmloader` is true for titles that Play starts through Competition Patch and CoreMod. Other titles launch their own executable.
   - `prefix_subdir` must be unique, because two titles sharing a Wine prefix could overwrite each other's installs.
   - The installer's `source_url`, `source_sha256` and `source_size` go here, not in `config/pins.json`, which holds only shared downloads.
   - New titles start with `status` provisional. Only `tmnf` is supported.
2. **Icon.** Add `assets/titles/<id>.svg`. The PNGs are generated and gitignored, and `tests/test_title_assets.py` fails without the SVG.
3. **New installer kind (only if no existing `installer` value fits):**
   - Update `INSTALLERS`/`ONLINE_INSTALLERS` in `src/python/tmnf/hub/registry.py` and the label in `src/python/tmnf/hub/snapshot.py`.
   - Update the `INSTALLERS` set in `tests/test_titles.py`.
   - Add the install flow in `src/shell/lib/install/game.sh` and `src/shell/lib/commands/import.sh`.
4. **Docs.** Update the title table in `docs/hub-app.md` and "Which games work?" in `README.md`.
5. **Verify.** Run `uv run --frozen --group test pytest -q tests/test_titles.py tests/test_title_assets.py tests/test_hub_backend.py`, then `make check`. `test_titles.py` checks that the shell's computed paths match the JSON for every title, which catches a missing `TMNF_TITLE` or a hard-coded path.
