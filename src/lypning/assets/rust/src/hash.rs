//! The hasher the interpreter's own maps use, and why it is not the default one.
//!
//! `std::collections::HashMap` defaults to SipHash-1-3 behind a `RandomState`
//! seeded from the operating system. Both halves of that are wrong here and for
//! different reasons.
//!
//! **The algorithm.** SipHash is a keyed MAC chosen to make hash-collision
//! denial of service impossible when the keys come from an attacker over a
//! network. The keys here are Python identifiers and short dict keys in a
//! program the agent that runs it also wrote, inside a process with a step
//! limit, that exits in under a millisecond. There is no adversary and nothing
//! to deny. What there is, is a scope lookup on every name read: SipHash's
//! setup and finalisation dominate a six-byte key, where FNV-1a is one multiply
//! per byte and no setup at all.
//!
//! **The seed.** `RandomState` also drags `getrandom` and a lazily initialised
//! thread-local into the startup path of a binary whose entire argument is that
//! it starts in a fraction of a millisecond, and it makes iteration order vary
//! between runs of the same program. lypning already refuses to expose set
//! order for exactly that reason (`value.rs`), and a dict's order is its
//! insertion order held in a `Vec`, so nothing here needs a random seed and
//! nothing here is allowed to want one.
//!
//! **What this cannot change.** Nothing about an answer. A `Dict` keeps its
//! entries in insertion order in a `Vec` and uses the map only as an index into
//! it; a `Set` is refused wherever its order would be observable. So the hash
//! function is invisible from Python, and the only risk it carries is
//! performance: a pathological key set would degrade lookups toward linear
//! rather than produce a wrong answer. FNV-1a on short ASCII identifiers does
//! not have that shape.
//!
//! No dependency — invariant 6. Twenty lines of std is the whole thing.

use std::collections::HashMap;
use std::collections::HashSet;
use std::hash::{BuildHasher, Hasher};

const OFFSET: u64 = 0xcbf2_9ce4_8422_2325;
const PRIME: u64 = 0x0000_0100_0000_01b3;

/// FNV-1a. Byte at a time, which is the right trade for keys this short.
pub struct Fnv(u64);

impl Hasher for Fnv {
    #[inline]
    fn finish(&self) -> u64 {
        self.0
    }

    #[inline]
    fn write(&mut self, bytes: &[u8]) {
        let mut h = self.0;
        for b in bytes {
            h ^= u64::from(*b);
            h = h.wrapping_mul(PRIME);
        }
        self.0 = h;
    }

    // The integer paths matter as much as the byte one: a `Dict` keyed by ints
    // is common, and the default `write_u64` would route through `write` and
    // eight rounds of the byte loop for a value that is already well mixed by
    // one multiply.
    #[inline]
    fn write_u64(&mut self, n: u64) {
        self.0 = (self.0 ^ n).wrapping_mul(PRIME);
    }

    #[inline]
    fn write_i64(&mut self, n: i64) {
        self.write_u64(n as u64);
    }

    #[inline]
    fn write_usize(&mut self, n: usize) {
        self.write_u64(n as u64);
    }

    #[inline]
    fn write_u8(&mut self, n: u8) {
        self.0 = (self.0 ^ u64::from(n)).wrapping_mul(PRIME);
    }
}

#[derive(Clone, Copy, Default)]
pub struct BuildFnv;

impl BuildHasher for BuildFnv {
    type Hasher = Fnv;

    #[inline]
    fn build_hasher(&self) -> Fnv {
        Fnv(OFFSET)
    }
}

/// The map type every interpreter-internal table uses.
pub type Map<K, V> = HashMap<K, V, BuildFnv>;

/// The set type, likewise.
pub type Set<T> = HashSet<T, BuildFnv>;

#[inline]
pub fn map<K, V>() -> Map<K, V> {
    Map::default()
}

#[inline]
pub fn set<T>() -> Set<T> {
    Set::default()
}
