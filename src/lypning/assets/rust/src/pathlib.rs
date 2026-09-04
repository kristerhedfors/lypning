//! `pathlib.Path` — the whole of the `cap-pathlib` capability, compiled into
//! `lypning-l` and into nothing smaller. Every line of this file, and every line
//! that reaches it, is behind `cfg(feature = "cap-pathlib")`, so the `lypning`
//! binary does not move a byte for it.
//!
//! A `Path` here is ONE `Rc<str>` — the normalised path text, which is exactly
//! what `str(p)` returns — plus a bit saying whether this value is the path or
//! its `.parents` view. That is the whole representation, and it is the reason
//! this capability is cheap: the pure-path surface is deterministic string
//! algebra over that one string, the filesystem surface is `io.rs`'s existing
//! commit barrier reached through `open()`, and there is no new container, no
//! new hash table and no new iterator.
//!
//! **The pure algebra is exact; everything that would need the filesystem to
//! answer is refused.** `.glob()`, `.rglob()`, `.iterdir()` and `.walk()` expose
//! directory order, which is filesystem-defined and not reproducible — the same
//! reason `os.listdir()` already refuses (`modules.rs`). `.resolve()`,
//! `.absolute()`, `.expanduser()` and `.home()` depend on state this engine
//! cannot pin; `.stat()` exposes inode and mtime. Each is
//! `unsupported: pathlib: …` and reaches CPython one spawn later.
//!
//! **Three traps, each measured against CPython 3.14.5 before the code below
//! was written.**
//!
//!   1. **`.parts` keeps the root.** `Path('/a/b/c.txt').parts` is
//!      `('/', 'a', 'b', 'c.txt')`, not `('a', 'b', 'c.txt')`. Dropping it is
//!      the one pathlib divergence the oracle records
//!      (`pathlib-parts-drops-root`, `lypning oracle --full`): a second
//!      reimplementation lost the root and answered at exit 0, and 46 corpus
//!      programs import pathlib. [`parts`] is written around that case and the
//!      grid has a block of rows for it.
//!   2. **Ordering is over the SPLIT string, not over `.parts`.** CPython
//!      compares `str(p).split('/')`, in which an absolute path's first element
//!      is `''` and `.parts`'s is `'/'` — so `Path('/a') < Path('!x')` is True
//!      where a parts comparison says False. 3.11 compared `.parts`; 3.12+
//!      compare the split. The two agree only when both sides share a root, so
//!      [`order`] answers there and refuses everywhere else rather than pick a
//!      CPython.
//!   3. **`.suffix` changed in 3.14.** `Path('a.').suffix` is `'.'` on 3.14 and
//!      `''` on 3.12; `Path('..a').suffix` is `''` on 3.14 and `'.a'` on 3.12.
//!      [`ambiguous`] names exactly the shapes the two eras disagree about and
//!      refuses them; every other name is the same on both.
//!
//! Every error path is a refusal for the reason invariant 1 gives: the message
//! text of `ValueError: 'a' is not in the subpath of 'b'` and its neighbours
//! moved between 3.11, 3.12 and 3.14, and an engine that cannot test the other
//! two must not print one of them.

use crate::args::Args;
use crate::err::{unsupported, LypningError, R};
use crate::eval::Interp;
use crate::fmt;
use crate::value::{type_name, Value};
use std::rc::Rc;

/// Sorted, because [`get_attr`] binary-searches it (`methods.rs` says why).
/// Every name here is answered exactly; a name CPython has and this list does
/// not is a refusal, never an `AttributeError` — CPython answers it, and an
/// `AttributeError` is exit 1, which the chain never retries.
const METHODS: &[&str] = &[
    "as_posix", "exists", "is_absolute", "is_dir", "is_file", "joinpath", "mkdir", "open",
    "read_bytes", "read_text", "relative_to", "unlink", "with_name", "with_stem", "with_suffix",
    "write_bytes", "write_text",
];

/// The properties, which are computed at attribute access and never bound.
const PROPERTIES: &[&str] =
    &["name", "parent", "parents", "parts", "stem", "suffix", "suffixes"];

pub fn refuse(what: &str) -> LypningError {
    unsupported("pathlib", what)
}

/// The router's optimistic method union has to admit these names or the very
/// programs this capability exists to run are blocked before they start — but
/// only for a program that imports `pathlib`. `.name` is an ordinary attribute
/// on other objects, and admitting it unconditionally would turn a program this
/// engine sends to CPython today into an `AttributeError` at exit 1. See
/// `route::walk_expr`.
pub fn known_method(name: &str) -> bool {
    name == "cwd" || METHODS.contains(&name) || PROPERTIES.contains(&name)
}

// ---- the string algebra ---------------------------------------------------

/// CPython's `PurePosixPath` parse: repeated slashes collapse, `.` components
/// drop, `..` is KEPT (a pure path is never resolved against a filesystem), an
/// empty path is `.`, and a root of exactly two slashes is preserved because
/// POSIX leaves `//` to the implementation and CPython passes it through.
fn normalize(raw: &str) -> String {
    let lead = raw.len() - raw.trim_start_matches('/').len();
    let mut out = String::from(match lead {
        0 => "",
        2 => "//",
        _ => "/",
    });
    let mut first = true;
    for c in raw[lead..].split('/') {
        if c.is_empty() || c == "." {
            continue;
        }
        if !first {
            out.push('/');
        }
        out.push_str(c);
        first = false;
    }
    if out.is_empty() {
        ".".to_string()
    } else {
        out
    }
}

/// `Path(*segments)` and `p / q` are the same operation: a later segment that
/// starts with `/` REPLACES everything before it.
fn join(segments: &[String]) -> String {
    let mut raw = String::new();
    for p in segments {
        if p.starts_with('/') {
            raw.clear();
            raw.push_str(p);
        } else if raw.is_empty() || raw.ends_with('/') {
            raw.push_str(p);
        } else {
            raw.push('/');
            raw.push_str(p);
        }
    }
    normalize(&raw)
}

/// The root of a normalised path: `""`, `"/"` or `"//"`.
fn root(s: &str) -> &'static str {
    match s.len() - s.trim_start_matches('/').len() {
        0 => "",
        2 => "//",
        _ => "/",
    }
}

/// The components BELOW the root. `.` has none, which is why an empty relative
/// path has an empty `.parts`.
fn tail(s: &str) -> Vec<&str> {
    if s == "." {
        return Vec::new();
    }
    s[root(s).len()..].split('/').filter(|c| !c.is_empty()).collect()
}

/// `.parts` — **the root is a component**. See trap 1 in the module comment.
fn parts(s: &str) -> Vec<&str> {
    let r = root(s);
    let mut out = tail(s);
    if !r.is_empty() {
        out.insert(0, r);
    }
    out
}

/// Rebuild a path from a root and its components. The components come from a
/// normalised path and carry no separators, so no second normalisation is
/// needed or wanted (it would drop a `..` that CPython keeps).
fn rebuild(r: &str, comps: &[&str]) -> String {
    if comps.is_empty() {
        return if r.is_empty() { ".".into() } else { r.into() };
    }
    format!("{r}{}", comps.join("/"))
}

fn name_of(s: &str) -> &str {
    tail(s).last().copied().unwrap_or("")
}

fn parent_of(s: &str) -> String {
    let comps = tail(s);
    if comps.is_empty() {
        return s.to_string();
    }
    rebuild(root(s), &comps[..comps.len() - 1])
}

/// Every parent in order, shortest path last: `/a/b/c` is `/a/b`, `/a`, `/`.
fn parents_of(s: &str) -> Vec<String> {
    let r = root(s);
    let comps = tail(s);
    (0..comps.len()).map(|i| rebuild(r, &comps[..comps.len() - 1 - i])).collect()
}

/// Do CPython 3.12 and 3.14 disagree about this name's stem and suffix?
///
/// 3.12 split at the last dot only when it was neither the first nor the last
/// character; 3.14 splits whenever the head holds a non-dot character. So they
/// differ on a name that ENDS in a dot (`'a.'`) and on one whose head is all
/// dots (`'..a'`), and agree on everything else — including a name that is
/// nothing but dots, which neither era gives a suffix. See trap 3.
fn ambiguous(name: &str) -> bool {
    if name.bytes().all(|b| b == b'.') {
        return false;
    }
    if name.ends_with('.') {
        return true;
    }
    match name.rfind('.') {
        Some(i) if i > 0 => name[..i].bytes().all(|b| b == b'.'),
        _ => false,
    }
}

fn version_split(name: &str) -> LypningError {
    refuse(&format!(
        "the stem and suffix of {name:?}, which CPython 3.12 and 3.14 split differently"
    ))
}

/// `(stem, suffix)` on CPython 3.14, for a name the two eras agree about.
fn stem_suffix(name: &str) -> R<(&str, &str)> {
    if ambiguous(name) {
        return Err(version_split(name));
    }
    match name.rfind('.') {
        Some(i) if i > 0 && !name[..i].bytes().all(|b| b == b'.') => Ok((&name[..i], &name[i..])),
        _ => Ok((name, "")),
    }
}

fn suffixes(name: &str) -> R<Vec<Value>> {
    if ambiguous(name) {
        return Err(version_split(name));
    }
    Ok(name
        .trim_start_matches('.')
        .split('.')
        .skip(1)
        .map(|s| Value::Str(format!(".{s}").into()))
        .collect())
}

// ---- values ---------------------------------------------------------------

fn path_value(s: String) -> Value {
    Value::Path(s.into(), false)
}

/// A `Path` or a `str` as the text to join. Anything else is what CPython
/// answers with a `TypeError` naming `os.PathLike`; refused rather than typed
/// out, because that message has moved.
fn segment(v: &Value) -> R<String> {
    match v {
        Value::Str(s) => Ok(s.to_string()),
        Value::Path(s, false) => Ok(s.to_string()),
        other => Err(refuse(&format!(
            "a path segment that is a {}, which CPython rejects with a TypeError naming os.PathLike",
            type_name(other)
        ))),
    }
}

/// `Path(...)`, `PurePosixPath`-style. Keyword arguments are 3.14's
/// `Path(..., **kwargs)` shape and nothing this engine serves.
pub fn construct(args: &mut Args, kw: &[(Rc<str>, Value)]) -> R<Value> {
    if !kw.is_empty() {
        return Err(refuse("Path() with keyword arguments"));
    }
    let segs: Vec<String> = args.iter().map(segment).collect::<R<Vec<_>>>()?;
    Ok(path_value(join(&segs)))
}

pub fn cwd() -> R<Value> {
    let d = std::env::current_dir().map_err(|e| crate::io::os_error(".", &e))?;
    match d.to_str() {
        Some(s) => Ok(path_value(normalize(s))),
        None => Err(refuse("a working directory whose name is not UTF-8")),
    }
}

// ---- attributes -----------------------------------------------------------

pub fn get_attr(s: &Rc<str>, view: bool, name: &str) -> R<Value> {
    if view {
        return Err(refuse(&format!("PosixPath.parents.{name}")));
    }
    Ok(match name {
        "name" => Value::Str(name_of(s).into()),
        "stem" => Value::Str(stem_suffix(name_of(s))?.0.into()),
        "suffix" => Value::Str(stem_suffix(name_of(s))?.1.into()),
        "suffixes" => crate::value::list(suffixes(name_of(s))?),
        "parent" => path_value(parent_of(s)),
        "parents" => Value::Path(s.clone(), true),
        "parts" => Value::Tuple(Rc::new(
            parts(s).into_iter().map(|p| Value::Str(p.into())).collect(),
        )),
        other => match METHODS.binary_search(&other) {
            Ok(i) => Value::Bound(Rc::new(Value::Path(s.clone(), false)), METHODS[i]),
            Err(_) => return Err(refuse(&format!("PosixPath.{other}"))),
        },
    })
}

// ---- methods --------------------------------------------------------------

fn one(args: &Args, what: &str) -> R<Value> {
    match (args.len(), args.first()) {
        (1, Some(v)) => Ok(v.clone()),
        _ => Err(refuse(&format!("{what} with {} arguments", args.len()))),
    }
}

pub fn method(
    it: &mut Interp,
    s: &Rc<str>,
    view: bool,
    name: &str,
    args: &mut Args,
    kw: Vec<(Rc<str>, Value)>,
) -> R<Value> {
    if view {
        return Err(refuse(&format!("PosixPath.parents.{name}()")));
    }
    // Only `mkdir`, `open`, `read_text` and `write_text` take keywords, and
    // each checks its own below; everything else refuses one rather than
    // ignore it, which is the failure `accepts_kw` in `methods.rs` was written
    // for.
    if !kw.is_empty() && !matches!(name, "mkdir" | "open" | "read_text" | "write_text") {
        return Err(refuse(&format!("PosixPath.{name}() with keyword arguments")));
    }
    Ok(match name {
        "as_posix" => Value::Str(s.clone()),
        "is_absolute" => Value::Bool(!root(s).is_empty()),
        "joinpath" => {
            let mut segs = vec![s.to_string()];
            for a in args.iter() {
                segs.push(segment(a)?);
            }
            path_value(join(&segs))
        }
        "with_name" => path_value(with_name(s, &fmt_str(&one(args, "with_name()")?)?)?),
        "with_stem" => {
            let stem = fmt_str(&one(args, "with_stem()")?)?;
            let suffix = stem_suffix(name_of(s))?.1.to_string();
            // CPython 3.13 added an explicit guard: an EMPTY stem on a name that
            // still has a suffix is a ValueError, not a dotfile. Answering
            // `.txt` there was a silent wrong answer, and 3.11/3.12 build the
            // dotfile instead — so the shapes disagree across the versions this
            // tree is graded against and the only correct move is to refuse.
            if stem.is_empty() && !suffix.is_empty() {
                return Err(refuse(
                    "with_stem('') on a name that has a suffix, which CPython 3.13+ answers \
                     with a ValueError and earlier versions answer with a dotfile",
                ));
            }
            path_value(with_name(s, &format!("{stem}{suffix}"))?)
        }
        "with_suffix" => {
            let suffix = fmt_str(&one(args, "with_suffix()")?)?;
            // CPython 3.12 raised on `'.'` and 3.14 accepts it; a suffix that
            // does not start with a dot is a ValueError on both. Only the
            // shapes both eras build are answered.
            if !suffix.is_empty() && (!suffix.starts_with('.') || suffix == ".") {
                return Err(refuse(&format!(
                    "with_suffix({suffix:?}), which CPython answers with a ValueError"
                )));
            }
            let stem = stem_suffix(name_of(s))?.0.to_string();
            path_value(with_name(s, &format!("{stem}{suffix}"))?)
        }
        "relative_to" => {
            let other = normalize(&segment(&one(args, "relative_to()")?)?);
            let (mine, theirs) = (tail(s), tail(&other));
            if root(s) != root(&other) || !mine.starts_with(theirs.as_slice()) {
                // CPython raises `ValueError: '…' is not in the subpath of
                // '…'`, whose wording changed in 3.12. A refusal reaches the
                // CPython the user actually has.
                return Err(refuse(&format!(
                    "relative_to({other:?}) where {:?} is not below it, which CPython answers \
                     with a ValueError",
                    s.as_ref()
                )));
            }
            path_value(rebuild("", &mine[theirs.len()..]))
        }
        // The filesystem half. Every one of these goes through `open()` or the
        // same staging `os.mkdir`/`os.remove` use, so the commit barrier holds
        // for a pathlib program exactly as it does for an `open()` one.
        "read_text" => {
            let f = crate::builtins::open_value(s, "r", &text_kw(&kw)?)?;
            crate::methods::call_method(it, &f, "read", &mut Args::new(), Vec::new())?
        }
        "read_bytes" => {
            let f = crate::builtins::open_value(s, "rb", &[])?;
            crate::methods::call_method(it, &f, "read", &mut Args::new(), Vec::new())?
        }
        "write_text" => {
            let data = one(args, "write_text()")?;
            if !matches!(data, Value::Str(_)) {
                return Err(refuse("write_text() of something that is not a str"));
            }
            let f = crate::builtins::open_value(s, "w", &text_kw(&kw)?)?;
            crate::methods::call_method(it, &f, "write", &mut Args::one(data), Vec::new())?
        }
        "write_bytes" => {
            let data = one(args, "write_bytes()")?;
            if !matches!(data, Value::Bytes(_)) {
                return Err(refuse("write_bytes() of something that is not bytes"));
            }
            let f = crate::builtins::open_value(s, "wb", &[])?;
            crate::methods::call_method(it, &f, "write", &mut Args::one(data), Vec::new())?
        }
        "open" => {
            let mode = match args.first().cloned().or_else(|| kwval(&kw, "mode")) {
                Some(v) => fmt_str(&v)?,
                None => "r".to_string(),
            };
            let rest: Vec<(Rc<str>, Value)> =
                kw.iter().filter(|(k, _)| k.as_ref() != "mode").cloned().collect();
            crate::builtins::open_value(s, &mode, &rest)?
        }
        "exists" => Value::Bool(crate::io::path_exists(s)),
        "is_file" => Value::Bool(if crate::io::effective_content(s)?.is_some() {
            true
        } else {
            !crate::io::is_staged_deleted(s) && std::path::Path::new(s.as_ref()).is_file()
        }),
        "is_dir" => Value::Bool(std::path::Path::new(s.as_ref()).is_dir()),
        "mkdir" | "unlink" => return fs_effect(s, name, args, &kw),
        other => return Err(refuse(&format!("PosixPath.{other}()"))),
    })
}

/// `str(v)`, but only for a value that already IS a str — every caller here
/// takes a name or a mode, and CPython would raise a TypeError rather than
/// convert.
fn fmt_str(v: &Value) -> R<String> {
    match v {
        Value::Str(s) => Ok(s.to_string()),
        other => Err(refuse(&format!(
            "a {} where CPython wants a str",
            type_name(other)
        ))),
    }
}

fn kwval(kw: &[(Rc<str>, Value)], name: &str) -> Option<Value> {
    kw.iter().find(|(k, _)| k.as_ref() == name).map(|(_, v)| v.clone())
}

/// `read_text`/`write_text` take `encoding` and `errors`; `open_value` already
/// knows which encodings this engine can answer for, and `errors=` changes what
/// a bad byte DECODES TO, so it is refused rather than dropped.
fn text_kw(kw: &[(Rc<str>, Value)]) -> R<Vec<(Rc<str>, Value)>> {
    for (k, _) in kw {
        if k.as_ref() != "encoding" {
            return Err(refuse(&format!("PosixPath text I/O with {}=…", k)));
        }
    }
    Ok(kw.to_vec())
}

/// `with_name`, and the one validation both CPython eras agree on: a name that
/// is empty, holds a separator, or is `'.'` is a ValueError on 3.14 and a
/// different answer on 3.12, so only the names both accept are built.
fn with_name(s: &str, name: &str) -> R<String> {
    if name.is_empty() || name.contains('/') || name == "." {
        return Err(refuse(&format!(
            "with_name({name:?}), which CPython answers with a ValueError"
        )));
    }
    let mut comps = tail(s);
    if comps.is_empty() {
        return Err(refuse(&format!(
            "with_name() on {s:?}, which has no name and which CPython answers with a ValueError"
        )));
    }
    let last = comps.len() - 1;
    comps[last] = name;
    Ok(rebuild(root(s), &comps))
}

/// `mkdir` and `unlink` — the two that change the disk. Both are exactly what
/// `os.mkdir`/`os.makedirs` and `os.remove` already do in `modules.rs`,
/// including the commit-barrier bookkeeping, because a second implementation of
/// the barrier is how a run stops being reversible without saying so.
fn fs_effect(s: &str, name: &str, args: &mut Args, kw: &[(Rc<str>, Value)]) -> R<Value> {
    if !crate::host::filesystem_allowed() {
        return Err(unsupported(
            "sandbox",
            &format!("PosixPath.{name}() with the filesystem denied"),
        ));
    }
    if !args.is_empty() {
        return Err(refuse(&format!("PosixPath.{name}() with positional arguments")));
    }
    if name == "unlink" {
        if !kw.is_empty() {
            return Err(refuse("PosixPath.unlink(missing_ok=…)"));
        }
        if !crate::io::path_exists(s) {
            return Err(LypningError::exc(
                "FileNotFoundError",
                format!("[Errno 2] No such file or directory: '{s}'"),
            ));
        }
        crate::io::stage_delete(s);
        return Ok(Value::None);
    }
    let mut parents = false;
    let mut exist_ok = false;
    for (k, v) in kw {
        match k.as_ref() {
            "parents" => parents = crate::value::truthy(v)?,
            "exist_ok" => exist_ok = crate::value::truthy(v)?,
            other => return Err(refuse(&format!("PosixPath.mkdir({other}=…)"))),
        }
    }
    let r = if parents {
        std::fs::create_dir_all(s)
    } else {
        std::fs::create_dir(s)
    };
    match r {
        Ok(()) => {
            // A directory cannot be staged, so making one is a real effect —
            // and whether the run is still re-runnable depends on whether doing
            // it twice differs from doing it once. `modules.rs` carries the
            // whole argument; this is the same rule for the same reason.
            if !exist_ok {
                crate::io::mark_committed();
            }
            Ok(Value::None)
        }
        Err(e) if exist_ok && e.kind() == std::io::ErrorKind::AlreadyExists => Ok(Value::None),
        Err(e) => Err(crate::io::os_error(s, &e)),
    }
}

// ---- the operators the interpreter hands here -----------------------------

/// `p / q`, in all three spellings CPython defines: `Path / str`, `Path / Path`
/// and `str / Path` (which is `__rtruediv__`, and joins in the SAME order).
/// Anything else is `None`, and the caller then raises the ordinary
/// `unsupported operand type(s) for /` — which is CPython's own answer, word
/// for word.
pub fn truediv(a: &Value, b: &Value) -> R<Option<Value>> {
    Ok(match (a, b) {
        (Value::Path(x, false), Value::Path(y, false)) => {
            Some(path_value(join(&[x.to_string(), y.to_string()])))
        }
        (Value::Path(x, false), Value::Str(y)) => {
            Some(path_value(join(&[x.to_string(), y.to_string()])))
        }
        (Value::Str(x), Value::Path(y, false)) => {
            Some(path_value(join(&[x.to_string(), y.to_string()])))
        }
        _ => None,
    })
}

/// `==` between two paths is `==` between their strings; a path is never equal
/// to the `str` that spells it. The `.parents` view compares by IDENTITY in
/// CPython (it is a fresh object every time `.parents` is read, so
/// `p.parents == p.parents` is False), which one `Rc<str>` cannot carry.
pub fn eq(a: &Value, b: &Value) -> R<Option<bool>> {
    match (a, b) {
        (Value::Path(_, true), _) | (_, Value::Path(_, true)) => Err(refuse(
            "== against a .parents view, which CPython compares by object identity",
        )),
        (Value::Path(x, false), Value::Path(y, false)) => Ok(Some(x == y)),
        _ => Ok(None),
    }
}

/// `<` between two paths — see trap 2. CPython compares `str(p).split('/')`,
/// whose first element is `''` for an absolute path; 3.11 compared `.parts`,
/// whose first element is `'/'`. The two orders agree exactly when both sides
/// carry the same root, so that is where this answers.
pub fn order(a: &Value, b: &Value) -> R<Option<std::cmp::Ordering>> {
    let (Value::Path(x, false), Value::Path(y, false)) = (a, b) else {
        return Ok(None);
    };
    if root(x) != root(y) {
        return Err(refuse(
            "ordering an absolute path against a relative one (or a '//' root against a '/' \
             one), where CPython 3.11 and 3.12+ disagree about the root component",
        ));
    }
    // CPython 3.11 ordered the `.parts` tuple; 3.12+ order `str(p).split('/')`.
    // For most paths the two agree, but not for a relative path whose text
    // carries a component `.parts` drops: `Path('.') < Path('-x')` is True by
    // parts (an empty tuple sorts first) and False by split ('.' > '-'). Answer
    // only where BOTH eras agree, and refuse where they do not — the same rule
    // this function already applies to differing roots.
    let by_parts = tail(x).cmp(&tail(y));
    let xs: Vec<&str> = x.split('/').collect();
    let ys: Vec<&str> = y.split('/').collect();
    let by_split = xs.cmp(&ys);
    if by_parts != by_split {
        return Err(refuse(
            "ordering these paths, where CPython 3.11 compares the .parts tuple and 3.12+ \
             compare str(p).split('/') and the two disagree",
        ));
    }
    Ok(Some(by_parts))
}

/// Everything the `.parents` view is asked that is not `len`, an index or
/// iteration: slicing (which CPython answers with a tuple), `reversed`, `in`,
/// and use as a dict key. Answered by the caller's generic path they would each
/// be a TypeError at exit 1 for a program CPython runs, so each refuses here.
pub fn guard_view(v: &Value, what: &str) -> R<()> {
    if matches!(v, Value::Path(_, true)) {
        return Err(refuse(&format!("{what} over a .parents view")));
    }
    Ok(())
}

pub fn view_len(s: &str) -> usize {
    parents_of(s).len()
}

pub fn view_index(s: &str, i: i64) -> R<Value> {
    let ps = parents_of(s);
    let n = ps.len() as i64;
    let k = if i < 0 { i + n } else { i };
    if k < 0 || k >= n {
        // CPython raises `IndexError(i)`. Refused: a program that catches it and
        // prints the exception would print this engine's text, at exit 0.
        return Err(refuse(&format!(
            "a .parents index out of range, which CPython answers with an IndexError"
        )));
    }
    Ok(path_value(ps[k as usize].clone()))
}

pub fn view_items(s: &str) -> Vec<Value> {
    parents_of(s).into_iter().map(path_value).collect()
}

/// `repr()`. `str()` of a path is the path text itself and is `fmt::to_str`'s
/// business; the view has no `str()` of its own, so both are this.
pub fn repr(s: &str, view: bool) -> R<String> {
    if view {
        return Ok("<PosixPath.parents>".to_string());
    }
    Ok(format!("PosixPath({})", fmt::str_repr(s)?))
}

/// `isinstance(x, Path)`. `Path` is not a builtin NAME — it resolves only
/// through `pathlib` — but it arrives as the `Value::Builtin` every type object
/// is, so the comparison is by that name rather than by `type_name`, which is
/// `PosixPath`.
pub fn isinstance_hit(cls: &str, v: &Value) -> bool {
    matches!(cls, "Path" | "PosixPath") && matches!(v, Value::Path(_, false))
}

#[cfg(test)]
mod tests {
    use super::*;

    /// The oracle's one pathlib family, as a unit test as well as a grid row:
    /// the root is a component of `.parts` and dropping it is a wrong answer at
    /// exit 0. `lypning oracle --full`, family `pathlib-parts-drops-root`.
    #[test]
    fn parts_keeps_the_root() {
        assert_eq!(parts("/a/b/c.txt"), vec!["/", "a", "b", "c.txt"]);
        assert_eq!(parts("a/b"), vec!["a", "b"]);
        assert_eq!(parts("."), Vec::<&str>::new());
        assert_eq!(parts("/"), vec!["/"]);
        assert_eq!(parts("//a"), vec!["//", "a"]);
    }

    #[test]
    fn normalisation_is_cpythons() {
        for (raw, want) in [
            ("", "."),
            (".", "."),
            ("a/", "a"),
            ("a//b", "a/b"),
            ("a/./b", "a/b"),
            ("a/../b", "a/../b"),
            ("/", "/"),
            ("//", "//"),
            ("///", "/"),
            ("/.", "/"),
            ("./a", "a"),
        ] {
            assert_eq!(normalize(raw), want, "{raw}");
        }
    }

    #[test]
    fn the_names_the_two_cpythons_split_differently_refuse() {
        for name in ["a.", "a..", "..a"] {
            assert!(ambiguous(name), "{name}");
            assert!(stem_suffix(name).is_err(), "{name}");
        }
        for name in ["..", "...", ".hidden", "a.tar.gz", "a"] {
            assert!(!ambiguous(name), "{name}");
        }
        assert_eq!(stem_suffix("a.tar.gz").unwrap(), ("a.tar", ".gz"));
        assert_eq!(stem_suffix(".hidden").unwrap(), (".hidden", ""));
        assert_eq!(stem_suffix("..").unwrap(), ("..", ""));
    }

    #[test]
    fn parents_are_shortest_last() {
        assert_eq!(parents_of("/a/b/c.txt"), vec!["/a/b", "/a", "/"]);
        assert_eq!(parents_of("a/b"), vec!["a", "."]);
        assert_eq!(parents_of("."), Vec::<String>::new());
        assert_eq!(parents_of("/"), Vec::<String>::new());
    }
}
