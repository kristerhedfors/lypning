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

/// A map sized for what it is about to hold.
///
/// Here rather than at the call site so `BuildFnv` is named in one file: a
/// `HashMap::with_capacity_and_hasher` written elsewhere would be the second
/// place that has to know which hasher this interpreter uses.
#[inline]
pub fn map_with_capacity<K, V>(n: usize) -> Map<K, V> {
    Map::with_capacity_and_hasher(n, BuildFnv::default())
}

#[inline]
pub fn set<T>() -> Set<T> {
    Set::default()
}

/// A set of identifiers with a one-word membership filter in front of it.
///
/// The set is still the answer. The word is only ever allowed to say **no** —
/// a clear bit means the name was never inserted, so [`Names::contains`] can
/// return `false` without hashing anything or touching the table at all. A set
/// bit means "maybe", and the table settles it.
///
/// It exists for one caller: [`crate::eval::Interp::lookup`] probes the current
/// function's assigned-name set on every name that is not a local, purely to
/// decide whether the answer is `UnboundLocalError`. That probe is a hash and a
/// table walk, and for the name it is asked about most — a global function
/// being called from inside another function, which is what recursion IS — the
/// answer is always no. Measured on this host on 2026-09-07: deleting that
/// probe outright (wrong, but it prices it) took `fib(26)` from 46.9 ms to
/// 45.6 ms, 2.8%.
///
/// [`bit`](Names::bit) is length and the two end bytes rather than a hash,
/// because a hash of the name is most of what the table probe costs and a
/// filter that costs the thing it is skipping is not a filter. Identifiers that
/// collide simply pay today's price; a name whose bit is CLEAR is not in the
/// set, and that is the only direction this is allowed to answer in.
pub struct Names {
    filter: u64,
    names: Set<std::rc::Rc<str>>,
}

impl Names {
    #[inline]
    pub fn new() -> Self {
        Names {
            filter: 0,
            names: set(),
        }
    }

    /// Which bit of the filter this name claims. Length and the first and last
    /// byte — three loads and no multiply.
    #[inline]
    fn bit(name: &str) -> u64 {
        let b = name.as_bytes();
        let first = u64::from(*b.first().unwrap_or(&0));
        let last = u64::from(*b.last().unwrap_or(&0));
        let h = (b.len() as u64).wrapping_add(first << 2).wrapping_add(last << 5);
        1u64 << (h & 63)
    }

    pub fn insert(&mut self, name: std::rc::Rc<str>) {
        self.filter |= Self::bit(&name);
        self.names.insert(name);
    }

    /// The filter first, and it is exact in the direction it answers: a name
    /// whose bit is clear is not in the set.
    #[inline]
    pub fn contains(&self, name: &str) -> bool {
        self.filter & Self::bit(name) != 0 && self.names.contains(name)
    }

    /// How many names, for sizing the scope map that will hold them.
    pub fn len(&self) -> usize {
        self.names.len()
    }

    pub fn is_empty(&self) -> bool {
        self.names.is_empty()
    }
}
