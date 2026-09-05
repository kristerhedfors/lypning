# Sandbox command performance

> **Status (2026-09-05):** measured upstream on 2026-07-24 by
> `tests/e2e/sandbox-perf.spec.js` (the turn trace by its companion,
> `sandbox-agent-trace.spec.js`) against the production CheerpX sandbox of the
> project `README.md` §8 credits; not reproducible from this tree — the ratios,
> not the milliseconds, are the stable quantity. It applies to every Rust
> variant in the spectrum; `lypning-mp`, the oracle — measured, never routed to
> — is gated only when named. `lypning gate` measures the shape cost follows
> from (shared objects, bytes in 131,072 B blocks, opens at `-c 'pass'`);
> `lypning bench` times a local filesystem and cannot see cold-block cost.
> Nothing was re-run on `lypning → lypning-l → cpython`.

### 1. Cold block streaming

The root filesystem is an ext2 image streamed over a WebSocket in 131,072 B
device blocks (`gate.DEVICE_BLOCK`; CheerpX's own constant, from its
`cx_esm.js`) and cached in IndexedDB; the first run of a binary pulls its ELF
and every library it links. Cold cost is a step function in blocks: a byte over
a boundary costs a whole block's fetch.

*upstream battery, 2026-07-24 — cold against warm:*

| command | cold | warm | ratio |
|---|---|---|---|
| `python3 --version` | 8573 ms | 87 ms | 98× |
| `perl -e 'print 42'` | 8333 ms | 108 ms | 77× |
| `find /usr/share/doc -maxdepth 2` | 9751 ms | 268 ms | 36× |
| `du -sh /etc` | 5770 ms | 161 ms | 36× |
| `ls /usr/bin \| wc -l` | 1143 ms | 125 ms | 9× |
| `/usr/bin/test -f …` | 343 ms | 69 ms | 5× |

Cold cost follows which files a command touches, not how much work it does:
`python3 -c 'pass'` opens 22 files, probes 7 that miss and makes 65 stat calls
(`gate.py` docstring; `lypning gate --compare`), each crossing the network —
the local proxy for the 8573 ms row (`gate.CPYTHON_COLD_MS`).

### 2. The exec round-trip floor

Every exec is one `/bin/sh -c` on a WASM CPU in a marker-and-base64 envelope;
`true` cost 50–85 ms (2026-07-24), the floor for any command — ten round-trips
cost about ten times the same ten commands batched into one.

### 3. Process spawns

*upstream battery, 2026-07-24 — one loop body, only the spawn count varying:*

```
   0 spawns →  86 ms
  10 spawns → 145 ms
  25 spawns → 238 ms
  50 spawns → 375 ms
 100 spawns → 740 ms
 → 6.51 ms per spawn (intercept 76 ms = the round-trip floor)
```

The floor is `/bin/true`; `find -exec grep` over 200 files cost 5994 ms against
111 ms for one recursive `grep`, while 1500 builtin `[ -f … ]` tests cost
169 ms: process creation is the cost, not syscalls.

### 4. Returning output

Cost tracks bytes returned to JS, not bytes read inside the VM.

*upstream battery, 2026-07-24 — a 2 MB file, warm; then by size returned:*

| command | warm | what it does |
|---|---|---|
| `wc -c < f2048k.txt` | 60 ms | reads 2 MB, returns 8 bytes |
| `cat f2048k.txt` | 1903 ms | reads 2 MB, returns 2 MB |
| `head -c 1024 f2048k.txt` | 63 ms | reads 2 MB, returns 1 KB |

| size | warm | ms/KB |
|---|---|---|
| 1 KB | 78 ms | 78 |
| 64 KB | 136 ms | 2.1 |
| 512 KB | 451 ms | 0.88 |
| 2048 KB | 1903 ms | 0.93 |

Below 64 KB the round-trip floor dominates; above it ~0.9 ms/KB, about
1.1 MB/s. The four together are why a subset exists (`docs/LYPNING.md` §1).

## The 30 s ceiling

The host races every command against a 30 s ceiling; on timeout it returns
rc 124 and discards the VM, because nothing can abort a running guest process,
and every later command in it returns `sandbox not ready` until a re-boot with
a fresh overlay. Treat rc 124 as "the VM is gone", not "this command was slow".
An unbounded `grep -rl … /usr/share/doc` and a `command -v node` (every cold
`PATH` directory stat'ed) each took the ceiling while the battery was built.

*upstream battery, 2026-07-24 — a 2 s guest budget under a 5 s host ceiling:*

| probe | result |
|---|---|
| `sleep 2; echo done` | 2321 ms, rc 0 — time itself passes correctly |
| `timeout 2 sleep 60` | 5004 ms, rc 124 — JS ceiling fired, VM destroyed |
| `timeout -s KILL 2 sleep 60` | 5004 ms, rc 124 — SIGKILL no better |
| `timeout -s KILL 2 sh -c 'while :; do :; done'` | 5004 ms, rc 124 — CPU-bound, same |
| `sleep 60 & P=$!; kill -9 $P; wait $P` | 5005 ms, rc 124 — an explicit kill fails too |

`timeout` is present and `sleep` proves the clock works, so **signal delivery
and process termination are not functional in the CheerpX guest.** A guest-side
`timeout` wrapper costs a spawn and terminates nothing; the reset on rc 124
must stay unconditional. Prevention is the only lever.

## A turn, traced

*upstream trace spec, 2026-07-24 — a turn that wrote a file and read it back:*

```
     t(ms)   Δ(ms)  event
        0       0  ── send-click ──
       83      83  req: /api/bash/step
     1691    1608  res: /api/bash/step 200
    26333   24642  req: /api/bash/step          ← VM boot 24352 ms + commands ~290 ms
    27389    1056  res: /api/bash/step 200
    27391       2  req: /api/chat
    28148     243  sse: step_start [introspect] Reading the site's own source…
    34597    5716  sse: step_done  [source] Read 5 source files from the project
    37143    2546  sse: 452 answer deltas over 6218 ms (2124 chars)
    43892     531  sse: done (15564ms)
    44275     262  ── turn-complete ──

  round 1: step 1608 ms   exec window 24642 ms  = VM boot 24352 ms + commands ~290 ms
  round 2: step 1056 ms   (last round)
  shell loop total : 27306 ms  (LLM steps 2664 ms + in-VM 24642 ms)
```

The commands were 290 ms of a 44 s turn; the rest was the cold boot (24.4 s
with the source mount; 3.6–4.4 s bare) and the model. **Optimising command
choice matters far less than not paying a cold boot**. The upstream host's
stdout cap, `cat` short-circuit and CDN-auth notes stayed upstream.

## What enforces it here

`lypning gate` holds the shape on every built variant: `gate.MAX_SHARED_OBJECTS`
0, `gate.MAX_OPENS` 3 at `-c 'pass'`, the budgets `gate.VARIANT_BLOCK_BUDGET`
(`lypning` 8, `lypning-l` 32 blocks) and `gate.MAX_BYTES` 700,000 B only when
`lypning-mp` is the binary named; `build.CHEERPX_BLOCK` is the same 131,072 B
for the blocks column of `lypning build --rust`. A `--` row is a check nobody
took and is not a pass; any FAIL exits 1. The three regressions — a `NEEDED`
entry, opens rising, a block boundary crossed — are `docs/VERIFICATION.md` §C6.

```bash
lypning gate; echo $?             # PASS (N of M checks unmeasured) · 0 — §C6
lypning gate --compare            # CPython's -c 'pass' shape beside the variant's
```
