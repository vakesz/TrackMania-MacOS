# Contributing

Thanks for helping improve TrackMania-MacOS. This is a runtime for one game on Apple
Silicon, so a change is judged by whether TMNF still installs, builds and plays
correctly.

## Before you start

- You need an Apple Silicon Mac, macOS 14 or later, and Rosetta 2. Building the
  runtime also needs the full Xcode app, not just the command line tools.
- Read [docs/building.md](../docs/building.md) for the build and release layout,
  and [docs/runtime.md](../docs/runtime.md) for what each patch does.
- Open an issue before a large change so the approach can be agreed first.

## Building

Use a checkout path without spaces; Wine's makefiles require it.

```sh
sudo xcode-select --switch /Applications/Xcode.app/Contents/Developer
make build
make install
```

## Before you open a pull request

```sh
make check     # script syntax, shellcheck, offline tests
make smoke     # runtime probes in the installed prefix
```

- Keep shell scripts shellcheck-clean; CI runs `make check` on every push.
- A runtime patch goes under `patches/<component>/`. It must apply with
  `git apply` against the source pinned in `config/sources.json`, carry a short
  header saying what it does and why, and come with a probe that covers it.
- Match the surrounding style: sentence-case headings, plain wording, no
  decorative language.
- Do not commit game files, installers, your Wine prefix, or anything under
  `.build`.

## Commits and pull requests

- Write commit messages in the imperative mood and explain the why, not just the
  what.
- Fill in the pull request template. Say how you tested, on which macOS version
  and which Mac.
