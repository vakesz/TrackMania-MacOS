# Compiler runtime notices

D9VK statically links MinGW's C/C++ runtime support. These notices accompany
that code in the runtime archive. The compiler version used for each build is
recorded in `build-info.json`.

- `mingw-w64.txt`: COPYING shipped with Homebrew's mingw-w64 14.0.0.
- `winpthreads.txt`: [mingw-w64 14.0.0 winpthreads COPYING](https://github.com/mingw-w64/mingw-w64/blob/v14.0.0/mingw-w64-libraries/winpthreads/COPYING).
- `GCC-RUNTIME.txt`: [GCC 16.2 runtime library exception](https://github.com/gcc-mirror/gcc/blob/releases/gcc-16.2.0/COPYING.RUNTIME).
- `GPL-3.0.txt`: [GCC 16.2 COPYING3](https://github.com/gcc-mirror/gcc/blob/releases/gcc-16.2.0/COPYING3).

The build also collects the licenses and notices from each bundled library's
pinned source tree. Review compiler runtime notices when updating the toolchain.
