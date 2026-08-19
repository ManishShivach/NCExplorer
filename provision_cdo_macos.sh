#!/usr/bin/env bash
#
# Build a MAGICS-enabled CDO for NCExplorer to bundle. macOS build hosts only.
#
# WHY THIS SCRIPT HAS TO EXIST
#
# MAGICS is linked into CDO at compile time. It is not a plugin and there is no
# way to add it to an existing binary: `brew install magics` installs the
# library and the already-built `cdo` will never pick it up. Homebrew's bottled
# cdo is not built against it — `brew deps cdo` lists eccodes, hdf5, libaec,
# netcdf and proj, and no magics — so `cdo --config has-magics` on a stock
# install answers "no" and all six plotting operators abort.
#
# build.py bundles whatever `cdo` the build host has into the .app, so the build
# host's CDO is what every user gets. Running this once on the build machine is
# what makes plotting work for every end user with zero installation on their
# side.
#
# WHAT WAS EVALUATED AND REJECTED
#
#   conda-forge. Its `cdo` feedstock *does* depend on magics and fftw, which
#   would have made this a one-line install. It has **no macOS build**: querying
#   the Anaconda API for conda-forge/cdo returns subdirs linux-64 and
#   linux-aarch64 only, at version 2.6.1. Verified rather than assumed. It is
#   still the right route inside WSL on Windows, where the platform is linux-64
#   — see the Windows installer.
#
# IDEMPOTENT: re-running with an already-good binary at the prefix exits early
# without rebuilding. Pass --force to rebuild anyway.
#
# NOTHING HERE NEEDS YOUR PASSWORD. It writes only to $PREFIX and $SRCDIR, both
# under your home directory, and installs Homebrew formulae as your own user.

set -euo pipefail

# --- pinned versions -------------------------------------------------------
# CDO is pinned to the version ncexplorer_toolkit/core/nc_operator_catalog.py
# is generated against. Changing it here without regenerating that catalog will
# put the operator schema and the binary out of step.
CDO_VERSION="2.6.3"
CDO_URL="https://code.mpimet.mpg.de/attachments/download/30224/cdo-${CDO_VERSION}.tar.gz"
# From Homebrew's own cdo formula, so a tampered mirror is caught.
CDO_SHA256="889ece29314b48cbf47ba14ab5f1779886f128767f6d22dfcc7fad1e62f2d017"

PREFIX="${CDO_MAGICS_PREFIX:-$HOME/.local/cdo-magics}"
SRCDIR="${CDO_MAGICS_SRC:-$HOME/.local/src}"
FORCE=0
[ "${1:-}" = "--force" ] && FORCE=1

say()  { printf '\033[1m==> %s\033[0m\n' "$*"; }
fail() { printf '\033[1;31mERROR: %s\033[0m\n' "$*" >&2; exit 1; }

[ "$(uname -s)" = "Darwin" ] || fail "macOS only; on Windows CDO runs inside WSL."

# --- 0. already good? ------------------------------------------------------
if [ "$FORCE" -eq 0 ] && [ -x "$PREFIX/bin/cdo" ]; then
  if [ "$("$PREFIX/bin/cdo" --config has-magics 2>/dev/null || true)" = "yes" ]; then
    say "Already provisioned: $PREFIX/bin/cdo reports has-magics:yes"
    "$PREFIX/bin/cdo" --version 2>&1 | head -1
    exit 0
  fi
  say "A binary exists at $PREFIX but has no MAGICS — rebuilding."
fi

command -v brew >/dev/null || fail "Homebrew is required. See https://brew.sh"

# --- 1. the libraries ------------------------------------------------------
# magics supplies libMagPlus; fftw is added so fourier2grid works too — it is
# the one other build gate on this binary, and it costs nothing to include here
# while a user hitting "FFTW3 support not compiled in!" has no recourse at all.
say "Installing library dependencies (idempotent)"
for f in magics fftw eccodes hdf5 libaec netcdf proj; do
  if brew list --versions "$f" >/dev/null 2>&1; then
    echo "    $f $(brew list --versions "$f" | awk '{print $2}') — present"
  else
    echo "    installing $f"; brew install "$f"
  fi
done

MAGICS_PREFIX="$(brew --prefix magics)"
[ -f "$MAGICS_PREFIX/include/magics/magics_api.h" ] \
  || fail "magics_api.h not under $MAGICS_PREFIX/include/magics — CDO's configure looks for it there."

# --- 2. architecture -------------------------------------------------------
# This is the part that silently produces a broken build if skipped.
#
# On an Apple Silicon Mac with an x86_64 Homebrew under Rosetta (prefix
# /usr/local rather than /opt/homebrew), Apple clang still defaults to arm64 —
# and `arch -x86_64 clang` does NOT retarget it; the flag has to be in CC/CXX.
# A native arm64 build will not link against an x86_64 libMagPlus at all.
BREW_PREFIX="$(brew --prefix)"
if file "$MAGICS_PREFIX/lib/libMagPlus.dylib" | grep -q x86_64; then
  ARCHFLAG="-arch x86_64"; HOSTFLAG="--host=x86_64-apple-darwin"
  say "Homebrew is x86_64 (prefix $BREW_PREFIX) — building x86_64 to match"
else
  ARCHFLAG="-arch arm64";  HOSTFLAG="--host=aarch64-apple-darwin"
  say "Homebrew is arm64 (prefix $BREW_PREFIX) — building arm64 to match"
fi

# CDO 2.6.3 needs C++20 std::jthread. Homebrew's formula refuses Apple clang
# builds <= 1699 for this reason. Checked by compiling rather than by version
# number, because the version that gained it differs between toolchains.
say "Checking the compiler can build what CDO needs"
probe="$(mktemp -d)"; trap 'rm -rf "$probe"' EXIT
cat > "$probe/t.cpp" <<'CPP'
#include <thread>
int main(){ std::jthread j([]{}); return 0; }
CPP
clang++ $ARCHFLAG -std=gnu++20 "$probe/t.cpp" -o "$probe/t" 2>/dev/null \
  || fail "This clang cannot build CDO ($ARCHFLAG, C++20 std::jthread).
       Install a newer toolchain:  brew install llvm
       then re-run with:           CC=\"\$(brew --prefix llvm)/bin/clang $ARCHFLAG\" CXX=\"\$(brew --prefix llvm)/bin/clang++ $ARCHFLAG\" $0"

# --- 3. source -------------------------------------------------------------
mkdir -p "$SRCDIR"; cd "$SRCDIR"
if [ ! -f "cdo-${CDO_VERSION}.tar.gz" ]; then
  say "Downloading cdo ${CDO_VERSION}"
  curl -fsSL -o "cdo-${CDO_VERSION}.tar.gz.part" "$CDO_URL"
  mv "cdo-${CDO_VERSION}.tar.gz.part" "cdo-${CDO_VERSION}.tar.gz"
fi
say "Verifying checksum"
echo "${CDO_SHA256}  cdo-${CDO_VERSION}.tar.gz" | shasum -a 256 -c - \
  || fail "Checksum mismatch — refusing to build. Delete $SRCDIR/cdo-${CDO_VERSION}.tar.gz and retry."

rm -rf "cdo-${CDO_VERSION}"
tar xzf "cdo-${CDO_VERSION}.tar.gz"
cd "cdo-${CDO_VERSION}"

# --- 4. configure and build ------------------------------------------------
# The --with-* set is Homebrew's own cdo formula, plus --with-magics and
# --with-fftw3. --disable-openmp matches the bottle (has-openmp:no).
say "Configuring"
# --with-fftw3 is a *boolean*, not a path — read out of CDO 2.6.3's configure:
# anything but "no" enables it, and it then looks for fftw3.h and -lfftw3 on the
# default search path only. Passing --with-fftw3=/some/prefix therefore enables
# the check and silently ignores the prefix, and unlike the magics check it does
# not abort when the library is missing — it just leaves FFTW3 off. So the
# location has to arrive through CPPFLAGS/LDFLAGS, and the result is verified
# below rather than assumed.
FFTW_PREFIX="$(brew --prefix fftw)"
CC="clang $ARCHFLAG" CXX="clang++ $ARCHFLAG" \
CPPFLAGS="-I$FFTW_PREFIX/include ${CPPFLAGS:-}" \
LDFLAGS="-L$FFTW_PREFIX/lib ${LDFLAGS:-}" \
./configure \
  "$HOSTFLAG" \
  --prefix="$PREFIX" \
  --disable-openmp \
  --with-magics="$MAGICS_PREFIX" \
  --with-fftw3 \
  --with-eccodes="$(brew --prefix eccodes)" \
  --with-netcdf="$(brew --prefix netcdf)" \
  --with-hdf5="$(brew --prefix hdf5)" \
  --with-proj="$(brew --prefix proj)" \
  --with-szlib="$(brew --prefix libaec)" \
  > configure.log 2>&1 || { tail -30 configure.log; fail "configure failed — see $PWD/configure.log"; }

grep -q '"magics"' configure.log \
  || fail "configure did not detect magics. See $PWD/configure.log"

say "Compiling (this takes a few minutes)"
make -j"$(sysctl -n hw.ncpu)" > make.log 2>&1 \
  || { grep -m20 -i "error:" make.log; fail "build failed — see $PWD/make.log"; }
make install > install.log 2>&1 || fail "make install failed — see $PWD/install.log"

# --- 5. prove it -----------------------------------------------------------
# A capability flag is not proof the link works, so this renders a real plot.
say "Verifying"
have="$("$PREFIX/bin/cdo" --config has-magics 2>/dev/null || true)"
[ "$have" = "yes" ] || fail "built, but has-magics is '$have' — the link did not take."

work="$(mktemp -d)"; trap 'rm -rf "$probe" "$work"' EXIT
"$PREFIX/bin/cdo" -s -f nc -topo,r36x18 "$work/in.nc" 2>/dev/null
"$PREFIX/bin/cdo" -s shaded,device=png "$work/in.nc" "$work/plot" >/dev/null 2>&1 || true
plot="$(find "$work" -name 'plot*' -type f | head -1)"
[ -n "$plot" ] || fail "has-magics:yes but no plot was produced — the runtime data may be missing."
size=$(stat -f%z "$plot")
# A wrong MAGPLUS_HOME still writes a small degenerate file rather than failing,
# so the size is checked, not merely the existence. Measured: a real 36x18
# shaded PNG is tens of KB; the degraded form is a few hundred bytes.
[ "$size" -gt 5000 ] || fail "plot was only ${size} bytes — Magics data files are probably missing."

say "Done."
echo "    $PREFIX/bin/cdo"
"$PREFIX/bin/cdo" --config all 2>/dev/null | tr -d '\n{}"' | tr ',' '\n' | grep -E 'magics|fftw|cmor' || true
echo "    rendered a ${size}-byte test plot"
echo
echo "build.py picks this up when it is first on PATH. Either:"
echo "    export PATH=\"$PREFIX/bin:\$PATH\" && python3 build.py"
echo "or set NCEXPLORER_CDO=$PREFIX/bin/cdo"

# Copyright (c) 2026 Manish Shivach
# SPDX-License-Identifier: MIT