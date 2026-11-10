# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Runtime (patched Wine + D9VK + MoltenVK + x87sidecar) and native Swift hub app for TrackMania Nations Forever on Apple Silicon. Only `tmnf` is supported; other titles are provisional.

## Commands

- `make check` runs everything CI's main job runs (bash -n, shellcheck, ruff, mypy, pytest, `tests/test-cli.sh`, swiftlint, swift test). Needs `shellcheck`, `uv`, `swiftlint`.
- Always `uv run --frozen --group <test|lint|typecheck> ...`; the repo is not pip-installed. Single test: `uv run --frozen --group test pytest -q tests/test_titles.py -k NAME`.
- Swift: `swift test --package-path src/swift --filter HubUITests`; lint with `(cd src/swift && swiftlint lint --strict --quiet)`.
- Run Python modules as `PYTHONPATH=src/python python3 -m tmnf.<pkg>.<module>`.
- Don't run unless asked: `make build`/`package` (hours, full Xcode), `make appbundle`/`zip`, and `make install`/`play`/`smoke`/`setup`/`diag` etc. (they touch the real prefix under `~/Games/TMNF`).

## Architecture rules

- Layers: Swift hub → `tmnf hub snapshot|run` → shell CLI (`src/shell/`) → Python (`src/python/tmnf/`) → runtime. Each layer calls the one below and never duplicates its rules.
- The Swift hub is a pure UI adapter: it only calls `tmnf hub snapshot` (read-only, never starts Wine) and `tmnf hub run TITLE ACTION [--source PATH] [--runtime MODE]`. No install logic in Swift. Contract: `docs/hub-app.md`; changing it means updating `hub/{snapshot,run}.py`, Swift `HubModels.swift`/`HubBackend.swift`, and both test suites.
- `config/titles/*.json` is the only source of title paths, markers, app names and bundle IDs. Swift sees it only via the snapshot. Set `TMNF_TITLE` before computing any title-dependent path.
- Shell: prefix-mutating commands go through `require_owned_prefix` + `guard_prefix_operation`; close `TMNF_OPERATION_FD` before exec'ing wine/wineserver so a detached wineserver doesn't hold the lock.
- Python must run on 3.9 (ruff targets py39; use `from __future__ import annotations`).

## Invariants enforced by tests/CI

- `VERSION` == `pyproject.toml` version == `trackmania-mac` version in `uv.lock` (run `uv lock` after bumping).
- `REVISION` in `tests/test_x87_patch.py` == x87sidecar revision in `config/sources.json` (only CI catches this).
- MinGW flags in `.github/workflows/check.yml` must match `runtime_builder.py` and `session/monitor.py`.
- Every `curl` under `src/` must pass `-q` as its first argument (`tests/test_supply_chain.py`).
- Every title needs `assets/titles/<id>.svg`.
- Runtime patches apply in sorted filename order from `patches/<component>/`.

## Conventions

- Shell stays shellcheck-clean. Python files are formatted with `ruff format` when edited.
- Docs and UI text: sentence-case headings, plain wording, no decorative language.
- Branches: `type/short-desc` (`fix/`, `feat/`, `improve/`, `ci/`, `hub/`). Changes land via squash-merged PRs; only `Release X.Y.Z` bump commits go straight to main.
- Commit messages in the imperative mood that explain the why.
- Never commit game files, installers, Wine prefixes, or anything under `.build/`.
