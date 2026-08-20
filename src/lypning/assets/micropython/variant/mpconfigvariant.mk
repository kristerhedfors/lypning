# lypning-mp — make-level configuration of the MicroPython unix variant.
#
# The .h file next to this one owns the C feature switches; this file owns the
# things the port's Makefile decides before the compiler runs: which optional
# subsystems get compiled and linked at all, and what the binary is called.
#
# Everything switched off here is off for one of two reasons: it drags in a git
# submodule (which would make the build need more network than the two pinned
# downloads), or it drags in a shared library (which would make the binary
# dynamic, and a dynamic binary loses on the only metric that matters —
# docs/RESEARCH.md §1).

PROG = lypning-mp

# btree needs lib/berkeley-db-1.xx, a git submodule. `import btree` is not in
# the corpus and never will be.
MICROPY_PY_BTREE = 0

# ffi links libffi (submodule or system .so) and exists to call into C
# libraries — the opposite of a self-contained static binary.
MICROPY_PY_FFI = 0

# Networking and TLS: the sandbox guest has no usable network
# (docs/SUBSET.md §5) and axtls is another submodule.
MICROPY_PY_SOCKET = 0
MICROPY_PY_SSL = 0
MICROPY_SSL_AXTLS = 0

# One WASM CPU; threading buys nothing and links libpthread.
MICROPY_PY_THREAD = 0

# termios and readline serve the REPL, which is out of scope.
MICROPY_PY_TERMIOS = 0
MICROPY_USE_READLINE = 0

# The only filesystem lypning-mp sees is the guest's own, through the POSIX VFS
# (which mpconfigport.h turns on unconditionally). FAT and littlefs are block
# devices that do not exist here, and each links its own driver.
MICROPY_VFS_FAT = 0
MICROPY_VFS_LFS1 = 0
MICROPY_VFS_LFS2 = 0

# Frozen stdlib. This is the mechanism, not a nicety: a stdlib that lives as
# .py files on disk is a stdlib fetched over a WebSocket one file at a time.
FROZEN_MANIFEST ?= $(VARIANT_DIR)/manifest.py

# Freeze the shim stdlib WITHOUT bytecode line-number tables. Every frozen scope
# otherwise carries a code-info block of line deltas; there are 2,282 B of them
# across 346 scopes. At -O3, py/emitbc.c's mp_emit_bc_set_source_line() returns
# early and they are not emitted.
#
# This applies to the FROZEN stdlib only. The user's own script is still compiled
# at runtime with mp_optimise_value == 0, so its tracebacks, its line numbers,
# its asserts and its __debug__ are all untouched — which is what makes this
# cheap. The single observable cost is that a traceback frame INSIDE a frozen
# shim now reads `File "json.py", line 1` instead of the true line.
#
# THE LATENT TRAP: any -O level also deletes `assert` statements and sets
# __debug__ False in the frozen modules. micropython/lib contains zero of both today
# (grep, and independently proven by -O1 and -O2 producing byte-identical
# output), so nothing is being deleted. If a future shim adds an assert, -O3
# will silently remove it — the smoke checks in scripts/build-micropython.sh pin the
# user-code half of this so the distinction cannot rot unnoticed.
MPY_CROSS_FLAGS += -O3

# manifest.py globs this directory rather than naming modules, so the shim
# stdlib in micropython/lib can grow without any build file changing. It has to
# arrive as an environment variable: makemanifest.py's $(VAR) substitution is
# applied inside freeze(), which is too late for the manifest's own isdir check.
export LYPNING_LIB_DIR := $(abspath $(VARIANT_DIR)/lib)

# >>> SHARED TOOLCHAIN BLOCK — do not move the markers.
# Everything between this line and the closing marker is EXTRACTED VERBATIM by
# `scripts/build-micropython.sh --stock` into the control variant's makefile. That is
# how the stock-MicroPython benchmark control is kept apples-to-apples: libc,
# architecture, optimisation level and strip state cannot drift between the two
# builds, because there is only one copy of these lines in the repository.
# Editing them changes BOTH binaries. Anything below the closing marker is a
# lypning-mp config choice and is deliberately NOT shared — that is what the
# benchmark measures (docs/BENCH-LEDGER.md).
#
# The static musl link. CC/LD come in from the environment (scripts/build-micropython.sh
# points them at the musl-i386 wrapper it builds); this file only asks for the
# static link and the i386 linker emulation.
#
# -Wl,-m,elf_i386 is load-bearing and cost a build to find: the musl-gcc wrapper
# does not propagate -m32 to the linker's emulation, so without it the link
# fails with "skipping incompatible .../libc.a" (docs/RESEARCH.md §2.1).
CFLAGS += -m32
LDFLAGS += -m32 -static -Wl,-m,elf_i386

# -Os over the port's default, plus dead-section stripping (the port already
# passes --gc-sections). Size is the product here, not speed: lypning-mp's warm
# interpreter init is 0.96 ms against a 0.92 ms empty-C-program floor
# (docs/RESEARCH.md §2.7), so there is nothing left to win on speed.
COPT = -Os -DNDEBUG

# musl has no __stack_chk_fail_local, and the guard is pointless in a binary
# whose whole input is a program the caller already controls.
CFLAGS += -fno-stack-protector

# Position-INdependent code, switched off. gcc 13.3 on Ubuntu ships with
# `-fPIE [enabled]` by default (`gcc -Q --help=common` says so) and the musl-gcc
# wrapper is a thin `gcc -m32` that does not turn it off — so every translation
# unit was compiled position-independent even though the link has always produced
# a non-PIE ELF of type EXEC loaded at a fixed 0x08048000. Nothing ever used the
# position independence; it was pure overhead, and on i386 it is charged twice:
# a GOT base pointer eats a register on the most register-starved ISA in common
# use, and every const aggregate holding a pointer is demoted out of .rodata into
# a relocated section. Measured: .data.rel.ro 20,992 B -> 4 B.
#
# -no-pie at link time alone does nothing here; the saving is entirely
# compile-side, which is why both flags are set.
CFLAGS  += -fno-pie
LDFLAGS += -no-pie

# i386 codegen, five independent levers on a register-starved ISA. Leave-one-out
# .text deltas, measured: -mpreferred-stack-boundary=2 -4,480 (the default aligns
# every call frame to 16 bytes for SSE spills this build never makes — `objdump`
# finds zero movaps/movapd/movdqa in the artifact), -fomit-frame-pointer -2,320
# (frees %ebp as a general register), -falign-*=1 -992 (no NOP padding between
# functions and branch targets; alignment buys pipeline throughput that a
# WebSocket-streamed cold start does not care about), -fmerge-all-constants -448
# text and -1,280 rodata, -fno-math-errno -96.
#
# These must be passed at LINK as well as compile: -flto defers code generation
# to link time, so a codegen flag that appears only in CFLAGS is silently ignored.
#
# They compose with -fno-pie at 96.5% — the two attack different registers
# (%ebx/GOT versus %ebp/stack frame), so the pair is worth 18,940 B of sections
# against 19,628 B if the savings were fully independent. Only 688 B overlap.
LYPNING_CODEGEN := -fomit-frame-pointer -fmerge-all-constants -fno-math-errno \
                  -mpreferred-stack-boundary=2 \
                  -falign-functions=1 -falign-jumps=1 -falign-loops=1 -falign-labels=1
CFLAGS  += $(LYPNING_CODEGEN)
LDFLAGS += $(LYPNING_CODEGEN)
# <<< SHARED TOOLCHAIN BLOCK

# DWARF unwind tables, deleted. MicroPython raises through setjmp/longjmp (the
# NLR machinery), not through a DWARF unwinder; there is no C++ in the tree and
# musl needs no unwinder for a binary with threads switched off. Measured on the
# baseline: `nm` finds ZERO _Unwind/__cxa/backtrace symbols, so nothing in the
# binary could read these tables — yet .eh_frame was 51,644 B, 13% of the
# stripped artifact, because --gc-sections does not collect .eh_frame.
CFLAGS += -fno-asynchronous-unwind-tables -fno-unwind-tables

# Error strings stay as plain text in .rodata rather than being compressed.
# Two reasons, both correctness rather than taste:
#   - docs/SUBSET.md §6 makes several exception messages contractual
#     ("invalid literal for int() with base 10: 'abc'"), and
#   - the unsupported contract has to print a NotImplementedError's message from
#     inside the VM (lypning_unsupported.h), which a compressed ROM string cannot
#     be turned back into at that point.
# Measured cost: about 8 KB against a 700 KB budget.
MICROPY_ROM_TEXT_COMPRESSION = 0

# Link-time optimisation. The unix port does not enable this, and it is the one
# flag here that improves BOTH terms at once: 16,384 B off the stripped binary
# (cross-translation-unit inlining and dead-code elimination that --gc-sections
# cannot see, because it works at section granularity and not at call graph
# granularity), and 16% off a real workload (loops + re + json + dict churn:
# 518 ms -> 433 ms).
#
# LTO across setjmp/longjmp is the classic miscompile hazard, and MicroPython
# raises exceptions through exactly that (the NLR machinery), so this is NOT
# accepted on the conformance battery alone. It is pinned by a dedicated
# stress case in tests/corpus/conformance.mjs ("nlr-stress"): deep recursive
# unwinding, finally ordering, locals live across a raise, generator close,
# 2000 sequential raises reusing the NLR buffer, and the recursion limit. All
# match CPython exactly.
CFLAGS += -flto=4
LDFLAGS += -flto=4 -Os
# Segment packing. Modern GNU ld defaults to `-z separate-code`, which gives
# executable code its own page-aligned LOAD segments so that no page is both
# writable-adjacent and executable. That hardening buys nothing here — the
# binary is static, runs in a single-process WASM guest, and its whole input is
# a program the caller already wrote — and it cost 4,096 B of pure inter-segment
# padding, measured by readelf as a gap between LOAD segments rather than as any
# section growing.
#
# Worth knowing when reading the numbers: the stripped FILE size is quantised to
# the 4,096 B page, so two different changes can both report exactly -4,096 B.
# Section sizes (`size -A`) are the fine-grained truth; the file size is what the
# gate measures because it is what the sandbox actually streams.
LDFLAGS += -Wl,-z,noseparate-code

# PT_GNU_RELRO, deleted for the same reason. RELRO is applied by the DYNAMIC
# LINKER, which re-mprotects the relocated region read-only after startup — and
# this binary is static, so there is no ld.so and nobody ever applies it.
# Confirmed rather than assumed: `strace -e trace=mprotect` on a run shows ZERO
# mprotect calls. All the segment did was force page-congruent padding between
# the read-only and read-write LOADs.
#
# Like its sibling above this is PURE PADDING, so its saving is not additive —
# re-measure it after any other change rather than carrying the number forward.
LDFLAGS += -Wl,-z,norelro

# MEASURED AND REJECTED, so it is not re-tried every time someone reads this
# file and has the same idea:
#
#   - ld.lld cannot link this at all. GCC's LTO plugin emits GIMPLE bytecode
#     and lld wants LLVM bitcode, so `main` comes out undefined from Scrt1.o.
#   - ld.gold + --icf=safe (identical code folding) links, but folds 14 bytes.
#     MicroPython has almost no byte-identical functions. It also cannot take
#     -z noseparate-code, so it ends up 156 B LARGER than the default linker,
#     while adding a non-default toolchain dependency to the CI build.
#   - -Oz does not exist in GCC (it is a clang flag). -Os is already the COPT.
