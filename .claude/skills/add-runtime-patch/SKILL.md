---
name: add-runtime-patch
description: Checklist for changing the MacTMNF runtime, meaning a patch under patches/ for Wine, D9VK, MoltenVK or x87sidecar, or a bump of a pinned upstream revision or archive in config/sources.json. Use it whenever the work touches patches/ or config/sources.json, or the user wants to fix or work around a Wine, Direct3D 9, Metal/Vulkan or x87 floating-point problem in the runtime itself, even if they don't say "patch".
---

The runtime is built from the pinned sources in `config/sources.json` plus every `patches/<component>/*.patch`, applied in sorted filename order (`runtime_builder.py`). A full build takes hours, so most mistakes only show up long after the edit. Each step below is there to catch one of them early.

1. **Find the pin.** Look up the component's revision or archive sha256 in `config/sources.json`. The patch must apply to exactly that revision.
2. **Name and header.** Write `patches/<component>/NNNN-short-name.patch`. The number sets where it applies in the order; gaps are fine (wine skips 0003/0004). Start the file with a few lines saying what it changes and why, because `docs/runtime.md` and future rebases rely on that header.
3. **Prove it applies.** In a scratch clone at the pinned revision (in the scratchpad, not inside the repo), apply every earlier patch in that directory, then `git apply --check` the new one. If you change an existing patch, re-check the patches after it too.
4. **Add coverage.** Write a probe as `src/shell/tools/probes/smoke-<name>.sh` and add its name to the `smoke` loop in `Makefile`, and/or a `tests/*.c` program. A patch with no probe can regress silently on the next rebase.
5. **Document it.** Add an entry to `docs/runtime.md`.
6. **Keep pinned values in sync:**
   - If the x87sidecar revision changes, set `REVISION` in `tests/test_x87_patch.py` to the same value. Only CI's x87-native job catches a mismatch.
   - If C helper compile flags change, keep `.github/workflows/check.yml`, `src/python/tmnf/build/runtime_builder.py` (`session()`) and `src/python/tmnf/session/monitor.py` (`start_state_helper()`) identical.
   - For a pin bump, rebase every patch for that component and repeat step 3 for all of them.
7. **Verify.** Run `make check`. Don't run `make build` or `make smoke` unless the user asks: the build takes hours and smoke runs against the real Wine prefix.
