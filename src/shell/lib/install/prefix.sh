#!/bin/bash
# Wine prefix lifecycle. Sourced by install.sh after common.sh.

initialize_prefix() {
  local wine_bin
  local wineboot_bin

  validate_managed_layout || die "Unsafe installation layout."
  tmnf_python -m tmnf.guard.managed_paths new-prefix "$TMNF_PREFIX" \
    || die "Choose an empty prefix or a managed TMNF installation."
  mkdir -p "$TMNF_PREFIX"
  if test -f "${TMNF_PREFIX}/system.reg"; then
    test -f "${TMNF_PREFIX}/.tmnf-prefix" || die "This prefix is not managed by TMNF. Choose a new TMNF_PREFIX."
    if test ! -f "${TMNF_PREFIX}/.tmnf-initializing"; then
      WINEDLLOVERRIDES='mscoree,mshtml=' run_wine wineboot --update
      return
    fi
  fi

  say "Initializing the Wine prefix"
  wine_bin="$(wine_binary)" || die "Wine is not installed."
  wineboot_bin="$(wineboot_binary "$wine_bin")" || die "Cannot locate wineboot."
  touch "${TMNF_PREFIX}/.tmnf-initializing" || die "Cannot mark prefix initialization."
  if test ! -f "${TMNF_PREFIX}/.tmnf-prefix"; then
    if ! printf '%s:1\n' "$TMNF_TITLE" >"${TMNF_PREFIX}/.tmnf-prefix"; then
      rm -f "${TMNF_PREFIX}/.tmnf-prefix" "${TMNF_PREFIX}/.tmnf-initializing"
      die "Cannot mark prefix ownership."
    fi
  fi
  tmnf_python -m tmnf.guard.managed_paths profile-baseline "$TMNF_PREFIX" \
    || die "Cannot record pre-Wineboot profiles; initialization remains incomplete."
  # TMNF is native code. Skip optional Mono/Gecko registration so prefix setup
  # does not stop at download dialogs for unrelated .NET or browser runtimes.
  without_operation_lock env WINEPREFIX="$TMNF_PREFIX" WINEDEBUG=-all WINEDLLOVERRIDES='mscoree,mshtml=' "$wineboot_bin" --init \
    || die "Wine prefix initialization failed; rerun setup to retry."
  # Redirect fresh Wine default links without touching host data. Preserve older
  # profile arrangements and require explicit backup/repair when seeding is unsafe.
  tmnf_python -m tmnf.guard.managed_paths setup-documents "$TMNF_PREFIX" "$TMNF_USERDATA" \
    || die "Documents setup is incomplete; no external settings were seeded."
  mkdir -p "${TMNF_USERDATA}/TmForever/Config"
  if test ! -e "${TMNF_USERDATA}/TmForever/Config/Default.SystemConfig.Gbx" \
    && test ! -L "${TMNF_USERDATA}/TmForever/Config/Default.SystemConfig.Gbx"; then
    cp "${TMNF_RUNTIME}/config/Default.SystemConfig.Gbx" "${TMNF_USERDATA}/TmForever/Config/"
  fi
  rm -f "${TMNF_PREFIX}/.tmnf-profile-baseline" "${TMNF_PREFIX}/.tmnf-initializing"
}
