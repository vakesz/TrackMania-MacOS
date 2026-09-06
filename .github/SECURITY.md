# Security policy

## Supported versions

The latest tagged release and the current `main` branch get security fixes.
Older releases do not.

## Reporting a vulnerability

Report privately through GitHub. Open the repository's **Security** tab and
choose **Report a vulnerability**. Do not open a public issue for a security
problem.

Include the affected version (`./bin/mactmnf doctor` prints it), your macOS
version and Mac model, and the steps to reproduce. A proof of concept helps but
is not required. Expect an acknowledgement within a few days and an agreed
disclosure timeline once the problem is confirmed.

## Scope

TrackMania-MacOS builds a patched Wine and D9VK runtime and installs TrackMania Nations
Forever. A report matters most for:

- The installer and build scripts, which download archives over the network and
  check them by SHA-256 (`install.sh`, `tools/`, `config/sources.json`).
- The Wine and D9VK patches under `patches/`.
- The runtime archive published on the Releases page.

The game, Microsoft DirectX and Wine upstream come from their own hosts and are
not redistributed here. Report a flaw in that upstream code to its own project.
