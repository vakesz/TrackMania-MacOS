---
name: release
description: Bump the TrackMania Mac release version (pyproject.toml, uv.lock) and walk through the release order for runtime archives, tag and hub zip. Run as /release X.Y.Z.
disable-model-invocation: true
---

Target version: $ARGUMENTS. If it's empty, show the current `[project] version` in `pyproject.toml` and ask for the new one.

## Bump

1. Check the tree is clean and `main` is up to date with `origin/main`. A release builds from a pushed commit, so local-only work would end up in no published source.
2. Write the new version to `[project] version` in `pyproject.toml`, then run `uv lock`. `uv.lock` records the project version too, and every `uv run --frozen` call fails if it is stale.
3. Run `uv run --frozen --group test pytest -q tests/test_project.py` (it checks the versions match), then `make check`.
4. Show the diff (only `pyproject.toml` and `uv.lock` should change). Ask before committing it as `Release X.Y.Z` on `main`, and ask again before pushing. Release bumps are the one kind of commit that goes straight to main, without a PR.

## Publish (manual, list these for the user; don't run them)

These take hours and use signing credentials, so hand them over as a checklist (source: `docs/building.md` "Release order"):

1. From the clean, pushed commit: `make build && make package`. This produces `.build/dist/trackmania-mac-X.Y.Z-{runtime,source}.tar.xz` and their `.sha256` files.
2. Upload all four files to GitHub release `vX.Y.Z`.
3. Push tag `vX.Y.Z`. `.github/workflows/release-hub.yml` checks that the tag equals `v` plus the `pyproject.toml` version and that the assets exist, pins the runtime sha256 into the hub, signs it and attaches `TrackMania-Mac-*.app.zip`.

Do step 2 before step 3. The hub downloads its runtime archive on first launch, so a hub published before its archive breaks every new install.
