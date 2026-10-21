.DEFAULT_GOAL := help
SHELL_SOURCES := $(shell find src/shell -type f \( -name '*.sh' -o -path 'src/shell/cli/tmnf' \) -print)

.PHONY: help build package appbundle zip install play configure doctor stop app setup check smoke

help:
	@echo 'make build       Build the pinned Wine runtime from source'
	@echo 'make package     Write runtime and source release archives'
	@echo 'make appbundle   Build and sign the native TrackMania Mac hub'
	@echo 'make zip         Create the distributable native hub .app.zip'
	@echo 'make install     Install the runtime, game and Applications launchers'
	@echo 'make play        Play with Competition Patch and all runtime fixes'
	@echo 'make configure   Open the original graphics/settings launcher'
	@echo 'make doctor      Check the installed game and runtime'
	@echo 'make stop        Stop Wine processes in the selected prefix'
	@echo 'make app         Install the game and settings apps in Applications'
	@echo 'make setup       Install runtime, prefix and DirectX without the game'
	@echo 'make check       Validate scripts and run offline regression tests'
	@echo 'make smoke       Run runtime regression probes in the selected prefix'

build:
	./src/shell/tools/build-runtime.sh

package:
	./src/shell/tools/build-runtime.sh --only package

appbundle:
	./src/shell/tools/package-app.sh appbundle

zip:
	./src/shell/tools/package-app.sh zip

install:
	./src/shell/cli/install.sh

play configure doctor stop app setup:
	./src/shell/cli/tmnf $@

check:
	bash -n $(SHELL_SOURCES) tests/test-cli.sh
	shellcheck $(SHELL_SOURCES) tests/test-cli.sh
	@command -v uv >/dev/null 2>&1 || (echo 'error: uv is required (https://docs.astral.sh/uv/)' >&2; exit 1)
	uv run --frozen --group lint ruff check src tests
	uv run --frozen --group test pytest -q
	./tests/test-cli.sh
	swift test --package-path src/swift

smoke:
	@status=0; for probe in sync shaders x87 x87sidecar audio network; do ./src/shell/tools/probes/smoke-$$probe.sh || status=1; done; exit $$status
