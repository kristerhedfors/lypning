//! `glob` — the whole of the `cap-glob` capability, compiled into `lypning-l`
//! and into nothing smaller. Every line of this file, and every line that
//! reaches it, is behind `cfg(feature = "cap-glob")`.
//!
//! **The trap this module is: `glob.glob()` returns filesystem order.** CPython
//! walks a directory with `os.scandir` and does not sort, so the order of a
//! multi-match result is whatever the filesystem handed back — the same fact
//! that makes `os.listdir()` a refusal here (`modules.rs`, kind `os-listdir`)
//! and `Path.glob()`/`.iterdir()` refusals in `pathlib.rs`. glob inherits it
//! exactly, so this module inherits the refusal exactly. Returning a sorted
//! list instead would be a DIFFERENT answer at exit 0, which is the worst
//! outcome this repository has.
//!
//! **The rule, in one sentence: the match SET is computed exactly, and the
//! match ORDER is never shown.**
//!
//!   * **Zero or one match** is an ordinary `list`. A list of nothing and a
//!     list of one path have no order to get wrong, so every use of one is
//!     exact — including `for f in glob.glob(...)`, `print(...)` and `[0]`.
//!     This is not a corner: a pattern with no magic character never lists a
//!     directory at all (CPython checks `lexists` and returns `[pathname]` or
//!     `[]`), and across the corpus the commonest shape by far is a pattern
//!     that matches one file or none.
//!   * **Two or more matches** is a [`Value::Glob`] — the same paths, held
//!     sorted, which answers exactly the questions whose answer does not depend
//!     on the order and refuses `glob-order` at every other point of
//!     observation. Answered: `len()`, `x in g`, `bool(g)`, `sorted(g)`,
//!     `min`/`max`/`any`/`all`, `set(g)`, and `g + h` where `h` is another glob
//!     result. Refused: iteration and `for`, indexing and slicing, `print`,
//!     `repr`, `str`, `==`, `%`, `format()`, `json.dumps`, `bytes()`, hashing,
//!     `<`, `.sort()` and every other method. That is the discipline
//!     `Value::Set` already carries for CPython's set order (`value.rs`), and
//!     it is carried here for the same reason and with the same shape.
//!
//! The refusal is a RUNTIME one and that is deliberate, against this
//! repository's usual preference for a static route. A `re.sub()` call is
//! certainly unanswerable the moment a walker sees it; a glob's order question
//! does not exist until the match count is known, and the count is zero or one
//! for most of the shapes the corpus types. Refusing `for f in
//! glob.glob(...)` statically would spend a CPython spawn on every one of them
//! to be told an answer this engine already had. Nothing here commits the
//! barrier either — `glob.glob()` reads directories and writes nothing — so
//! the refusal always falls onward.
//!
//! **A listing cannot see through the commit barrier, so it refuses instead.**
//! `io.rs` stages every write until the run ends, which is what lets a refusal
//! fall onward with nothing left behind — and it is invisible to a program
//! asking about ONE path, because `open()` and `os.path.exists()` merge the
//! staging into their answer. A listing cannot: it would have to decide which
//! staged spelling names an entry of which directory. So `glob.glob()` refuses
//! while anything is staged rather than answer `[]` for a file the program has
//! just written.
//!
//! **The rest of the module.** `glob.escape` and `glob.has_magic` are pure
//! string algebra and are answered exactly. `glob.iglob` returns a generator
//! whose repr embeds an address and whose only use is iteration, `glob.glob0`,
//! `glob.glob1` and `glob.translate` are CPython's own, and every one of them
//! refuses through `module-attr` — statically, because the router resolves
//! `glob.<name>` before anything runs. `root_dir=`, `dir_fd=` and
//! `include_hidden=` refuse; `recursive=` is served. Every error path refuses
//! rather than raising, for the reason invariant 1 gives: a `TypeError`'s
//! wording is CPython's and moves between releases.
//!
//! **Three exactness traps, each measured against CPython 3.14.5 before the
//! code below was written** (`tests/test_glob_grid.py` holds all three):
//!
//!   1. **A leading `.` is not matched by `*` or `?`.** `_glob1` drops hidden
//!      names unless the PATTERN's own basename starts with a dot, so
//!      `glob('*')` never sees `.hidden` while `glob('.*')` sees only hidden
//!      names. `_glob0` — a basename with no magic — does not filter, so
//!      `glob('.hidden')` finds it. `_rlistdir`, which is what `**` walks with,
//!      drops hidden names at every level.
//!   2. **A pattern with no magic never lists anything.** It is a `lexists`
//!      test, so a BROKEN SYMLINK matches, where an `exists` test would not.
//!      A pattern ending in `/` is the other half of that rule: the basename is
//!      empty, so it matches only if the directory exists, and the result keeps
//!      the trailing slash.
//!   3. **`recursive=True` and `**`.** `**` matches the empty path first, so
//!      `glob('a/**', recursive=True)` yields `'a/'` before anything under it,
//!      and `glob('**/*.py', recursive=True)` matches `a.py` at the top level
//!      as well as `d/a.py`. `**` is only special as a WHOLE component and only
//!      when `recursive=True`; anywhere else — `'**x'`, or `'**'` without the
//!      keyword — it is an ordinary `*`.

use crate::args::Args;
use crate::err::{unsupported, LypningError, R};
use crate::eval::Interp;
use crate::fmt;
use crate::value::{list, truthy, Value};
use std::rc::Rc;

/// The names this module serves. Everything else under `glob` is a
/// `module-attr` refusal, which the router sees statically.
const SERVED: &[&str] = &["escape", "glob", "has_magic"];

pub fn refuse(what: &str) -> LypningError {
    unsupported("glob", what)
}

/// The order refusal, spelled once so `conformance --plan` ranks one row for
/// it however it was reached. The wording is `os.listdir`'s, because it is the
/// same fact about the same system call.
pub fn order_refused(what: &str) -> LypningError {
    unsupported(
        "glob-order",
        &format!("{what} — glob() order is filesystem-defined and not reproducible"),
    )
}

/// `glob.<name>` as a value. A served name is a bound module method; every
/// other name refuses with the kind the router blocks on statically.
pub fn module_attr(name: &str) -> R<Value> {
    match SERVED.iter().find(|n| **n == name) {
        Some(n) => Ok(Value::Bound(Rc::new(Value::Module("glob")), n)),
        None => Err(unsupported("module-attr", &format!("glob.{name}"))),
    }
}

pub fn call(it: &mut Interp, name: &str, args: &mut Args, kw: &[(Rc<str>, Value)]) -> R<Value> {
    // One positional, and it must already be a `str`: `os.fspath` on a Path or
    // an int file descriptor is CPython's coercion and its TypeError.
    let pat = match args.first() {
        Some(Value::Str(s)) => s.to_string(),
        Some(v) => {
            return Err(refuse(&format!(
                "glob.{name}() over a pattern that is not a str (a {})",
                crate::value::type_name(v)
            )))
        }
        None => return Err(refuse(&format!("glob.{name}() with no pattern"))),
    };
    if args.len() > 1 {
        return Err(refuse(&format!("glob.{name}() with extra positional arguments")));
    }
    match name {
        // `escape` wraps each of `*?[` in a bracket expression. The drive part
        // CPython splits off first is always empty on POSIX.
        "escape" => {
            let mut out = String::with_capacity(pat.len());
            for c in pat.chars() {
                if matches!(c, '*' | '?' | '[') {
                    out.push('[');
                    out.push(c);
                    out.push(']');
                } else {
                    out.push(c);
                }
            }
            no_kw(name, kw)?;
            Ok(Value::Str(out.into()))
        }
        "has_magic" => {
            no_kw(name, kw)?;
            Ok(Value::Bool(has_magic(&pat)))
        }
        _ => {
            let mut recursive = false;
            for (k, v) in kw {
                match k.as_ref() {
                    "recursive" => recursive = truthy(v)?,
                    other => return Err(refuse(&format!("glob.glob({other}=…)"))),
                }
            }
            // THE BARRIER IS INVISIBLE TO ONE PATH AND NOT TO A LISTING.
            // `io.rs` stages every write until the run ends, so a program that
            // writes `d/a.py` and then globs `d/*.py` would be shown the
            // directory as it was BEFORE its own write — `[]` at exit 0, where
            // CPython lists the file it just made. `open()` and
            // `os.path.exists()` merge the staging into their answer because
            // they are asked about one path they were handed; a listing would
            // have to work out which staged spelling belongs to which
            // directory, and a wrong answer there is a wrong LISTING at exit 0.
            // So this refuses while anything is staged, and the barrier stays
            // the thing that makes the refusal free: nothing has happened yet.
            if crate::io::has_staged() {
                return Err(refuse(
                    "glob() after this run wrote or removed a file — the commit barrier is \
                     holding it back, so the directory on disk is not the one the program sees",
                ));
            }
            let mut out = Vec::new();
            iglob(it, &mut out, &pat, recursive, false)?;
            // `iglob` drops the empty string `**` yields first, but only when
            // the pattern itself starts with `**` and only if it came first.
            if pat.is_empty() || (recursive && pat.starts_with("**")) {
                if out.first().map(String::is_empty) == Some(true) {
                    out.remove(0);
                }
            }
            Ok(result(out))
        }
    }
}

fn no_kw(name: &str, kw: &[(Rc<str>, Value)]) -> R<()> {
    match kw.first() {
        Some((k, _)) => Err(refuse(&format!("glob.{name}({k}=…)"))),
        None => Ok(()),
    }
}

/// The one place the order rule is applied: nothing else in this file decides
/// it, and nothing outside this file may produce a [`Value::Glob`].
fn result(mut paths: Vec<String>) -> Value {
    if paths.len() < 2 {
        return list(paths.into_iter().map(|p| Value::Str(p.into())).collect());
    }
    // Held sorted so that anything which does reach the elements reaches them
    // in an order that is at least this engine's own and not the disk's. Every
    // path that could SHOW that order refuses; this is the second line.
    paths.sort();
    Value::Glob(Rc::new(paths.into_iter().map(|p| Value::Str(p.into())).collect()))
}

// ---- the algorithm, which is CPython's `glob._iglob` with `root_dir` empty --
//
// `root_dir=None` makes `_join(root_dir, x)` return `x` for every x, so the
// parameter drops out of the recursion entirely rather than being threaded
// through it. That is why it is refused above rather than defaulted here.

/// `*`, `?` and `[` are the three characters that make a component a pattern.
fn has_magic(s: &str) -> bool {
    s.bytes().any(|c| matches!(c, b'*' | b'?' | b'['))
}

fn hidden(s: &str) -> bool {
    s.starts_with('.')
}

/// `os.path.split`: the head keeps its slashes only when it is ALL slashes,
/// which is what makes `split('/a')` be `('/', 'a')` and `split('a//b')` be
/// `('a', 'b')`.
fn split(p: &str) -> (&str, &str) {
    match p.rfind('/') {
        None => ("", p),
        Some(i) => {
            let (head, tail) = (&p[..i + 1], &p[i + 1..]);
            if head.bytes().any(|c| c != b'/') {
                (head.trim_end_matches('/'), tail)
            } else {
                (head, tail)
            }
        }
    }
}

/// `os.path.join` of exactly two parts.
fn join(a: &str, b: &str) -> String {
    if b.starts_with('/') {
        b.to_string()
    } else if a.is_empty() || a.ends_with('/') {
        format!("{a}{b}")
    } else {
        format!("{a}/{b}")
    }
}

/// `glob._join`, which is [`join`] except that an empty part wins outright —
/// `_join('', 'b')` is `'b'` and `_join('a', '')` is `'a'`, where
/// `os.path.join` would have made `'a/'`.
fn join_nonempty(a: &str, b: &str) -> String {
    if a.is_empty() {
        b.to_string()
    } else if b.is_empty() {
        a.to_string()
    } else {
        join(a, b)
    }
}

/// `os.path.lexists` — an `lstat`, so a broken symlink is still a match.
fn lexists(p: &str) -> bool {
    std::fs::symlink_metadata(p).is_ok()
}

/// `os.path.isdir` — a `stat`, so a symlink to a directory IS one.
fn isdir(p: &str) -> bool {
    std::fs::metadata(p).map(|m| m.is_dir()).unwrap_or(false)
}

/// How deep `**` walks before refusing. CPython has no limit and raises
/// `RecursionError` at its own; a symlink loop makes the walk unbounded on
/// both, and an unbounded walk here is a stack overflow — a signal, which the
/// dispatcher cannot route onward at all. So the walk is bounded and the bound
/// is a refusal.
const MAX_DEPTH: usize = 128;

/// `glob._listdir`. An unreadable directory is EMPTY, not an error: CPython
/// swallows `OSError` here, so a permission denial is part of the answer and
/// not a failure. A name that is not valid UTF-8 is the one thing that is —
/// CPython would hand the program a surrogate-escaped `str` this engine has no
/// way to spell, so it refuses rather than drop the entry.
fn listdir(dirname: &str, dironly: bool) -> R<Vec<String>> {
    let mut out = Vec::new();
    let rd = match std::fs::read_dir(if dirname.is_empty() { "." } else { dirname }) {
        Ok(rd) => rd,
        Err(_) => return Ok(out),
    };
    for e in rd {
        let e = match e {
            Ok(e) => e,
            Err(_) => continue,
        };
        if dironly {
            // `entry.is_dir()` follows the symlink, so a link to a directory
            // counts as one; an entry whose type cannot be read is skipped,
            // which is CPython's inner `except OSError: pass`.
            let is_dir = match e.file_type() {
                Ok(t) if t.is_symlink() => isdir(&e.path().to_string_lossy()),
                Ok(t) => t.is_dir(),
                Err(_) => continue,
            };
            if !is_dir {
                continue;
            }
        }
        match e.file_name().into_string() {
            Ok(n) => out.push(n),
            Err(_) => {
                return Err(refuse(
                    "a directory entry whose name is not valid UTF-8 (CPython spells it with surrogate escapes)",
                ))
            }
        }
    }
    Ok(out)
}

/// `glob._iglob`, appending to `out` in CPython's own yield order — which is
/// the order [`result`] then refuses to show.
fn iglob(it: &mut Interp, out: &mut Vec<String>, pattern: &str, rec: bool, dironly: bool) -> R<()> {
    it.tick()?;
    let (dirname, basename) = split(pattern);
    if !has_magic(pattern) {
        if !basename.is_empty() {
            if lexists(pattern) {
                out.push(pattern.to_string());
            }
        } else if isdir(dirname) {
            // A pattern ending in `/` matches only a directory, and keeps the
            // slash: `glob('d/')` is `['d/']`.
            out.push(pattern.to_string());
        }
        return Ok(());
    }
    if dirname.is_empty() {
        return if rec && basename == "**" {
            glob2(it, out, "", dironly, 0)
        } else {
            glob1(out, "", basename, dironly)
        };
    }
    let dirs: Vec<String> = if dirname != pattern && has_magic(dirname) {
        let mut d = Vec::new();
        iglob(it, &mut d, dirname, rec, true)?;
        d
    } else {
        vec![dirname.to_string()]
    };
    for d in dirs {
        let mut names = Vec::new();
        if has_magic(basename) {
            if rec && basename == "**" {
                glob2(it, &mut names, &d, dironly, 0)?;
            } else {
                glob1(&mut names, &d, basename, dironly)?;
            }
        } else {
            glob0(&mut names, &d, basename);
        }
        for n in names {
            out.push(join(&d, &n));
        }
    }
    Ok(())
}

/// `glob._glob1`: the names in one directory that match one pattern. The
/// hidden-file rule lives here and nowhere else — a pattern whose own first
/// character is a dot sees hidden names, and no other pattern does.
fn glob1(out: &mut Vec<String>, dirname: &str, pattern: &str, dironly: bool) -> R<()> {
    let show_hidden = hidden(pattern);
    for n in listdir(dirname, dironly)? {
        if !show_hidden && hidden(&n) {
            continue;
        }
        if fnmatch(&n, pattern)? {
            out.push(n);
        }
    }
    Ok(())
}

/// `glob._glob0`: a basename with no magic is an existence test, not a listing,
/// which is why `glob('d/.hidden')` finds a hidden file that `glob('d/*')`
/// cannot.
fn glob0(out: &mut Vec<String>, dirname: &str, basename: &str) {
    if !basename.is_empty() {
        if lexists(&join_nonempty(dirname, basename)) {
            out.push(basename.to_string());
        }
    } else if isdir(dirname) {
        out.push(String::new());
    }
}

/// `glob._glob2`: `**` is the empty path FIRST and then everything below.
fn glob2(it: &mut Interp, out: &mut Vec<String>, dirname: &str, dironly: bool, depth: usize) -> R<()> {
    if dirname.is_empty() || isdir(dirname) {
        out.push(String::new());
    }
    rlistdir(it, out, dirname, dironly, depth)
}

/// `glob._rlistdir`: every name below `dirname`, relative to it, hidden names
/// dropped at every level.
fn rlistdir(it: &mut Interp, out: &mut Vec<String>, dirname: &str, dironly: bool, depth: usize) -> R<()> {
    it.tick()?;
    if depth >= MAX_DEPTH {
        return Err(refuse(
            "a ** walk deeper than this engine follows (a symlink loop, or a very deep tree)",
        ));
    }
    for x in listdir(dirname, dironly)? {
        if hidden(&x) {
            continue;
        }
        let path = join_nonempty(dirname, &x);
        out.push(x.clone());
        let mut sub = Vec::new();
        rlistdir(it, &mut sub, &path, dironly, depth + 1)?;
        for y in sub {
            out.push(join_nonempty(&x, &y));
        }
    }
    Ok(())
}

// ---- fnmatch ---------------------------------------------------------------

/// `fnmatch.fnmatchcase` over ONE path component: `*` and `?` never cross a
/// `/` here because the caller has already split the pattern on it.
/// `os.path.normcase` is the identity on POSIX, so there is no case folding.
fn fnmatch(name: &str, pattern: &str) -> R<bool> {
    let n: Vec<char> = name.chars().collect();
    let p: Vec<char> = pattern.chars().collect();
    let (mut i, mut j) = (0usize, 0usize);
    let (mut star, mut mark) = (usize::MAX, 0usize);
    while i < n.len() {
        let mut matched = false;
        if j < p.len() {
            match p[j] {
                '*' => {
                    star = j;
                    mark = i;
                    j += 1;
                    continue;
                }
                '?' => {
                    i += 1;
                    j += 1;
                    continue;
                }
                '[' => match class(&p, j)? {
                    Some((body, next)) => {
                        if class_holds(body, n[i])? {
                            i += 1;
                            j = next;
                            continue;
                        }
                    }
                    // An unterminated `[` is a literal `[`.
                    None => matched = n[i] == '[',
                },
                c => matched = c == n[i],
            }
        }
        if matched {
            i += 1;
            j += 1;
            continue;
        }
        if star == usize::MAX {
            return Ok(false);
        }
        mark += 1;
        i = mark;
        j = star + 1;
    }
    while j < p.len() && p[j] == '*' {
        j += 1;
    }
    Ok(j == p.len())
}

/// The bracket expression starting at `p[at]`, as `(body, index after ']')`, or
/// `None` when there is no closing `]` at all. The scan is CPython's: a `!`
/// and then a `]` immediately after the `[` are both part of the body, so
/// `[]]` matches a `]` and `[!]]` matches anything else.
#[allow(clippy::type_complexity)]
fn class(p: &[char], at: usize) -> R<Option<(&[char], usize)>> {
    let mut k = at + 1;
    if k < p.len() && p[k] == '!' {
        k += 1;
    }
    if k < p.len() && p[k] == ']' {
        k += 1;
    }
    while k < p.len() && p[k] != ']' {
        k += 1;
    }
    if k >= p.len() {
        return Ok(None);
    }
    Ok(Some((&p[at + 1..k], k + 1)))
}

/// Does this bracket expression hold `c`? A range whose ends are reversed —
/// `[z-a]` — is where CPython's translation stops being a plain character
/// class and starts merging chunks, so it refuses rather than guess which of
/// the two readings the regex ended up with.
fn class_holds(body: &[char], c: char) -> R<bool> {
    let (neg, body) = match body.first() {
        Some('!') => (true, &body[1..]),
        _ => (false, body),
    };
    let mut hit = false;
    let mut k = 0;
    while k < body.len() {
        if k + 2 < body.len() && body[k + 1] == '-' {
            let (lo, hi) = (body[k], body[k + 2]);
            if lo > hi {
                return Err(refuse("a [z-a] range in a pattern (CPython rewrites it)"));
            }
            hit |= lo <= c && c <= hi;
            k += 3;
        } else {
            hit |= body[k] == c;
            k += 1;
        }
    }
    // `[!]` with nothing left is CPython's "negated empty range": any char.
    Ok(hit != neg)
}

// ---- the value -------------------------------------------------------------

/// The paths, for the order-blind consumers — `len`, `in`, `sorted`, `set` and
/// the rest. Every caller of this is a place whose answer does not depend on
/// the order it receives them in; a caller whose answer would depend on it
/// calls [`order_refused`] instead.
pub fn items(v: &Rc<Vec<Value>>) -> Vec<Value> {
    v.as_ref().clone()
}

/// `g + h`, both glob results. Concatenating two multisets whose orders are
/// both unshowable gives a third — never a plain list, however few paths are
/// left, because `len() >= 2` on either side already made the question live.
pub fn concat(mut items: Vec<Value>) -> Value {
    items.sort_by(|a, b| match (a, b) {
        (Value::Str(x), Value::Str(y)) => x.cmp(y),
        _ => std::cmp::Ordering::Equal,
    });
    Value::Glob(Rc::new(items))
}
