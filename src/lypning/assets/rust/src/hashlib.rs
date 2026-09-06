//! `hashlib` — `md5`, `sha1`, `sha256`, `sha512`: the `cap-hashlib` capability.
//!
//! Hashing is the rare capability with **no numeric trap**. MD5, SHA-1 and the
//! SHA-2 family are bit-exact specifications: there is no rounding to disagree
//! about, no locale, no platform word size, no CPython version that answers
//! differently. Every digest here is checked against the published test vectors
//! AND against the reference interpreter in `tests/test_hashlib_grid.py`. What
//! is left to get wrong is **surface** — which names exist, what they accept,
//! what the object they return does when a program treats it as something else
//! — and that is what this file is mostly about.
//!
//! # The object is an `Iter`, not a `Value`
//!
//! A hash object has identity and mutable state, which in this engine means a
//! `Value` variant — and a new `Value` variant is the defect five capabilities
//! in a row shipped (`docs/HILLCLIMB.md` iterations 74, 76, 77): it reaches
//! `eq`, `hash`, `repr`, `bool`, `len`, `in`, indexing, slice assignment,
//! `del`, augmented assignment and `json.dumps` through arms nothing forces you
//! to remember, and the ones that were forgotten answered WRONGLY at exit 0.
//!
//! So there is no new variant. A hash object is
//! [`Value::IterObj`]`(Iter::Hash(..), "_hashlib.HASH")` — the shape
//! `csv.reader` and `re.finditer` already return — and every one of those arms
//! is already correct for it, by accident of what an iterator object is:
//!
//! | arm | what it already does | what CPython does |
//! |---|---|---|
//! | `type_name` | the `&'static str` tag: `_hashlib.HASH` | `_hashlib.HASH` |
//! | `repr` / `str` | refuses: the repr holds a heap address | `<sha256 _hashlib.HASH object @ 0x…>` |
//! | `type()` | refuses, same reason | `<class '_hashlib.HASH'>` |
//! | `eq` / `is` | `Rc::ptr_eq` — identity | identity |
//! | `hash` (dict/set key) | refuses: CPython hashes by identity | hashes by identity |
//! | `bool` | `true` | `True` (no `__bool__`, no `__len__`) |
//! | `+`, `[i]`, `del x[i]`, `x[i] = …` | `TypeError` naming `type_name` | the same `TypeError` |
//! | `json.dumps` | `TypeError: … is not JSON serializable` | the same |
//!
//! The three arms that are NOT already right are the three an iterator has and
//! a hash object does not — iteration, `in`, and attribute access — and each is
//! answered explicitly: [`Iter::Hash`](crate::iter::Iter) yields CPython's own
//! `TypeError` instead of a value, `ops::contains` sends a hash object to the
//! same error instead of consuming it, and [`attr`] answers all SEVEN names
//! CPython gives this type — which is what lets an unknown one be CPython's own
//! `AttributeError` rather than a refusal, unlike every sibling capability.
//!
//! # What is refused, and where it is decided
//!
//! Everything a static walk can see is refused in `route.rs`, before the
//! interpreter exists, because a refusal reached after `os.mkdir` has committed
//! the barrier is exit 1 with the output discarded and no answer (issue #51):
//!
//!   * every module attribute outside [`SERVED`] — `new`, `algorithms_guaranteed`,
//!     `blake2b`, `shake_128`, `sha3_256`, `sha224`, `sha384`, `pbkdf2_hmac`,
//!     `file_digest` — through `route::MODULE_ATTRS`, in the CORE's walk, so the
//!     program never enters `lypning-l` at all;
//!   * every keyword argument (`usedforsecurity=`, `data=`) and every extra
//!     positional, through `route::hash_call_block`, in `lypning-l`'s walk and
//!     in the static check `main.rs` runs before `Interp::new()`.
//!
//! Three shapes stay RUNTIME refusals, because no walk can see them, and they
//! are named here rather than left to be discovered: a keyword spliced from a
//! computed `**kwargs`; a DUNDER on a hash object, which refuses by the
//! argument `ops::get_attr` makes once for every dunder in the engine; and the
//! three arms a hash object shares with an iterator and cannot be pulled out of
//! — `repr()`, `len()` and use as a dict key, whose refusals are `repr`,
//! `iterator-type-name` and `iterator-identity` and belong to the shape rather
//! than to this capability. All are exit 90 with nothing on stdout, and all are
//! exit 1 if a side effect already committed — the barrier class, not this
//! capability's to fix. Ordinary attributes are NOT in that residue:
//! [`HASH_ATTRS`] is complete, so an unknown one is an `AttributeError` that
//! agrees with CPython exactly, at the same exit code, with the program's own
//! stdout intact.
//!
//! `hashlib.new(name)` is absent on purpose even though the corpus contains one
//! call, because that call is `hashlib.new('bogus')`: CPython answers it with a
//! two-frame chained traceback out of `hashlib.py` naming an OpenSSL error, and
//! the only way to be exactly right about that is to let CPython print it.
//! Absent from [`SERVED`], it is a static `module-attr` block in every variant.

use crate::args::Args;
use crate::err::{attr_err, type_err, unsupported, LypningError, R};
use crate::eval::Interp;
use crate::iter::Iter;
use crate::value::{ival, Value};
use std::cell::RefCell;
use std::rc::Rc;

/// The `hashlib` names this capability serves. `route::MODULE_ATTRS` carries
/// the same list for the CORE, which has to block `hashlib.sha3_256` out of its
/// own walk without this file compiled in; [`the_route_table_names_exactly_what_is_served`]
/// holds the two together.
///
/// Sorted, because the route table is and a test compares them as written.
pub const SERVED: &[&str] = &["md5", "sha1", "sha256", "sha512"];

/// The type name CPython prints for every object in [`SERVED`] — they are all
/// one type, `_hashlib.HASH`, and `.name` is what tells them apart.
pub const HASH_TYPE: &str = "_hashlib.HASH";

/// Every non-dunder attribute a `_hashlib.HASH` has — CPython's whole list, and
/// this capability serves all seven.
///
/// That completeness is what lets [`attr`] answer an unknown name with
/// CPython's own `AttributeError` instead of a refusal, which the sibling
/// capabilities cannot do: a `Path`, a `Counter` and a `csv.reader` each have
/// attributes CPython answers and this engine does not, so for them an
/// AttributeError would be exit 1 where CPython prints a value. A hash object
/// has none left over, so "unknown attribute" here is a fact about the program
/// rather than a gap in the engine — and answering it exactly keeps the
/// program's own stdout, which a refusal past the commit barrier discards.
pub const HASH_ATTRS: &[&str] =
    &["block_size", "copy", "digest", "digest_size", "hexdigest", "name", "update"];

/// The names in [`HASH_ATTRS`] the ROUTER is not optimistic about, and so the
/// names `route::CAP_METHODS`'s `hashlib` row does not carry.
///
/// `route::cap_method` asks its question by NAME and cannot see the receiver,
/// so a name in that row admits it on ANY object for a program that imports
/// `hashlib`. `.name` is an ordinary attribute on a file object, a module and
/// an exception, this engine answers it on none of them, and claiming it would
/// route `import hashlib; print(open(p).name)` into this variant to stop at an
/// `AttributeError` — exit 1, the program's own, which the chain never retries,
/// where CPython prints a value. `block_size` is withheld for the weaker
/// reason: `route::hash_method` never admitted it either, and a router that
/// claims more than the variant's own walk admits is two tables disagreeing.
///
/// Both are still ANSWERED by [`attr`] for a program that gets here. What they
/// cost is a CPython spawn for `h.name` and `h.block_size`, which is coverage
/// given away in the safe direction — `tests/test_method_tables.py` names the
/// two directions and which one is a wrong exit code.
pub const ROUTER_WITHHELD: &[&str] = &["block_size", "name"];

/// Is `name` one of the attributes the ROUTER may assume this variant answers?
///
/// The names are `route::CAP_METHODS` and not a table here, for the reason
/// `glob::SERVED` is `route::GLOB_SERVED`: the binary that ROUTES is the core,
/// which has no `cap-hashlib` and therefore neither [`HASH_ATTRS`] nor
/// [`attr`]'s arms — and two tables that must agree are one table.
/// `tests/test_method_tables.py` holds the routing row to [`HASH_ATTRS`] minus
/// the probe-type names minus [`ROUTER_WITHHELD`], because there is no `cargo
/// test` in CI.
pub fn known_method(name: &str) -> bool {
    crate::route::cap_serves("hashlib", name)
}

pub fn refuse(what: &str) -> LypningError {
    unsupported("hashlib", what)
}

/// CPython's own TypeError for a `str` where bytes were required — the same
/// message from `hashlib.sha256("x")` and from `h.update("x")`.
fn not_bytes(v: &Value) -> LypningError {
    match v {
        Value::Str(_) => type_err("Strings must be encoded before hashing"),
        _ => type_err("object supporting the buffer API required"),
    }
}

// ---- the algorithms --------------------------------------------------------

const MD5: u8 = 0;
const SHA1: u8 = 1;
const SHA256: u8 = 2;
const SHA512: u8 = 3;

/// Digest length in bytes, by algorithm index — `h.digest_size`.
const DIGEST_SIZE: [usize; 4] = [16, 20, 32, 64];
/// Compression block length in bytes, by algorithm index — also `h.block_size`.
const BLOCK: [usize; 4] = [64, 64, 64, 128];

const MD5_IV: [u32; 4] = [0x67452301, 0xefcdab89, 0x98badcfe, 0x10325476];
const SHA1_IV: [u32; 5] = [0x67452301, 0xefcdab89, 0x98badcfe, 0x10325476, 0xc3d2e1f0];
const SHA256_IV: [u32; 8] = [
    0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19,
];
const SHA512_IV: [u64; 8] = [
    0x6a09e667f3bcc908,
    0xbb67ae8584caa73b,
    0x3c6ef372fe94f82b,
    0xa54ff53a5f1d36f1,
    0x510e527fade682d1,
    0x9b05688c2b3e6c1f,
    0x1f83d9abfb41bd6b,
    0x5be0cd19137e2179,
];

/// `floor(abs(sin(i + 1)) * 2**32)`, the table RFC 1321 spells out. Written
/// rather than computed: the alternative needs floating point in a const
/// context, and 256 bytes of `__const` is cheaper than the code that would
/// avoid them.
const MD5_K: [u32; 64] = [
    0xd76aa478, 0xe8c7b756, 0x242070db, 0xc1bdceee, 0xf57c0faf, 0x4787c62a, 0xa8304613, 0xfd469501,
    0x698098d8, 0x8b44f7af, 0xffff5bb1, 0x895cd7be, 0x6b901122, 0xfd987193, 0xa679438e, 0x49b40821,
    0xf61e2562, 0xc040b340, 0x265e5a51, 0xe9b6c7aa, 0xd62f105d, 0x02441453, 0xd8a1e681, 0xe7d3fbc8,
    0x21e1cde6, 0xc33707d6, 0xf4d50d87, 0x455a14ed, 0xa9e3e905, 0xfcefa3f8, 0x676f02d9, 0x8d2a4c8a,
    0xfffa3942, 0x8771f681, 0x6d9d6122, 0xfde5380c, 0xa4beea44, 0x4bdecfa9, 0xf6bb4b60, 0xbebfbc70,
    0x289b7ec6, 0xeaa127fa, 0xd4ef3085, 0x04881d05, 0xd9d4d039, 0xe6db99e5, 0x1fa27cf8, 0xc4ac5665,
    0xf4292244, 0x432aff97, 0xab9423a7, 0xfc93a039, 0x655b59c3, 0x8f0ccc92, 0xffeff47d, 0x85845dd1,
    0x6fa87e4f, 0xfe2ce6e0, 0xa3014314, 0x4e0811a1, 0xf7537e82, 0xbd3af235, 0x2ad7d2bb, 0xeb86d391,
];
const MD5_S: [u8; 64] = [
    7, 12, 17, 22, 7, 12, 17, 22, 7, 12, 17, 22, 7, 12, 17, 22, 5, 9, 14, 20, 5, 9, 14, 20, 5, 9,
    14, 20, 5, 9, 14, 20, 4, 11, 16, 23, 4, 11, 16, 23, 4, 11, 16, 23, 4, 11, 16, 23, 6, 10, 15,
    21, 6, 10, 15, 21, 6, 10, 15, 21, 6, 10, 15, 21,
];

const SHA256_K: [u32; 64] = [
    0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
    0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
    0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
    0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
    0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
    0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
    0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
    0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
];

const SHA512_K: [u64; 80] = [
    0x428a2f98d728ae22, 0x7137449123ef65cd, 0xb5c0fbcfec4d3b2f, 0xe9b5dba58189dbbc,
    0x3956c25bf348b538, 0x59f111f1b605d019, 0x923f82a4af194f9b, 0xab1c5ed5da6d8118,
    0xd807aa98a3030242, 0x12835b0145706fbe, 0x243185be4ee4b28c, 0x550c7dc3d5ffb4e2,
    0x72be5d74f27b896f, 0x80deb1fe3b1696b1, 0x9bdc06a725c71235, 0xc19bf174cf692694,
    0xe49b69c19ef14ad2, 0xefbe4786384f25e3, 0x0fc19dc68b8cd5b5, 0x240ca1cc77ac9c65,
    0x2de92c6f592b0275, 0x4a7484aa6ea6e483, 0x5cb0a9dcbd41fbd4, 0x76f988da831153b5,
    0x983e5152ee66dfab, 0xa831c66d2db43210, 0xb00327c898fb213f, 0xbf597fc7beef0ee4,
    0xc6e00bf33da88fc2, 0xd5a79147930aa725, 0x06ca6351e003826f, 0x142929670a0e6e70,
    0x27b70a8546d22ffc, 0x2e1b21385c26c926, 0x4d2c6dfc5ac42aed, 0x53380d139d95b3df,
    0x650a73548baf63de, 0x766a0abb3c77b2a8, 0x81c2c92e47edaee6, 0x92722c851482353b,
    0xa2bfe8a14cf10364, 0xa81a664bbc423001, 0xc24b8b70d0f89791, 0xc76c51a30654be30,
    0xd192e819d6ef5218, 0xd69906245565a910, 0xf40e35855771202a, 0x106aa07032bbd1b8,
    0x19a4c116b8d2d0c8, 0x1e376c085141ab53, 0x2748774cdf8eeb99, 0x34b0bcb5e19b48a8,
    0x391c0cb3c5c95a63, 0x4ed8aa4ae3418acb, 0x5b9cca4f7763e373, 0x682e6ff3d6b2b8a3,
    0x748f82ee5defb2fc, 0x78a5636f43172f60, 0x84c87814a1f0ab72, 0x8cc702081a6439ec,
    0x90befffa23631e28, 0xa4506cebde82bde9, 0xbef9a3f7b2c67915, 0xc67178f2e372532b,
    0xca273eceea26619c, 0xd186b8c721c0c207, 0xeada7dd6cde0eb1e, 0xf57d4f7fee6ed178,
    0x06f067aa72176fba, 0x0a637dc5a2c898a6, 0x113f9804bef90dae, 0x1b710b35131c471b,
    0x28db77f523047d84, 0x32caab7b40c72493, 0x3c9ebe0a15c9bebc, 0x431d67c49c100d4c,
    0x4cc5d4becb3e42b6, 0x597f299cfc657e2a, 0x5fcb6fab3ad6faec, 0x6c44198c4a475817,
];

/// One hash in progress: the chaining state, the tail of the message that has
/// not filled a block yet, and the total length the padding needs.
///
/// The 32-bit algorithms keep their words in the low half of `w`, which is one
/// array instead of two and costs a cast per round. `Clone` is what `digest()`
/// is built on: padding is destructive, so a digest is taken from a copy and
/// the original keeps accepting `update()` exactly as CPython's does.
#[derive(Clone)]
pub struct Hasher {
    alg: u8,
    w: [u64; 8],
    buf: [u8; 128],
    /// Bytes of `buf` that are filled — always less than the block size
    /// between calls, because [`Hasher::absorb`] compresses as soon as it is
    /// full.
    n: usize,
    /// Total message length in bytes. Wrapping, like every implementation's:
    /// the field the padding writes is 64 bits wide.
    len: u64,
}

impl Hasher {
    fn new(alg: u8) -> Hasher {
        let mut w = [0u64; 8];
        match alg {
            MD5 => copy32(&mut w, &MD5_IV),
            SHA1 => copy32(&mut w, &SHA1_IV),
            SHA256 => copy32(&mut w, &SHA256_IV),
            _ => w.copy_from_slice(&SHA512_IV),
        }
        Hasher { alg, w, buf: [0; 128], n: 0, len: 0 }
    }

    /// Feed bytes WITHOUT counting them — the half padding needs.
    ///
    /// The early return is load-bearing and was not there in the first draft:
    /// falling through to the tail store with a PARTIALLY filled buffer
    /// overwrote it and reset `n` to the length of what was left, which is 0
    /// for the one-byte feeds the padding loop uses — so `n` oscillated 0, 1,
    /// 0, 1 and `digest()` never terminated. The unit tests below feed a
    /// message one byte at a time for exactly this reason.
    fn absorb(&mut self, mut data: &[u8]) {
        let bs = BLOCK[self.alg as usize];
        if self.n > 0 {
            let take = (bs - self.n).min(data.len());
            self.buf[self.n..self.n + take].copy_from_slice(&data[..take]);
            self.n += take;
            data = &data[take..];
            if self.n < bs {
                // `take` was limited by `data.len()`, so a buffer still short
                // of a block means the input is spent.
                return;
            }
            let b = self.buf;
            self.compress(&b[..bs]);
            self.n = 0;
        }
        while data.len() >= bs {
            let (block, rest) = data.split_at(bs);
            self.compress(block);
            data = rest;
        }
        self.buf[..data.len()].copy_from_slice(data);
        self.n = data.len();
    }

    pub fn update(&mut self, data: &[u8]) {
        self.len = self.len.wrapping_add(data.len() as u64);
        self.absorb(data);
    }

    fn compress(&mut self, block: &[u8]) {
        match self.alg {
            MD5 => md5_block(&mut self.w, block),
            SHA1 => sha1_block(&mut self.w, block),
            SHA256 => sha256_block(&mut self.w, block),
            _ => sha512_block(&mut self.w, block),
        }
    }

    /// The digest of everything fed so far, taken from a COPY: CPython's
    /// `hexdigest()` does not end the object, and `h.update(b); h.hexdigest()`
    /// twice over must give the running answer both times.
    pub fn digest(&self) -> Vec<u8> {
        let mut c = self.clone();
        let bs = BLOCK[c.alg as usize];
        let bits = c.len.wrapping_mul(8);
        // 8 length bytes on a 64-byte block, 16 on a 128-byte one (SHA-512
        // counts bits in 128, of which the top 64 are always zero here).
        let lenfield = if bs == 128 { 16 } else { 8 };
        c.absorb(&[0x80]);
        while c.n != bs - lenfield {
            c.absorb(&[0]);
        }
        let mut tail = [0u8; 16];
        if c.alg == MD5 {
            tail[..8].copy_from_slice(&bits.to_le_bytes());
        } else {
            tail[lenfield - 8..lenfield].copy_from_slice(&bits.to_be_bytes());
        }
        c.absorb(&tail[..lenfield]);
        let mut out = Vec::with_capacity(DIGEST_SIZE[c.alg as usize]);
        let words = DIGEST_SIZE[c.alg as usize] / if c.alg == SHA512 { 8 } else { 4 };
        for i in 0..words {
            match c.alg {
                MD5 => out.extend_from_slice(&(c.w[i] as u32).to_le_bytes()),
                SHA512 => out.extend_from_slice(&c.w[i].to_be_bytes()),
                _ => out.extend_from_slice(&(c.w[i] as u32).to_be_bytes()),
            }
        }
        out
    }

    pub fn hexdigest(&self) -> String {
        let mut s = String::with_capacity(DIGEST_SIZE[self.alg as usize] * 2);
        for b in self.digest() {
            s.push(char::from_digit((b >> 4) as u32, 16).unwrap_or('0'));
            s.push(char::from_digit((b & 15) as u32, 16).unwrap_or('0'));
        }
        s
    }

    pub fn name(&self) -> &'static str {
        SERVED[self.alg as usize]
    }
}

fn copy32(w: &mut [u64; 8], iv: &[u32]) {
    for (i, x) in iv.iter().enumerate() {
        w[i] = *x as u64;
    }
}

fn be32(b: &[u8], i: usize) -> u32 {
    u32::from_be_bytes([b[4 * i], b[4 * i + 1], b[4 * i + 2], b[4 * i + 3]])
}

fn md5_block(w: &mut [u64; 8], b: &[u8]) {
    let mut m = [0u32; 16];
    for (i, x) in m.iter_mut().enumerate() {
        *x = u32::from_le_bytes([b[4 * i], b[4 * i + 1], b[4 * i + 2], b[4 * i + 3]]);
    }
    let (mut a, mut bb, mut c, mut d) = (w[0] as u32, w[1] as u32, w[2] as u32, w[3] as u32);
    for i in 0..64 {
        let (f, g) = match i / 16 {
            0 => ((bb & c) | (!bb & d), i),
            1 => ((d & bb) | (!d & c), (5 * i + 1) % 16),
            2 => (bb ^ c ^ d, (3 * i + 5) % 16),
            _ => (c ^ (bb | !d), (7 * i) % 16),
        };
        let t = f
            .wrapping_add(a)
            .wrapping_add(MD5_K[i])
            .wrapping_add(m[g])
            .rotate_left(MD5_S[i] as u32);
        a = d;
        d = c;
        c = bb;
        bb = bb.wrapping_add(t);
    }
    for (i, x) in [a, bb, c, d].iter().enumerate() {
        w[i] = (w[i] as u32).wrapping_add(*x) as u64;
    }
}

fn sha1_block(w: &mut [u64; 8], b: &[u8]) {
    let mut m = [0u32; 80];
    for i in 0..16 {
        m[i] = be32(b, i);
    }
    for i in 16..80 {
        m[i] = (m[i - 3] ^ m[i - 8] ^ m[i - 14] ^ m[i - 16]).rotate_left(1);
    }
    let (mut a, mut bb, mut c, mut d, mut e) =
        (w[0] as u32, w[1] as u32, w[2] as u32, w[3] as u32, w[4] as u32);
    for (i, mi) in m.iter().enumerate() {
        let (f, k) = match i / 20 {
            0 => ((bb & c) | (!bb & d), 0x5a827999u32),
            1 => (bb ^ c ^ d, 0x6ed9eba1),
            2 => ((bb & c) | (bb & d) | (c & d), 0x8f1bbcdc),
            _ => (bb ^ c ^ d, 0xca62c1d6),
        };
        let t = a
            .rotate_left(5)
            .wrapping_add(f)
            .wrapping_add(e)
            .wrapping_add(k)
            .wrapping_add(*mi);
        e = d;
        d = c;
        c = bb.rotate_left(30);
        bb = a;
        a = t;
    }
    for (i, x) in [a, bb, c, d, e].iter().enumerate() {
        w[i] = (w[i] as u32).wrapping_add(*x) as u64;
    }
}

fn sha256_block(w: &mut [u64; 8], b: &[u8]) {
    let mut m = [0u32; 64];
    for i in 0..16 {
        m[i] = be32(b, i);
    }
    for i in 16..64 {
        let s0 = m[i - 15].rotate_right(7) ^ m[i - 15].rotate_right(18) ^ (m[i - 15] >> 3);
        let s1 = m[i - 2].rotate_right(17) ^ m[i - 2].rotate_right(19) ^ (m[i - 2] >> 10);
        m[i] = m[i - 16]
            .wrapping_add(s0)
            .wrapping_add(m[i - 7])
            .wrapping_add(s1);
    }
    let mut v = [0u32; 8];
    for (i, x) in v.iter_mut().enumerate() {
        *x = w[i] as u32;
    }
    for i in 0..64 {
        let s1 = v[4].rotate_right(6) ^ v[4].rotate_right(11) ^ v[4].rotate_right(25);
        let ch = (v[4] & v[5]) ^ (!v[4] & v[6]);
        let t1 = v[7]
            .wrapping_add(s1)
            .wrapping_add(ch)
            .wrapping_add(SHA256_K[i])
            .wrapping_add(m[i]);
        let s0 = v[0].rotate_right(2) ^ v[0].rotate_right(13) ^ v[0].rotate_right(22);
        let maj = (v[0] & v[1]) ^ (v[0] & v[2]) ^ (v[1] & v[2]);
        let t2 = s0.wrapping_add(maj);
        v[7] = v[6];
        v[6] = v[5];
        v[5] = v[4];
        v[4] = v[3].wrapping_add(t1);
        v[3] = v[2];
        v[2] = v[1];
        v[1] = v[0];
        v[0] = t1.wrapping_add(t2);
    }
    for (i, x) in v.iter().enumerate() {
        w[i] = (w[i] as u32).wrapping_add(*x) as u64;
    }
}

fn sha512_block(w: &mut [u64; 8], b: &[u8]) {
    let mut m = [0u64; 80];
    for (i, x) in m.iter_mut().take(16).enumerate() {
        let mut v = 0u64;
        for j in 0..8 {
            v = (v << 8) | b[8 * i + j] as u64;
        }
        *x = v;
    }
    for i in 16..80 {
        let s0 = m[i - 15].rotate_right(1) ^ m[i - 15].rotate_right(8) ^ (m[i - 15] >> 7);
        let s1 = m[i - 2].rotate_right(19) ^ m[i - 2].rotate_right(61) ^ (m[i - 2] >> 6);
        m[i] = m[i - 16]
            .wrapping_add(s0)
            .wrapping_add(m[i - 7])
            .wrapping_add(s1);
    }
    let mut v = *w;
    for i in 0..80 {
        let s1 = v[4].rotate_right(14) ^ v[4].rotate_right(18) ^ v[4].rotate_right(41);
        let ch = (v[4] & v[5]) ^ (!v[4] & v[6]);
        let t1 = v[7]
            .wrapping_add(s1)
            .wrapping_add(ch)
            .wrapping_add(SHA512_K[i])
            .wrapping_add(m[i]);
        let s0 = v[0].rotate_right(28) ^ v[0].rotate_right(34) ^ v[0].rotate_right(39);
        let maj = (v[0] & v[1]) ^ (v[0] & v[2]) ^ (v[1] & v[2]);
        let t2 = s0.wrapping_add(maj);
        v[7] = v[6];
        v[6] = v[5];
        v[5] = v[4];
        v[4] = v[3].wrapping_add(t1);
        v[3] = v[2];
        v[2] = v[1];
        v[1] = v[0];
        v[0] = t1.wrapping_add(t2);
    }
    for (i, x) in v.iter().enumerate() {
        w[i] = w[i].wrapping_add(*x);
    }
}

// ---- the value -------------------------------------------------------------

/// The hash object. An existing `Value` variant over a new `Iter` one — see the
/// module note for the table of arms this buys already correct.
fn hash_value(h: Hasher) -> Value {
    Value::IterObj(Rc::new(RefCell::new(Iter::Hash(Box::new(h)))), HASH_TYPE)
}

/// The `Hasher` inside a value, if it is a hash object.
pub fn as_hasher(v: &Value) -> Option<Rc<RefCell<Iter>>> {
    match v {
        Value::IterObj(i, k) if *k == HASH_TYPE => Some(i.clone()),
        _ => None,
    }
}

/// CPython's `TypeError` for the three things an iterator does and a hash
/// object does not. Raised rather than refused: CPython raises here too, at the
/// same exit code, with nothing on stdout — a refusal would cost a spawn to be
/// told the same thing.
pub fn not_iterable() -> LypningError {
    type_err(format!("'{HASH_TYPE}' object is not iterable"))
}

/// CPython's `TypeError` for `x in h`, which is worded differently from the
/// one above and is the message 3.11+ prints.
pub fn not_a_container() -> LypningError {
    type_err(format!(
        "argument of type '{HASH_TYPE}' is not a container or iterable"
    ))
}

// ---- the module surface ----------------------------------------------------

/// `hashlib.<name>` as a value. A served name is a bound module method; every
/// other name is the `module-attr` refusal `route::MODULE_ATTRS` already
/// blocked on statically, repeated here for a run that entered as `-c`.
pub fn module_attr(name: &str) -> R<Value> {
    match SERVED.iter().find(|n| **n == name) {
        Some(n) => Ok(Value::Bound(Rc::new(Value::Module("hashlib")), n)),
        None => Err(unsupported("module-attr", &format!("hashlib.{name}"))),
    }
}

/// `hashlib.md5([data])`, and its three siblings.
///
/// The keyword and extra-positional refusals below are the RUNTIME backstop for
/// `route::hash_call_block`, which decides both statically. They are reachable
/// only through a spelling no walk can read — a keyword spliced out of a
/// computed `**kwargs`.
pub fn call(_it: &mut Interp, name: &str, args: &mut Args, kw: &[(Rc<str>, Value)]) -> R<Value> {
    let Some(i) = SERVED.iter().position(|n| *n == name) else {
        return Err(unsupported("module-attr", &format!("hashlib.{name}")));
    };
    if let Some((k, _)) = kw.first() {
        return Err(refuse(&format!("hashlib.{name}({k}=…)")));
    }
    if args.len() > 1 {
        return Err(refuse(&format!("hashlib.{name}() with extra positional arguments")));
    }
    let mut h = Hasher::new(i as u8);
    match args.first() {
        None => {}
        Some(Value::Bytes(b)) => h.update(b),
        Some(v) => return Err(not_bytes(v)),
    }
    Ok(hash_value(h))
}

/// `h.<name>` — the seven in [`HASH_ATTRS`], which is the whole of CPython's
/// non-dunder surface for this type.
///
/// An unknown name is CPython's own `AttributeError` and not a refusal, which
/// is the opposite of what `pathlib`, `collections` and `csv` do here and is
/// justified by [`HASH_ATTRS`] being COMPLETE: there is no name left that
/// CPython answers and this does not, so exit 1 is the answer rather than a
/// gap — and it keeps whatever the program already printed, where a refusal
/// past the commit barrier discards it.
///
/// A DUNDER still refuses, by the argument `ops::get_attr` makes once for all
/// of them: a dunder is part of the data model, every object carries the ones
/// its type declares, and answering `AttributeError` for one is a false claim
/// about Python rather than a fact about the program.
pub fn attr(recv: &Value, cell: &Rc<RefCell<Iter>>, name: &str) -> R<Value> {
    let h = borrow(cell)?;
    Ok(match name {
        "name" => Value::Str(h.name().into()),
        "digest_size" => ival(DIGEST_SIZE[h.alg as usize] as i64),
        "block_size" => ival(BLOCK[h.alg as usize] as i64),
        "copy" | "digest" | "hexdigest" | "update" => {
            drop(h);
            Value::Bound(Rc::new(recv.clone()), interned(name))
        }
        _ if name.starts_with("__") && name.ends_with("__") => {
            return Err(refuse(&format!("{HASH_TYPE}.{name}")))
        }
        _ => {
            return Err(attr_err(format!(
                "'{HASH_TYPE}' object has no attribute '{name}'"
            )))
        }
    })
}

/// The four methods. `update()` returns `None` and mutates in place, so every
/// alias of the object sees it — which is what `Rc<RefCell<..>>` is for and what
/// a value that copied its state would have got wrong; `copy()` is the one
/// place a hash object's state is DUPLICATED, and it is exactly a `Hasher`
/// clone, which is what makes it free to serve rather than refuse.
pub fn method(
    cell: &Rc<RefCell<Iter>>,
    name: &str,
    args: &mut Args,
    kw: &[(Rc<str>, Value)],
) -> R<Value> {
    // Every message below is CPython's own, spelled the way `_hashlib` spells
    // it — `HASH.update()`, not `_hashlib.HASH.update()`, which is what the C
    // method's `__qualname__` gives. A message this engine invented would be a
    // difference an agent reads even though the exit code agrees.
    if !kw.is_empty() {
        return Err(type_err(format!("HASH.{name}() takes no keyword arguments")));
    }
    match name {
        "update" => {
            if args.len() != 1 {
                return Err(type_err(format!(
                    "HASH.update() takes exactly one argument ({} given)",
                    args.len()
                )));
            }
            let v = args.take(0);
            let Value::Bytes(b) = &v else { return Err(not_bytes(&v)) };
            borrow_mut(cell)?.update(b);
            Ok(Value::None)
        }
        "copy" | "digest" | "hexdigest" => {
            if !args.is_empty() {
                return Err(type_err(format!(
                    "HASH.{name}() takes no arguments ({} given)",
                    args.len()
                )));
            }
            let h = borrow(cell)?;
            Ok(match name {
                "digest" => Value::Bytes(Rc::new(h.digest())),
                "hexdigest" => Value::Str(h.hexdigest().into()),
                _ => hash_value(h.clone()),
            })
        }
        _ => Err(attr_err(format!(
            "'{HASH_TYPE}' object has no attribute '{name}'"
        ))),
    }
}

/// `interned` for the four method names, so `Value::Bound` can hold a
/// `&'static str` without allocating.
fn interned(name: &str) -> &'static str {
    match name {
        "copy" => "copy",
        "digest" => "digest",
        "hexdigest" => "hexdigest",
        _ => "update",
    }
}

/// The `Hasher` behind the cell. The `RefCell` can only be borrowed twice at
/// once if a hash object reached itself, which no path here allows; the error
/// is a refusal rather than a panic because a panic is exit 101 and the
/// contract has no room for one.
fn borrow(cell: &Rc<RefCell<Iter>>) -> R<std::cell::Ref<'_, Hasher>> {
    let b = cell
        .try_borrow()
        .map_err(|_| refuse("a hash object used while it was being updated"))?;
    std::cell::Ref::filter_map(b, |i| match i {
        Iter::Hash(h) => Some(&**h),
        _ => None,
    })
    .map_err(|_| refuse("a hash object that is not one"))
}

fn borrow_mut(cell: &Rc<RefCell<Iter>>) -> R<std::cell::RefMut<'_, Hasher>> {
    let b = cell
        .try_borrow_mut()
        .map_err(|_| refuse("a hash object used while it was being updated"))?;
    std::cell::RefMut::filter_map(b, |i| match i {
        Iter::Hash(h) => Some(&mut **h),
        _ => None,
    })
    .map_err(|_| refuse("a hash object that is not one"))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn hex(alg: u8, data: &[u8]) -> String {
        let mut h = Hasher::new(alg);
        h.update(data);
        h.hexdigest()
    }

    /// The published vectors, which are the whole correctness argument for the
    /// four compression functions: RFC 1321 for MD5, FIPS 180-4 for the rest.
    #[test]
    fn the_published_test_vectors() {
        assert_eq!(hex(MD5, b""), "d41d8cd98f00b204e9800998ecf8427e");
        assert_eq!(hex(MD5, b"abc"), "900150983cd24fb0d6963f7d28e17f72");
        assert_eq!(
            hex(MD5, b"12345678901234567890123456789012345678901234567890123456789012345678901234567890"),
            "57edf4a22be3c955ac49da2e2107b67a"
        );
        assert_eq!(hex(SHA1, b""), "da39a3ee5e6b4b0d3255bfef95601890afd80709");
        assert_eq!(hex(SHA1, b"abc"), "a9993e364706816aba3e25717850c26c9cd0d89d");
        assert_eq!(
            hex(SHA1, b"abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq"),
            "84983e441c3bd26ebaae4aa1f95129e5e54670f1"
        );
        assert_eq!(
            hex(SHA256, b""),
            "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        );
        assert_eq!(
            hex(SHA256, b"abc"),
            "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
        );
        assert_eq!(
            hex(SHA256, b"abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq"),
            "248d6a61d20638b8e5c026930c3e6039a33ce45964ff2167f6ecedd419db06c1"
        );
        assert_eq!(hex(SHA512, b""), "cf83e1357eefb8bdf1542850d66d8007d620e4050b5715dc83f4a921d36ce9ce47d0d13c5d85f2b0ff8318d2877eec2f63b931bd47417a81a538327af927da3e");
        assert_eq!(hex(SHA512, b"abc"), "ddaf35a193617abacc417349ae20413112e6fa4e89a97ea20a9eeee64b55d39a2192992a274fc1a836ba3c23a3feebbd454d4423643ce80e2a9ac94fa54ca49f");
        assert_eq!(hex(SHA512, b"abcdefghbcdefghicdefghijdefghijkefghijklfghijklmghijklmnhijklmnoijklmnopjklmnopqklmnopqrlmnopqrsmnopqrstnopqrstu"), "8e959b75dae313da8cf4f72814fc143f8f7779c6eb9f7fa17299aeadb6889018501d289e4900f7e4331b99dec4b5433ac7d329eeb6dd26545e96e55b874be909");
    }

    /// A message split across `update()` calls must give the digest of the
    /// concatenation, at every block boundary — the one thing the streaming
    /// buffer can get wrong and the vectors above cannot see.
    #[test]
    fn chunking_agrees_with_one_shot() {
        let msg: Vec<u8> = (0u16..600).map(|i| (i % 251) as u8).collect();
        for alg in [MD5, SHA1, SHA256, SHA512] {
            let want = hex(alg, &msg);
            for split in [0, 1, 55, 63, 64, 65, 111, 127, 128, 129, 256, 599, 600] {
                let mut h = Hasher::new(alg);
                h.update(&msg[..split]);
                h.update(&msg[split..]);
                assert_eq!(h.hexdigest(), want, "alg {alg} split {split}");
            }
            // One byte at a time, which crosses every boundary there is.
            let mut h = Hasher::new(alg);
            for b in &msg {
                h.update(&[*b]);
            }
            assert_eq!(h.hexdigest(), want, "alg {alg} one byte at a time");
        }
    }

    /// `hexdigest()` does not end the object: CPython's returns the running
    /// digest and takes more input afterwards.
    #[test]
    fn a_digest_does_not_consume_the_hasher() {
        let mut h = Hasher::new(SHA256);
        h.update(b"a");
        let first = h.hexdigest();
        assert_eq!(h.hexdigest(), first);
        h.update(b"bc");
        assert_eq!(
            h.hexdigest(),
            "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
        );
    }

    /// [`SERVED`] and `route::MODULE_ATTRS` are two tables that must agree, so
    /// a name added to one and not the other is a build failure and not a
    /// program routed to a variant that refuses it.
    #[test]
    fn the_route_table_names_exactly_what_is_served() {
        let row = crate::route::MODULE_ATTRS
            .iter()
            .find(|(m, _)| *m == "hashlib")
            .expect("route::MODULE_ATTRS has no hashlib row");
        assert_eq!(row.1, SERVED);
        let mut sorted = SERVED.to_vec();
        sorted.sort_unstable();
        assert_eq!(sorted, SERVED, "SERVED must be sorted");
        let mut attrs = HASH_ATTRS.to_vec();
        attrs.sort_unstable();
        assert_eq!(attrs, HASH_ATTRS, "HASH_ATTRS must be sorted");
    }

    /// `digest()` and `hexdigest()` are the same bytes, and `digest_size` is
    /// how many of them there are.
    #[test]
    fn digest_and_hexdigest_and_digest_size_agree() {
        for (alg, size) in [(MD5, 16), (SHA1, 20), (SHA256, 32), (SHA512, 64)] {
            let mut h = Hasher::new(alg);
            h.update(b"lypning");
            let d = h.digest();
            assert_eq!(d.len(), size);
            assert_eq!(DIGEST_SIZE[alg as usize], size);
            assert_eq!(h.hexdigest().len(), size * 2);
            assert_eq!(
                h.hexdigest(),
                d.iter().map(|b| format!("{b:02x}")).collect::<String>()
            );
        }
    }
}
