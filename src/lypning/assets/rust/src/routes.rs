//! The route ledger's second writer — the one on the path a session takes.
//!
//! `lypning/routes.py` states what this store is for and holds every reader;
//! this file writes the same file format from the dispatcher that actually
//! runs. Until it existed the ledger was appended only by `engines.dispatch`,
//! the PYTHON dispatcher, reached from `python -m lypning run` and from the
//! suite — while an installed chain execs THIS binary and takes
//! `main.rs::dispatch`. So the infrastructure built to learn from mis-routed
//! scripts had, on a real machine, never seen one: `lypning routes` said "no
//! routes learned yet" and always would.
//!
//! **What is recorded, and nothing else:** a CLEAN static route (`Route::kind`
//! empty) whose named tier then refused at RUNTIME — a refusal that FIRED, not
//! merely an exit 90, which is a number `sys.exit` may pick. That is the one
//! refusal `route.rs` provably cannot predict: `print(2**10)` and
//! `print(2**100)` share every token, every import and every construct, and one
//! of them exits 90 with `bigint`. `main.rs::dispatch` holds the condition and
//! is the only caller here.
//!
//! **The invariant this file is subordinate to: the ledger is WRITE-ONLY with
//! respect to routing.** Nothing here is read by `route::route` or by either
//! dispatcher, and the only thing this module learns about the store is its
//! SIZE — from the `fstat` on the descriptor it is already holding open to
//! write to, which decides header-or-no-header and enforces the cap. A
//! machine-local file that could move a route would make `lypning conformance`
//! a measurement of one laptop, and would give the two dispatchers a way to
//! disagree that no test could see.
//!
//! **What a write may cost: nothing.** One `mkdir`, one `open`, one `fstat`
//! and one `write(2)` of at most one page, to a descriptor opened
//! `O_WRONLY|O_APPEND|O_CREAT` — so the append lands whole and parallel
//! sessions need no lock — and only on a path that was ALREADY about to start
//! another interpreter. Nothing is written on the success path. Every error is
//! swallowed, the posture invariant 5 sets for the hooks and for the same
//! reason: the caller is in the middle of answering somebody's program, and a
//! full disk, a read-only `$LYPNING_HOME` or a store somebody chmod-ed are not
//! theirs to hear about. `LYPNING_ROUTES=0` turns it off, and so does
//! `LYPNING_CAPTURE=0`: the documented capture opt-out covers every recording
//! feed (invariant 7 — a switch may not quietly narrow), which is also what
//! keeps a conformance battery out of the store, since `engines.run` sets it
//! in every child it spawns.
//!
//! **Byte for byte the record the Python writer makes.** Line 1 is a header
//! naming the engine, its `cap-*` set and its binary's `<size>:<mtime_ns>`,
//! and `routes.load` discards the WHOLE file when that no longer describes the
//! engine as it is now; then one JSON object per refusal, keyed by
//! `blake2b(program, digest_size=6)` — EXACT program identity, never a feature
//! key, because a feature key cannot tell `print(2**10)` from `print(2**100)`
//! and those two are the entire reason the ledger exists. Both writers append
//! to one file and `routes.compact` folds their records together;
//! `tests/test_routes.py` holds the two shapes to each other.

use lypning::route;
use std::io::Write;
use std::os::unix::fs::{MetadataExt, OpenOptionsExt};

/// Header schema version — `routes.VERSION`. A bump makes every older header
/// fail to match, which is the only migration this store wants.
const VERSION: u32 = 1;

/// `routes.MAX_BYTES` — the record cap at a generous 128 bytes apiece. The
/// WRITER enforces bytes and not records because counting records is a read.
const MAX_BYTES: u64 = 4096 * 128;

/// `routes.MAX_LINE`. One page: an `O_APPEND` write of at most this lands
/// whole, so two sessions appending at once interleave as records rather than
/// shredding one.
const MAX_LINE: usize = 4096;

/// `routes.KIND_MAX` / `routes.DETAIL_MAX`, in CHARACTERS — Python slices a
/// `str`, so a byte cap here would cut a different string on non-ASCII prose.
const KIND_MAX: usize = 64;
const DETAIL_MAX: usize = 96;

// --- the switch and the paths ------------------------------------------------

/// `LYPNING_ROUTES=0` disables the writer, and so does `LYPNING_CAPTURE=0`.
fn enabled() -> bool {
    let off = |k: &str| std::env::var(k).map_or(false, |v| v.trim() == "0");
    !off("LYPNING_CAPTURE") && !off("LYPNING_ROUTES")
}

/// `$LYPNING_HOME`, else `~/.lypning` — `paths.state_dir`, spelled once for
/// this whole binary. `main.rs::engine_path_named` asks the same question when
/// it looks for a sibling variant, and two resolutions of one directory in one
/// process is the thing that drifts.
pub fn state_dir() -> Option<String> {
    if let Ok(h) = std::env::var("LYPNING_HOME") {
        let h = h.trim();
        if !h.is_empty() {
            return Some(h.to_string());
        }
    }
    std::env::var("HOME").ok().map(|h| format!("{h}/.lypning"))
}

/// `<size>:<mtime_ns>` for a binary, `""` when there is not one —
/// `routes.binary_stamp`. Not a content hash: this is the write path, and
/// hashing a megabyte to write 120 bytes would be the one expensive thing in a
/// module whose entire argument is that it is free.
fn stamp(path: &str) -> String {
    match std::fs::metadata(path) {
        Ok(m) => format!("{}:{}", m.len(), m.mtime() * 1_000_000_000 + m.mtime_nsec()),
        Err(_) => String::new(),
    }
}

// --- the digest --------------------------------------------------------------

const IV: [u64; 8] = [
    0x6a09_e667_f3bc_c908,
    0xbb67_ae85_84ca_a73b,
    0x3c6e_f372_fe94_f82b,
    0xa54f_f53a_5f1d_36f1,
    0x510e_527f_ade6_82d1,
    0x9b05_688c_2b3e_6c1f,
    0x1f83_d9ab_fb41_bd6b,
    0x5be0_cd19_137e_2179,
];

const SIGMA: [[u8; 16]; 10] = [
    [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15],
    [14, 10, 4, 8, 9, 15, 13, 6, 1, 12, 0, 2, 11, 7, 5, 3],
    [11, 8, 12, 0, 5, 2, 15, 13, 10, 14, 3, 6, 7, 1, 9, 4],
    [7, 9, 3, 1, 13, 12, 11, 14, 2, 6, 5, 10, 4, 0, 15, 8],
    [9, 0, 5, 7, 2, 4, 10, 15, 14, 1, 11, 12, 6, 8, 3, 13],
    [2, 12, 6, 10, 0, 11, 8, 3, 4, 13, 7, 5, 15, 14, 1, 9],
    [12, 5, 1, 15, 14, 13, 4, 10, 0, 7, 6, 3, 9, 2, 8, 11],
    [13, 11, 7, 14, 12, 1, 3, 9, 5, 0, 15, 4, 8, 6, 2, 10],
    [6, 15, 14, 9, 11, 3, 0, 8, 12, 2, 13, 7, 1, 4, 10, 5],
    [10, 2, 8, 4, 7, 6, 1, 5, 15, 11, 9, 14, 3, 12, 13, 0],
];

/// The digest length, in bytes. It is a BLAKE2 parameter and not a truncation:
/// it goes into the parameter block below, so a 6-byte digest is not the first
/// six bytes of a 64-byte one, and getting that wrong would produce a store
/// whose ids never fold with the Python writer's.
const OUT: usize = 6;

#[allow(clippy::too_many_arguments)]
fn mix(v: &mut [u64; 16], a: usize, b: usize, c: usize, d: usize, x: u64, y: u64) {
    v[a] = v[a].wrapping_add(v[b]).wrapping_add(x);
    v[d] = (v[d] ^ v[a]).rotate_right(32);
    v[c] = v[c].wrapping_add(v[d]);
    v[b] = (v[b] ^ v[c]).rotate_right(24);
    v[a] = v[a].wrapping_add(v[b]).wrapping_add(y);
    v[d] = (v[d] ^ v[a]).rotate_right(16);
    v[c] = v[c].wrapping_add(v[d]);
    v[b] = (v[b] ^ v[c]).rotate_right(63);
}

fn compress(h: &mut [u64; 8], block: &[u8; 128], t: u128, last: bool) {
    let mut m = [0u64; 16];
    for (i, w) in m.iter_mut().enumerate() {
        let mut b = [0u8; 8];
        b.copy_from_slice(&block[i * 8..i * 8 + 8]);
        *w = u64::from_le_bytes(b);
    }
    let mut v = [0u64; 16];
    v[..8].copy_from_slice(h);
    v[8..].copy_from_slice(&IV);
    v[12] ^= t as u64;
    v[13] ^= (t >> 64) as u64;
    if last {
        v[14] = !v[14];
    }
    for r in 0..12 {
        let s = &SIGMA[r % 10];
        let w = |i: usize| m[s[i] as usize];
        mix(&mut v, 0, 4, 8, 12, w(0), w(1));
        mix(&mut v, 1, 5, 9, 13, w(2), w(3));
        mix(&mut v, 2, 6, 10, 14, w(4), w(5));
        mix(&mut v, 3, 7, 11, 15, w(6), w(7));
        mix(&mut v, 0, 5, 10, 15, w(8), w(9));
        mix(&mut v, 1, 6, 11, 12, w(10), w(11));
        mix(&mut v, 2, 7, 8, 13, w(12), w(13));
        mix(&mut v, 3, 4, 9, 14, w(14), w(15));
    }
    for (i, x) in h.iter_mut().enumerate() {
        *x ^= v[i] ^ v[i + 8];
    }
}

/// `hashlib.blake2b(program.encode(), digest_size=6).hexdigest()` —
/// `routes.digest`, and the record's id.
///
/// Written out rather than reached for, because there is nothing to reach for:
/// invariant 6 says zero dependencies, and this is the whole of BLAKE2b that a
/// twelve-hex-character id needs. The final block is compressed even when the
/// program is empty, which is the boundary a naive loop gets wrong — and so is
/// a program of exactly 128 bytes, whose last block must be flagged final
/// rather than followed by an empty one.
pub fn digest(program: &str) -> String {
    let mut h = IV;
    h[0] ^= 0x0101_0000 ^ OUT as u64; // parameter block: no key, 6-byte digest
    let data = program.as_bytes();
    let mut t: u128 = 0;
    let mut i = 0;
    while data.len() - i > 128 {
        let mut b = [0u8; 128];
        b.copy_from_slice(&data[i..i + 128]);
        i += 128;
        t += 128;
        compress(&mut h, &b, t, false);
    }
    let mut b = [0u8; 128];
    let n = data.len() - i;
    b[..n].copy_from_slice(&data[i..]);
    t += n as u128;
    compress(&mut h, &b, t, true);
    let mut out = String::with_capacity(OUT * 2);
    for k in 0..OUT {
        let byte = h[k / 8].to_le_bytes()[k % 8];
        for nib in [byte >> 4, byte & 0xf] {
            out.push(char::from(if nib < 10 { b'0' + nib } else { b'a' + nib - 10 }));
        }
    }
    out
}

// --- the record --------------------------------------------------------------

/// `json.dumps(s, ensure_ascii=False)` for one string. Non-ASCII passes
/// through raw, which is what `ensure_ascii=False` means and what makes the
/// two writers' bytes comparable.
fn jstr(s: &str, out: &mut String) {
    out.push('"');
    for c in s.chars() {
        match c {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            '\u{8}' => out.push_str("\\b"),
            '\u{c}' => out.push_str("\\f"),
            c if (c as u32) < 0x20 => out.push_str(&format!("\\u{:04x}", c as u32)),
            c => out.push(c),
        }
    }
    out.push('"');
}

fn head(s: &str, cap: usize) -> String {
    s.chars().take(cap).collect()
}

/// `datetime.now(timezone.utc).strftime("%Y-%m-%d")`, without a calendar.
///
/// Days since the epoch, then Howard Hinnant's `civil_from_days` — exact for
/// every date, with no table and no leap-year special case to get wrong.
fn day_utc(secs: i64) -> String {
    let z = secs.div_euclid(86_400) + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z - era * 146_097; // [0, 146096]
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365; // [0, 399]
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100); // [0, 365]
    let mp = (5 * doy + 2) / 153; // [0, 11]
    let d = doy - (153 * mp + 2) / 5 + 1; // [1, 31]
    let m = if mp < 10 { mp + 3 } else { mp - 9 }; // [1, 12]
    let y = yoe + era * 400 + i64::from(m <= 2);
    format!("{y:04}-{m:02}-{d:02}")
}

fn today() -> String {
    let secs = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs() as i64)
        .unwrap_or(0);
    day_utc(secs)
}

/// `routes.record`, serialised. `n` is 1 here; only `routes.compact` folds.
fn record_line(program: &str, kind: &str, detail: &str) -> String {
    let mut s = String::from("{\"id\":");
    jstr(&digest(program), &mut s);
    s.push_str(",\"kind\":");
    jstr(&head(kind, KIND_MAX), &mut s);
    s.push_str(",\"detail\":");
    jstr(&head(detail, DETAIL_MAX), &mut s);
    s.push_str(",\"n\":1,\"t\":");
    jstr(&today(), &mut s);
    s.push_str("}\n");
    s
}

/// `routes.header`: which engine, exactly, these records were learned against.
///
/// `routes.load` discards the WHOLE file when this no longer describes the
/// engine as it is now, which is why the caps come from `route::SPECTRUM` —
/// the table every variant carries whole, and the one `engines.VARIANT_CAPS`
/// is pinned against — rather than from a second list here.
fn header_line(engine: &str, binary: &str) -> String {
    let mut s = format!("{{\"v\":{VERSION},\"engine\":");
    jstr(engine, &mut s);
    s.push_str(",\"caps\":[");
    let caps = route::SPECTRUM
        .iter()
        .find(|v| v.name == engine)
        .map(|v| v.caps)
        .unwrap_or(&[]);
    for (i, c) in caps.iter().enumerate() {
        if i > 0 {
            s.push(',');
        }
        jstr(c, &mut s);
    }
    s.push_str("],\"bin\":");
    jstr(&stamp(binary), &mut s);
    s.push_str("}\n");
    s
}

// --- the write path ----------------------------------------------------------

/// Append one record. Never panics, never blocks, never reads the store.
pub fn note(engine: &str, binary: &str, program: &str, kind: &str, detail: &str) {
    let _ = append(engine, binary, program, kind, detail);
}

fn append(engine: &str, binary: &str, program: &str, kind: &str, detail: &str) -> Option<()> {
    if !enabled() {
        return None;
    }
    let dir = format!("{}/routes", state_dir()?);
    std::fs::create_dir_all(&dir).ok()?;
    let path = format!("{dir}/{engine}.jsonl");
    let mut f = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .mode(0o600)
        .open(&path)
        .ok()?;
    let size = f.metadata().ok()?.len();
    if size >= MAX_BYTES {
        return None;
    }
    let mut line = record_line(program, kind, detail);
    if size == 0 {
        // Header and first record in ONE write, or a second session could
        // append a record between them and land it above the header it
        // belongs to.
        line.insert_str(0, &header_line(engine, binary));
    }
    if line.len() > MAX_LINE {
        return None;
    }
    f.write(line.as_bytes()).ok()?;
    Some(())
}
