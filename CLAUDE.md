# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Runtime (patched Wine + D9VK + MoltenVK + x87sidecar) and native Swift hub app for a collection of TrackMania games on Apple Silicon. `tmnf` (Nations Forever) is the supported title id; the others are provisional.

## Commands

- `make check` runs `check-scripts` (bash -n, shellcheck, ruff, mypy, pytest, `tests/test-cli.sh`) and `check-swift` (swiftlint, swift test). Needs `shellcheck`, `uv`, `swiftlint`.
- CI runs each job only when its files change; `.github/scripts/ci-areas.sh` maps paths to jobs (tested by `tests/test_ci_areas.py`). A new file type that a job must check needs a rule there.
- Always `uv run --frozen --group <test|lint|typecheck> ...`; the repo is not pip-installed. Single test: `uv run --frozen --group test pytest -q tests/test_titles.py -k NAME`.
- Swift: `swift test --package-path src/swift --filter HubUITests`; lint with `(cd src/swift && swiftlint lint --strict --quiet)`.
- Run Python modules as `PYTHONPATH=src/python python3 -m trackmania_mac.<pkg>.<module>`.
- Don't run unless asked: `make build`/`package` (hours, full Xcode), `make appbundle`/`zip`, and `make install`/`play`/`smoke`/`setup`/`diag` etc. (they touch the real prefix under `~/Library/TrackManiaMacOS`).
- Title commands (`install`, `play`, `uninstall` and so on) run from a checkout with the real home folder will migrate an existing install from an earlier release into `~/Library/TrackManiaMacOS`, unless `TRACKMANIA_MAC_ROOT`, `_PREFIX` or `_USERDATA` is set. Use a scratch `HOME` for experiments.

## Architecture rules

- Layers: Swift hub → `trackmania-mac hub snapshot|run|migrate` → shell CLI (`src/shell/`) → Python (`src/python/trackmania_mac/`) → runtime. Each layer calls the one below and never duplicates its rules.
- The Swift hub is a pure UI adapter: it only calls `trackmania-mac hub snapshot` (read-only, never starts Wine), `trackmania-mac hub run TITLE ACTION [--source PATH] [--runtime MODE]` and `trackmania-mac hub migrate`. No install logic in Swift. Contract: `docs/hub-app.md`; changing it means updating `hub/{snapshot,run}.py`, Swift `HubModels.swift`/`HubBackend.swift`, and both test suites.
- `config/titles/*.json` is the only source of title facts: app names, bundle IDs, executables and installers (default paths derive from the title id; the `legacy_*` fields are read only by the migration). Swift sees it only via the snapshot. Set `TRACKMANIA_MAC_TITLE` before computing any title-dependent path.
- Shell: prefix-mutating commands go through `require_owned_prefix` + `guard_prefix_operation`; close `TRACKMANIA_MAC_OPERATION_FD` before exec'ing wine/wineserver so a detached wineserver doesn't hold the lock.
- Every default location is under `~/Library/TrackManiaMacOS` (`games/<id>/prefix`, `saves/<id>`, `runtime/<version>`, `launchers/`, `icons/`, `cache`, `logs`); `hub/paths.py` and `src/shell/lib/common.sh` compute them and must agree.
- Python must run on 3.9 (ruff targets py39; use `from __future__ import annotations`).

## Invariants enforced by tests/CI

- `pyproject.toml` version == `trackmania-mac` version in `uv.lock` (run `uv lock` after bumping).
- `REVISION` in `tests/test_x87_patch.py` == x87sidecar revision in `config/sources.json` (only CI catches this).
- MinGW flags in `.github/workflows/check.yml` must match `runtime_builder.py` and `session/monitor.py`.
- Every `curl` under `src/` must pass `-q` as its first argument (`tests/test_supply_chain.py`).
- Every title needs `assets/titles/<id>.svg`.
- `tests/test_naming.py` fails on a stray old name (`tmnf` package or CLI, `TMNF_*`, `.tmnf-*`, `local.tmnf.*`, `MacTMNF`) outside its labelled allow-list. Only the title id `tmnf` and what derives from it keep the name.
- Names of earlier releases are recognised only in `src/python/trackmania_mac/legacy.py`, `src/shell/lib/legacy.sh` and `src/swift/Sources/TrackManiaHub/HubLegacy.swift` (the current marker names are in `trackmania_mac/markers.py`); the allow-list also covers the registry's `legacy_*` fields and test fixtures.
- Runtime patches apply in sorted filename order from `patches/<component>/`.

## Conventions

- Shell stays shellcheck-clean. Python files are formatted with `ruff format` when edited.
- Docs and UI text: sentence-case headings, plain wording, no decorative language.
- Branches: `type/short-desc` (`fix/`, `feat/`, `improve/`, `ci/`, `hub/`). Changes land via squash-merged PRs; only `Release X.Y.Z` bump commits go straight to main.
- Commit messages in the imperative mood that explain the why.
- Never commit game files, installers, Wine prefixes, or anything under `.build/`.
