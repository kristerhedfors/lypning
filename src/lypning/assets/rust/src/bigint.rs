//! `cap-bigint` — Python's integers, which are arbitrary precision.
//!
//! This is not a module capability. There is no `import` to serve: the thing
//! being widened is [`crate::value::Value::Int`] itself, so the surface is every
//! path in the interpreter that ever held an integer. That shape is why the
//! payload was widened rather than a `Value::BigInt` added beside it — a new
//! variant is invisible to every `_ =>` arm in the tree, and five capabilities in
//! a row shipped one that reached print, `==`, `hash`, `sorted`, `in`, `bool`,
//! `len`, indexing and `%`-format through an arm nobody remembered
//! (`docs/HILLCLIMB.md` iterations 74 and 76). Changing the *type* of the payload
//! makes the compiler enumerate those arms instead: every site that read the
//! `i64` stopped compiling until it said what it does with a value that is not
//! one, and the default answer — [`crate::value::Int::get`] — is a refusal.
//!
//! ## What is exact, and what refuses
//!
//! Exact, at any width: `+ - * ** // % divmod << >>`, unary `-`, `abs`,
//! `== != < <= > >=` between two integers, `str`/`repr`/`print`/`{}`/`%s`/`%d`,
//! `bin`/`hex`/`oct`, `bool`, `int(str)` and integer literals of any size,
//! `json.dumps`, and `sum` over integers.
//!
//! Refused, as `bigint`, with the reason written at each site below: any mixing
//! of a wide integer with a **float** (including `int / int` when an operand is
//! wide, `float(x)` and an ordering against a float); a wide integer as a
//! **dict key or set member**; `& | ^` on a wide operand; and a wide integer
//! anywhere a machine word is required — an index, a count, a repeat, a
//! codepoint, `range`. A refusal is never a bug (`CLAUDE.md` invariant 1); a
//! truncating `as i64` would have been one, at exit 0.
//!
//! ## The two budgets
//!
//! `2 ** 10**9` is a hang, not an answer, and a hang is worse than either. So
//! the bit length of a result is estimated before it is built ([`MAX_BITS`]) and
//! division is capped by the dividend's width ([`DIV_BUDGET_BITS`]), exactly as
//! `re.rs` caps backtracking steps. Both refuse; neither wraps or truncates.
//!
//! CPython carries a third budget of its own and it is *observable*:
//! `str()` of an integer past `sys.get_int_max_str_digits()` raises
//! `ValueError` rather than answering. Two different messages (one names the
//! digit count, one does not) guard `str()` and `int()`, so this refuses at the
//! same threshold rather than reproduce them.
//!
//! That threshold is **not a constant**, which is how it was first written
//! here. It is 4300 by default and `PYTHONINTMAXSTRDIGITS` otherwise, read at
//! interpreter start, so `PYTHONINTMAXSTRDIGITS=640 lypning -c
//! 'print(len(str(10**700)))'` printed 701 where CPython raises — an answer at
//! exit 0 to a program CPython refuses. [`crate::host::int_max_str_digits`] is
//! the reader, [`over_str_digit_limit`] the screen that keeps it off the hot
//! path, and the ceiling stays 4300 for the reason that function gives.

use crate::ast::BinOp;
use crate::err::{unsupported, value_err, zero_div, LypningError, R};
use crate::value::{Int, Value};
use std::cmp::Ordering;
use std::rc::Rc;

/// Sign and magnitude, little-endian base 2**32.
///
/// The magnitude is trimmed (no high zero limb) and is never small enough to fit
/// an `i64` — [`norm`] is the only constructor and it demotes. So a `Big` is
/// never zero, never equal to any [`Int::S`], and `neg` is never a negative
/// zero.
pub struct Big {
    pub neg: bool,
    pub mag: Vec<u32>,
}

/// The widest result this engine will build: 2**20 bits, about 315,653 decimal
/// digits and 128 KiB of limbs. Past it a `**` or a `<<` refuses instead of
/// allocating; CPython would answer, one spawn later.
const MAX_BITS: usize = 1 << 20;

/// The widest dividend `//`, `%` and `divmod` will take through the bit-at-a-time
/// long division below when the divisor is more than one limb. The single-limb
/// path — which is what decimal conversion and every small divisor use — has no
/// such cap because it is linear.
const DIV_BUDGET_BITS: usize = 1 << 16;

/// Is a base-10 conversion of `digits` digits past the limit CPython would
/// apply to it?
///
/// The environment is read only when `digits` is past CPython's own
/// `str_digits_check_threshold`, below which no accepted limit can bite — so
/// `print(2**100)` costs no `getenv` at all and only a conversion already
/// hundreds of digits wide pays for one.
///
/// A `PYTHONINTMAXSTRDIGITS` CPython would not start with answers `true`, so
/// the caller refuses; `main.rs` has already refused the run for the same
/// reason, and this keeps the library path honest for a host that has not.
fn over_str_digit_limit(digits: usize) -> bool {
    digits > crate::host::STR_DIGITS_CHECK_THRESHOLD
        && crate::host::int_max_str_digits().map_or(true, |lim| digits > lim)
}

pub fn refuse(what: &str) -> LypningError {
    unsupported("bigint", what)
}

// ---- construction ---------------------------------------------------------

fn trim(mut m: Vec<u32>) -> Vec<u32> {
    while m.last() == Some(&0) {
        m.pop();
    }
    m
}

fn bits_of(m: &[u32]) -> usize {
    match m.last() {
        None => 0,
        Some(top) => m.len() * 32 - top.leading_zeros() as usize,
    }
}

/// The one constructor. Trims, then DEMOTES anything an `i64` can hold.
///
/// The demotion is not an optimisation: `Int::S` and `Int::B` must partition the
/// integers, or `==` between a small and a wide value would have to normalise
/// before comparing and every caller that forgot would be wrong at exit 0.
pub fn norm(neg: bool, mag: Vec<u32>) -> Int {
    let mag = trim(mag);
    if mag.len() <= 2 {
        let mut v: u128 = 0;
        for (i, l) in mag.iter().enumerate() {
            v |= (*l as u128) << (32 * i);
        }
        if neg {
            if v <= i64::MAX as u128 + 1 {
                return Int::S((v as i128).wrapping_neg() as i64);
            }
        } else if v <= i64::MAX as u128 {
            return Int::S(v as i64);
        }
    }
    Int::B(Rc::new(Big { neg, mag }))
}

fn mag_of_i64(i: i64) -> Vec<u32> {
    let u = i.unsigned_abs();
    trim(vec![u as u32, (u >> 32) as u32])
}

/// `(negative, magnitude)` of any integer, wide or not.
fn parts(n: &Int) -> (bool, Vec<u32>) {
    match n {
        Int::S(i) => (*i < 0, mag_of_i64(*i)),
        Int::B(b) => (b.neg, b.mag.clone()),
    }
}

/// The integer inside a value, promoting `bool` and `re.RegexFlag` the way every
/// other numeric path here does. `None` for anything that is not an integer —
/// including a float, which the callers below refuse rather than mix.
fn as_int(v: &Value) -> Option<Int> {
    match v {
        Value::Int(n) => Some(n.clone()),
        Value::Bool(b) => Some(Int::S(*b as i64)),
        #[cfg(feature = "cap-re")]
        Value::ReFlag(f) => Some(Int::S(*f as i64)),
        _ => None,
    }
}

/// Is this value an integer too wide for a machine word?
pub fn is_wide(v: &Value) -> bool {
    matches!(v, Value::Int(n) if n.small().is_none())
}

// ---- magnitude arithmetic -------------------------------------------------

fn cmp_mag(a: &[u32], b: &[u32]) -> Ordering {
    if a.len() != b.len() {
        return a.len().cmp(&b.len());
    }
    for i in (0..a.len()).rev() {
        if a[i] != b[i] {
            return a[i].cmp(&b[i]);
        }
    }
    Ordering::Equal
}

fn add_mag(a: &[u32], b: &[u32]) -> Vec<u32> {
    let (long, short) = if a.len() >= b.len() { (a, b) } else { (b, a) };
    let mut out = Vec::with_capacity(long.len() + 1);
    let mut carry = 0u64;
    for (i, &x) in long.iter().enumerate() {
        let s = x as u64 + if i < short.len() { short[i] as u64 } else { 0 } + carry;
        out.push(s as u32);
        carry = s >> 32;
    }
    if carry != 0 {
        out.push(carry as u32);
    }
    out
}

/// `a - b`, and the caller has already established `a >= b`.
fn sub_mag(a: &[u32], b: &[u32]) -> Vec<u32> {
    let mut out = Vec::with_capacity(a.len());
    let mut borrow = 0i64;
    for (i, &x) in a.iter().enumerate() {
        let d = x as i64 - if i < b.len() { b[i] as i64 } else { 0 } - borrow;
        if d < 0 {
            out.push((d + (1i64 << 32)) as u32);
            borrow = 1;
        } else {
            out.push(d as u32);
            borrow = 0;
        }
    }
    trim(out)
}

fn mul_mag(a: &[u32], b: &[u32]) -> R<Vec<u32>> {
    if a.is_empty() || b.is_empty() {
        return Ok(Vec::new());
    }
    if bits_of(a) + bits_of(b) > MAX_BITS {
        return Err(refuse("an integer product wider than 2**20 bits"));
    }
    let mut out = vec![0u32; a.len() + b.len()];
    for (i, &x) in a.iter().enumerate() {
        if x == 0 {
            continue;
        }
        let mut carry = 0u64;
        for (j, &y) in b.iter().enumerate() {
            let t = x as u64 * y as u64 + out[i + j] as u64 + carry;
            out[i + j] = t as u32;
            carry = t >> 32;
        }
        let mut k = i + b.len();
        while carry != 0 {
            let t = out[k] as u64 + carry;
            out[k] = t as u32;
            carry = t >> 32;
            k += 1;
        }
    }
    Ok(trim(out))
}

fn shl_mag(a: &[u32], n: usize) -> Vec<u32> {
    if a.is_empty() {
        return Vec::new();
    }
    let (words, bits) = (n / 32, n % 32);
    let mut out = vec![0u32; words];
    if bits == 0 {
        out.extend_from_slice(a);
    } else {
        let mut carry = 0u32;
        for &x in a {
            out.push((x << bits) | carry);
            carry = x >> (32 - bits);
        }
        if carry != 0 {
            out.push(carry);
        }
    }
    trim(out)
}

fn shr_mag(a: &[u32], n: usize) -> Vec<u32> {
    let (words, bits) = (n / 32, n % 32);
    if words >= a.len() {
        return Vec::new();
    }
    let src = &a[words..];
    let mut out = Vec::with_capacity(src.len());
    if bits == 0 {
        out.extend_from_slice(src);
    } else {
        for i in 0..src.len() {
            let hi = if i + 1 < src.len() { src[i + 1] } else { 0 };
            out.push((src[i] >> bits) | (hi << (32 - bits)));
        }
    }
    trim(out)
}

/// `|a| / d` and `|a| % d` for a single-limb divisor. Linear, and the path every
/// decimal conversion takes.
fn divmod_small(a: &[u32], d: u32) -> (Vec<u32>, u32) {
    let mut q = vec![0u32; a.len()];
    let mut r = 0u64;
    for i in (0..a.len()).rev() {
        let cur = (r << 32) | a[i] as u64;
        q[i] = (cur / d as u64) as u32;
        r = cur % d as u64;
    }
    (trim(q), r as u32)
}

/// `(|a| / |b|, |a| % |b|)`, truncating. `b` is non-zero.
///
/// Bit at a time rather than Knuth D. The corpus has no wide division at all, so
/// the choice is between an algorithm that is obviously correct and one that is
/// fast — and the one thing this file may not be is subtly wrong. The cost is
/// bounded by [`DIV_BUDGET_BITS`] instead.
fn divmod_mag(a: &[u32], b: &[u32]) -> R<(Vec<u32>, Vec<u32>)> {
    if cmp_mag(a, b) == Ordering::Less {
        return Ok((Vec::new(), a.to_vec()));
    }
    if b.len() == 1 {
        let (q, r) = divmod_small(a, b[0]);
        return Ok((q, trim(vec![r])));
    }
    let nbits = bits_of(a);
    if nbits > DIV_BUDGET_BITS {
        return Err(refuse(
            "division of an integer wider than 2**16 bits by a multi-word divisor",
        ));
    }
    let mut q = vec![0u32; a.len()];
    let mut rem: Vec<u32> = Vec::new();
    for i in (0..nbits).rev() {
        rem = shl_mag(&rem, 1);
        if (a[i / 32] >> (i % 32)) & 1 == 1 {
            if rem.is_empty() {
                rem.push(1);
            } else {
                rem[0] |= 1;
            }
        }
        if cmp_mag(&rem, b) != Ordering::Less {
            rem = sub_mag(&rem, b);
            q[i / 32] |= 1 << (i % 32);
        }
    }
    Ok((trim(q), rem))
}

// ---- signed arithmetic ----------------------------------------------------

fn add_signed(an: bool, a: &[u32], bn: bool, b: &[u32]) -> Int {
    if an == bn {
        norm(an, add_mag(a, b))
    } else {
        match cmp_mag(a, b) {
            Ordering::Equal => Int::S(0),
            Ordering::Greater => norm(an, sub_mag(a, b)),
            Ordering::Less => norm(bn, sub_mag(b, a)),
        }
    }
}

/// Python's `//` and `%`: the quotient floors toward negative infinity and the
/// remainder takes the sign of the DIVISOR. Rust's `/` and `%` do neither, and
/// the correction is the same one `ops::num_binop` applies to `i64`.
fn floor_divmod(an: bool, a: &[u32], bn: bool, b: &[u32]) -> R<(Int, Int)> {
    let (q, r) = divmod_mag(a, b)?;
    let neg = an != bn;
    if !neg || r.is_empty() {
        return Ok((norm(neg, q), norm(an, r)));
    }
    // Truncated toward zero and the signs differ, so the floor is one lower and
    // the remainder is the divisor minus what is left.
    let q = add_mag(&q, &[1]);
    let r = sub_mag(b, &r);
    Ok((norm(true, q), norm(bn, r)))
}

fn pow_int(a: &Int, e: u64) -> R<Int> {
    let (an, am) = parts(a);
    if am.is_empty() {
        return Ok(if e == 0 { Int::S(1) } else { Int::S(0) });
    }
    // Estimated BEFORE anything is allocated: `2 ** 10**9` must refuse, not
    // spend a minute discovering it cannot finish.
    if bits_of(&am) as u128 * e as u128 > MAX_BITS as u128 {
        return Err(refuse("an integer power wider than 2**20 bits"));
    }
    let mut acc = vec![1u32];
    let mut base = am;
    let mut n = e;
    while n > 0 {
        if n & 1 == 1 {
            acc = mul_mag(&acc, &base)?;
        }
        n >>= 1;
        if n > 0 {
            base = mul_mag(&base, &base)?;
        }
    }
    Ok(norm(an && e % 2 == 1, acc))
}

pub fn cmp_int(a: &Int, b: &Int) -> Ordering {
    match (a.small(), b.small()) {
        (Some(x), Some(y)) => x.cmp(&y),
        _ => {
            let (an, am) = parts(a);
            let (bn, bm) = parts(b);
            match (an, bn) {
                (false, true) => Ordering::Greater,
                (true, false) => Ordering::Less,
                (false, false) => cmp_mag(&am, &bm),
                (true, true) => cmp_mag(&bm, &am),
            }
        }
    }
}

/// Equality between two integers. A [`Int::B`] never fits an `i64`, so a wide
/// value and a small one are never equal and the arms decide it alone.
pub fn eq_int(a: &Int, b: &Int) -> bool {
    match (a, b) {
        (Int::S(x), Int::S(y)) => x == y,
        (Int::B(x), Int::B(y)) => x.neg == y.neg && x.mag == y.mag,
        _ => false,
    }
}

// ---- rendering ------------------------------------------------------------

/// The decimal digits, refused past CPython's own `int_max_str_digits`.
pub fn to_dec(b: &Big) -> R<String> {
    let mut cur = b.mag.clone();
    let mut chunks: Vec<u32> = Vec::new();
    while !cur.is_empty() {
        let (q, r) = divmod_small(&cur, 1_000_000_000);
        chunks.push(r);
        cur = q;
    }
    if chunks.is_empty() {
        chunks.push(0);
    }
    let digits = (chunks.len() - 1) * 9 + chunks[chunks.len() - 1].to_string().len();
    if over_str_digit_limit(digits) {
        return Err(refuse(
            "str() of an integer past sys.get_int_max_str_digits(), where CPython raises ValueError",
        ));
    }
    let mut out = String::with_capacity(digits + 1);
    if b.neg {
        out.push('-');
    }
    for (i, c) in chunks.iter().enumerate().rev() {
        if i == chunks.len() - 1 {
            out.push_str(&c.to_string());
        } else {
            out.push_str(&format!("{c:09}"));
        }
    }
    Ok(out)
}

/// The digits for `bin`, `oct`, `hex` and the `b`/`o`/`x`/`X` format codes —
/// magnitude only, unsigned, no prefix and no sign, which is what every caller
/// wants because CPython puts the sign before the prefix (`-0x10`).
///
/// No digit cap: CPython's applies to base 10 only, and `hex(10**5000)` answers
/// there.
pub fn to_radix(b: &Big, radix: u32, upper: bool) -> String {
    let bits = match radix {
        2 => 1,
        8 => 3,
        _ => 4,
    };
    let n = bits_of(&b.mag);
    let mut out = String::with_capacity(n / bits + 1);
    let mut i = (n + bits - 1) / bits * bits;
    let digits = if upper { b"0123456789ABCDEF" } else { b"0123456789abcdef" };
    while i >= bits {
        i -= bits;
        let mut v = 0u32;
        for k in 0..bits {
            let bit = i + k;
            if bit < n && (b.mag[bit / 32] >> (bit % 32)) & 1 == 1 {
                v |= 1 << k;
            }
        }
        if out.is_empty() && v == 0 {
            continue;
        }
        out.push(digits[v as usize] as char);
    }
    if out.is_empty() {
        out.push('0');
    }
    out
}

/// An integer literal or `int(s)` argument of any width. `digits` has already had
/// its sign, prefix and underscores removed by the caller.
pub fn parse(digits: &str, radix: u32) -> Option<Int> {
    if digits.is_empty() {
        return None;
    }
    if radix == 10 && over_str_digit_limit(digits.len()) {
        // CPython raises ValueError here too, with a message that names the
        // digit count. Refused as a None the caller turns into the `bigint`
        // refusal, for the reason `to_dec` gives.
        return None;
    }
    let mut mag: Vec<u32> = Vec::new();
    for ch in digits.bytes() {
        let d = (ch as char).to_digit(radix)?;
        // mag = mag * radix + d
        let mut carry = d as u64;
        for l in mag.iter_mut() {
            let t = *l as u64 * radix as u64 + carry;
            *l = t as u32;
            carry = t >> 32;
        }
        while carry != 0 {
            mag.push(carry as u32);
            carry >>= 32;
        }
    }
    Some(norm(false, trim(mag)))
}

// ---- the entry points the interpreter calls -------------------------------

/// `x op y` for two `i64`s whose exact result does not fit one.
///
/// Called from `ops::num_binop` where the checked operation returned `None`, so
/// this is the promotion point and the only one: every other overflow in the
/// tree is a machine-word requirement, not an integer result.
pub fn i64_op(op: BinOp, x: i64, y: i64) -> R<Value> {
    int_op(op, &Int::S(x), &Int::S(y))
}

/// `a op b` when at least one side is a WIDE integer.
///
/// `Ok(None)` means "not mine": neither operand is wide, or the other operand is
/// not an integer at all and the generic `TypeError` below is CPython's own
/// answer. A wide integer against a FLOAT is the one case that must not fall
/// through — `2.0**100 == 2**100` is True in CPython and this engine cannot say
/// so — and it refuses here.
pub fn binop(op: BinOp, a: &Value, b: &Value) -> R<Option<Value>> {
    if !is_wide(a) && !is_wide(b) {
        return Ok(None);
    }
    if matches!(a, Value::Float(_)) || matches!(b, Value::Float(_)) {
        return Err(refuse(
            "an integer past 64 bits mixed with a float, whose exact value this engine cannot round",
        ));
    }
    match (as_int(a), as_int(b)) {
        (Some(x), Some(y)) => int_op(op, &x, &y).map(Some),
        _ => Ok(None),
    }
}

fn int_op(op: BinOp, a: &Int, b: &Int) -> R<Value> {
    use BinOp::*;
    let (an, am) = parts(a);
    let (bn, bm) = parts(b);
    Ok(match op {
        Add => Value::Int(add_signed(an, &am, bn, &bm)),
        Sub => Value::Int(add_signed(an, &am, !bn, &bm)),
        Mul => Value::Int(norm(an != bn, mul_mag(&am, &bm)?)),
        Pow => {
            let e = b.get().map_err(|_| refuse("an integer exponent past 64 bits"))?;
            if e < 0 {
                // CPython answers a float here, from an exact quotient this
                // engine will not round. `2 ** -1` on two small ints never
                // reaches this function.
                return Err(refuse(
                    "an integer past 64 bits raised to a negative power, whose answer is a float",
                ));
            }
            Value::Int(pow_int(a, e as u64)?)
        }
        FloorDiv | Mod => {
            if bm.is_empty() {
                return Err(zero_div("integer division or modulo by zero"));
            }
            let (q, r) = floor_divmod(an, &am, bn, &bm)?;
            Value::Int(if matches!(op, FloorDiv) { q } else { r })
        }
        LShift => {
            let n = b.get().map_err(|_| refuse("a shift count past 64 bits"))?;
            if n < 0 {
                return Err(value_err("negative shift count"));
            }
            if bits_of(&am) + n as usize > MAX_BITS {
                return Err(refuse("a left shift wider than 2**20 bits"));
            }
            Value::Int(norm(an, shl_mag(&am, n as usize)))
        }
        RShift => {
            let n = b.get().map_err(|_| refuse("a shift count past 64 bits"))?;
            if n < 0 {
                return Err(value_err("negative shift count"));
            }
            // Python's `>>` on a NEGATIVE integer is an arithmetic shift — it
            // floors, so `-5 >> 1` is -3 and not -2. Computed as a floor
            // division by 2**n rather than as a magnitude shift, which would
            // truncate toward zero and be off by one for every negative operand
            // that shifts a set bit out.
            if !an {
                Value::Int(norm(false, shr_mag(&am, n as usize)))
            } else {
                let q = shr_mag(&am, n as usize);
                let lost = cmp_mag(&shl_mag(&q, n as usize), &am) != Ordering::Equal;
                Value::Int(norm(true, if lost { add_mag(&q, &[1]) } else { q }))
            }
        }
        Div => {
            return Err(refuse(
                "float division of an integer past 64 bits, whose quotient needs exact rounding",
            ))
        }
        BitAnd | BitOr | BitXor => {
            // Two's complement over an infinite sign extension. Correct for
            // non-negative operands by limb-wise masking and fiddly for negative
            // ones; the corpus has neither, so both refuse rather than one work
            // and one be almost right.
            return Err(refuse(
                "a bitwise operator on an integer past 64 bits",
            ));
        }
    })
}

/// Unary `-` on a wide integer, and on `i64::MIN`, whose negation is not one.
pub fn neg(n: &Int) -> Value {
    let (neg, mag) = parts(n);
    Value::Int(norm(!neg, mag))
}

/// `abs()` of a wide integer, and of `i64::MIN`.
pub fn abs(n: &Int) -> Value {
    let (_, mag) = parts(n);
    Value::Int(norm(false, mag))
}

pub fn bit_length(b: &Big) -> usize {
    bits_of(&b.mag)
}

// ---- int / int, computed from the integers -------------------------------

/// `x / y` for two `i64`s where an operand is past 2**53.
///
/// This is the `int-div-precision` refusal, answered. CPython computes the
/// quotient FROM THE INTEGERS and rounds once; converting each to `f64` first
/// loses the low bits before the divide, and the error survives it —
/// `9007199254740993 / 3` came out 3002399751580330.5 where CPython says
/// 3002399751580331.0.
///
/// `u128` is wide enough for every `i64` pair, and the proof is the shift: the
/// dividend is scaled so the quotient carries 55 or 56 bits, which needs at most
/// `55 + bits(y) <= 119` bits of headroom. Round-half-EVEN, with the division's
/// own remainder as the sticky bit — dropping it would round exact halves the
/// wrong way one time in two.
pub fn div_exact(x: i64, y: i64) -> R<Value> {
    let neg = (x < 0) != (y < 0);
    let (a, b) = (x.unsigned_abs() as u128, y.unsigned_abs() as u128);
    if a == 0 {
        return Ok(Value::Float(if neg { -0.0 } else { 0.0 }));
    }
    let n = 128 - a.leading_zeros() as i32;
    let m = 128 - b.leading_zeros() as i32;
    let s = 55 - (n - m);
    let (a2, b2) = if s >= 0 { (a << s, b) } else { (a, b << (-s)) };
    let q = a2 / b2;
    let r = a2 % b2;
    let bl = 128 - q.leading_zeros() as i32;
    let drop = bl - 53;
    let (hi, e) = if drop > 0 {
        let round = (q >> (drop - 1)) & 1;
        let sticky = (q & ((1u128 << (drop - 1)) - 1)) != 0 || r != 0;
        let mut hi = q >> drop;
        if round == 1 && (sticky || hi & 1 == 1) {
            hi += 1;
        }
        (hi, drop)
    } else {
        (q, 0)
    };
    // `hi` is at most 2**53 and `e - s` is within [-116, 10] for any `i64` pair,
    // so both the conversion and the scaling are exact and neither can reach a
    // subnormal or an infinity.
    let f = hi as f64 * (2f64).powi(e - s);
    Ok(Value::Float(if neg { -f } else { f }))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn dec(s: &str) -> Int {
        let (neg, d) = match s.strip_prefix('-') {
            Some(r) => (true, r),
            None => (false, s),
        };
        let v = parse(d, 10).unwrap();
        if neg {
            match neg_of(&v) {
                Value::Int(i) => i,
                _ => unreachable!(),
            }
        } else {
            v
        }
    }
    fn neg_of(n: &Int) -> Value {
        neg(n)
    }
    fn show(n: &Int) -> String {
        match n {
            Int::S(i) => i.to_string(),
            Int::B(b) => to_dec(b).unwrap(),
        }
    }
    fn op(o: BinOp, a: &str, b: &str) -> String {
        match int_op(o, &dec(a), &dec(b)).unwrap() {
            Value::Int(i) => show(&i),
            v => format!("{:?}", crate::value::type_name(&v)),
        }
    }

    #[test]
    fn the_demotion_partitions_the_integers() {
        // Everything an i64 holds comes back as one, including both ends.
        for s in ["0", "1", "-1", "9223372036854775807", "-9223372036854775808"] {
            assert!(dec(s).small().is_some(), "{s}");
        }
        for s in ["9223372036854775808", "-9223372036854775809", "10000000000000000000000"] {
            assert!(dec(s).small().is_none(), "{s}");
        }
    }

    #[test]
    fn arithmetic_agrees_with_cpython_on_the_shapes_the_corpus_types() {
        assert_eq!(op(BinOp::Pow, "2", "100"), "1267650600228229401496703205376");
        assert_eq!(op(BinOp::Add, "100000000000000000000", "1"), "100000000000000000001");
        assert_eq!(op(BinOp::Pow, "2", "63"), "9223372036854775808");
        assert_eq!(op(BinOp::Sub, "9223372036854775808", "1"), "9223372036854775807");
        // 30!, which six corpus programs build one multiplication at a time.
        let mut acc = Int::S(1);
        for i in 2..=30i64 {
            acc = match int_op(BinOp::Mul, &acc, &Int::S(i)).unwrap() {
                Value::Int(v) => v,
                _ => unreachable!(),
            };
        }
        assert_eq!(show(&acc), "265252859812191058636308480000000");
    }

    #[test]
    fn floor_division_and_modulo_take_pythons_signs_not_rusts() {
        // CPython: (-2**100)//7 == -181092942889747057356671886483, (-2**100)%7 == 5
        assert_eq!(op(BinOp::FloorDiv, "-1267650600228229401496703205376", "7"),
                   "-181092942889747057356671886483");
        assert_eq!(op(BinOp::Mod, "-1267650600228229401496703205376", "7"), "5");
        assert_eq!(op(BinOp::FloorDiv, "1267650600228229401496703205376", "-7"),
                   "-181092942889747057356671886483");
        assert_eq!(op(BinOp::Mod, "1267650600228229401496703205376", "-7"), "-5");
        // A multi-limb divisor takes the bit-at-a-time path.
        assert_eq!(op(BinOp::FloorDiv, "1267650600228229401496703205376",
                      "1152921504606846977"), "1099511627775");
    }

    #[test]
    fn a_right_shift_floors_a_negative_operand_instead_of_truncating_it() {
        // -(2**100) >> 3 is -158456325028528675187087900672 in CPython, and a
        // magnitude shift would answer that too; the off-by-one only shows when
        // a set bit is shifted out.
        assert_eq!(op(BinOp::RShift, "-1267650600228229401496703205377", "3"),
                   "-158456325028528675187087900673");
        assert_eq!(op(BinOp::LShift, "1267650600228229401496703205376", "3"),
                   "10141204801825835211973625643008");
    }

    #[test]
    fn the_budgets_refuse_rather_than_run() {
        assert!(int_op(BinOp::Pow, &Int::S(2), &Int::S(1 << 30)).is_err());
        assert!(int_op(BinOp::LShift, &dec("1267650600228229401496703205376"),
                       &Int::S(1 << 30)).is_err());
    }

    #[test]
    fn the_radix_forms_are_cpythons() {
        let b = match dec("1267650600228229401496703205376") {
            Int::B(b) => b,
            _ => unreachable!(),
        };
        assert_eq!(to_radix(&b, 16, false), "10000000000000000000000000");
        assert_eq!(to_radix(&b, 2, false).len(), 101);
        assert_eq!(bit_length(&b), 101);
    }

    #[test]
    fn int_div_is_correctly_rounded_past_the_exact_range() {
        // The four corpus programs' operands, and their CPython 3.14.5 answers.
        let f = |x: i64, y: i64| match div_exact(x, y).unwrap() {
            Value::Float(f) => f,
            _ => unreachable!(),
        };
        assert_eq!(f(9007199254740993, 3), 3002399751580331.0);
        assert_eq!(f(1152921504606846977, 3), 3.843071682022823e17);
        assert_eq!(f(543804029693342780, 509), 1068377268552736.2);
        assert_eq!(f(9007199254740993, 1), 9007199254740992.0);
        assert_eq!(f(-9007199254740993, 3), -3002399751580331.0);
        assert_eq!(f(0, -3), 0.0);
        assert!(f(0, -3).is_sign_negative());
        // Below 2**53 the plain f64 divide is already correctly rounded, so the
        // two must agree there — which is what makes the cheap path in
        // `num_binop` safe to keep.
        for x in 1i64..400 {
            for y in 1i64..400 {
                assert_eq!(f(x, y), x as f64 / y as f64, "{x}/{y}");
                assert_eq!(f(-x, y), -x as f64 / y as f64, "-{x}/{y}");
            }
        }
        // Exactly representable quotients stay exact at any width.
        for k in 0..62 {
            assert_eq!(f(1i64 << k, 1), (1u64 << k) as f64);
        }
    }
}
