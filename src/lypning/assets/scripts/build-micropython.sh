#!/usr/bin/env bash
#
# Build lypning: a stripped, statically linked, i386 Python-subset runtime for
# the in-browser CheerpX sandbox. See docs/MICROPYTHON.md for why it exists and
# docs/RESEARCH.md for why it is built this way.
#
#   bash scripts/build-micropython.sh              # → micropython/build/lypning-mp
#   bash scripts/build-micropython.sh --clean      # discard the work dir first
#   bash scripts/build-micropython.sh --verify     # build, then run the gates
#   bash scripts/build-micropython.sh --stock      # → micropython/build/micropython-stock
#
# --stock builds the BENCHMARK CONTROL: upstream MicroPython, unpatched, with
# no frozen lypning-mp stdlib, from the same pinned commit and through the same
# toolchain. It is what lypning bench --micropython compares lypning-mp against, and
# the guarantees that make that comparison meaningful are documented at
# build_stock() below. See docs/BENCH-LEDGER.md.
#
# From scratch this takes a few minutes, almost all of it musl and the first
# MicroPython compile. Both are cached in micropython/.build, so a rebuild after
# editing the variant or micropython/lib is seconds.
#
# NETWORK is needed exactly once, for two downloads, both pinned by version and
# both verified: the musl source tarball (by SHA-256) and the MicroPython git
# tag (by commit SHA). Nothing floats on `master`.
#
# WHY THIS SHAPE, and not the obvious alternatives — every one of these was
# tried in the container this runs in (docs/RESEARCH.md §6 item 4):
#
#   - Docker: no daemon available.
#   - musl-cross-make: needs a full GCC bootstrap, which is long and pointless
#     once `gcc -m32` exists.
#   - Ubuntu's musl-tools: x86_64 only. Its -m32 mode cannot work, because the
#     package contains no 32-bit musl libc.
#   - A prebuilt i686-linux-musl toolchain: means trusting a third-party binary.
#   - tcc: x86_64 only here, and has no i386 crt files.
#   - glibc-static: 635,744 B for an empty main() against musl's 13,020, and
#     /usr/lib32/libm.a does not even resolve fmod. It would blow the 700 KB
#     gate before a line of interpreter existed.
#
# Building musl from source with the host gcc's -m32 takes under a minute and
# needs no root, so that is what this does.

set -euo pipefail

# --------------------------------------------------------------------------
# Pinned inputs. Changing either of these is a deliberate act: re-run the
# gates and the conformance battery, and expect the port patch to need a
# rebase.
# --------------------------------------------------------------------------
MUSL_VERSION="1.2.5"
MUSL_SHA256="a9a118bbe84d8764da0ea0d28b3ab3fae8477fc7e4085d90102b8596fc7c75e4"
MUSL_URL="https://musl.libc.org/releases/musl-${MUSL_VERSION}.tar.gz"

# v1.28.0, the newest MicroPython RELEASE tag. The research pass measured
# against a 1.29.0-preview commit; a tag is pinned here instead, because a
# preview moves and the whole point of the variant is that tracking upstream
# stays a rebase we choose to do.
MPY_TAG="v1.28.0"
MPY_COMMIT="e0e9fbb17ed6fd06bb76e266ae554784c9c80804"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LYPNING_MP_DIR="$REPO_ROOT/micropython"
WORK="${LYPNING_WORK:-$LYPNING_MP_DIR/.build}"
OUT="$LYPNING_MP_DIR/build/lypning-mp"

MUSL_PREFIX="$WORK/musl-i386"
MPY_DIR="$WORK/micropython"
VARIANT_DEST="$MPY_DIR/ports/unix/variants/lypning-mp"
# The control build gets its OWN checkout of the same pinned commit. It is a
# local clone, so it costs no network and about three seconds — and it means the
# stock tree is never reached by `git apply`, and the two builds do not
# invalidate each other's object files by alternately patching and unpatching
# one shared source tree.
STOCK_DIR="$WORK/micropython-stock"
STOCK_VARIANT_DEST="$STOCK_DIR/ports/unix/variants/stock"
STOCK_OUT="$LYPNING_MP_DIR/build/micropython-stock"
MPY_LIB_STUB="$WORK/mpy-lib-stub"
JOBS="$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 4)"

DO_CLEAN=0
DO_VERIFY=0
DO_STOCK=0
for arg in "$@"; do
    case "$arg" in
        --clean) DO_CLEAN=1 ;;
        --verify) DO_VERIFY=1 ;;
        --stock) DO_STOCK=1 ;;
        -h|--help) sed -n '2,48p' "${BASH_SOURCE[0]}"; exit 0 ;;
        *) echo "lypning-build: unknown option $arg" >&2; exit 2 ;;
    esac
done

say() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
die() { printf '\nlypning-build: %s\n' "$*" >&2; exit 1; }

# --------------------------------------------------------------------------
# 0. Host requirements
# --------------------------------------------------------------------------
for tool in gcc make git python3 tar; do
    command -v "$tool" >/dev/null 2>&1 || die "missing required tool: $tool"
done
if command -v curl >/dev/null 2>&1; then
    FETCH=(curl -fsSL -o)
elif command -v wget >/dev/null 2>&1; then
    FETCH=(wget -q -O)
else
    die "need curl or wget to download the musl tarball"
fi

# The 32-bit host toolchain. Without it there is no i386 target at all, and the
# apt package name is the single most useful thing this script can say.
if ! echo 'int main(void){return 0;}' | gcc -m32 -x c - -o /dev/null 2>/dev/null; then
    die "gcc cannot target i386. Install the multilib toolchain:
    sudo apt-get update && sudo apt-get install -y gcc-multilib libc6-dev-i386"
fi

if [ "$DO_CLEAN" = 1 ]; then
    say "Clean: removing $WORK"
    rm -rf "$WORK" "$LYPNING_MP_DIR/build"
fi
mkdir -p "$WORK/dl" "$LYPNING_MP_DIR/build"

# --------------------------------------------------------------------------
# 1. musl for i386, from source, cached
# --------------------------------------------------------------------------
if [ -f "$MUSL_PREFIX/lib/libc.a" ] && [ -x "$MUSL_PREFIX/bin/musl-gcc" ]; then
    say "musl $MUSL_VERSION: cached"
else
    say "musl $MUSL_VERSION: building for i386 (about a minute)"
    TARBALL="$WORK/dl/musl-${MUSL_VERSION}.tar.gz"
    if [ ! -f "$TARBALL" ]; then
        "${FETCH[@]}" "$TARBALL" "$MUSL_URL"
    fi
    echo "$MUSL_SHA256  $TARBALL" | sha256sum -c - >/dev/null \
        || die "musl tarball checksum mismatch — refusing to build against it"

    rm -rf "$WORK/musl-${MUSL_VERSION}" "$MUSL_PREFIX"
    tar -xzf "$TARBALL" -C "$WORK"
    (
        cd "$WORK/musl-${MUSL_VERSION}"
        # TRAP 1 (cost a build): --target=i386 makes musl look for `i386-ar` and
        # `i386-ranlib`, which do not exist. It must be i686, AND the tools have
        # to be named explicitly. docs/RESEARCH.md §2.1.
        ./configure --prefix="$MUSL_PREFIX" --target=i686 \
            CC="gcc -m32" AR=ar RANLIB=ranlib >"$WORK/musl-configure.log" 2>&1 \
            || { tail -20 "$WORK/musl-configure.log"; die "musl configure failed"; }
        make -j"$JOBS" >"$WORK/musl-make.log" 2>&1 \
            || { tail -20 "$WORK/musl-make.log"; die "musl build failed"; }
        make install >>"$WORK/musl-make.log" 2>&1 \
            || { tail -20 "$WORK/musl-make.log"; die "musl install failed"; }
    )

    # TRAP 2 (cost a build): the musl-gcc wrapper musl generates does not pass
    # -m32 through to the linker's emulation, so the link dies with
    # "skipping incompatible .../libc.a". -Wl,-m,elf_i386 is the fix.
    cat >"$MUSL_PREFIX/bin/musl-gcc" <<EOF
#!/bin/sh
exec gcc -m32 "\$@" -Wl,-m,elf_i386 -specs "$MUSL_PREFIX/lib/musl-gcc.specs"
EOF
    chmod +x "$MUSL_PREFIX/bin/musl-gcc"
fi

# Prove the toolchain before spending minutes on the interpreter: a static
# hello that is 13 KB, not 636 KB, is the whole reason musl is here.
FLOOR_C="$WORK/floor.c"
printf 'int main(void){return 0;}\n' >"$FLOOR_C"
"$MUSL_PREFIX/bin/musl-gcc" -Os -static -o "$WORK/floor" "$FLOOR_C" \
    || die "the musl-i386 wrapper cannot link a static binary"
strip "$WORK/floor"
FLOOR_BYTES="$(stat -c %s "$WORK/floor")"
echo "    static i386 floor: ${FLOOR_BYTES} B (glibc-static would be ~635,744)"
[ "$FLOOR_BYTES" -lt 100000 ] || die "static floor is ${FLOOR_BYTES} B — this is not linking against musl"

# --------------------------------------------------------------------------
# 2. MicroPython at a pinned tag
# --------------------------------------------------------------------------
if [ -d "$MPY_DIR/.git" ] && [ "$(git -C "$MPY_DIR" rev-parse HEAD 2>/dev/null || true)" = "$MPY_COMMIT" ]; then
    say "MicroPython $MPY_TAG: cached"
else
    say "MicroPython $MPY_TAG: cloning"
    rm -rf "$MPY_DIR"
    git clone --depth 1 --branch "$MPY_TAG" \
        https://github.com/micropython/micropython.git "$MPY_DIR" >/dev/null 2>&1 \
        || die "could not clone MicroPython $MPY_TAG"
fi
HEAD_SHA="$(git -C "$MPY_DIR" rev-parse HEAD)"
[ "$HEAD_SHA" = "$MPY_COMMIT" ] \
    || die "MicroPython HEAD is $HEAD_SHA, expected $MPY_COMMIT for $MPY_TAG"

# --------------------------------------------------------------------------
# 2b. --stock: the benchmark CONTROL build
# --------------------------------------------------------------------------
# lypning bench --micropython measures lypning-mp against stock MicroPython to isolate
# what OUR variant costs — the port patch, the frozen stdlib, insertion-ordered
# dicts, unicode \w, exact float repr. That subtraction is only valid if the two
# binaries differ in NOTHING ELSE, so here is exactly how each dimension is
# held equal, and exactly where the control is forced to differ.
#
# HELD EQUAL — and each one is mechanically enforced, not just intended:
#
#   MicroPython commit  Both trees are checked out at $MPY_COMMIT and the SHA is
#                       re-verified below. The stock tree is additionally
#                       asserted CLEAN (`git diff --quiet`) after its reset,
#                       which is the mechanical proof that no patch from
#                       micropython/variant/patches/ has touched it.
#   libc + architecture  Both link the same $MUSL_PREFIX built by step 1 of this
#                       same script, through the same musl-gcc wrapper, for
#                       i686. There is one musl in the work directory and both
#                       builds use it.
#   compiler + flags    The stock variant makefile is not hand-written. The
#                       block between the SHARED TOOLCHAIN BLOCK markers in
#                       micropython/variant/mpconfigvariant.mk is EXTRACTED VERBATIM
#                       into it, so -m32, -static, -Wl,-m,elf_i386,
#                       -fno-stack-protector and COPT=-Os -DNDEBUG cannot drift
#                       between the two. Editing them changes both binaries.
#   strip state         Both are built with STRIP=strip and then stripped again
#                       here, and the smoke check below asserts `file` reports
#                       ", stripped" for the control exactly as it does for
#                       lypning-mp.
#   invocation          Same make target, same -j, same CC/LD/STRIP/MPY_LIB_DIR.
#
# FORCED TO DIFFER — five deltas, all of them consequences of building offline
# and statically, none of them a lypning-mp design choice. They are listed here
# because an unlisted difference is how a benchmark quietly starts measuring the
# toolchain:
#
#   1. FROZEN_MANIFEST is empty. Upstream `standard` freezes mip-cmdline and ssl
#      from micropython-lib, a git submodule this build deliberately never
#      clones. Empty is also the honest control for "what does lypning-mp's frozen
#      stdlib cost", which is one of the things being measured.
#   2. MICROPY_PY_BTREE=0  — needs the berkeley-db submodule.
#   3. MICROPY_PY_FFI=0    — links libffi, which would make the binary dynamic.
#   4. MICROPY_PY_SSL=0 / MICROPY_SSL_AXTLS=0 — needs the axtls submodule.
#   5. MICROPY_VFS_FAT=0 / MICROPY_VFS_LFS1=0 / MICROPY_VFS_LFS2=0 — submodules.
#
# Everything else is left at upstream's `standard` defaults ON PURPOSE, even
# where lypning-mp switches it off: socket, _thread, termios, readline and the REPL
# are all present in the control. Those are lypning-mp SCOPE decisions, so their
# cost in bytes belongs in the comparison rather than being quietly normalised
# away. The control's mpconfigvariant.h is upstream's `standard` header copied
# byte for byte.
build_stock() {
    say "Stock control: cloning $MPY_TAG into a separate tree"
    if [ -d "$STOCK_DIR/.git" ] && [ "$(git -C "$STOCK_DIR" rev-parse HEAD 2>/dev/null || true)" = "$MPY_COMMIT" ]; then
        echo "    cached"
    else
        rm -rf "$STOCK_DIR"
        git clone --quiet --no-checkout "$MPY_DIR" "$STOCK_DIR" \
            || die "could not make a local clone of $MPY_DIR"
        git -C "$STOCK_DIR" checkout --quiet "$MPY_COMMIT" \
            || die "could not check out $MPY_COMMIT in the stock tree"
    fi

    # Reset every run, exactly as the lypning-mp path does — and then simply do not
    # patch. The assertion after it is the point: a clean tree at the pinned
    # commit is a *checked* claim that the control carries none of our changes.
    git -C "$STOCK_DIR" reset --hard "$MPY_COMMIT" >/dev/null
    git -C "$STOCK_DIR" clean -qfd -e ports/unix/build-stock -e ports/unix/variants/stock -e mpy-cross/build
    local stock_head
    stock_head="$(git -C "$STOCK_DIR" rev-parse HEAD)"
    [ "$stock_head" = "$MPY_COMMIT" ] \
        || die "stock tree HEAD is $stock_head, expected $MPY_COMMIT"
    git -C "$STOCK_DIR" diff --quiet \
        || die "the stock tree has local modifications — the control must be unpatched upstream"

    say "Stock control: generating the variant"
    # The header is upstream's, verbatim. The makefile is upstream's plus the
    # extracted shared toolchain block plus the five forced disables above.
    local upstream_variant="$STOCK_DIR/ports/unix/variants/standard"
    [ -f "$upstream_variant/mpconfigvariant.h" ] \
        || die "upstream has no variants/standard/mpconfigvariant.h — did the pin move?"
    rm -rf "$STOCK_VARIANT_DEST"
    mkdir -p "$STOCK_VARIANT_DEST"
    cp "$upstream_variant/mpconfigvariant.h" "$STOCK_VARIANT_DEST/mpconfigvariant.h"

    # Extract the shared block. If the markers are gone the build must FAIL,
    # never silently fall back to hand-copied flags — a control built with
    # different optimisation flags measures the compiler, not lypning-mp.
    local shared
    shared="$(awk '/^# >>> SHARED TOOLCHAIN BLOCK/{f=1;next} /^# <<< SHARED TOOLCHAIN BLOCK/{f=0} f' \
        "$LYPNING_MP_DIR/variant/mpconfigvariant.mk")"
    printf '%s' "$shared" | grep -q -- '-static' \
        || die "could not extract the SHARED TOOLCHAIN BLOCK from micropython/variant/mpconfigvariant.mk.
The markers are what keeps the benchmark control apples-to-apples; restore them
rather than duplicating the flags here."

    {
        printf '# GENERATED by scripts/build-micropython.sh --stock. Do not edit.\n'
        printf '#\n'
        printf '# The benchmark control: upstream MicroPython, unpatched, no frozen lypning-mp\n'
        printf '# stdlib, same pinned commit, same musl-i386 toolchain, same flags.\n'
        printf '# The rationale and the five forced deltas are in build_stock().\n\n'
        printf 'PROG = micropython-stock\n\n'
        printf '# micropython-lib is a submodule this build never clones.\n'
        printf 'FROZEN_MANIFEST =\n\n'
        printf '# Forced off: each needs a git submodule or a shared library.\n'
        printf 'MICROPY_PY_BTREE = 0\n'
        printf 'MICROPY_PY_FFI = 0\n'
        printf 'MICROPY_PY_SSL = 0\n'
        printf 'MICROPY_SSL_AXTLS = 0\n'
        printf 'MICROPY_VFS_FAT = 0\n'
        printf 'MICROPY_VFS_LFS1 = 0\n'
        printf 'MICROPY_VFS_LFS2 = 0\n\n'
        printf '# --- extracted verbatim from micropython/variant/mpconfigvariant.mk ---\n'
        printf '%s\n' "$shared"
    } >"$STOCK_VARIANT_DEST/mpconfigvariant.mk"

    # The same micropython-lib stub the lypning-mp build uses: the port Makefile
    # insists the directory exists even when nothing is frozen from it.
    mkdir -p "$MPY_LIB_STUB"

    say "Building mpy-cross for the stock tree (host tool)"
    make -C "$STOCK_DIR/mpy-cross" -j"$JOBS" >"$WORK/mpy-cross-stock.log" 2>&1 \
        || { tail -30 "$WORK/mpy-cross-stock.log"; die "stock mpy-cross build failed"; }

    say "Building stock MicroPython (i386, musl, static)"
    make -C "$STOCK_DIR/ports/unix" \
        VARIANT=stock \
        CC="$MUSL_PREFIX/bin/musl-gcc" \
        LD="$MUSL_PREFIX/bin/musl-gcc" \
        STRIP=strip \
        MPY_LIB_DIR="$MPY_LIB_STUB" \
        -j"$JOBS" >"$WORK/stock.log" 2>&1 \
        || { tail -40 "$WORK/stock.log"; die "stock build failed — full log in $WORK/stock.log"; }

    local built="$STOCK_DIR/ports/unix/build-stock/micropython-stock"
    [ -f "$built" ] || die "the stock build reported success but produced no binary"
    strip "$built" 2>/dev/null || true
    cp "$built" "$STOCK_OUT"

    # The control's own smoke checks. These are NOT lypning-mp's contract checks —
    # stock is upstream MicroPython and owes none of them. They assert only the
    # four properties the comparison depends on.
    say "Stock control: shape checks"
    local sfail=0
    scheck() {
        if [ "$2" = "$3" ]; then printf '    ok    %-42s %s\n' "$1" "$2"
        else printf '    FAIL  %-42s got %s, want %s\n' "$1" "$2" "$3"; sfail=1; fi
    }
    scheck "statically linked" "$(file -L "$STOCK_OUT" | grep -c 'statically linked')" "1"
    scheck "ELF 32-bit i386" "$(file -L "$STOCK_OUT" | grep -c 'ELF 32-bit.*80386')" "1"
    scheck "stripped" "$(file -L "$STOCK_OUT" | grep -c ', stripped')" "1"
    scheck "runs -c 'pass'" "$("$STOCK_OUT" -c 'pass' >/dev/null 2>&1; echo $?)" "0"
    # The control must NOT be lypning-mp. If a copy of lypning-mp ever landed here every
    # ratio in the ledger would read 1.00 and look like a clean result, so this
    # is checked on two things only lypning-mp does: the pinned sys.path and the
    # version line.
    scheck "is not lypning: sys.path is upstream's" \
        "$("$STOCK_OUT" -c 'import sys; print(len(sys.path) > 1)' 2>/dev/null)" "True"
    scheck "is not lypning: no lypning-mp version line" \
        "$("$STOCK_OUT" --version 2>&1 | grep -c 'lypning-mp' || true)" "0"
    [ "$sfail" = 0 ] || die "stock shape checks failed"

    say "Built $STOCK_OUT — $(stat -c %s "$STOCK_OUT") bytes"
    echo "    benchmark against lypning:  lypning bench"
}

if [ "$DO_STOCK" = 1 ]; then
    build_stock
    exit 0
fi

# --------------------------------------------------------------------------
# 3. The port patch
# --------------------------------------------------------------------------
# Reset to the pinned commit and re-apply, every run. This is what keeps the
# script idempotent: the tree is a pure function of (commit, patches), never of
# how many times it has been built. `reset --hard` leaves untracked files, so
# the object files from the last build survive and make stays incremental.
say "Applying the port patch"
git -C "$MPY_DIR" reset --hard "$MPY_COMMIT" >/dev/null
shopt -s nullglob
PATCHES=("$LYPNING_MP_DIR/variant/patches"/*.patch)
shopt -u nullglob
[ ${#PATCHES[@]} -gt 0 ] || die "no patches in micropython/variant/patches — the variant cannot build without them"
for patch in "${PATCHES[@]}"; do
    git -C "$MPY_DIR" apply --whitespace=nowarn "$patch" \
        || die "patch failed to apply: $(basename "$patch")
This is what a MicroPython version bump looks like. Rebase the patch against
$MPY_TAG rather than editing the checkout by hand — see micropython/README.md."
    echo "    $(basename "$patch")"
done

# --------------------------------------------------------------------------
# 4. Sync the variant and the frozen library
# --------------------------------------------------------------------------
# micropython/lib/ is owned by the frozen-stdlib work and is globbed by manifest.py,
# so modules appear in the build purely by existing. The destination is wiped
# rather than overwritten: a stale copy of a module that was deleted upstream
# would otherwise stay frozen into the binary forever.
say "Syncing the variant"
rm -rf "$VARIANT_DEST"
mkdir -p "$VARIANT_DEST/lib"
cp "$LYPNING_MP_DIR/variant"/*.h "$LYPNING_MP_DIR/variant"/*.mk "$LYPNING_MP_DIR/variant/manifest.py" "$VARIANT_DEST/"
LIB_COUNT=0
if compgen -G "$LYPNING_MP_DIR/lib/*.py" >/dev/null; then
    cp -r "$LYPNING_MP_DIR/lib"/*.py "$VARIANT_DEST/lib/"
    LIB_COUNT="$(find "$LYPNING_MP_DIR/lib" -name '*.py' | wc -l | tr -d ' ')"
fi
for d in "$LYPNING_MP_DIR/lib"/*/; do
    [ -d "$d" ] || continue
    cp -r "$d" "$VARIANT_DEST/lib/"
done
echo "    frozen modules from micropython/lib: $LIB_COUNT"

# micropython-lib is a git submodule this build never reads: manifest.py freezes
# micropython/lib and requires nothing from upstream's library. The Makefile still
# insists the directory exists, so point it at a stub rather than cloning tens
# of megabytes of modules that would not be frozen.
mkdir -p "$MPY_LIB_STUB"
printf 'Not micropython-lib. lypning-mp freezes only micropython/lib; see manifest.py.\n' \
    >"$MPY_LIB_STUB/README.md"

# --------------------------------------------------------------------------
# 5. Build
# --------------------------------------------------------------------------
# mpy-cross is a HOST tool (it compiles the frozen .py to .mpy at build time),
# so it is built first, on its own, with the plain host compiler. Building it
# in the same invocation as the port would push CC=musl-gcc into its sub-make
# through MAKEFLAGS and cross-compile the build tool.
say "Building mpy-cross (host tool)"
make -C "$MPY_DIR/mpy-cross" -j"$JOBS" >"$WORK/mpy-cross.log" 2>&1 \
    || { tail -30 "$WORK/mpy-cross.log"; die "mpy-cross build failed"; }

say "Building lypning-mp (i386, musl, static)"
make -C "$MPY_DIR/ports/unix" \
    VARIANT=lypning-mp \
    CC="$MUSL_PREFIX/bin/musl-gcc" \
    LD="$MUSL_PREFIX/bin/musl-gcc" \
    STRIP=strip \
    MPY_LIB_DIR="$MPY_LIB_STUB" \
    -j"$JOBS" >"$WORK/lypning-mp.log" 2>&1 \
    || { tail -40 "$WORK/lypning-mp.log"; die "lypning-mp build failed — full log in $WORK/lypning-mp.log"; }

BUILT="$MPY_DIR/ports/unix/build-lypning-mp/lypning-mp"
[ -f "$BUILT" ] || die "the build reported success but produced no binary"

# The port's own strip already ran; this is belt and braces for the case where
# STRIP was overridden, and it is what the size gate measures.
strip "$BUILT" 2>/dev/null || true
cp "$BUILT" "$OUT"

# --------------------------------------------------------------------------
# 6. Smoke checks — the contracts that must hold on every build
# --------------------------------------------------------------------------
# These are not the gates (lypning gate) and not the conformance
# battery (lypning conformance). They are the handful of properties
# that make those two worth running at all, checked here so a broken build
# fails at the build rather than three tools later.
say "Smoke checks"
fail=0
check() {
    local name="$1" got="$2" want="$3"
    if [ "$got" = "$want" ]; then
        printf '    ok    %-42s %s\n' "$name" "$got"
    else
        printf '    FAIL  %-42s got %s, want %s\n' "$name" "$got" "$want"
        fail=1
    fi
}

check "statically linked" \
    "$(file -L "$OUT" | grep -c 'statically linked')" "1"
check "ELF 32-bit i386" \
    "$(file -L "$OUT" | grep -c 'ELF 32-bit.*80386')" "1"
check "stripped" \
    "$(file -L "$OUT" | grep -c ', stripped')" "1"
check "runs -c 'pass'" \
    "$("$OUT" -c 'pass' >/dev/null 2>&1; echo $?)" "0"
check "--version does not claim CPython" \
    "$("$OUT" --version)" "lypning-mp 0.1 (python subset)"
check "-V is the same" \
    "$("$OUT" -V)" "lypning-mp 0.1 (python subset)"
check "sys.argv[0] is -c, as in CPython" \
    "$("$OUT" -c 'import sys; print(sys.argv)' a b)" "['-c', 'a', 'b']"
check "program on stdin, argv[0] is -" \
    "$(echo 'import sys; print(sys.argv)' | "$OUT" - x)" "['-', 'x']"
check "sys.path is pinned to ['.frozen']" \
    "$("$OUT" -c 'import sys; print(sys.path)')" "['.frozen']"

# The unsupported contract (docs/SUBSET.md §7). Rule 4 — the distinction
# between "lypning-mp is too small" and "your dependency is missing" — is the one
# that makes exit 90 branchable, so it is checked in both directions.
check "unsupported module exits 90" \
    "$("$OUT" -c 'import subprocess' >/dev/null 2>&1; echo $?)" "90"
check "unsupported module: one stderr line" \
    "$("$OUT" -c 'import subprocess' 2>&1 >/dev/null)" "lypning-mp: unsupported: module: subprocess"
check "unsupported module: stdout untouched" \
    "$("$OUT" -c 'import subprocess' 2>/dev/null | wc -c | tr -d ' ')" "0"
check "a module CPython lacks too still exits 1" \
    "$("$OUT" -c 'import PIL' >/dev/null 2>&1; echo $?)" "1"

# The SHIM half of the same contract, and it was broken from the day the shims
# were written until 2026-08-14: `lypning_exit_not_implemented()` is reached from
# py/runtime.c's mp_raise_NotImplementedError(), which is C — so a shim raising
# NotImplementedError from PYTHON produced exit 1 and a traceback instead of the
# contractual 90 and one line. A caller branching on 90 never fired for ANY
# shim-level gap. All three execution paths are pinned because they do not share
# a handler: -c and a script file leave through shared/runtime/pyexec.c, a
# program on STDIN leaves through main.c's handle_uncaught_exception().
#
# The PROBE has to be a gap that is still open, and it moves as coverage lands:
# it was `re.VERBOSE` until VERBOSE was implemented, and `csv(quoting=)` until
# quoting was. That is the probe going stale, not the contract changing — when
# these fail with `got 0, want 90`, check whether the feature was just built
# before touching anything else, and move the probe to a gap listed in
# micropython/lib/README.md rather than reopening the one that closed.
check "shim unsupported exits 90 (-c)" \
    "$("$OUT" -c 'import re; re.findall(r"\d+", "a1", re.LOCALE)' >/dev/null 2>&1; echo $?)" "90"
check "shim unsupported: one stderr line" \
    "$("$OUT" -c 'import re; re.findall(r"\d+", "a1", re.LOCALE)' 2>&1 >/dev/null)" \
    "lypning-mp: unsupported: argument: re(LOCALE)"
check "shim unsupported: stdout untouched" \
    "$("$OUT" -c 'import re; re.findall(r"\d+", "a1", re.LOCALE)' 2>/dev/null | wc -c | tr -d ' ')" "0"
check "shim unsupported exits 90 (file)" \
    "$(printf 'import re\nre.findall(r"\\d+", "a1", re.LOCALE)\n' > "$WORK/u.py"; "$OUT" "$WORK/u.py" >/dev/null 2>&1; echo $?)" "90"
check "shim unsupported exits 90 (stdin)" \
    "$(printf 'import re\nre.findall(r"\\d+", "a1", re.LOCALE)\n' | "$OUT" - >/dev/null 2>&1; echo $?)" "90"
check "csv silently-swallowed kwarg exits 90" \
    "$("$OUT" -c 'import csv, io; csv.reader(io.StringIO("a\n"), restkey="x")' >/dev/null 2>&1; echo $?)" "90"

# Syntax CPython accepts and this parser cannot read is the same contract seen
# from the other side, and it is the LARGEST class of silent divergence there
# was: `match`, `{**d}`, `except*` and a positional-only `/` all exited 1 with
# "SyntaxError: invalid syntax", which tells an agent its own correct program is
# broken. All three execution paths are pinned for the same reason as above —
# the source reaches the scanner differently in each (a string for -c, a path
# for a file, a buffered read for stdin), so one working proves nothing about
# the others. micropython/variant/lypning_compat.h; the scanner's own battery is
# tests/test_syntax_scan.py.
check "missing syntax exits 90 (-c)" \
    "$("$OUT" -c 'match x:
    case 1: pass' >/dev/null 2>&1; echo $?)" "90"
check "missing syntax: one stderr line" \
    "$("$OUT" -c 'print({**a, "b": 1})' 2>&1 >/dev/null)" \
    "lypning-mp: unsupported: syntax: dict unpacking in a literal ({**d})"
check "missing syntax exits 90 (file)" \
    "$(printf 'x = 1\nmatch x:\n    case 1: pass\n' > "$WORK/s.py"; "$OUT" "$WORK/s.py" >/dev/null 2>&1; echo $?)" "90"
check "missing syntax exits 90 (stdin)" \
    "$(printf 'x = 1\nmatch x:\n    case 1: pass\n' | "$OUT" - >/dev/null 2>&1; echo $?)" "90"
# The other direction: a program that is genuinely malformed is not lypning-mp being
# too small, and must keep CPython's exit 1.
check "a real syntax error still exits 1" \
    "$("$OUT" -c 'print(1' >/dev/null 2>&1; echo $?)" "1"

# An uncaught MemoryError is the GC heap running out, which is never the
# program's answer — it was the single MISMATCH in a 472-entry conformance run.
check "heap exhaustion exits 90" \
    "$("$OUT" -c 'x = "a" * (200 * 1024 * 1024)' >/dev/null 2>&1; echo $?)" "90"

# A C function handed a keyword it does not take, named rather than generic.
check "builtin keyword exits 90, naming it" \
    "$("$OUT" -c 'print(list(zip([1], [2], strict=True)))' 2>&1 >/dev/null)" \
    "lypning-mp: unsupported: argument: keyword strict"

# str() on a subclass of a native type must reach the subclass's __repr__, the
# way CPython's object.__str__ does. It did not, and print(defaultdict) and
# print(Counter) answered with a plain dict at exit 0 — a silent wrong answer,
# which docs/SUBSET.md calls the one unacceptable outcome.
check "print() reaches a subclass __repr__" \
    "$("$OUT" -c 'import collections
d = collections.defaultdict(int)
d["x"] += 1
print(d)' 2>/dev/null)" "defaultdict(<class 'int'>, {'x': 1})"
check "print() on a str subclass keeps str semantics" \
    "$("$OUT" -c 'class S(str):
    def __repr__(self): return "S!"
print(S("x"))' 2>/dev/null)" "x"
# The other direction: hijacking a program's OWN NotImplementedError would be a
# worse bug than the one being fixed.
check "a program's own NotImplementedError still exits 1" \
    "$("$OUT" -c 'raise NotImplementedError("mine")' >/dev/null 2>&1; echo $?)" "1"

# mpy-cross -O3 drops line numbers from the FROZEN stdlib only. The user's own
# script is still compiled at runtime at optimise level 0, so if either of these
# breaks, -O3 has started reaching code it must not touch.
check "user code keeps its line numbers" \
    "$(printf 'x = 1\ny = 2\nraise ValueError("b")\n' > "$WORK/l.py"; "$OUT" "$WORK/l.py" 2>&1 >/dev/null | grep -c 'line 3')" "1"
check "assert still fires in user code" \
    "$("$OUT" -c 'assert 1 == 2' >/dev/null 2>&1; echo $?)" "1"
check "__debug__ is True in user code" \
    "$("$OUT" -c 'print(__debug__)')" "True"

# The stdout/stderr split. A traceback on stdout means a failing
# `lypning-mp ... | wc -l` counts the traceback and `lypning-mp ... > f` writes the
# error into the file — a pipeline silently ingesting garbage, which is exactly
# the silent divergence docs/MICROPYTHON.md exists to prevent.
check "a failing program writes nothing to stdout" \
    "$("$OUT" -c 'raise ValueError("boom")' 2>/dev/null | wc -c | tr -d ' ')" "0"
check "a failing program writes to stderr" \
    "$("$OUT" -c 'raise ValueError("boom")' 2>&1 >/dev/null | grep -c 'ValueError')" "1"
check "a failing program exits 1, not 90" \
    "$("$OUT" -c 'raise ValueError("boom")' >/dev/null 2>&1; echo $?)" "1"

# Semantics §6 makes contractual, and which a config change could silently undo.
check "float repr is shortest round-trip" \
    "$("$OUT" -c 'print(0.1 + 0.2, 9.7, 100.0 / 4, 1e22)')" "0.30000000000000004 9.7 25.0 1e+22"
check "round() rounds the binary value" \
    "$("$OUT" -c 'print(round(2.675, 2), round(2.5), round(1.5))')" "2.67 2 2"
check "int() arbitrary precision" \
    "$("$OUT" -c 'print(2 ** 100)')" "1267650600228229401496703205376"
check "floor division floors toward -inf" \
    "$("$OUT" -c 'print(-7 // 2)')" "-4"
check "slicing with a step, both directions" \
    "$("$OUT" -c 'print("abcdefgh"[::2], "abcdefgh"[::-1], (1, 2, 3)[::-1], [1, 2, 3, 4][::-2])')" \
    "aceg hgfedcba (3, 2, 1) [4, 2]"

# CLAUDE.md invariant 6: equal Swedish and English support in every
# deterministic gate, with a parity check in the same change. Both of these
# were real, and both were SILENT — a plausible answer with a zero exit code.
# re1.5 matches bytes, so an ASCII-only \w shredded `räksmörgås` into
# ['r', 'ksm', 'rg', 's'], and repr() escaped every non-ASCII character, so
# any Swedish string inside a list or dict printed as \xNN.
check "Swedish: \\w keeps a word whole" \
    "$("$OUT" -c 'import re; print(re.findall(r"\w+", "åäö abc räksmörgås"))')" \
    "['åäö', 'abc', 'räksmörgås']"
check "Swedish: \\W does not split inside a word" \
    "$("$OUT" -c 'import re; print(re.sub(r"\W+", "-", "Kalle Anka på Öland!"))')" \
    "Kalle-Anka-på-Öland-"
check "Swedish: repr inside a container" \
    "$("$OUT" -c 'print(["Ärlig", "Öl"], {"å": "ö"})')" \
    "['Ärlig', 'Öl'] {'å': 'ö'}"
check "Swedish: len and slicing are by character" \
    "$("$OUT" -c 's = "räksmörgås"; print(len(s), len(s.encode()), s[:3])')" \
    "10 13 räk"

BYTES="$(stat -c %s "$OUT")"
[ "$fail" = 0 ] || die "smoke checks failed"

say "Built $OUT — $BYTES bytes"
echo "    file opens on -c 'pass':  lypning gate $OUT --compare"
echo "    conformance vs CPython:   LYPNING_MP_BIN=$OUT lypning conformance"

if [ "$DO_VERIFY" = 1 ]; then
    say "Gate"
    lypning gate "$OUT" --compare
    say "Conformance"
    LYPNING_MP_BIN="$OUT" lypning conformance
fi
