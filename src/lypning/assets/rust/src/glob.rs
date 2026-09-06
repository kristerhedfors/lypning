//! `glob` — the whole of the `cap-glob` capability, compiled into `lypning-l`
//! and into nothing smaller. Every line of this file, and every line that
//! reaches it, is behind `cfg(feature = "cap-glob")`.
//!
//! **The invariant this module exists to hold: the match SET is computed
//! exactly, and the match ORDER is never computed at all — because a call
//! whose order could be observed never reaches this file.**
//!
//! CPython walks a directory with `os.scandir` and does not sort, so the order
//! of a multi-match result is whatever the filesystem handed back. That is the
//! same fact that makes `os.listdir()` a refusal here (kind `os-listdir`,
//! `modules.rs`) and `Path.glob()`/`.rglob()`/`.iterdir()` refusals in
//! `pathlib.rs`. glob inherits the fact, so it inherits the refusal.
//!
//! **Where the refusal lives is the whole design.** It is in `route.rs`, in the
//! WALK, and it is static: a `glob.glob(...)` call is admitted only where the
//! walker can see that the order cannot be observed — wrapped directly in
//! `sorted()`, `len()`, `set()`, `bool()`, `sum()`, `min()`/`max()`,
//! `any()`/`all()`, or as the right operand of `in`. Every other position — a
//! bare `for p in glob.glob(...)`, `print(glob.glob(...))`, an assignment, an
//! index, a slice — is a `glob-order` blocker before the program starts.
//!
//! Two consequences, and both are the point:
//!
//!   1. **`glob.glob()` returns a plain [`Value::List`].** There is no
//!      `Value::Glob`, no order taint, and therefore no arm of `ops.rs`,
//!      `fmt.rs`, `value.rs`, `json.rs`, `iter.rs` or `builtins.rs` that has to
//!      remember a new variant exists. The first attempt at this capability
//!      (branch `cap-glob`, `docs/HILLCLIMB.md` iteration 76) added the variant
//!      and missed `set_item`, `del_item`, slice assignment and AugAssign's
//!      in-place-extend arm; `g += [...]` rebound instead of mutating and left
//!      every alias stale, at exit 0.
//!   2. **The refusal happens before anything runs.** A runtime `glob-order`
//!      reached after `os.makedirs()` has committed the barrier is exit 1 with
//!      the output discarded, which the chain never retries — the first attempt
//!      turned correct programs into failures exactly that way. A static
//!      blocker costs the program nothing: it was never started here.
//!
//! **`glob.iglob` is served and returns the same list.** In every position the
//! router admits, the generator is consumed exactly once and immediately, so a
//! list is byte-identical — except `len()`, which is a `TypeError` on a
//! generator, and which `route.rs` therefore does not bless for `iglob`. A
//! generator can never be BOUND here, because binding is not an admitted
//! position, so the one shape where the two really differ — iterating it twice
//! — is unreachable.
//!
//! **`glob.escape` and `glob.has_magic` are pure string algebra** and are
//! answered exactly, in any position. `glob.translate`, `glob.glob0` and
//! `glob.glob1` are not served: they refuse through `module-attr`, which the
//! router also sees statically.
//!
//! **The barrier is not invisible to a listing, so the listing merges it.**
//! `io.rs` stages every write until the run ends, which is what lets a refusal
//! fall onward with nothing left behind. `open()` and `os.path.exists()` merge
//! the staging into their answer because they are asked about one path they
//! were handed; a directory listing has to work out which staged spelling names
//! an entry of which directory, and [`listdir`] does it by comparing normalised
//! paths — the same `normpath` `os.path.normpath` answers with. Without it
//! `open("d/a.py","w"); glob.glob("d/*.py")` would answer `[]` at exit 0 for a
//! file the program had just written, which is the worst outcome this
//! repository has.
//!
//! **Three exactness traps, each measured against CPython 3.14.5**
//! (`tests/test_glob_grid.py` holds all three):
//!
//!   1. **A leading `.` is not matched by `*`, `?` or `[...]`.** `_glob1` drops
//!      hidden names unless the PATTERN's own basename starts with a dot, so
//!      `glob('*')` never sees `.hidden` while `glob('.*')` sees only hidden
//!      names. `_glob0` — a basename with no magic — does not filter, so
//!      `glob('.hidden')` finds it. `_rlistdir`, which is what `**` walks with,
//!      drops hidden names at every level.
//!   2. **A pattern with no magic never lists anything.** It is an `lexists`
//!      test, so a BROKEN SYMLINK matches where an `exists` test would not. A
//!      pattern ending in `/` is the other half of that rule: the basename is
//!      empty, so it matches only if the directory exists, and the result keeps
//!      the trailing slash.
//!   3. **`recursive=True` and `**`.** `**` matches the empty path first, so
//!      `glob('a/**', recursive=True)` yields `'a/'` before anything under it,
//!      and `glob('**/*.py', recursive=True)` matches `a.py` at the top level as
//!      well as `d/a.py`. `**` is only special as a WHOLE component and only
//!      with `recursive=True`; anywhere else — `'**x'`, or `'**'` without the
//!      keyword — it is an ordinary `*`.
//!
//! Every error path refuses rather than raising, for the reason invariant 1
//! gives: a `TypeError`'s wording is CPython's and moves between releases.

use crate::args::Args;
use crate::err::{unsupported, LypningError, R};
use crate::eval::Interp;
use crate::io as mio;
use crate::modules::normpath;
use crate::value::{list, truthy, Value};
use std::rc::Rc;

/// The names this module serves. Everything else under `glob` is a
/// `module-attr` refusal, which the router sees statically.
const SERVED: &[&str] = &["escape", "glob", "has_magic", "iglob"];

pub fn refuse(what: &str) -> LypningError {
    unsupported("glob", what)
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
    // One positional, and it must already be a `str`: `os.fspath` on a Path, a
    // `bytes` pattern and an int file descriptor are CPython's coercions and
    // CPython's TypeErrors, and none of the three is worth reproducing.
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
    // `glob(pathname, *, root_dir=None, dir_fd=None, recursive=False,
    // include_hidden=False)` — everything after the pattern is keyword-only, so
    // a second positional is a TypeError and not a parameter.
    if args.len() > 1 {
        return Err(refuse(&format!("glob.{name}() with extra positional arguments")));
    }
    match name {
        // `escape` wraps each of `*?[` in a bracket expression. The drive part
        // CPython splits off first is always empty on POSIX.
        "escape" => {
            no_kw(name, kw)?;
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
                    // `root_dir=` and `dir_fd=` do not appear anywhere in the
                    // corpus (mined 2026-09-06) and `include_hidden=` changes
                    // the hidden-name rule at every level of the walk. All
                    // three refuse rather than being approximated.
                    other => return Err(refuse(&format!("glob.{name}({other}=…)"))),
                }
            }
            let mut out = Vec::new();
            iglob(it, &mut out, &pat, recursive, false)?;
            // `iglob` yields an empty string first when the pattern is empty or
            // STARTS with `**` under `recursive=`; CPython drops that one item
            // and keeps everything after it.
            if (pat.is_empty() || (recursive && pat.starts_with("**")))
                && out.first().map(String::is_empty) == Some(true)
            {
                out.remove(0);
            }
            Ok(list(out.into_iter().map(|p| Value::Str(p.into())).collect()))
        }
    }
}

fn no_kw(name: &str, kw: &[(Rc<str>, Value)]) -> R<()> {
    match kw.first() {
        Some((k, _)) => Err(refuse(&format!("glob.{name}({k}=…)"))),
        None => Ok(()),
    }
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

/// Which DIRECTORY a path names, as an identity two spellings can be compared
/// on: the real path, with symlinks resolved.
///
/// `os.path.normpath` is not enough and the grid says why. `d/e` and `dlink/e`
/// where `dlink -> d` are ONE directory, so a file staged as `d/e/new.py` is an
/// entry of both — and a lexical comparison sees two directories and drops the
/// entry from the second, which is a listing short one name at exit 0. The
/// lexical answer is the fallback for a directory that cannot be resolved,
/// which a listing has already failed to read.
fn real_dir(d: &str) -> String {
    let d = if d.is_empty() { "." } else { d };
    match std::fs::canonicalize(d) {
        Ok(q) => q.to_string_lossy().into_owned(),
        Err(_) => normpath(d),
    }
}

/// Is `(dir, name)` one of these staged paths? The comparison both halves of
/// the staging merge are built on.
fn staged_here(paths: &[String], dir: &str, name: &str) -> bool {
    paths.iter().any(|p| {
        let (h, t) = split(p);
        t == name && real_dir(h) == dir
    })
}

/// `os.path.lexists`, as the PROGRAM sees it — an `lstat`, so a broken symlink
/// is still a match, plus whatever the commit barrier is holding back. A path
/// this run has written but not committed exists; one it has removed does not.
fn lexists(p: &str) -> bool {
    if mio::staging_active() {
        let (head, tail) = split(p);
        let dir = real_dir(head);
        if staged_here(&mio::staged_delete_paths(), &dir, tail) {
            return false;
        }
        if staged_here(&mio::staged_write_paths(), &dir, tail) {
            return true;
        }
    }
    std::fs::symlink_metadata(p).is_ok()
}

/// `os.path.isdir` — a `stat`, so a symlink to a directory IS one. The barrier
/// never stages a directory (there is no content to hold back), so this needs
/// no staging half.
fn isdir(p: &str) -> bool {
    std::fs::metadata(p).map(|m| m.is_dir()).unwrap_or(false)
}

/// How deep `**` walks before refusing. CPython has no limit and raises
/// `RecursionError` at its own; a symlink loop makes the walk unbounded on
/// both, and an unbounded walk here is a stack overflow — a signal, which the
/// dispatcher cannot route onward at all. So the walk is bounded and the bound
/// is a refusal.
const MAX_DEPTH: usize = 128;

/// `glob._listdir`, merged with the commit barrier.
///
/// An unreadable directory is EMPTY, not an error: CPython swallows `OSError`
/// here, so a permission denial is part of the answer and not a failure. A name
/// that is not valid UTF-8 is the one thing that is — CPython would hand the
/// program a surrogate-escaped `str` this engine has no way to spell, so it
/// refuses rather than drop the entry.
///
/// The staged half is why `open("d/a.py","w"); glob.glob("d/*.py")` is right:
/// a file this run wrote is an entry of its directory even though it is not on
/// disk yet, and a file this run removed is not an entry even though it is. The
/// attribution is by `normpath`, so `./d/a.py` and `d/a.py` are one path. Order
/// is not preserved and does not need to be — see the module note.
fn listdir(dirname: &str, dironly: bool) -> R<Vec<String>> {
    let mut out = Vec::new();
    let rd = std::fs::read_dir(if dirname.is_empty() { "." } else { dirname });
    if let Ok(rd) = rd {
        for e in rd.flatten() {
            if dironly {
                // `entry.is_dir()` follows the symlink, so a link to a
                // directory counts as one; an entry whose type cannot be read
                // is skipped, which is CPython's inner `except OSError: pass`.
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
    }
    if !mio::staging_active() {
        return Ok(out);
    }
    let dir = real_dir(dirname);
    let gone = mio::staged_delete_paths();
    if !gone.is_empty() {
        out.retain(|n| !staged_here(&gone, &dir, n));
    }
    // A staged write is always a FILE, so it is invisible to a `dironly` pass.
    if !dironly {
        for p in mio::staged_write_paths() {
            let (head, tail) = split(&p);
            if tail.is_empty() || real_dir(head) != dir {
                continue;
            }
            if !out.iter().any(|x| x == tail) {
                out.push(tail.to_string());
            }
        }
    }
    Ok(out)
}

/// `glob._iglob`, appending to `out` in CPython's own yield order.
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
fn glob2(
    it: &mut Interp,
    out: &mut Vec<String>,
    dirname: &str,
    dironly: bool,
    depth: usize,
) -> R<()> {
    if dirname.is_empty() || isdir(dirname) {
        out.push(String::new());
    }
    rlistdir(it, out, dirname, dironly, depth)
}

/// `glob._rlistdir`: every name below `dirname`, relative to it, hidden names
/// dropped at every level.
fn rlistdir(
    it: &mut Interp,
    out: &mut Vec<String>,
    dirname: &str,
    dironly: bool,
    depth: usize,
) -> R<()> {
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
/// `fnmatch.translate` anchors both ends and compiles with `(?s:…)`, so this is
/// a whole-name match and a newline in a name is an ordinary character.
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
/// `None` when there is no closing `]` at all. The scan is CPython's: a `!` and
/// then a `]` immediately after the `[` are both part of the body, so `[]]`
/// matches a `]` and `[!]]` matches anything else.
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
/// `[z-a]` — is where CPython's translation stops being a plain character class
/// and starts merging chunks, so it refuses rather than guess which of the two
/// readings the regex ended up with.
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
