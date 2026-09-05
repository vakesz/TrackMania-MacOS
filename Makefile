.DEFAULT_GOAL := help
.PHONY: help install build package play configure doctor stop app check smoke

help:
	@echo 'make install     Install the runtime, game and Applications launchers'
	@echo 'make build       Download pinned sources and build the complete runtime'
	@echo 'make play        Play with Competition Patch and all runtime fixes'
	@echo 'make configure   Open the original graphics/settings launcher'
	@echo 'make doctor      Check the installed game and runtime'
	@echo 'make package     Package runtime, corresponding source and checksums'
	@echo 'make check       Validate scripts and run offline regression tests'
	@echo 'make smoke       Run runtime regression probes in the selected prefix'

install:
	./install.sh

build:
	./tools/build.sh

package:
	./tools/build.sh --only package

play configure doctor stop app:
	./bin/mactmnf $@

check:
	bash -n install.sh bin/mactmnf lib/*.sh tools/*.sh tests/*.sh
	@if command -v shellcheck >/dev/null 2>&1; then shellcheck -x install.sh bin/mactmnf lib/*.sh tools/*.sh tests/*.sh; fi
	./tests/test-cli.sh
	python3 -m unittest discover -s tests -p 'test_*.py'

smoke:
	./tools/smoke-network.sh
	./tools/smoke-sync.sh
	./tools/smoke-shaders.sh
	./tools/smoke-x87sidecar.sh
	./tools/smoke-audio.sh
