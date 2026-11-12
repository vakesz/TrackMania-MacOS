# Security policy

## Supported versions

The latest tagged release and the current `main` branch get security fixes.
Older releases do not.

## Reporting a vulnerability

Report privately through GitHub. Open the repository's **Security** tab and
choose **Report a vulnerability**. Do not open a public issue for a security
problem.

Include the affected version (the hub app's version, or the one printed
by `./src/shell/cli/trackmania-mac doctor` in a checkout), your macOS version and Mac model,
and the steps to reproduce. A proof of concept helps but is not required. Expect
an acknowledgement within a few days and an agreed disclosure timeline once the
problem is confirmed.

## Scope

TrackMania-MacOS builds a patched Wine and D9VK runtime and installs TrackMania
games. A report matters most for:

- The installer and build scripts, which download archives over the network and
  check them by SHA-256 (`src/shell/cli/install.sh`, `src/`, `config/sources.json`).
- The hub's runtime download pinning, which decides whether a downloaded runtime
  is trusted (`config/release-assets.sha256` and `config/runtime-pin`).
- The Wine and D9VK patches under `patches/`.
- The runtime archive published on the Releases page, including redistributed
  upstream Wine, D9VK, MoltenVK, x87sidecar and bundled libraries.

The games and Microsoft DirectX are downloaded from their original hosts during
installation, or supplied by you, and are not redistributed here. Report flaws in their code to
their vendors. Wine is built from upstream source with local patches and bundled
in our runtime, so vulnerabilities in the version we distribute are in scope.
For flaws in bundled upstream components, coordinate with the upstream project
as well; include the affected release so we can track a runtime update.
