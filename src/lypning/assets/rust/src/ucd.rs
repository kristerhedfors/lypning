//! `unicodedata` — `unidata_version`, `category`, `combining`,
//! `decomposition`, `normalize` and `is_normalized`, from the REFERENCE
//! CPython's own tables. Compiled into `lypning-l` under `cap-re`, whose
//! `route::CAPS` row lists the module, and into nothing smaller.
//!
//! **The data is the reference's, dumped at build time.** Every minor ships a
//! different Unicode (3.9/3.10 13.0.0, 3.11 14.0.0, 3.12 15.0.0, 3.13 15.1.0,
//! 3.14 16.0.0), and 3.13 began answering `decomposition()` for Hangul
//! syllables, so no table written down here could be right on more than one.
//! `build.rs` runs `ucd_dump.py` on the interpreter it probed, only when that
//! is the named reference (`err::REF_PY_EXACT`), and this file reads the dump
//! back with `include_bytes!`. A build with no dump has an empty table, and
//! `import unicodedata` refuses (`modules::import`).
//!
//! **The algorithm is UAX #15**, which is the same on every version: full
//! canonical or compatibility decomposition (Hangul algorithmically), the
//! stable canonical reorder by combining class, and canonical composition with
//! the blocking rule, the reference's own exclusions (two-character canonical
//! decompositions its NFC does not recompose) and Hangul LV/LVT.
//!
//! **Refused, never raised:** every argument CPython would reject — a non-str,
//! a str of any length but one, an unknown form, a keyword — because 3.14
//! rewrote those `TypeError`s; and every other module attribute (`name`,
//! `lookup`, `numeric`, `east_asian_width`, `ucd_3_2_0`, `__file__`, …).
//!
//! **Guards on the rest of the engine, while the module is imported.** `str`'s
//! own predicates and case mappings, `int()`/`float()` of a str and argument-
//! less `split`/`strip` read Rust's Unicode tables, which are not the
//! reference's; a program that has imported `unicodedata` is exactly the one
//! that will ask them about non-ASCII text, so [`drift_guard`] refuses that
//! when the versions differ. `chr()` of a surrogate, which CPython answers and
//! this engine's `str` cannot hold, refuses too ([`surrogate_refused`]).
//! Both are keyed on the import, so a program that never imports the module
//! runs exactly as the core runs it (invariant 10).

use crate::args::Args;
use crate::err::{unsupported, LypningError, R};
use crate::value::{ival, Value};
use std::cell::Cell;
use std::rc::Rc;
use std::sync::OnceLock;

/// `build.rs`'s dump of the reference's tables (`ucd_dump.py`), or empty.
static RAW: &[u8] = include_bytes!(concat!(env!("OUT_DIR"), "/ucd.bin"));

/// The names served; every other attribute refuses as `module-attr`.
pub const SERVED: &[&str] =
    &["category", "combining", "decomposition", "is_normalized", "normalize", "unidata_version"];

pub fn refuse(what: &str) -> LypningError {
    unsupported("unicodedata", what)
}

const S_BASE: u32 = 0xAC00;
const L_BASE: u32 = 0x1100;
const V_BASE: u32 = 0x1161;
const T_BASE: u32 = 0x11A7;
const L_COUNT: u32 = 19;
const V_COUNT: u32 = 21;
const T_COUNT: u32 = 28;
const N_COUNT: u32 = V_COUNT * T_COUNT;
const S_COUNT: u32 = L_COUNT * N_COUNT;

struct Ucd {
    version: String,
    /// `decomposition()` of a Hangul syllable is its full jamo sequence
    /// (3.13+), not `''`.
    hangul_decomposition: bool,
    cat_names: Vec<String>,
    /// `(first code point, value)` runs covering 0..=0x10FFFF.
    cats: Vec<(u32, u8)>,
    ccc: Vec<(u32, u8)>,
    tags: Vec<String>,
    /// `(cp, tag, offset, len)` into `pool`, sorted by cp; tag 0 is canonical.
    decomp: Vec<(u32, u8, u32, u8)>,
    pool: Vec<u32>,
    /// `((first, second), composite)`, sorted: the primary composites.
    compose: Vec<((u32, u32), u32)>,
}

struct Reader<'a> {
    b: &'a [u8],
    i: usize,
}

impl Reader<'_> {
    fn byte(&mut self) -> Option<u8> {
        let v = *self.b.get(self.i)?;
        self.i += 1;
        Some(v)
    }
    fn varint(&mut self) -> Option<u32> {
        let mut v: u32 = 0;
        for shift in (0..35).step_by(7) {
            let b = self.byte()?;
            v |= u32::from(b & 0x7F).checked_shl(shift)?;
            if b & 0x80 == 0 {
                return Some(v);
            }
        }
        None
    }
    fn text(&mut self) -> Option<String> {
        let n = self.varint()? as usize;
        let s = self.b.get(self.i..self.i + n)?;
        self.i += n;
        String::from_utf8(s.to_vec()).ok()
    }
    fn runs(&mut self) -> Option<Vec<(u32, u8)>> {
        let n = self.varint()?;
        let mut at = 0u32;
        let mut out = Vec::with_capacity(n as usize);
        for _ in 0..n {
            at = at.checked_add(self.varint()?)?;
            out.push((at, self.byte()?));
        }
        (out.first().map(|r| r.0) == Some(0)).then_some(out)
    }
}

fn parse(raw: &[u8]) -> Option<Ucd> {
    let mut r = Reader { b: raw.strip_prefix(b"UCD1")?, i: 0 };
    let version = r.text()?;
    let hangul_decomposition = match r.byte()? {
        0 => false,
        1 => true,
        _ => return None,
    };
    let cat_names = (0..r.varint()?).map(|_| r.text()).collect::<Option<Vec<_>>>()?;
    let cats = r.runs()?;
    if cats.iter().any(|c| usize::from(c.1) >= cat_names.len()) {
        return None;
    }
    let ccc = r.runs()?;
    let tags = (0..r.varint()?).map(|_| r.text()).collect::<Option<Vec<_>>>()?;
    let n = r.varint()?;
    let mut decomp = Vec::with_capacity(n as usize);
    let mut pool = Vec::new();
    let mut at = 0u32;
    for _ in 0..n {
        at = at.checked_add(r.varint()?)?;
        let tag = u8::try_from(r.varint()?).ok()?;
        if usize::from(tag) > tags.len() {
            return None;
        }
        let len = u8::try_from(r.varint()?).ok()?;
        let off = u32::try_from(pool.len()).ok()?;
        for _ in 0..len {
            pool.push(r.varint()?);
        }
        decomp.push((at, tag, off, len));
    }
    let mut excluded = Vec::new();
    let mut at = 0u32;
    for _ in 0..r.varint()? {
        at = at.checked_add(r.varint()?)?;
        excluded.push(at);
    }
    let mut compose: Vec<((u32, u32), u32)> = decomp
        .iter()
        .filter(|d| d.1 == 0 && d.3 == 2 && excluded.binary_search(&d.0).is_err())
        .map(|d| ((pool[d.2 as usize], pool[d.2 as usize + 1]), d.0))
        .collect();
    compose.sort_unstable();
    (r.i == r.b.len()).then_some(Ucd {
        version,
        hangul_decomposition,
        cat_names,
        cats,
        ccc,
        tags,
        decomp,
        pool,
        compose,
    })
}

fn table() -> Option<&'static Ucd> {
    static T: OnceLock<Option<Ucd>> = OnceLock::new();
    T.get_or_init(|| parse(RAW)).as_ref()
}

/// Is there a reference table at all? `import unicodedata` refuses without.
pub fn available() -> bool {
    table().is_some()
}

thread_local!(static IMPORTED: Cell<bool> = const { Cell::new(false) });

/// `import unicodedata` ran: from here the guards below apply.
pub fn imported() {
    IMPORTED.with(|i| i.set(true));
}

/// `chr()` of a lone surrogate, in a run that imported `unicodedata`: CPython
/// returns it (and `category` says `Cs`), and this engine's `str` cannot hold
/// it, so it refuses instead of raising. Elsewhere the core's answer stands.
pub fn surrogate_refused(n: i64) -> Option<LypningError> {
    ((0xD800..=0xDFFF).contains(&n) && IMPORTED.with(|i| i.get()))
        .then(|| refuse("chr() of a lone surrogate, which this engine's str cannot hold"))
}

/// A Unicode-property question about non-ASCII text — `str.isalpha()` and the
/// other predicates, the case mappings, argument-less `split`/`strip`,
/// `int()`/`float()` of a str — in a run that imported `unicodedata`, when the
/// reference's Unicode is not the one Rust's `char` tables were built from:
/// the two disagree on code points one version assigns and the other does
/// not, so it refuses. ASCII, and every run that never imported the module,
/// is answered as before.
pub fn drift_guard(s: &str) -> R<()> {
    if s.is_ascii() || !IMPORTED.with(|i| i.get()) {
        return Ok(());
    }
    let (a, b, c) = char::UNICODE_VERSION;
    match table() {
        Some(t) if t.version == format!("{a}.{b}.{c}") => Ok(()),
        _ => Err(refuse(
            "a Unicode property of non-ASCII text, whose tables here are not the reference's",
        )),
    }
}

/// The `str` methods [`drift_guard`] applies to; `split`/`strip` and their
/// siblings only with no argument (or `None`), which is when they ask
/// `isspace`.
pub fn drift_method(name: &str, args: &Args) -> bool {
    match name {
        "isalnum" | "isalpha" | "isdecimal" | "isdigit" | "isidentifier" | "islower" | "isnumeric"
        | "isprintable" | "isspace" | "istitle" | "isupper" | "upper" | "lower" | "casefold"
        | "title" | "swapcase" | "capitalize" => true,
        "split" | "rsplit" | "strip" | "lstrip" | "rstrip" => {
            matches!(args.first(), None | Some(Value::None))
        }
        _ => false,
    }
}

/// `unicodedata.<name>` as a value.
pub fn module_attr(name: &str) -> R<Value> {
    let t = table().ok_or_else(|| unsupported("module", "unicodedata: no reference UCD"))?;
    if name == "unidata_version" {
        return Ok(Value::Str(t.version.as_str().into()));
    }
    match SERVED.iter().find(|n| **n == name) {
        Some(n) => Ok(Value::Bound(Rc::new(Value::Module("unicodedata")), n)),
        None => Err(unsupported("module-attr", &format!("unicodedata.{name}"))),
    }
}

pub fn call(name: &str, args: &mut Args, kw: &[(Rc<str>, Value)]) -> R<Value> {
    let t = table().ok_or_else(|| unsupported("module", "unicodedata: no reference UCD"))?;
    if !kw.is_empty() {
        return Err(refuse(&format!("unicodedata.{name}() with a keyword")));
    }
    let want = if matches!(name, "normalize" | "is_normalized") { 2 } else { 1 };
    if args.len() != want {
        return Err(refuse(&format!("unicodedata.{name}() with {} arguments", args.len())));
    }
    let arg = |i: usize| match args.get(i) {
        Some(Value::Str(s)) => Ok(s.clone()),
        _ => Err(refuse(&format!("unicodedata.{name}() of anything but a str"))),
    };
    if want == 2 {
        let form = arg(0)?;
        let s = arg(1)?;
        let (compat, comp) = match form.as_ref() {
            "NFC" => (false, true),
            "NFD" => (false, false),
            "NFKC" => (true, true),
            "NFKD" => (true, false),
            _ => return Err(refuse("unicodedata.normalize() of an unknown form")),
        };
        let out = if s.is_ascii() { None } else { Some(t.normalize(&s, compat, comp)) };
        return Ok(match name {
            "normalize" => match out {
                Some(o) if o != *s => Value::Str(o.into()),
                _ => Value::Str(s),
            },
            _ => Value::Bool(out.map_or(true, |o| o == *s)),
        });
    }
    let s = arg(0)?;
    let mut it = s.chars();
    let (Some(c), None) = (it.next(), it.next()) else {
        return Err(refuse(&format!("unicodedata.{name}() of a str whose length is not 1")));
    };
    let cp = c as u32;
    Ok(match name {
        "category" => {
            let i = run(&t.cats, cp);
            Value::Str(t.cat_names[usize::from(i)].as_str().into())
        }
        "combining" => ival(i64::from(t.ccc(cp))),
        "decomposition" => Value::Str(t.decomposition(cp).into()),
        _ => return Err(unsupported("module-attr", &format!("unicodedata.{name}"))),
    })
}

/// The value of the run `cp` falls in.
fn run(runs: &[(u32, u8)], cp: u32) -> u8 {
    runs[runs.partition_point(|r| r.0 <= cp) - 1].1
}

impl Ucd {
    fn ccc(&self, cp: u32) -> u8 {
        run(&self.ccc, cp)
    }

    fn mapping(&self, cp: u32) -> Option<(u8, &[u32])> {
        let i = self.decomp.binary_search_by_key(&cp, |d| d.0).ok()?;
        let (_, tag, off, len) = self.decomp[i];
        Some((tag, &self.pool[off as usize..off as usize + usize::from(len)]))
    }

    /// CPython's `decomposition()` text: `<tag> ` and four-or-more-digit
    /// uppercase hex, space-separated; a Hangul syllable per the reference.
    fn decomposition(&self, cp: u32) -> String {
        let hex = |v: &[u32]| v.iter().map(|c| format!("{c:04X}")).collect::<Vec<_>>().join(" ");
        if (S_BASE..S_BASE + S_COUNT).contains(&cp) {
            if !self.hangul_decomposition {
                return String::new();
            }
            let mut v = Vec::new();
            hangul(cp, &mut v);
            return hex(&v);
        }
        match self.mapping(cp) {
            None => String::new(),
            Some((0, cps)) => hex(cps),
            Some((tag, cps)) => format!("{} {}", self.tags[usize::from(tag) - 1], hex(cps)),
        }
    }

    fn decompose(&self, cp: u32, compat: bool, out: &mut Vec<u32>) {
        if (S_BASE..S_BASE + S_COUNT).contains(&cp) {
            return hangul(cp, out);
        }
        if let Some((tag, cps)) = self.mapping(cp) {
            if tag == 0 || compat {
                for &c in cps {
                    self.decompose(c, compat, out);
                }
                return;
            }
        }
        out.push(cp);
    }

    fn pair(&self, a: u32, b: u32) -> Option<u32> {
        if (L_BASE..L_BASE + L_COUNT).contains(&a) && (V_BASE..V_BASE + V_COUNT).contains(&b) {
            return Some(S_BASE + ((a - L_BASE) * V_COUNT + (b - V_BASE)) * T_COUNT);
        }
        if (S_BASE..S_BASE + S_COUNT).contains(&a)
            && (a - S_BASE) % T_COUNT == 0
            && b > T_BASE
            && b < T_BASE + T_COUNT
        {
            return Some(a + (b - T_BASE));
        }
        let i = self.compose.binary_search_by_key(&(a, b), |e| e.0).ok()?;
        Some(self.compose[i].1)
    }

    fn normalize(&self, s: &str, compat: bool, comp: bool) -> String {
        let mut v = Vec::with_capacity(s.len());
        for c in s.chars() {
            self.decompose(c as u32, compat, &mut v);
        }
        // The canonical ordering: each run of non-starters, stably by class.
        let mut i = 0;
        while i < v.len() {
            if self.ccc(v[i]) == 0 {
                i += 1;
                continue;
            }
            let start = i;
            while i < v.len() && self.ccc(v[i]) != 0 {
                i += 1;
            }
            v[start..i].sort_by_key(|&c| self.ccc(c));
        }
        if comp {
            let mut out: Vec<u32> = Vec::with_capacity(v.len());
            let mut starter: Option<usize> = None;
            // The class of the last character kept since the starter, or none
            // when the starter is the last one kept.
            let mut last: Option<u8> = None;
            for c in v {
                let cc = self.ccc(c);
                if let Some(si) = starter {
                    // Blocked by a kept character of class 0 or >= this one's.
                    let blocked = last.is_some_and(|l| l == 0 || l >= cc);
                    if !blocked {
                        if let Some(p) = self.pair(out[si], c) {
                            out[si] = p;
                            continue;
                        }
                    }
                }
                if cc == 0 {
                    starter = Some(out.len());
                    last = None;
                } else {
                    last = Some(cc);
                }
                out.push(c);
            }
            v = out;
        }
        v.into_iter().filter_map(char::from_u32).collect()
    }
}

fn hangul(cp: u32, out: &mut Vec<u32>) {
    let s = cp - S_BASE;
    out.push(L_BASE + s / N_COUNT);
    out.push(V_BASE + (s % N_COUNT) / T_COUNT);
    if s % T_COUNT != 0 {
        out.push(T_BASE + s % T_COUNT);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_missing_or_torn_dump_is_no_table() {
        assert!(parse(b"").is_none());
        assert!(parse(b"UCD1").is_none());
        if !RAW.is_empty() {
            assert!(parse(&RAW[..RAW.len() - 1]).is_none());
            assert!(parse(RAW).is_some());
        }
    }

    #[test]
    fn the_served_names_are_what_get_attr_answers() {
        if table().is_none() {
            return;
        }
        let m = Value::Module("unicodedata");
        for n in SERVED {
            assert!(crate::modules::get_attr(&m, n).is_ok(), "{n}");
        }
        for n in ["name", "lookup", "numeric", "east_asian_width", "ucd_3_2_0", "__file__"] {
            assert!(crate::modules::get_attr(&m, n).is_err(), "{n}");
        }
    }
}
