//! `base64` — the whole of the `cap-base64` capability, compiled into
//! `lypning-l` and into nothing smaller. Every line of this file, and every
//! line that reaches it, is behind `cfg(feature = "cap-base64")`.
//!
//! **The invariant this module exists to hold: `b64decode` DISCARDS what is not
//! in the alphabet, and it is the discarding that decides the padding.** A
//! naive decoder raises on `b"a!Gk="` and on `b"aG k="`; CPython answers
//! `b'hi'` for both, because `binascii.a2b_base64` in its default non-strict
//! mode drops every byte outside the 64-character alphabet before it counts
//! anything. A decoder that got that wrong would raise where CPython answers —
//! exit 1, which the chain never retries — and, worse, would ANSWER where
//! CPython raises, at exit 0.
//!
//! **`Value::Bytes` in, `Value::Bytes` out. There is no new `Value` variant**,
//! so no arm of `ops.rs`, `fmt.rs`, `value.rs`, `json.rs`, `iter.rs` or
//! `builtins.rs` has to remember this capability exists. Five capabilities in a
//! row shipped a variant that reached print, `==`, hash, `in`, `len`, indexing,
//! slice assignment and `%`-formatting through an arm nobody remembered
//! (`docs/HILLCLIMB.md` iterations 74, 76 and 77). `bytes` is what CPython's
//! own functions return, so the exact shape is also the free one.
//!
//! # The decode rule, measured rather than recalled
//!
//! CPython 3.14.5's `a2b_base64(data, strict_mode=False)`, derived from 150,000
//! differential rows against this engine's own model on 2026-09-06 and pinned
//! in [`tests`] and in `tests/test_base64_grid.py`:
//!
//!   1. Let `data` be the subsequence of input bytes that are in the standard
//!      alphabet `A-Za-z0-9+/`. `=` is NOT in it, and neither is anything else:
//!      whitespace, punctuation, NUL and every byte above 127 are simply gone.
//!   2. `n = data.len()`, `rem = n % 4`. `rem == 0` decodes to `n / 4 * 3`
//!      bytes and always succeeds — `b"===="` is `b''` and `b"-_--"` is `b''`,
//!      because neither input has a single alphabet byte in it.
//!   3. `rem == 1` is an error whatever the padding: one leftover character
//!      carries six bits and a byte needs eight.
//!   4. `rem == 2` or `3` succeeds only if the input carries at least `4 - rem`
//!      `=` bytes AFTER ITS LAST ALPHABET BYTE, and then yields `rem - 1` extra
//!      bytes with the leftover bits dropped. This is the rule a reimplementation
//!      gets wrong: the pads are counted at the END of the input and not
//!      per-quad, so `b"AA==AA=="` is the four data characters `AAAA` and
//!      decodes to THREE bytes, while `b"aGk=aGk="` is the six data characters
//!      `aGkaGk`, has no trailing pad at all, and raises.
//!
//! Every error in 3 and 4 is a `binascii.Error` whose message text is CPython's
//! (`"Incorrect padding"`, `"Invalid base64-encoded string: number of data
//! characters (N) cannot be 1 more than a multiple of 4"`) and whose CLASS this
//! engine does not have. So it refuses — and [`data_block`]
//! decides it in the WALK for every literal, so the refusal lands before the
//! program starts rather than past a committed write barrier.
//!
//! # What is refused, and where each refusal is decided
//!
//! `altchars=`, `validate=` that is not literally falsy, a second positional
//! argument, a wrong argument count, a non-bytes literal handed to an encoder,
//! and a literal whose decode would raise are all decided STATICALLY, by
//! [`crate::route::base64_call_block`], from the walk. `route::base64_static_check`
//! runs that walk before the first statement of a `-c` run, so nothing here is
//! reached after a side effect at all — which is what kept these programs
//! answerable while `os.mkdir` still committed the barrier (`io.rs`, issue #51,
//! now fixed at the barrier itself).
//!
//! The walk decides the WHOLE program, so a base64 call the run would never
//! reach — one behind `if False:`, or the unused arm of a ternary — is decided
//! too, and such a program refuses where CPython answers. That is a coverage
//! loss and never a wrong answer: the chain hands it to CPython for one spawn.
//! `static_stop_check` has the identical property and for the identical reason,
//! and the alternative — deciding only what the run reaches — is the runtime
//! refusal, one spawn already spent, that this whole design exists to avoid.
//!
//! What is left at runtime is the residue no walk could read: an argument whose
//! VALUE is computed. That is one refusal kind (`base64`) and it is the
//! backstop, not the design — `tests/test_base64_grid.py::RUNTIME_BACKSTOP`
//! lists every shape that reaches it.
//!
//! `b16*`, `b32*`, `b32hex*`, `b85*`, `a85*`, `encodebytes`, `decodebytes`,
//! `standard_b64encode`/`standard_b64decode` and `binascii` are not served at
//! all: they refuse through `module-attr`, which `route::MODULE_ATTRS` makes a
//! static block in the CORE's walk — the binary that routes, and the one with
//! none of this file compiled into it.

use crate::args::Args;
use crate::err::{unsupported, LypningError, R};
use crate::value::Value;
use std::rc::Rc;

/// The names this module serves — `route::BASE64_SERVED` itself, not a copy of
/// it. The router answers the same question BEFORE this file is reached, out of
/// a table every variant carries, and two tables that must agree are one table.
use crate::route::BASE64_SERVED as SERVED;

pub fn refuse(what: &str) -> LypningError {
    unsupported("base64", what)
}

/// `base64.<name>` as a value. A served name is a bound module method; every
/// other name refuses with the kind the router blocks on statically.
pub fn module_attr(name: &str) -> R<Value> {
    match SERVED.iter().find(|n| **n == name) {
        Some(n) => Ok(Value::Bound(Rc::new(Value::Module("base64")), n)),
        None => Err(unsupported("module-attr", &format!("base64.{name}"))),
    }
}

/// The method names a `base64` program may reach that this engine answers on
/// no other type. Empty, and deliberately: every served function takes `bytes`
/// and answers `bytes`, so the only methods a caller uses afterwards are
/// `bytes`' own — which `route::known_method` already admits for every program.
/// The pathlib/re shape (`known_method` widened by an import) buys nothing here
/// and would widen the optimistic union for no reason.
pub fn call(_it: &mut crate::eval::Interp, name: &str, args: &mut Args, kw: &[(Rc<str>, Value)]) -> R<Value> {
    // Everything below is asked again by `route::base64_call_block` over a
    // literal, in the walk, so a program whose arguments the source spells is
    // refused before it starts. This is the backstop for the rest.
    let urlsafe = matches!(name, "urlsafe_b64encode" | "urlsafe_b64decode");
    let decoding = matches!(name, "b64decode" | "urlsafe_b64decode");
    if args.is_empty() {
        return Err(refuse(&format!("base64.{name}() with no argument")));
    }
    if args.len() > 1 {
        // The second positional is `altchars` on the two standard functions and
        // a TypeError on the urlsafe pair. Both refuse: `altchars` is a second
        // alphabet this engine does not implement, and the TypeError's wording
        // is CPython's.
        return Err(refuse(&format!("base64.{name}() with extra positional arguments")));
    }
    for (k, v) in kw {
        match crate::route::base64_kw_block(name, k, matches!(v, Value::None), falsy(v)) {
            Some(why) => return Err(refuse(&why)),
            None => {}
        }
    }
    let arg = args.first().expect("checked non-empty above");
    if decoding {
        let data = decode_input(name, arg)?;
        match data_block(&data, urlsafe) {
            Some(why) => Err(refuse(why)),
            None => Ok(Value::Bytes(Rc::new(decode(&data, urlsafe)))),
        }
    } else {
        let Value::Bytes(b) = arg else {
            // CPython: `TypeError: a bytes-like object is required, not 'str'`.
            // Refused rather than raised — the wording is CPython's, and a
            // `str` here is the single most common way an agent's one-liner
            // gets this call wrong.
            return Err(refuse(&format!(
                "base64.{name}() over a {} (CPython requires a bytes-like object)",
                crate::value::type_name(arg)
            )));
        };
        Ok(Value::Bytes(Rc::new(encode(b, urlsafe))))
    }
}

/// Is this keyword's value falsy — the `validate=` question, and ONLY that one.
///
/// `validate` reaches C through a `bool` converter that calls `PyObject_IsTrue`,
/// so `0` and `None` are `False` there exactly as `False` is, and all three are
/// served. `altchars=` asks a different question and gets a different predicate
/// (`matches!(v, Value::None)`, at the call above): its default is ABSENT, and
/// `None` is the only value that spells absent, because CPython's test is
/// `if altchars is not None`. A PRESENT falsy `altchars` is a value CPython
/// REJECTS — `TypeError` for `0` and `False`, `AssertionError` for `b""` and
/// `""` — and answering it here was a wrong answer at exit 0.
/// `route::base64_kw_block` holds both rules and the measurement.
fn falsy(v: &Value) -> bool {
    matches!(v, Value::None | Value::Bool(false)) || matches!(v, Value::Int(i) if i.is_zero())
}

/// `base64._bytes_from_decode_data`, minus the two error paths it owns.
///
/// A `str` IS accepted by every decoder — that is not a coercion this engine
/// invented, it is `s.encode('ascii')` in `base64.py`, and the corpus reaches
/// it through `json.load(...)['content']`, which is always a `str`. A non-ASCII
/// `str` raises `ValueError('string argument should contain only ASCII
/// characters')` and anything that is not bytes-like raises a `TypeError`;
/// both messages are CPython's, so both refuse.
fn decode_input(name: &str, v: &Value) -> R<Vec<u8>> {
    match v {
        Value::Bytes(b) => Ok(b.as_ref().clone()),
        Value::Str(s) if s.is_ascii() => Ok(s.as_bytes().to_vec()),
        Value::Str(_) => Err(refuse(&format!(
            "base64.{name}() over a str holding a non-ASCII character (CPython raises a ValueError this engine does not word)"
        ))),
        v => Err(refuse(&format!(
            "base64.{name}() over a {} (CPython requires a bytes-like object or an ASCII str)",
            crate::value::type_name(v)
        ))),
    }
}

// ---- the alphabet ----------------------------------------------------------

const STD: &[u8; 64] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
const URL: &[u8; 64] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_";

/// This byte's 6-bit value in the STANDARD alphabet, or `None`.
///
/// The urlsafe pair does NOT get an alphabet of its own here, and that is
/// CPython's shape rather than a shortcut: `urlsafe_b64decode` translates `-`
/// to `+` and `_` to `/` and then calls `b64decode`, so `+` and `/` remain
/// valid input to it — `urlsafe_b64decode(b"+/++")` answers the same three
/// bytes `b"-_--"` does. [`translate`] is that step, applied before this one.
fn sextet(c: u8) -> Option<u8> {
    match c {
        b'A'..=b'Z' => Some(c - b'A'),
        b'a'..=b'z' => Some(c - b'a' + 26),
        b'0'..=b'9' => Some(c - b'0' + 52),
        b'+' => Some(62),
        b'/' => Some(63),
        _ => None,
    }
}

/// `s.translate(bytes.maketrans(b'-_', b'+/'))`, the whole of what
/// `urlsafe_b64decode` does before delegating.
fn translate(data: &[u8]) -> std::borrow::Cow<'_, [u8]> {
    if !data.iter().any(|c| matches!(c, b'-' | b'_')) {
        return std::borrow::Cow::Borrowed(data);
    }
    std::borrow::Cow::Owned(
        data.iter()
            .map(|c| match c {
                b'-' => b'+',
                b'_' => b'/',
                c => *c,
            })
            .collect(),
    )
}

/// The two numbers the padding rule is decided from: how many alphabet bytes
/// the input holds, and how many `=` bytes follow the LAST of them.
///
/// Both are properties of the whole input rather than of any quad, which is the
/// half a per-quad decoder gets wrong. Shared by [`decode`] and by
/// [`data_block`], so the walk and the run cannot disagree
/// about whether an input decodes.
pub fn shape(data: &[u8]) -> (usize, usize) {
    let mut n = 0usize;
    let mut pads = 0usize;
    for c in data {
        if sextet(*c).is_some() {
            n += 1;
            pads = 0;
        } else if *c == b'=' {
            pads += 1;
        }
    }
    (n, pads)
}

/// Would CPython's `b64decode` RAISE on this input, and why?
///
/// `None` is "it decodes"; `Some(why)` is the refusal line, and both the WALK
/// (`route::base64_call_block`, over a literal) and the run (`call` above, over
/// a computed value) get their answer from this one function, so they cannot
/// disagree about whether a program is served.
///
/// The two failures are `binascii.Error`s whose message text is CPython's and
/// whose CLASS this engine does not have — so this is a refusal and never a
/// raise. The detail says what CPython would do rather than quoting it.
pub fn data_block(data: &[u8], urlsafe: bool) -> Option<&'static str> {
    let data = if urlsafe { translate(data) } else { std::borrow::Cow::Borrowed(data) };
    let (n, pads) = shape(&data);
    match n % 4 {
        0 => None,
        // Six bits cannot make a byte, whatever follows them.
        1 => Some(
            "b64decode() over data whose alphabet characters are 1 more than a multiple of 4 \
             (CPython raises a binascii.Error this engine does not word)",
        ),
        rem if pads >= 4 - rem => None,
        _ => Some(
            "b64decode() over data with incorrect padding (CPython raises a binascii.Error \
             this engine does not word)",
        ),
    }
}

/// The decode itself, for an input [`data_block`] has already said is
/// decodable. Leftover bits at the end are DROPPED, which is
/// why `b"aa=="` and `b"ab=="` are both `b'i'`.
fn decode(data: &[u8], urlsafe: bool) -> Vec<u8> {
    let data = if urlsafe { translate(data) } else { std::borrow::Cow::Borrowed(data) };
    let mut out = Vec::with_capacity(data.len() / 4 * 3 + 2);
    let mut acc: u32 = 0;
    let mut bits = 0u32;
    for c in data.iter() {
        let Some(v) = sextet(*c) else { continue };
        acc = (acc << 6) | v as u32;
        bits += 6;
        if bits >= 8 {
            bits -= 8;
            out.push(((acc >> bits) & 0xFF) as u8);
        }
    }
    out
}

/// `binascii.b2a_base64(data, newline=False)` — always padded to a multiple of
/// four, and never line-wrapped, which is the difference between this and
/// `base64.encodebytes` (not served).
fn encode(data: &[u8], urlsafe: bool) -> Vec<u8> {
    let a = if urlsafe { URL } else { STD };
    let mut out = Vec::with_capacity((data.len() + 2) / 3 * 4);
    for chunk in data.chunks(3) {
        let b0 = chunk[0] as u32;
        let b1 = *chunk.get(1).unwrap_or(&0) as u32;
        let b2 = *chunk.get(2).unwrap_or(&0) as u32;
        let n = (b0 << 16) | (b1 << 8) | b2;
        out.push(a[(n >> 18) as usize & 63]);
        out.push(a[(n >> 12) as usize & 63]);
        out.push(if chunk.len() > 1 { a[(n >> 6) as usize & 63] } else { b'=' });
        out.push(if chunk.len() > 2 { a[n as usize & 63] } else { b'=' });
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    /// The whole capability, as the four numbers the rule is stated in.
    #[test]
    fn the_pad_count_is_the_inputs_and_not_the_quads() {
        // Four data characters, two pad runs, THREE bytes out.
        assert_eq!(decode(b"AA==AA==", false), vec![0, 0, 0]);
        // Six data characters and ONE trailing pad, where a quad boundary
        // needs two: not decodable. The per-quad decoder sees two padded
        // groups here and answers `b'hihi'`.
        assert_eq!(shape(b"aGk=aGk="), (6, 1));
        assert!(data_block(b"aGk=aGk=", false).is_some());
        // …and the pad count is reset by a later alphabet byte, which is the
        // half "count the pads" gets wrong: `AA==A` is three data characters
        // whose trailing pad run is EMPTY.
        assert_eq!(shape(b"AA==A"), (3, 0));
        assert!(data_block(b"AA==A", false).is_some());
        assert_eq!(shape(b"aa=\n="), (2, 2));
        assert_eq!(decode(b"aa=\n=", false), b"i".to_vec());
        // Three data characters and two pads, which is one more than needed.
        assert_eq!(decode(b"aGk==", false), b"hi".to_vec());
        // Everything outside the alphabet is gone before anything is counted.
        assert_eq!(decode(b"a!G k=", false), b"hi".to_vec());
        assert_eq!(shape(b"-_--"), (0, 0));
        assert_eq!(decode(b"-_--", false), Vec::<u8>::new());
        assert_eq!(decode(b"-_--", true), vec![0xfb, 0xff, 0xbe]);
    }

    #[test]
    fn a_round_trip_over_every_length_up_to_a_quad_boundary() {
        for n in 0..64usize {
            let raw: Vec<u8> = (0..n).map(|i| (i * 37 + 11) as u8).collect();
            let e = encode(&raw, false);
            assert_eq!(e.len() % 4, 0, "{n}");
            assert!(data_block(&e, false).is_none(), "{n}");
            assert_eq!(decode(&e, false), raw, "{n}");
            let u = encode(&raw, true);
            assert!(!u.contains(&b'+') && !u.contains(&b'/'), "{n}");
            assert_eq!(decode(&u, true), raw, "{n}");
        }
    }

    /// `route::BASE64_SERVED` is read by the CORE, which has none of this file
    /// compiled in — so the one thing that can keep it honest is this: the
    /// variant that DOES have `module_attr` holds the two lists to each other.
    #[test]
    fn the_route_table_names_exactly_what_is_served() {
        let table = crate::route::MODULE_ATTRS
            .iter()
            .find(|(m, _)| *m == "base64")
            .expect("route::MODULE_ATTRS has no base64 row")
            .1;
        for name in table {
            assert!(module_attr(name).is_ok(), "route claims base64.{name} and base64.rs refuses it");
        }
        for name in SERVED {
            assert!(table.contains(name), "base64.rs serves base64.{name} and route::MODULE_ATTRS omits it");
        }
        assert!(table.windows(2).all(|w| w[0] < w[1]), "route::MODULE_ATTRS base64 row is unsorted");
    }
}
