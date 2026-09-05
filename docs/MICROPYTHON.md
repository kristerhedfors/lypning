# lypning-mp — the oracle

*`lypning-mp` is the oracle — measured, never routed to: a second, independent
reimplementation of Python (a MicroPython unix-port variant) with every
disagreement against CPython recorded; it left the chain on 2026-09-04 (#38).*

What the oracle may not do: widen a capability table (invariant 1), stand in
for the CPython reference, or gate a build. It is absent on almost every
machine, so a missing oracle is a hole in a report and never a zero.

## 1. What it is, and why CPython loses by shape

It is deliberately **not** a Python implementation. It is the smallest thing
that executes the observed corpus. Everything it does not cover, it must fail on
loudly and predictably (§4). Its value is the catalogue:
`.github/known-mismatches.json` records each disagreement by identity, in
families, and `lypning oracle` renders it without the binary (`lypning oracle ·
2026-09-04 · 437056c`: 79 divergences in 34 families); every family is something
a larger Rust variant must implement exactly or refuse. A subset runtime that
silently disagrees with Python is worse than no runtime, because the agent will
not notice.

Its cost model is the Rust spectrum's: in the target sandbox the root filesystem
is streamed block by block, so a cold run pays for the binary's own bytes, every
shared object it links and every path it opens — and for nothing else. CheerpX
streams the disk image in **128 KiB device blocks** (`gate.DEVICE_BLOCK`). Cold
cost is therefore not linear in bytes, it is a **step function**: any saving
that does not cross a 131,072 B boundary streams exactly the same number of
blocks as before. CPython loses there by shape, not speed — its stdlib is files,
fetched over a network — and the 30 s exec ceiling (`DEFAULT_EXEC_TIMEOUT_MS`,
`docs/SANDBOX-PERFORMANCE.md`) that destroys the VM is the one absolute.

## 2. The budget

`lypning gate <bin>` measures the three predictors of cold cost, in seconds, and
never accepts on its own numbers: acceptance is a cold run in a real VM.

| check | budget | code home |
|---|---|---|
| stripped static bytes | 700,000 B (`gate.MAX_BYTES`), only when `lypning-mp` is the binary named | `gate._size_check` |
| paths opened on `-c 'pass'` | 3 (`gate.MAX_OPENS`) | `gate.file_opens`, where `strace` runs |
| shared objects | 0 (`gate.MAX_SHARED_OBJECTS`); `file(1)` must say `statically linked` | `gate._needed`, `gate.is_static` |

Measured upstream on 2026-08-14; not reproducible from this tree: CPython's `-c
'pass'` opens 22 files, probes 7 more that miss and makes 65 stat calls — what
`gate --compare` puts beside the oracle's zeros. **The stripped file size is
quantised to the 4,096 B page.** `size -A` is the fine-grained truth; the file
size is what the gate measures because it is what the sandbox streams. A Rust
variant is gated in device blocks instead (`gate.VARIANT_BLOCK_BUDGET`,
`docs/VERIFICATION.md` §C6).

## 3. Building it

```bash
lypning build --micropython; echo $?     # a 32-bit musl toolchain and a network
lypning status | sed -n '/^oracles/,/^$/p'
```

`build-micropython.sh` fetches musl by pinned digest (`MUSL_SHA256`), builds
the i386 static variant with the frozen shims under
`src/lypning/assets/micropython/` into `paths.build_dir()/micropython/build/`
(`LYPNING_MP_BIN` pins another path), and runs its smoke checks first: `import
subprocess` exits 90 with exactly `lypning-mp: unsupported: module: subprocess`
on stderr and nothing on stdout; `import PIL` exits 1; a shim refusal exits 90
from `-c`, a file and stdin alike; `raise NotImplementedError("mine")` exits 1.

## 4. The C-side refusal contract

The marker is the literal `lypning-mp: unsupported: ` in
`lypning_unsupported.h:lypning_exit_unsupported`; the C kinds are `argument`,
`attribute`, `builtin`, `memory`, `module`, `syntax`. A frozen shim refuses by
raising `NotImplementedError` with that prefix, which the port patch turns into
the same exit 90. Two facts are pinned by the smoke checks (§3):

- **It needs two call sites, and that was found by deleting one.** `-c` and a
  script file surface their exception through `shared/runtime/pyexec.c`; a
  program piped on **stdin** comes out through `main.c`'s
  `handle_uncaught_exception()`. Removing either silently returns that path to
  exit 1.
- **A program's own `NotImplementedError` must not be hijacked.** Only the marker
  prefix triggers the 90; anything else keeps its traceback and exit 1. That is
  pinned too, because getting it wrong would be a worse bug than the one fixed.

## 5. Measuring it

`lypning conformance --engine lypning-mp` grades the arm
(`conformance.OPT_IN_ARMS`; a seeded `random` stream is compared on every arm
but this one, `conformance.is_seeded_stream`). A new divergence is never fixed
by a table: the CI job `micropython-conformance` admits it to the catalogue by
identity (`.github/scripts/known-mismatches.py`), and an entry that stops
reproducing reddens that job too. `lypning bench --micropython` measures it
against a stock MicroPython control. `lypning oracle --json` prints `engine`,
`built`, `ledger`, `divergences`, `families[].{family,programs,why}`.

## 6. Absent by default

Every path degrades to `not built` and carries on: `lypning status` prints it
under `oracles (measured, never routed to)` (`engines.ORACLES`), `doctor` a
WARN row, `conformance --engine lypning-mp` a `note:` line, `bench` no row at
all, `gate` with no binary named gates `lypning` and says so; never a zero. Test
it by moving the binary aside. **Verify:** `docs/VERIFICATION.md` §C12, which
holds `lypning oracle` rendering without the binary, exit 0.
