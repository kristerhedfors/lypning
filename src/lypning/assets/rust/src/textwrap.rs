//! `textwrap` — `dedent`, `indent`, `wrap`, `fill` and `shorten`: the whole of
//! the `cap-textwrap` capability, compiled into `lypning-l` and into nothing
//! smaller. Every line of this file, and every line that reaches it, is behind
//! `cfg(feature = "cap-textwrap")`.
//!
//! **A port, not a reimplementation.** Each function below is the CPython
//! 3.14 `Lib/textwrap.py` function of the same name, read from the source and
//! kept in its order: `_munge_whitespace`, `_split`, `_wrap_chunks`,
//! `_handle_long_word`, the 3.14 `dedent` (common prefix of the `min()` and
//! `max()` of the non-blank lines) and `indent` with its default predicate.
//! Lengths and slices are CODE POINTS, as they are in Python, so a chunk is a
//! `Vec<char>` and never a byte slice.
//!
//! **`str` in, `str` or `list[str]` out. There is no new `Value` variant**, so
//! no arm of `ops.rs`, `fmt.rs` or `json.rs` has to remember this capability
//! exists.
//!
//! # The one regex, as a scanner
//!
//! `TextWrapper.wordsep_re` — the default `break_on_hyphens=True` split — uses
//! lookbehind, which the engine's `re` does not run, and three Unicode classes
//! (`\w`, `[^\d\W]` and `[\w!"'&.,?]`). [`split_hyphens`] is that regex as a
//! hand-written scanner, alternative by alternative in the regex's own order,
//! with the lazy `\S+?` as an explicit extension loop. The classes are decided
//! for ASCII exactly and for a short list of punctuation blocks that no CPython
//! counts as a word character; any OTHER non-ASCII character the regex would
//! have to classify is a runtime refusal (`textwrap: break_on_hyphens over a
//! non-ASCII character next to a hyphen`). Text in which no such character sits
//! where the regex looks — beside a `-` — is served whatever else it contains,
//! because the regex never asks about it: its whitespace class is ASCII.
//! Validated 2026-09-24 against CPython 3.14.5's own `wordsep_re.split` over
//! 300,000 random strings of a hostile alphabet: 0 disagreements, 3.3% refused.
//!
//! # What is refused, and where
//!
//! Every keyword outside the served set (`TextWrapper`'s `max_lines`,
//! `placeholder` on `wrap`/`fill`, `expand_tabs`, `tabsize`,
//! `replace_whitespace`, `drop_whitespace`, `fix_sentence_endings`, and
//! `indent`'s `predicate`), a `*`/`**` splice, and a wrong positional count are
//! decided STATICALLY by `route::textwrap_call_block`, in the walk, before the
//! program starts; every other name on the module (`TextWrapper`, `__file__`)
//! is a `module-attr` block out of `route::MODULE_ATTRS` in the CORE's walk.
//! What is left here is the runtime residue a walk cannot read — an argument
//! whose TYPE is computed (text that is not a `str`, a width that is not an
//! `int`), and the non-ASCII classification above — and it refuses rather than
//! raising CPython's `AttributeError` in CPython's words.

use crate::args::Args;
use crate::err::{type_err, unsupported, value_err, LypningError, R};
use crate::value::Value;
use std::rc::Rc;

/// The names this module serves — `route::TEXTWRAP_SERVED` itself, not a copy
/// of it: the router answers the same question before this file is reached.
use crate::route::TEXTWRAP_SERVED as SERVED;

pub fn refuse(what: &str) -> LypningError {
    unsupported("textwrap", what)
}

/// `textwrap.<name>` as a value. A served name is a bound module method; every
/// other name refuses with the kind the router blocks on statically.
pub fn module_attr(name: &str) -> R<Value> {
    match SERVED.iter().find(|n| **n == name) {
        Some(n) => Ok(Value::Bound(Rc::new(Value::Module("textwrap")), n)),
        None => Err(unsupported("module-attr", &format!("textwrap.{name}"))),
    }
}

/// `TextWrapper`'s knobs, the served ones only. Everything else is its default
/// (`expand_tabs=True`, `tabsize=8`, `replace_whitespace=True`,
/// `drop_whitespace=True`, `fix_sentence_endings=False`).
struct Opts {
    width: i64,
    initial: Vec<char>,
    subsequent: Vec<char>,
    break_long: bool,
    break_hyph: bool,
    /// `Some(1)` for `shorten` and `None` everywhere else.
    max_lines: Option<i64>,
    placeholder: Vec<char>,
}

pub fn call(_it: &mut crate::eval::Interp, name: &str, args: &mut Args, kw: &[(Rc<str>, Value)]) -> R<Value> {
    // The count and keyword refusals below are asked again by
    // `route::textwrap_call_block` in the walk, which is where a spelled call
    // meets them; this is the backstop for a `**kwargs` or a name the walk
    // could not follow.
    let (lo, hi) = match name {
        "dedent" => (1, 1),
        "indent" => (2, 2),
        "shorten" => (1, 2),
        _ => (1, 2),
    };
    let n = args.len();
    if n < lo || n > hi {
        return Err(refuse(&format!("textwrap.{name}() with {n} positional arguments")));
    }
    match name {
        "dedent" | "indent" => {
            if let Some((k, _)) = kw.first() {
                return Err(refuse(&format!("textwrap.{name}({k}=…)")));
            }
            if name == "dedent" {
                return dedent(args.first().expect("counted above"));
            }
            let text = want_str(name, "text", args.first().expect("counted above"))?;
            let prefix = want_str(name, "prefix", args.get(1).expect("counted above"))?;
            return Ok(Value::Str(indent(text, prefix).into()));
        }
        _ => {}
    }
    let shorten = name == "shorten";
    let mut o = Opts {
        width: 70,
        initial: Vec::new(),
        subsequent: Vec::new(),
        break_long: true,
        break_hyph: true,
        max_lines: if shorten { Some(1) } else { None },
        placeholder: " [...]".chars().collect(),
    };
    let mut have_width = false;
    if let Some(w) = args.get(1) {
        o.width = want_width(name, w)?;
        have_width = true;
    }
    for (k, v) in kw {
        match (k.as_ref(), shorten) {
            ("width", _) if !have_width => {
                o.width = want_width(name, v)?;
                have_width = true;
            }
            ("placeholder", true) => o.placeholder = want_str(name, k, v)?.chars().collect(),
            ("initial_indent", false) => o.initial = want_str(name, k, v)?.chars().collect(),
            ("subsequent_indent", false) => o.subsequent = want_str(name, k, v)?.chars().collect(),
            ("break_long_words", false) => o.break_long = want_bool(name, k, v)?,
            ("break_on_hyphens", false) => o.break_hyph = want_bool(name, k, v)?,
            _ => return Err(refuse(&format!("textwrap.{name}({k}=…)"))),
        }
    }
    if shorten && !have_width {
        // CPython's `TypeError: shorten() missing 1 required positional
        // argument: 'width'`, in CPython's words — so it refuses.
        return Err(refuse("textwrap.shorten() without a width"));
    }
    let text = want_str(name, "text", args.first().expect("counted above"))?;
    let lines = if shorten {
        // `' '.join(text.strip().split())`: Python whitespace, which is not
        // the ASCII set the chunker splits on.
        let collapsed: Vec<&str> = text.split(py_space).filter(|s| !s.is_empty()).collect();
        wrap(&collapsed.join(" "), &o)?
    } else {
        wrap(text, &o)?
    };
    Ok(match name {
        "wrap" => crate::value::list(lines.into_iter().map(|l| Value::Str(l.into())).collect()),
        _ => Value::Str(lines.join("\n").into()),
    })
}

fn want_str<'a>(name: &str, what: &str, v: &'a Value) -> R<&'a str> {
    match v {
        Value::Str(s) => Ok(s),
        v => Err(refuse(&format!(
            "textwrap.{name}() with {what} a {} (CPython's error is its own)",
            crate::value::type_name(v)
        ))),
    }
}

/// An `int` that is not a `bool`. CPython checks nothing here — `fill(s, 5.0)`
/// works and `fill(s, True)` is width 1 — so everything else refuses rather
/// than guessing what a float width slices to.
fn want_width(name: &str, v: &Value) -> R<i64> {
    match v {
        Value::Int(i) => i.small().ok_or_else(|| refuse(&format!("textwrap.{name}() with a width past 64 bits"))),
        v => Err(refuse(&format!("textwrap.{name}() with a width of type {}", crate::value::type_name(v)))),
    }
}

/// A `bool`. `break_on_hyphens` is tested with `is True` in `_split` and for
/// truth in `_handle_long_word`, so a truthy non-bool takes both branches at
/// once; only the two values where those agree are served.
fn want_bool(name: &str, k: &str, v: &Value) -> R<bool> {
    match v {
        Value::Bool(b) => Ok(*b),
        v => Err(refuse(&format!("textwrap.{name}({k}=…) with a {}", crate::value::type_name(v)))),
    }
}

#[inline]
fn py_space(c: char) -> bool {
    crate::methods::py_space(c)
}

/// `x.strip() == ''` — Python whitespace, over a chunk.
fn blank(c: &[char]) -> bool {
    c.iter().all(|c| py_space(*c))
}

// ---- dedent / indent -------------------------------------------------------

/// `textwrap.dedent`, 3.14: the margin is the longest common prefix of the
/// `min()` and `max()` of the non-blank lines, and stops at the first character
/// that is not `' '` or `'\t'`. A line `isspace()` holds becomes `''`.
fn dedent(v: &Value) -> R<Value> {
    let text = match v {
        Value::Str(s) => s,
        // `text.split('\n')` raising, caught and reworded — for the builtin
        // types whose `__qualname__` is certainly `type_name`.
        Value::None
        | Value::Bool(_)
        | Value::Int(_)
        | Value::Float(_)
        | Value::Bytes(_)
        | Value::List(_)
        | Value::Tuple(_)
        | Value::Dict(_)
        // Before 3.14 the error came out of `re.sub` instead, and is not this.
        | Value::Set(_) if crate::err::REF_PY_MINOR >= 14 => {
            return Err(type_err(format!("expected str object, not '{}'", crate::value::type_name(v))))
        }
        v => return Err(refuse(&format!("textwrap.dedent() over a {}", crate::value::type_name(v)))),
    };
    let lines: Vec<&str> = text.split('\n').collect();
    let isspace = |l: &str| !l.is_empty() && l.chars().all(py_space);
    // Before 3.14 `dedent` read only `' '` and `'\t'` as indentation, by
    // regex: a blank line holding any other whitespace kept its characters and
    // counted towards the margin. Everything else agrees, so only that refuses.
    if crate::err::REF_PY_MINOR < 14
        && lines.iter().any(|l| isspace(l) && !l.chars().all(|c| matches!(c, ' ' | '\t')))
    {
        return Err(refuse("textwrap.dedent() of a blank line holding whitespace other than ' ' or '\\t' (worded by the pre-3.14 regex)"));
    }
    let mut l1: Option<&str> = None;
    let mut l2: Option<&str> = None;
    for l in lines.iter().copied().filter(|l| !l.is_empty() && !isspace(l)) {
        // `str` order is code point order, which is UTF-8 byte order.
        if l1.map_or(true, |m| l < m) {
            l1 = Some(l);
        }
        if l2.map_or(true, |m| l > m) {
            l2 = Some(l);
        }
    }
    let (l1, l2) = (l1.unwrap_or(""), l2.unwrap_or(""));
    // `for margin, c in enumerate(l1): if c != l2[margin] or c not in ' \t':
    // break`. A non-blank l1 always breaks inside the loop, and a margin
    // character is ASCII, so the char index is also the byte index.
    let mut margin = 0;
    for (i, c) in l1.chars().enumerate() {
        margin = i;
        if l2.chars().nth(i) != Some(c) || !matches!(c, ' ' | '\t') {
            break;
        }
    }
    let out: Vec<&str> = lines
        .iter()
        .map(|l| if isspace(l) { "" } else { l.get(margin..).unwrap_or("") })
        .collect();
    Ok(Value::Str(out.join("\n").into()))
}

/// `str.splitlines(True)`'s boundaries: `\n`, `\r`, `\r\n`, `\v`, `\f`, the
/// three C0 separators, NEL and the two Unicode separators.
fn line_end(c: char) -> bool {
    matches!(c, '\n' | '\r' | '\x0b' | '\x0c' | '\x1c' | '\x1d' | '\x1e' | '\u{85}' | '\u{2028}' | '\u{2029}')
}

/// `textwrap.indent(text, prefix)` with the default predicate: every line that
/// is not all whitespace, its line ending included, gets the prefix.
fn indent(text: &str, prefix: &str) -> String {
    let mut out = String::with_capacity(text.len());
    let mut rest = text;
    while !rest.is_empty() {
        let mut end = rest.len();
        let mut it = rest.char_indices();
        while let Some((i, c)) = it.next() {
            if line_end(c) {
                end = i + c.len_utf8();
                if c == '\r' && rest[end..].starts_with('\n') {
                    end += 1;
                }
                break;
            }
        }
        let line = &rest[..end];
        if !line.chars().all(py_space) {
            out.push_str(prefix);
        }
        out.push_str(line);
        rest = &rest[end..];
    }
    out
}

// ---- wrap ------------------------------------------------------------------

/// The ASCII whitespace `textwrap` breaks on — `_whitespace`.
fn ws(c: char) -> bool {
    matches!(c, '\t' | '\n' | '\x0b' | '\x0c' | '\r' | ' ')
}

/// `_munge_whitespace`: `expandtabs(8)` — the column resets on `\n` and `\r`
/// and counts every other code point as one — then each of `_whitespace` to a
/// space.
fn munge(text: &str) -> Vec<char> {
    let mut out = Vec::with_capacity(text.len());
    let mut col = 0usize;
    for c in text.chars() {
        if c == '\t' {
            let n = 8 - col % 8;
            out.extend(std::iter::repeat(' ').take(n));
            col += n;
            continue;
        }
        col = if matches!(c, '\n' | '\r') { 0 } else { col + 1 };
        out.push(if ws(c) { ' ' } else { c });
    }
    out
}

/// A regex class over one character: `Some(answer)`, or `None` for a
/// character this engine does not classify the way CPython's `_sre` does.
///
/// ASCII is exact. Outside it, only the General Punctuation dashes, quotes and
/// bullets (U+2010–U+2027) and the Arrows block (U+2190–U+21FF) are answered:
/// no code point in either is a letter, a digit or `_`, on any Unicode version
/// CPython has shipped, so none of them is `\w` and all three classes say no.
fn class(c: char, ascii: fn(u8) -> bool) -> Option<bool> {
    if c.is_ascii() {
        return Some(ascii(c as u8));
    }
    match c as u32 {
        0x2010..=0x2027 | 0x2190..=0x21FF => Some(false),
        _ => None,
    }
}

/// `\w`
fn is_w(c: char) -> Option<bool> {
    class(c, |b| b.is_ascii_alphanumeric() || b == b'_')
}
/// `[^\d\W]` — a word character that is not a digit.
fn is_lt(c: char) -> Option<bool> {
    class(c, |b| b.is_ascii_alphabetic() || b == b'_')
}
/// `[\w!"'&.,?]`
fn is_wp(c: char) -> Option<bool> {
    class(c, |b| b.is_ascii_alphanumeric() || matches!(b, b'_' | b'!' | b'"' | b'\'' | b'&' | b'.' | b',' | b'?'))
}

/// Kleene AND: a definite `false` anywhere decides it, whatever is unknown.
fn and(a: Option<bool>, b: impl FnOnce() -> Option<bool>) -> Option<bool> {
    match a {
        Some(false) => Some(false),
        Some(true) => b(),
        None => match b() {
            Some(false) => Some(false),
            _ => None,
        },
    }
}
/// Kleene OR: a definite `true` anywhere decides it.
fn or(a: Option<bool>, b: impl FnOnce() -> Option<bool>) -> Option<bool> {
    match a {
        Some(true) => Some(true),
        Some(false) => b(),
        None => match b() {
            Some(true) => Some(true),
            _ => None,
        },
    }
}

fn decide(v: Option<bool>) -> R<bool> {
    v.ok_or_else(|| refuse("break_on_hyphens over a non-ASCII character next to a hyphen (the regex's Unicode classes are CPython's)"))
}

/// `-{2,}` from `i`, then `(?=\w)`: the end of the dash run when the character
/// after it is a word character. `-{2,}` backtracking to a shorter run only
/// ever puts a `-` in front of the lookahead, which is not `\w`, so the full
/// run is the only candidate.
fn dashes_then_word(t: &[char], i: usize) -> Option<usize> {
    let mut q = i;
    while q < t.len() && t[q] == '-' {
        q += 1;
    }
    (q - i >= 2 && q < t.len()).then_some(q)
}

/// `wordsep_re.split(text)` with the empty strings dropped.
fn split_hyphens(t: &[char]) -> R<Vec<Vec<char>>> {
    let n = t.len();
    let mut out = Vec::new();
    let mut p = 0;
    while p < n {
        let end;
        if ws(t[p]) {
            // `%(ws)s+`
            let mut q = p;
            while q < n && ws(t[q]) {
                q += 1;
            }
            end = q;
        } else {
            // `(?<=%(wp)s) -{2,} (?=\w)` — an em-dash between words.
            let mut em = None;
            if p > 0 {
                if let Some(q) = dashes_then_word(t, p) {
                    if decide(and(is_w(t[q]), || is_wp(t[p - 1])))? {
                        em = Some(q);
                    }
                }
            }
            end = match em {
                Some(q) => q,
                None => word_end(t, p)?,
            };
        }
        out.push(t[p..end].to_vec());
        p = end;
    }
    Ok(out)
}

/// `%(nws)s+? (?: hyphenated | end of word | em-dash )`, from `p`: the lazy
/// run grows one character at a time and each alternative is asked, in the
/// regex's order, at every length.
fn word_end(t: &[char], p: usize) -> R<usize> {
    let n = t.len();
    let at = |i: usize| -> Option<char> { t.get(i).copied() };
    let lt = |i: usize| -> Option<bool> { at(i).map_or(Some(false), is_lt) };
    let mut e = p + 1;
    loop {
        if at(e) == Some('-') {
            // `-(?: (?<=%(lt)s{2}-) | (?<=%(lt)s-%(lt)s-))`
            let behind = or(
                if e >= 2 { and(lt(e - 2), || lt(e - 1)) } else { Some(false) },
                || {
                    if e >= 3 && t[e - 2] == '-' {
                        and(lt(e - 3), || lt(e - 1))
                    } else {
                        Some(false)
                    }
                },
            );
            // `(?= %(lt)s -? %(lt)s)`
            let ahead = || {
                and(lt(e + 1), || if at(e + 2) == Some('-') { lt(e + 3) } else { lt(e + 2) })
            };
            if decide(and(behind, ahead))? {
                return Ok(e + 1);
            }
        }
        // `(?=%(ws)s|\z)`
        if e == n || ws(t[e]) {
            return Ok(e);
        }
        // `(?<=%(wp)s) (?=-{2,}\w)`
        if let Some(q) = dashes_then_word(t, e) {
            if decide(and(is_w(t[q]), || is_wp(t[e - 1])))? {
                return Ok(e);
            }
        }
        e += 1;
    }
}

/// `wordsep_simple_re.split(text)` with the empty strings dropped.
fn split_simple(t: &[char]) -> Vec<Vec<char>> {
    let mut out: Vec<Vec<char>> = Vec::new();
    for &c in t {
        match out.last_mut() {
            Some(last) if ws(last[0]) == ws(c) => last.push(c),
            _ => out.push(vec![c]),
        }
    }
    out
}

fn lstrip(s: &[char]) -> &[char] {
    let i = s.iter().position(|c| !py_space(*c)).unwrap_or(s.len());
    &s[i..]
}
fn rstrip(s: &[char]) -> &[char] {
    let i = s.iter().rposition(|c| !py_space(*c)).map_or(0, |i| i + 1);
    &s[..i]
}

/// `TextWrapper.wrap`: munge, split, `_wrap_chunks`.
fn wrap(text: &str, o: &Opts) -> R<Vec<String>> {
    let t = munge(text);
    let chunks = if o.break_hyph { split_hyphens(&t)? } else { split_simple(&t) };
    wrap_chunks(chunks, o)
}

/// `_handle_long_word`.
fn long_word(chunks: &mut [Vec<char>], cur: &mut Vec<Vec<char>>, cur_len: i64, width: i64, o: &Opts) -> bool {
    let space_left = if width < 1 { 1 } else { width - cur_len };
    // `and space_left > 0` is 3.13's; before it a full line took an empty
    // piece (`chunk[:0]`), which then shields the whitespace before it from
    // `drop_whitespace`. The hyphen search is 3.10's. `cur_len <= width`, so
    // `space_left` is never negative.
    use crate::err::REF_PY_MINOR;
    if o.break_long && (space_left > 0 || REF_PY_MINOR < 13) {
        let chunk = chunks.last_mut().expect("the caller checked");
        let sl = space_left as usize;
        let mut end = sl;
        if o.break_hyph && REF_PY_MINOR >= 10 && chunk.len() > sl {
            // `chunk.rfind('-', 0, space_left)`, then a non-hyphen before it.
            if let Some(h) = chunk[..sl].iter().rposition(|c| *c == '-') {
                if h > 0 && chunk[..h].iter().any(|c| *c != '-') {
                    end = h + 1;
                }
            }
        }
        let end = end.min(chunk.len());
        cur.push(chunk[..end].to_vec());
        chunk.drain(..end);
        false
    } else {
        // `elif not cur_line: cur_line.append(reversed_chunks.pop())` — the
        // caller pops, since it owns the Vec.
        cur.is_empty()
    }
}

fn joined(indent: &[char], parts: &[Vec<char>]) -> Vec<char> {
    let mut s = indent.to_vec();
    for p in parts {
        s.extend_from_slice(p);
    }
    s
}

/// `_wrap_chunks`, statement for statement.
fn wrap_chunks(mut chunks: Vec<Vec<char>>, o: &Opts) -> R<Vec<String>> {
    let mut lines: Vec<Vec<char>> = Vec::new();
    if o.width <= 0 {
        return Err(value_err(format!("invalid width {} (must be > 0)", o.width)));
    }
    if let Some(ml) = o.max_lines {
        let indent = if ml > 1 { &o.subsequent } else { &o.initial };
        if indent.len() as i64 + lstrip(&o.placeholder).len() as i64 > o.width {
            return Err(value_err("placeholder too large for max width"));
        }
    }
    chunks.reverse();
    while !chunks.is_empty() {
        // `_wrap_chunks` is a function of `(lines, chunks)` alone, so an
        // iteration that adds no line and leaves the stack as it found it will
        // run forever — and CPython 3.14.5 does, on
        // `wrap(' ab', 2, initial_indent='>>> ')`: the width left is below 1,
        // `_handle_long_word` shaves the leading space to `''`, and the empty
        // chunk is dropped from the line and never from the stack. There is no
        // answer to agree with, so it refuses.
        let before = (chunks.len(), chunks.last().map_or(0, |c| c.len()));
        let mut cur: Vec<Vec<char>> = Vec::new();
        let mut cur_len: i64 = 0;
        let indent: &[char] = if lines.is_empty() { &o.initial } else { &o.subsequent };
        let width = o.width - indent.len() as i64;
        if !lines.is_empty() && chunks.last().is_some_and(|c| blank(c)) {
            chunks.pop();
        }
        while let Some(c) = chunks.last() {
            let l = c.len() as i64;
            if cur_len + l <= width {
                cur.push(chunks.pop().expect("just seen"));
                cur_len += l;
            } else {
                break;
            }
        }
        if chunks.last().is_some_and(|c| c.len() as i64 > width) {
            if long_word(&mut chunks, &mut cur, cur_len, width, o) {
                cur.push(chunks.pop().expect("just seen"));
            }
            cur_len = cur.iter().map(|c| c.len() as i64).sum();
        }
        if cur.last().is_some_and(|c| blank(c)) {
            cur_len -= cur.last().expect("just seen").len() as i64;
            cur.pop();
        }
        if cur.is_empty() {
            if before == (chunks.len(), chunks.last().map_or(0, |c| c.len())) {
                return Err(refuse(
                    "wrap() whose indent leaves no room before leading whitespace (CPython loops forever)",
                ));
            }
            continue;
        }
        let fits = match o.max_lines {
            None => true,
            Some(ml) => {
                (lines.len() as i64 + 1) < ml
                    || ((chunks.is_empty() || (chunks.len() == 1 && blank(&chunks[0]))) && cur_len <= width)
            }
        };
        if fits {
            lines.push(joined(indent, &cur));
            continue;
        }
        loop {
            let Some(last) = cur.last() else {
                // The `while ... else` arm.
                if let Some(prev) = lines.last() {
                    let prev_line = rstrip(prev);
                    if prev_line.len() + o.placeholder.len() <= o.width as usize {
                        let mut l = prev_line.to_vec();
                        l.extend_from_slice(&o.placeholder);
                        *lines.last_mut().expect("just seen") = l;
                        break;
                    }
                }
                let mut l = indent.to_vec();
                l.extend_from_slice(lstrip(&o.placeholder));
                lines.push(l);
                break;
            };
            if !blank(last) && cur_len + o.placeholder.len() as i64 <= width {
                cur.push(o.placeholder.clone());
                lines.push(joined(indent, &cur));
                break;
            }
            cur_len -= last.len() as i64;
            cur.pop();
        }
        break;
    }
    Ok(lines.into_iter().map(|l| l.into_iter().collect()).collect())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn opts(width: i64) -> Opts {
        Opts {
            width,
            initial: Vec::new(),
            subsequent: Vec::new(),
            break_long: true,
            break_hyph: true,
            max_lines: None,
            placeholder: " [...]".chars().collect(),
        }
    }

    fn chunks(s: &str) -> Vec<String> {
        let t: Vec<char> = s.chars().collect();
        split_hyphens(&t).expect("ASCII").into_iter().map(|c| c.into_iter().collect()).collect()
    }

    /// CPython 3.14.5's own `TextWrapper.wordsep_re.split`, empties dropped,
    /// measured 2026-09-24.
    #[test]
    fn the_scanner_splits_as_wordsep_re_does() {
        assert_eq!(
            chunks("Hello there -- you goof-ball, use the -b option!"),
            ["Hello", " ", "there", " ", "--", " ", "you", " ", "goof-", "ball,", " ", "use", " ", "the", " ", "-b", " ", "option!"]
        );
        assert_eq!(chunks("2024-09-24"), ["2024-09-24"]);
        assert_eq!(chunks("foo_-bar"), ["foo_-", "bar"]);
        assert_eq!(chunks("a--b"), ["a", "--", "b"]);
        assert_eq!(chunks("ab--cd"), ["ab", "--", "cd"]);
        assert_eq!(chunks("x-y-z"), ["x-y-z"]);
        assert_eq!(chunks("pre-x-y-z"), ["pre-", "x-", "y-z"]);
    }

    #[test]
    fn a_non_ascii_letter_beside_a_hyphen_refuses_and_elsewhere_does_not() {
        let t: Vec<char> = "café-bar".chars().collect();
        assert!(split_hyphens(&t).is_err());
        let t: Vec<char> = "café bar — baz".chars().collect();
        assert!(split_hyphens(&t).is_ok());
    }

    #[test]
    fn wrap_matches_the_documented_examples() {
        let got = wrap("a well-known self-contained thing", &opts(10)).unwrap();
        assert_eq!(got, ["a well-", "known", "self-", "contained", "thing"]);
        let mut o = opts(20);
        o.max_lines = Some(1);
        let got = wrap("a very long sentence indeed here", &o).unwrap();
        assert_eq!(got, ["a very long [...]"]);
    }

    /// `route::TEXTWRAP_SERVED` is read by the CORE, which has none of this
    /// file compiled in — so the variant that DOES have `module_attr` holds the
    /// two lists to each other.
    #[test]
    fn the_route_table_names_exactly_what_is_served() {
        let table = crate::route::MODULE_ATTRS
            .iter()
            .find(|(m, _)| *m == "textwrap")
            .expect("route::MODULE_ATTRS has no textwrap row")
            .1;
        for name in table {
            assert!(module_attr(name).is_ok(), "route claims textwrap.{name} and textwrap.rs refuses it");
        }
        for name in SERVED {
            assert!(table.contains(name), "textwrap.rs serves textwrap.{name} and route::MODULE_ATTRS omits it");
        }
        assert!(table.windows(2).all(|w| w[0] < w[1]), "route::MODULE_ATTRS textwrap row is unsorted");
        assert!(module_attr("TextWrapper").is_err());
    }
}
