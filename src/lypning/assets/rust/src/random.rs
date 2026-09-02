//! `random`, the seeded-integer subset.
//!
//! The one invariant: **a seeded stream is CPython's stream, bit for bit, or it
//! is a refusal.** This is MT19937 exactly as `_randommodule.c` runs it —
//! `init_by_array` over the seed's 32-bit words, the same tempering, the same
//! 53-bit `random()` and the same little-endian `getrandbits()` — with the
//! Python-level `_randbelow_with_getrandbits` on top, which is what `randint`,
//! `randrange` and `choice` draw from. No libm is involved anywhere, which is
//! what keeps this module out of the trap `math` sits in: every answer is
//! integer arithmetic plus one exact division by a power of two.
//!
//! Everything outside that is refused rather than approximated, and each
//! refusal names something CPython genuinely does differently:
//!
//! - an **unseeded** stream comes from the OS and is not reproducible at all;
//! - `seed(str)` / `seed(bytes)` mix through SHA-512, and `seed(float)` through
//!   the float's hash — neither is here;
//! - every **error path** (`randrange` on an empty range, `choice` of an empty
//!   sequence, negative bit counts) is refused instead of raised, because the
//!   message text has changed between CPython versions and a wrong message is
//!   a wrong answer;
//! - `shuffle`, `sample`, `choices`, `uniform`, `gauss`, `Random(…)` and every
//!   other name are refused at `modules::get_attr`, before any call.
//!
//! Every refusal raised here carries the kind `random`, which `route.rs` lists
//! as only-CPython: MicroPython's generator is not MT19937, so a seeded stream
//! that falls one tier instead of two would be answered plausibly and wrongly.
//! The module is off that tier's import table for the same reason.

use std::rc::Rc;

use crate::args::Args;
use crate::err::{unsupported, R};
use crate::eval::Interp;
use crate::value::{type_name, Value};

const N: usize = 624;
const M: usize = 397;
const MATRIX_A: u32 = 0x9908_b0df;
const UPPER: u32 = 0x8000_0000;
const LOWER: u32 = 0x7fff_ffff;

/// One MT19937 state, `mti == N` meaning "regenerate before the next draw".
pub struct Mt {
    mt: [u32; N],
    mti: usize,
}

impl Mt {
    fn init_genrand(s: u32) -> Mt {
        let mut mt = [0u32; N];
        mt[0] = s;
        for i in 1..N {
            mt[i] = 1_812_433_253u32
                .wrapping_mul(mt[i - 1] ^ (mt[i - 1] >> 30))
                .wrapping_add(i as u32);
        }
        Mt { mt, mti: N }
    }

    /// `init_by_array`: the seeding CPython uses for every integer seed.
    pub fn from_key(key: &[u32]) -> Mt {
        let mut g = Mt::init_genrand(19_650_218);
        let mt = &mut g.mt;
        let (mut i, mut j) = (1usize, 0usize);
        let mut k = if N > key.len() { N } else { key.len() };
        while k > 0 {
            mt[i] = (mt[i] ^ (mt[i - 1] ^ (mt[i - 1] >> 30)).wrapping_mul(1_664_525))
                .wrapping_add(key[j])
                .wrapping_add(j as u32);
            i += 1;
            j += 1;
            if i >= N {
                mt[0] = mt[N - 1];
                i = 1;
            }
            if j >= key.len() {
                j = 0;
            }
            k -= 1;
        }
        k = N - 1;
        while k > 0 {
            mt[i] = (mt[i] ^ (mt[i - 1] ^ (mt[i - 1] >> 30)).wrapping_mul(1_566_083_941))
                .wrapping_sub(i as u32);
            i += 1;
            if i >= N {
                mt[0] = mt[N - 1];
                i = 1;
            }
            k -= 1;
        }
        mt[0] = 0x8000_0000;
        g
    }

    fn next_u32(&mut self) -> u32 {
        if self.mti >= N {
            let mt = &mut self.mt;
            let twist = |y: u32| (y >> 1) ^ if y & 1 == 1 { MATRIX_A } else { 0 };
            for kk in 0..N - M {
                let y = (mt[kk] & UPPER) | (mt[kk + 1] & LOWER);
                mt[kk] = mt[kk + M] ^ twist(y);
            }
            for kk in N - M..N - 1 {
                let y = (mt[kk] & UPPER) | (mt[kk + 1] & LOWER);
                mt[kk] = mt[kk + M - N] ^ twist(y);
            }
            let y = (mt[N - 1] & UPPER) | (mt[0] & LOWER);
            mt[N - 1] = mt[M - 1] ^ twist(y);
            self.mti = 0;
        }
        let mut y = self.mt[self.mti];
        self.mti += 1;
        y ^= y >> 11;
        y ^= (y << 7) & 0x9d2c_5680;
        y ^= (y << 15) & 0xefc6_0000;
        y ^= y >> 18;
        y
    }

    /// `random()`: 53 bits from two draws, exactly `_random.Random.random`.
    pub fn random(&mut self) -> f64 {
        let a = (self.next_u32() >> 5) as f64;
        let b = (self.next_u32() >> 6) as f64;
        (a * 67_108_864.0 + b) * (1.0 / 9_007_199_254_740_992.0)
    }

    /// `getrandbits(k)` for `0 <= k <= 63`: 32-bit words, least significant
    /// first, the last one right-shifted to its remaining width.
    pub fn getrandbits(&mut self, k: u32) -> u64 {
        if k == 0 {
            return 0;
        }
        if k <= 32 {
            return (self.next_u32() >> (32 - k)) as u64;
        }
        let mut out = 0u64;
        let mut shift = 0;
        let mut left = k;
        while left > 0 {
            let mut r = self.next_u32();
            if left < 32 {
                r >>= 32 - left;
            }
            out |= (r as u64) << shift;
            shift += 32;
            left = left.saturating_sub(32);
        }
        out
    }

    /// `_randbelow_with_getrandbits(n)`: uniform on `[0, n)` for `n > 0`, by
    /// rejection over `n.bit_length()` bits.
    pub fn below(&mut self, n: u64) -> u64 {
        let k = 64 - n.leading_zeros();
        loop {
            let r = self.getrandbits(k);
            if r < n {
                return r;
            }
        }
    }
}

/// The seed's absolute value as little-endian 32-bit words, at least one —
/// `random_seed` in `_randommodule.c` (`keyused = bits == 0 ? 1 : (bits-1)/32+1`).
fn key_of(n: i64) -> R<Vec<u32>> {
    let a = n
        .checked_abs()
        .ok_or_else(|| unsupported("random", "random.seed(-2**63) needs a bignum"))? as u64;
    if a == 0 {
        return Ok(vec![0]);
    }
    let mut key = Vec::with_capacity(2);
    let mut v = a;
    while v > 0 {
        key.push(v as u32);
        v >>= 32;
    }
    Ok(key)
}

pub fn call(it: &mut Interp, name: &str, args: &mut Args, kw: &[(Rc<str>, Value)]) -> R<Value> {
    if !kw.is_empty() {
        return Err(unsupported("random", &format!("random.{name}() with keyword arguments")));
    }
    let int = |v: &Value| -> R<i64> {
        match v {
            Value::Int(i) => Ok(*i),
            Value::Bool(b) => Ok(*b as i64),
            other => Err(unsupported(
                "random",
                &format!("random.{name}() of a {}", type_name(other)),
            )),
        }
    };
    let arity = |lo: usize, hi: usize| -> R<()> {
        if args.len() < lo || args.len() > hi {
            // CPython's TypeError text for a wrong count is version-shaped.
            return Err(unsupported(
                "random",
                &format!("random.{name}() with {} argument(s)", args.len()),
            ));
        }
        Ok(())
    };
    if name == "seed" {
        arity(1, 1)?;
        let n = int(&args[0])?;
        it.rng = Some(Box::new(Mt::from_key(&key_of(n)?)));
        return Ok(Value::None);
    }
    if it.rng.is_none() {
        return Err(unsupported(
            "random",
            "unseeded stream — CPython seeds it from the OS, which is not reproducible",
        ));
    }
    let empty = || unsupported("random", &format!("random.{name}() over an empty range"));
    let big = || unsupported("random", &format!("random.{name}() past 64-bit range"));
    Ok(match name {
        "random" => {
            arity(0, 0)?;
            Value::Float(it.rng.as_mut().expect("seeded").random())
        }
        "getrandbits" => {
            arity(1, 1)?;
            let k = int(&args[0])?;
            if k < 0 {
                return Err(unsupported("random", "random.getrandbits() of a negative count"));
            }
            if k > 63 {
                return Err(big());
            }
            Value::Int(it.rng.as_mut().expect("seeded").getrandbits(k as u32) as i64)
        }
        "randint" | "randrange" => {
            // Both are `start + _randbelow(stop - start)`; `randint(a, b)` is
            // `randrange(a, b + 1)` and `randrange(n)` is `randrange(0, n)`.
            let (start, stop) = if name == "randint" {
                arity(2, 2)?;
                (int(&args[0])?, int(&args[1])?.checked_add(1).ok_or_else(big)?)
            } else {
                arity(1, 3)?;
                if args.len() == 3 {
                    return Err(unsupported("random", "random.randrange() with a step"));
                }
                if args.len() == 1 {
                    (0, int(&args[0])?)
                } else {
                    (int(&args[0])?, int(&args[1])?)
                }
            };
            let width = stop.checked_sub(start).ok_or_else(big)?;
            if width <= 0 {
                return Err(empty());
            }
            let r = it.rng.as_mut().expect("seeded").below(width as u64) as i64;
            Value::Int(start + r)
        }
        "choice" => {
            arity(1, 1)?;
            let seq = args[0].clone();
            let n = match &seq {
                Value::List(l) => l.borrow().len(),
                Value::Tuple(t) => t.len(),
                Value::Str(s) => if s.is_ascii() { s.len() } else { s.chars().count() },
                other => {
                    return Err(unsupported(
                        "random",
                        &format!("random.choice() of a {}", type_name(other)),
                    ))
                }
            };
            if n == 0 {
                return Err(empty());
            }
            let i = it.rng.as_mut().expect("seeded").below(n as u64) as i64;
            it.index(&seq, &Value::Int(i))?
        }
        _ => return Err(unsupported("module-attr", &format!("random.{name}"))),
    })
}
