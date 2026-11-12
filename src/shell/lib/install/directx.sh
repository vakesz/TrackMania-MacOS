#!/bin/bash
# June 2010 DirectX End-User Runtimes. Sourced by install.sh after common.sh.

install_directx_runtime() (
  local extract_dir cleanup

  if directx_is_current; then
    repair_runtime_dlls
    say "DirectX End-User Runtimes $DIRECTX_VERSION are already installed"
    return
  fi

  download_verified "$DIRECTX_URL" "$DIRECTX_ARCHIVE" "$DIRECTX_SHA256"
  sweep_stale "$TMNF_CACHE" '*-extract.*'
  extract_dir="$(mktemp -d "${TMNF_CACHE}/directx-extract.XXXXXX")"
  printf -v cleanup 'rm -rf -- %q' "$extract_dir"
  # shellcheck disable=SC2064
  trap "$cleanup" EXIT
  bsdtar -xf "$DIRECTX_ARCHIVE" -C "$extract_dir"
  test -f "${extract_dir}/DXSETUP.exe" || die "DirectX archive did not contain DXSETUP.exe."

  say "Installing DirectX End-User Runtimes $DIRECTX_VERSION"
  # Managed DirectX probes Wine's .NET shim even when Mono is absent. TMNF
  # needs the native components; treat .NET as unavailable for this setup.
  WINEDLLOVERRIDES='mscoree=' run_wine "${extract_dir}/DXSETUP.exe" /silent

  # Refresh Wine's own prefix files and registrations. This does not restore
  # DLLs DXSETUP replaced: wineboot never overwrites files that are not Wine's,
  # so repair_runtime_dlls below copies the d3dx9 compilers back explicitly.
  WINEDLLOVERRIDES='mscoree,mshtml=' run_wine wineboot --update

  repair_runtime_dlls

  printf '%s\n' "$DIRECTX_SHA256" >"$DIRECTX_MARKER"
  rm -rf "$extract_dir"
  directx_is_current || die "DirectX runtime installation failed."
  say "Installed DirectX End-User Runtimes $DIRECTX_VERSION"
)

repair_runtime_dlls() {
  local wine_root arch dll source
  wine_root="$(dirname "$(dirname "$(wine_binary)")")"
  for arch in i386 x86_64; do
    for dll in "${RUNTIME_D3DX9_DLLS[@]}"; do
      source="${wine_root}/lib/wine/${arch}-windows/${dll}.dll"
      test -f "$source" || die "The runtime is missing $source"
      if test "$arch" = i386; then
        cp "$source" "${TMNF_PREFIX}/drive_c/windows/syswow64/${dll}.dll"
      else
        cp "$source" "${TMNF_PREFIX}/drive_c/windows/system32/${dll}.dll"
      fi
    done
  done
}
