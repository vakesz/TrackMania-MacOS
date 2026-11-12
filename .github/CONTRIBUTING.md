# Contributing

Thanks for helping improve TrackMania-MacOS. This is a runtime and hub for a
collection of TrackMania titles on Apple Silicon. A change is judged against
Nations Forever, the supported title, which must still install, build and play
correctly, and it must not break the other titles' install paths. Please follow
the [code of conduct](CODE_OF_CONDUCT.md).

## Before you start

- You need an Apple Silicon Mac and Rosetta 2. macOS 14 is the deployment target,
  not proof of compatibility on every later release. The current tested host is
  an M1 Pro with macOS 27 and Xcode 27. Building the runtime needs the full Xcode
  app, not just the command line tools.
- Read [docs/building.md](../docs/building.md) for the build and release layout,
  [docs/runtime.md](../docs/runtime.md) for what each patch does, and
  [docs/hub-app.md](../docs/hub-app.md) for the contract between the hub app and
  the command line, which changes only together with its tests.
- Open an issue before a large change so the approach can be agreed first.

## Commands

Use a checkout path without spaces; Wine's makefiles require it.

```sh
sudo xcode-select --switch /Applications/Xcode.app/Contents/Developer
make build     # compile the runtime from the pinned sources (hours)
make install   # install the runtime and game via the bundled installer
make check     # everything CI runs
```

`make check` runs Bash syntax checks, ShellCheck, Ruff, mypy, pytest, the CLI
integration test, SwiftLint and the Swift tests. It needs `shellcheck`, `uv` and
`swiftlint`. The `make` targets act on the title in `TRACKMANIA_MAC_TITLE`
(default `tmnf`, Nations Forever). A command run from a checkout with your real
home folder moves an existing install from an earlier release into
`~/Library/TrackManiaMacOS` first.

## Guidelines

- Keep shell scripts shellcheck-clean. CI runs the parts of `make check` that a pull request touches; `.github/scripts/ci-areas.sh` decides which.
- A runtime patch goes under `patches/<component>/`. It must apply with
  `git apply` against the source pinned in `config/sources.json`, carry a short
  header saying what it does and why, and come with a probe that covers it.
- Match the surrounding style: sentence-case headings, plain wording, no
  decorative language.
- Do not commit game files, installers, your Wine prefix, or anything under
  `.build`.

## Commits and pull requests

- Name branches `type/short-desc`, with a type such as `fix`, `feat`, `improve`,
  `ci` or `hub`.
- Write commit messages in the imperative mood and explain the why, not just the
  what.
- Pull requests are squash-merged. Fill in the pull request template. Say how
  you tested, which titles you tried, on which macOS version and which Mac.
