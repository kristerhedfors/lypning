//! The module surface: `MODULES` below — and, on the variant built with the
//! `cap-*` feature for it, `collections`, `pathlib`, `re`, `csv`, `glob` and
//! `base64` and `hashlib`.
//!
//! Chosen from the corpus, in frequency order: `sys` (82 imports), `json` (74),
//! `io` (63 — almost entirely `io.open(p, encoding='utf-8').read()`, which is
//! the file-read idiom agents actually type), `os` (21). `re` is on the larger
//! variant only, and as a SURFACE — `re.rs`: the flags, `escape`, `purge` and
//! the names of the matcher-backed functions. A program that imports it is
//! admitted where it used to be a routing decision; a program that CALLS a
//! matcher is still one — `route.rs` sees the call statically and sends it to
//! CPython, and `lypning conformance --plan` ranks what that costs. The runtime
//! refusal in `re.rs` is the backstop for the reach a static walk cannot see.

use crate::args::Args;
use crate::err::*;
use crate::eval::Interp;
use crate::fmt;
use crate::io as mio;
use crate::json;
use crate::value::*;
use std::cell::RefCell;
use std::rc::Rc;

/// Modules lypning can serve. `route.rs` reads this to decide whether a program's
/// imports are within reach before anything is executed.
///
/// Spelled twice rather than appended to, so the smaller variant's table is the
/// same bytes it always was: a capability that added an entry at runtime would
/// still have compiled the branch that adds it. `route::CAPS` carries the same
/// claim for the ROUTER, which has to answer for a sibling it is not.
#[cfg(not(any(
    feature = "cap-collections",
    feature = "cap-pathlib",
    feature = "cap-re",
    feature = "cap-csv",
    feature = "cap-glob",
    feature = "cap-base64"
)))]
pub const MODULES: &[&str] = &["sys", "os", "os.path", "io", "json", "posixpath", "random"];
#[cfg(all(
    feature = "cap-collections",
    not(feature = "cap-pathlib"),
    not(feature = "cap-re"),
    not(feature = "cap-csv"),
    not(feature = "cap-glob"),
    not(feature = "cap-base64")
))]
pub const MODULES: &[&str] =
    &["sys", "os", "os.path", "io", "json", "posixpath", "random", "collections"];
#[cfg(all(
    feature = "cap-pathlib",
    not(feature = "cap-collections"),
    not(feature = "cap-re"),
    not(feature = "cap-csv"),
    not(feature = "cap-glob"),
    not(feature = "cap-base64")
))]
pub const MODULES: &[&str] =
    &["sys", "os", "os.path", "io", "json", "posixpath", "random", "pathlib"];
#[cfg(all(
    feature = "cap-collections",
    feature = "cap-pathlib",
    not(feature = "cap-re"),
    not(feature = "cap-csv"),
    not(feature = "cap-glob"),
    not(feature = "cap-base64")
))]
pub const MODULES: &[&str] = &[
    "sys", "os", "os.path", "io", "json", "posixpath", "random", "collections", "pathlib",
];
#[cfg(all(
    feature = "cap-collections",
    feature = "cap-pathlib",
    feature = "cap-re",
    not(feature = "cap-csv"),
    not(feature = "cap-glob"),
    not(feature = "cap-base64")
))]
pub const MODULES: &[&str] = &[
    "sys", "os", "os.path", "io", "json", "posixpath", "random", "collections", "pathlib", "re",
];
#[cfg(all(
    feature = "cap-collections",
    feature = "cap-pathlib",
    feature = "cap-re",
    feature = "cap-csv",
    not(feature = "cap-glob"),
    not(feature = "cap-base64")
))]
pub const MODULES: &[&str] = &[
    "sys", "os", "os.path", "io", "json", "posixpath", "random", "collections", "pathlib", "re",
    "csv",
];
#[cfg(all(
    feature = "cap-collections",
    feature = "cap-pathlib",
    feature = "cap-re",
    feature = "cap-csv",
    feature = "cap-glob",
    not(feature = "cap-base64"),
    not(feature = "cap-hashlib")
))]
pub const MODULES: &[&str] = &[
    "sys", "os", "os.path", "io", "json", "posixpath", "random", "collections", "pathlib", "re",
    "csv", "glob",
];
#[cfg(all(
    feature = "cap-collections",
    feature = "cap-pathlib",
    feature = "cap-re",
    feature = "cap-csv",
    feature = "cap-glob",
    feature = "cap-base64",
    not(feature = "cap-hashlib")
))]
pub const MODULES: &[&str] = &[
    "sys", "os", "os.path", "io", "json", "posixpath", "random", "collections", "pathlib", "re",
    "csv", "glob", "base64",
];
#[cfg(all(
    feature = "cap-collections",
    feature = "cap-pathlib",
    feature = "cap-re",
    feature = "cap-csv",
    feature = "cap-glob",
    feature = "cap-base64",
    feature = "cap-hashlib"
))]
pub const MODULES: &[&str] = &[
    "sys", "os", "os.path", "io", "json", "posixpath", "random", "collections", "pathlib", "re",
    "csv", "glob", "base64", "hashlib",
];
// The rows above are the build-order CHAIN, not every subset: each capability
// appends one row and stops the row before it. `cap-re`, `cap-csv`, `cap-glob`,
// `cap-base64` and `cap-hashlib` are never built except as part of `variant-l`,
// whose feature names the full set — which is what the five guards below say,
// each naming the caps that precede it in the chain.
#[cfg(all(feature = "cap-re", not(all(feature = "cap-collections", feature = "cap-pathlib"))))]
compile_error!("cap-re is only built as part of variant-l (it names the full set)");
#[cfg(all(
    feature = "cap-csv",
    not(all(feature = "cap-collections", feature = "cap-pathlib", feature = "cap-re"))
))]
compile_error!("cap-csv is only built as part of variant-l (it names the full set)");
#[cfg(all(
    feature = "cap-glob",
    not(all(
        feature = "cap-collections",
        feature = "cap-pathlib",
        feature = "cap-re",
        feature = "cap-csv"
    ))
))]
compile_error!("cap-glob is only built as part of variant-l (it names the full set)");
#[cfg(all(
    feature = "cap-base64",
    not(all(
        feature = "cap-collections",
        feature = "cap-pathlib",
        feature = "cap-re",
        feature = "cap-csv",
        feature = "cap-glob"
    ))
))]
compile_error!("cap-base64 is only built as part of variant-l (it names the full set)");
#[cfg(all(
    feature = "cap-hashlib",
    not(all(
        feature = "cap-collections",
        feature = "cap-pathlib",
        feature = "cap-re",
        feature = "cap-csv",
        feature = "cap-glob",
        feature = "cap-base64"
    ))
))]
compile_error!("cap-hashlib is only built as part of variant-l (it names the full set)");

pub fn import(path: &str) -> R<Value> {
    match MODULES.iter().find(|m| **m == path) {
        Some(m) => Ok(Value::Module(m)),
        None => Err(unsupported("module", &format!("import {path}"))),
    }
}

pub fn get_attr(m: &Value, name: &str) -> R<Value> {
    let Value::Module(mname) = m else {
        return Err(attr_err(format!("'{}' has no attribute", type_name(m))));
    };
    Ok(match (*mname, name) {
        // `sys.argv` is NOT the process argv. For `python -c PROG a b` CPython
        // gives `['-c', 'a', 'b']`; for `python f.py a b` it gives
        // `['f.py', 'a', 'b']`. Reproducing that exactly matters — every
        // `sys.argv[1:]` one-liner depends on it.
        ("sys", "argv") => {
            // Embedded there is no process argv to reconstruct this from: the
            // host hands over the list the program must see, already in
            // CPython's shape, because only the host knows whether it is
            // running a `-c` string or a named file.
            if let Some(given) = crate::host::argv_override() {
                return Ok(list(given.into_iter().map(|a| Value::Str(a.into())).collect()));
            }
            let mut raw: Vec<String> = std::env::args().skip(1).collect();
            // Under `lypning run -c PROG a b` the dispatcher's own subcommand is
            // in the process argv but must never be in `sys.argv`.
            if raw.first().map(String::as_str) == Some("run") {
                raw.remove(0);
            }
            let mut out: Vec<Value> = Vec::new();
            let mut i = 0;
            while i < raw.len() {
                match raw[i].as_str() {
                    "-c" => {
                        out.push(Value::Str("-c".into()));
                        i += 2;
                        break;
                    }
                    "-" => {
                        out.push(Value::Str("-".into()));
                        i += 1;
                        break;
                    }
                    f if f.starts_with('-') => i += 1,
                    path => {
                        out.push(Value::Str(path.into()));
                        i += 1;
                        break;
                    }
                }
            }
            out.extend(raw[i.min(raw.len())..].iter().map(|a| Value::Str(a.as_str().into())));
            list(out)
        }
        ("sys", "stdin") => Value::Module("sys.stdin"),
        ("sys", "stdout") => Value::Module("sys.stdout"),
        ("sys", "stderr") => Value::Module("sys.stderr"),
        ("sys", "platform") => Value::Str("linux".into()),
        ("sys", "maxsize") => ival(i64::MAX),
        ("sys", "exit") => Value::Bound(Rc::new(m.clone()), "exit"),
        ("sys", "path") => {
            return Err(unsupported(
                "module-attr",
                "sys.path (lypning has no import machinery)",
            ))
        }
        // Not `buffer`: it was handed back as a bound METHOD, so
        // `sys.stdin.buffer.read()` was an AttributeError at exit 1 where
        // CPython returns bytes. It falls to the `module-attr` arm below and
        // refuses — statically too, since the router resolves `sys.stdin`.
        ("sys.stdin", "read" | "readline" | "readlines") => {
            Value::Bound(Rc::new(m.clone()), interned(name)?)
        }
        ("sys.stdout" | "sys.stderr", "write" | "flush") => {
            Value::Bound(Rc::new(m.clone()), interned(name)?)
        }
        ("os", "path") => Value::Module("os.path"),
        ("os", "sep") => Value::Str("/".into()),
        ("os", "linesep") => Value::Str("\n".into()),
        // The ONE place `Dict::environ` is set. It is what makes the four
        // operations that NAME this mapping — `type()`, `repr`/`str`,
        // `isinstance` and `json.dumps` — able to tell it from the `{}` it is
        // otherwise modelled on; everything that merely READS it is a dict's
        // job and stays one. `dict(os.environ)` and `os.environ.copy()` build a
        // fresh untagged `Dict`, which is a plain dict in CPython too.
        ("os", "environ") => {
            let mut d = Dict::new();
            for (k, v) in std::env::vars() {
                d.insert(Value::Str(k.into()), Value::Str(v.into()))?;
            }
            d.environ = true;
            Value::Dict(Rc::new(RefCell::new(d)))
        }
        (
            "os",
            "getcwd" | "listdir" | "makedirs" | "mkdir" | "remove" | "unlink" | "rename"
            | "getenv" | "rmdir" | "replace",
        ) => Value::Bound(Rc::new(m.clone()), interned(name)?),
        (
            "os.path" | "posixpath",
            "join" | "exists" | "basename" | "dirname" | "splitext" | "abspath" | "isfile"
            | "isdir" | "getsize" | "expanduser" | "split" | "relpath" | "normpath" | "islink",
        ) => Value::Bound(Rc::new(Value::Module("os.path")), interned(name)?),
        ("io", "open") => Value::Builtin("open"),
        // The seeded-integer subset; every other name refuses below.
        ("random", "seed" | "random" | "randint" | "randrange" | "choice" | "getrandbits") => {
            Value::Bound(Rc::new(m.clone()), interned(name)?)
        }
        ("json", "loads" | "dumps" | "load" | "dump") => {
            Value::Bound(Rc::new(m.clone()), interned(name)?)
        }
        ("json", "JSONDecodeError") => Value::Builtin("ValueError"),
        // The two names 83 corpus programs import `collections` for. They are
        // TYPES, not module functions, so they are `Value::Builtin` — the same
        // shape `int` and `list` have, which is what makes
        // `isinstance(c, Counter)` fall out of the existing comparison.
        // Every other name under `collections` (`OrderedDict`, `deque`,
        // `namedtuple`, …) refuses through the arm below.
        #[cfg(feature = "cap-collections")]
        ("collections", "Counter") => Value::Builtin("Counter"),
        #[cfg(feature = "cap-collections")]
        ("collections", "defaultdict") => Value::Builtin("defaultdict"),
        // `Path` is a TYPE, so it is the `Value::Builtin` every type object is
        // here — which is what makes `isinstance(p, Path)` a name comparison
        // rather than a second spelling. `PosixPath` is the SAME class on POSIX,
        // repr included, so it is the same value; `PurePosixPath` and `PurePath`
        // are not — they repr as `PurePosixPath('a')`, and aliasing them would
        // print `PosixPath('a')` at exit 0 — so they refuse below along with
        // `WindowsPath`, `Path.home` and the rest.
        #[cfg(feature = "cap-pathlib")]
        ("pathlib", "Path" | "PosixPath") => Value::Builtin("Path"),
        // The `re` surface: flags, `escape`, `purge`, and the matcher-backed
        // names that refuse when called. Everything else under `re` —
        // `re.error`, `re.Pattern`, `re.TEMPLATE` — refuses through the same
        // `module-attr` kind the arm below raises, which is what blocks it
        // statically in the router too.
        #[cfg(feature = "cap-re")]
        ("re", _) => return crate::re::module_attr(name),
        // `csv.reader`, `csv.DictReader` and the four `QUOTE_*` constants.
        // Everything else under `csv` — `writer`, `DictWriter`, `Sniffer`,
        // `field_size_limit`, `Error` — refuses with the `module-attr` kind,
        // which is what makes it a STATIC block in the router: `route.rs`
        // resolves `csv.writer` through this very function during the walk, so
        // the program never starts. That is the whole shape of this capability
        // (`csv.rs`), and it is why the writers cost no bytes at all.
        #[cfg(feature = "cap-csv")]
        ("csv", _) => return crate::csv::module_attr(name),
        // `glob.glob`, `glob.iglob`, `glob.escape` and `glob.has_magic`. Every
        // other name — `translate`, `glob0`, `glob1` — refuses with the
        // `module-attr` kind, which the router blocks on statically.
        #[cfg(feature = "cap-glob")]
        ("glob", _) => return crate::glob::module_attr(name),
        // `base64.b64encode`, `b64decode`, `urlsafe_b64encode`,
        // `urlsafe_b64decode`. Every other name — `b16encode`, `b32decode`,
        // `a85encode`, `encodebytes`, `standard_b64encode` — refuses with the
        // `module-attr` kind, which `route::MODULE_ATTRS` makes a STATIC block
        // in the core's walk: the router reads that table, not this match.
        #[cfg(feature = "cap-base64")]
        ("base64", _) => return crate::base64::module_attr(name),
        // `hashlib.md5` / `sha1` / `sha256` / `sha512`. Every other name —
        // `new`, `algorithms_guaranteed`, `blake2b`, `sha3_256`, `pbkdf2_hmac`
        // — refuses with the `module-attr` kind, which the router blocks on
        // statically out of `route::MODULE_ATTRS`, in the CORE's walk.
        #[cfg(feature = "cap-hashlib")]
        ("hashlib", _) => return crate::hashlib::module_attr(name),
        _ => {
            return Err(unsupported(
                "module-attr",
                &format!("{mname}.{name}"),
            ))
        }
    })
}

/// Method names are stored as `&'static str` in `Value::Bound`; this maps a
/// borrowed name onto the static one, refusing anything not in the table.
fn interned(name: &str) -> R<&'static str> {
    const NAMES: &[&str] = &[
        "read", "readline", "readlines", "write", "flush", "exit", "getcwd", "listdir", "makedirs",
        "mkdir", "remove", "unlink", "rename", "replace", "rmdir", "getenv", "join", "exists",
        "basename", "dirname", "splitext", "abspath", "isfile", "isdir", "getsize", "expanduser",
        "split", "relpath", "normpath", "islink", "loads", "dumps", "load", "dump",
        "seed", "random", "randint", "randrange", "choice", "getrandbits",
    ];
    NAMES
        .iter()
        .find(|n| **n == name)
        .copied()
        .ok_or_else(|| unsupported("module-attr", name))
}

/// Does this module call reach the disk?
///
/// Conservative by construction: everything under `os` and `os.path` counts
/// EXCEPT the handful that are pure string arithmetic over a path that need
/// never exist. Getting this list wrong in the safe direction costs a refusal
/// the host can route onward; getting it wrong the other way would let a
/// denied program read a file, so a new `os` function is denied until someone
/// adds it here on purpose.
fn touches_disk(m: &str, name: &str) -> bool {
    match (m, name) {
        ("os.path", "join" | "basename" | "dirname" | "splitext" | "split" | "normpath") => false,
        ("pathlib", "cwd") => true,
        // `glob.glob()` and `glob.iglob()` list directories; `escape` and
        // `has_magic` are string algebra over a path that need never exist.
        #[cfg(feature = "cap-glob")]
        ("glob", "escape" | "has_magic") => false,
        #[cfg(feature = "cap-glob")]
        ("glob", _) => true,
        ("os", "getenv") => false,
        ("os", _) | ("os.path", _) => true,
        _ => false,
    }
}

pub fn call_module_method(
    it: &mut Interp,
    m: &str,
    name: &str,
    args: &mut Args,
    kw: Vec<(Rc<str>, Value)>,
) -> R<Value> {
    if !crate::host::filesystem_allowed() && touches_disk(m, name) {
        return Err(unsupported(
            "sandbox",
            &format!("{m}.{name}() with the filesystem denied"),
        ));
    }
    let s = |i: usize| -> R<String> {
        match args.get(i) {
            Some(v) => fmt::to_str(v),
            None => Err(type_err(format!("{name}() missing required argument"))),
        }
    };
    Ok(match (m, name) {
        #[cfg(feature = "cap-re")]
        ("re", _) => return crate::re::call(it, name, args, &kw),
        #[cfg(feature = "cap-csv")]
        ("csv", _) => return crate::csv::call(it, name, args, &kw),
        #[cfg(feature = "cap-glob")]
        ("glob", _) => return crate::glob::call(it, name, args, &kw),
        #[cfg(feature = "cap-base64")]
        ("base64", _) => return crate::base64::call(it, name, args, &kw),
        #[cfg(feature = "cap-hashlib")]
        ("hashlib", _) => return crate::hashlib::call(it, name, args, &kw),
        ("random", _) => return crate::random::call(it, name, args, &kw),
        // `Path.cwd()`. A classmethod on the type object, reached through
        // `ops::get_attr`, which spells it as a method on the module so that
        // the one `filesystem_allowed` gate above covers it exactly as it
        // covers `os.getcwd`.
        #[cfg(feature = "cap-pathlib")]
        ("pathlib", "cwd") => return crate::pathlib::cwd(),
        // `sys.exit(x)` is `raise SystemExit(x)`, and it is the SAME exception
        // here — one an `except SystemExit` catches and a `finally` runs for.
        // Reading the status back out of it is `LypningError::is_exit`.
        ("sys", "exit") => {
            if !kw.is_empty() {
                // CPython: TypeError "sys.exit() takes no keyword arguments".
                // Refused rather than raised: the text is version-shaped.
                return Err(unsupported("exception", "sys.exit() with keyword arguments"));
            }
            // `sys.exit(None)` raises a BARE `SystemExit()` — `PyErr_SetObject`
            // with a None value calls the class with no arguments — so
            // `e.args == ()`, where `raise SystemExit(None)` carries `(None,)`.
            let msg = if args.len() == 1 && matches!(args.first(), Some(Value::None)) {
                String::new()
            } else {
                crate::builtins::system_exit_msg(args)?
            };
            return Err(LypningError::exc("SystemExit", msg));
        }
        // One cursor serves all four of these, `input()`, `for line in
        // sys.stdin` and a `csv.reader` over the stream — which is what makes
        // an interleaving of them come out in CPython's order. The reader used
        // to DRAIN the one stream a program cannot reopen, and three of these
        // carried a guard saying so; it is lazy now, and takes one line per row
        // like everything else here.
        ("sys.stdin", "read") => {
            Value::Str(crate::iter::decode_text(&mio::stdin_rest()?, "non-UTF-8 bytes on stdin (CPython decodes it with surrogateescape)")?)
        }
        ("sys.stdin", "readline") => {
            match mio::stdin_line()? {
                Some(b) => Value::Str(crate::iter::decode_text(&b, "non-UTF-8 bytes on stdin (CPython decodes it with surrogateescape)")?),
                None => Value::Str("".into()),
            }
        }
        ("sys.stdin", "readlines") => {
            let mut out = Vec::new();
            while let Some(b) = mio::stdin_line()? {
                out.push(Value::Str(crate::iter::decode_text(&b, "non-UTF-8 bytes on stdin (CPython decodes it with surrogateescape)")?));
            }
            list(out)
        }
        ("sys.stdout", "write") | ("sys.stderr", "write") => {
            let text = s(0)?;
            if m == "sys.stderr" {
                mio::write_err(text.as_bytes())?;
            } else {
                mio::write_out(text.as_bytes())?;
            }
            ival(text.chars().count() as i64)
        }
        ("sys.stdout", "flush") | ("sys.stderr", "flush") => Value::None,
        ("os", "getcwd") => Value::Str(
            std::env::current_dir()
                .map_err(|e| mio::os_error(".", &e))?
                .to_string_lossy()
                .into_owned()
                .into(),
        ),
        ("os", "getenv") => match std::env::var(s(0)?) {
            Ok(v) => Value::Str(v.into()),
            Err(_) => args.get(1).cloned().unwrap_or(Value::None),
        },
        ("os", "listdir") => {
            let p = if args.is_empty() { ".".to_string() } else { s(0)? };
            let mut names: Vec<String> = std::fs::read_dir(&p)
                .map_err(|e| mio::os_error(&p, &e))?
                .filter_map(|e| e.ok().map(|e| e.file_name().to_string_lossy().into_owned()))
                .collect();
            // CPython returns them in the order the OS gives, which is not
            // reproducible. Sorting is a DIFFERENT answer, so refuse instead —
            // unless the caller is going to sort anyway, which it cannot say.
            names.sort();
            return Err(unsupported(
                "os-listdir",
                "os.listdir() order is filesystem-defined and not reproducible",
            ));
        }
        ("os", "makedirs" | "mkdir") => {
            let p = s(0)?;
            // CPython's two signatures are `os.mkdir(path, mode=0o777, *,
            // dir_fd=None)` and `os.makedirs(name, mode=0o777,
            // exist_ok=False)`, and this arm used to search the keywords for
            // `exist_ok` and DROP everything else — so `os.mkdir("D",
            // exist_ok=True)` was exit 0 where CPython raises TypeError
            // (`exist_ok` is not a `mkdir` keyword at all) and
            // `os.makedirs("D", nonsense=1)` was exit 0 where CPython raises
            // it too. Ignoring a keyword is answering a question the caller
            // did not ask.
            //
            // `mode` is refused rather than served, and the reason is the undo
            // log: `std::fs::create_dir` hands the kernel 0o777 and the umask
            // decides, which is exactly CPython's default and nothing else, so
            // `mode=0o700` exits 0 with permissions that differ. It is also
            // what keeps `io::rewind` honest — a directory it has to put back
            // after a failed undo is re-made by `create_dir`, and a mode
            // `create_dir` cannot spell is a mode the rewind would lose.
            //
            // Refused rather than raised, like `sys.exit()`'s keyword check
            // above: the TypeError text is version-shaped, and CPython prints
            // its own one spawn later.
            let mut exist_ok = false;
            for (k, v) in kw.iter() {
                match k.as_ref() {
                    "exist_ok" if name == "makedirs" => exist_ok = truthy(v)?,
                    other => {
                        return Err(unsupported(
                            "mkdir",
                            &format!("os.{name}({other}=...)"),
                        ))
                    }
                }
            }
            if args.len() > 1 {
                return Err(unsupported(
                    "mkdir",
                    &format!("os.{name}() with a mode argument"),
                ));
            }
            // The barrier's own, because a directory this run makes is a
            // directory this run can un-make: `io::rewind` removes it if a
            // later refusal has to fall onward, so `os.mkdir` no longer ends
            // the run's reversibility (issue #51). It is also the only place
            // that knows which parents `makedirs` actually created.
            mio::make_dir(&p, name == "makedirs", exist_ok)?;
            Value::None
        }
        ("os", "remove" | "unlink") => {
            let p = s(0)?;
            if !mio::path_exists(&p) {
                return Err(LypningError::exc(
                    "FileNotFoundError",
                    format!("[Errno 2] No such file or directory: '{p}'"),
                ));
            }
            mio::stage_delete(&p);
            Value::None
        }
        ("os", "rmdir") => {
            let p = s(0)?;
            // Reversible only when it undoes this run's own `mkdir`; anyone
            // else's directory cannot be put back as it was, so that commits.
            mio::remove_dir(&p)?;
            Value::None
        }
        ("os", "rename" | "replace") => {
            let (a, b) = (s(0)?, s(1)?);
            let content = match mio::effective_content(&a)? {
                Some(c) => c,
                None => {
                    if mio::is_staged_deleted(&a) {
                        return Err(LypningError::exc(
                            "FileNotFoundError",
                            format!("[Errno 2] No such file or directory: '{a}'"),
                        ));
                    }
                    // `os.rename` here COPIES: it stages the bytes under the
                    // new name and stages a delete of the old one. So it reads
                    // whole files too, and a device would never finish.
                    mio::require_regular_file(&a)?;
                    std::fs::read(&a).map_err(|e| mio::os_error(&a, &e))?
                }
            };
            mio::stage_write(&b, content);
            mio::stage_delete(&a);
            Value::None
        }
        ("os.path", "join") => {
            let mut out = String::new();
            for a in args.iter() {
                let part = fmt::to_str(a)?;
                if part.starts_with('/') {
                    out = part;
                } else if out.is_empty() || out.ends_with('/') {
                    out.push_str(&part);
                } else {
                    out.push('/');
                    out.push_str(&part);
                }
            }
            Value::Str(out.into())
        }
        // These consult the commit-barrier overlay first, so a program sees the
        // files it has itself created or removed during this run.
        ("os.path", "exists") => Value::Bool(mio::path_exists(&s(0)?)),
        ("os.path", "isfile") => {
            let p = s(0)?;
            Value::Bool(if mio::effective_content(&p)?.is_some() {
                true
            } else {
                !mio::is_staged_deleted(&p) && std::path::Path::new(&p).is_file()
            })
        }
        ("os.path", "isdir") => {
            let p = s(0)?;
            Value::Bool(std::path::Path::new(&p).is_dir())
        }
        ("os.path", "islink") => Value::Bool(std::fs::symlink_metadata(s(0)?).map(|m| m.file_type().is_symlink()).unwrap_or(false)),
        ("os.path", "getsize") => {
            let p = s(0)?;
            match mio::effective_content(&p)? {
                Some(c) => ival(c.len() as i64),
                None => ival(
                    std::fs::metadata(&p)
                        .map_err(|e| mio::os_error(&p, &e))?
                        .len() as i64,
                ),
            }
        }
        ("os.path", "basename") => {
            let p = s(0)?;
            Value::Str(p.rsplit('/').next().unwrap_or("").into())
        }
        ("os.path", "dirname") => {
            let p = s(0)?;
            Value::Str(match p.rfind('/') {
                Some(0) => "/".into(),
                Some(i) => p[..i].to_string().into(),
                None => "".into(),
            })
        }
        ("os.path", "split") => {
            let p = s(0)?;
            let (d, b) = match p.rfind('/') {
                Some(0) => ("/".to_string(), p[1..].to_string()),
                Some(i) => (p[..i].to_string(), p[i + 1..].to_string()),
                None => (String::new(), p.clone()),
            };
            Value::Tuple(Rc::new(vec![Value::Str(d.into()), Value::Str(b.into())]))
        }
        ("os.path", "splitext") => {
            let p = s(0)?;
            let base_at = p.rfind('/').map_or(0, |i| i + 1);
            let dot = p[base_at..].rfind('.').map(|i| base_at + i);
            let (a, b) = match dot {
                // A leading dot is part of the name, not an extension.
                Some(i) if i > base_at => (p[..i].to_string(), p[i..].to_string()),
                _ => (p.clone(), String::new()),
            };
            Value::Tuple(Rc::new(vec![Value::Str(a.into()), Value::Str(b.into())]))
        }
        ("os.path", "abspath" | "normpath" | "relpath") => {
            let p = s(0)?;
            if name == "relpath" {
                return Err(unsupported("module-attr", "os.path.relpath"));
            }
            let joined = if name == "abspath" && !p.starts_with('/') {
                let cwd = std::env::current_dir().map_err(|e| mio::os_error(".", &e))?;
                format!("{}/{p}", cwd.to_string_lossy())
            } else {
                p
            };
            Value::Str(normpath(&joined).into())
        }
        ("os.path", "expanduser") => {
            let p = s(0)?;
            Value::Str(match p.strip_prefix('~') {
                Some(rest) if rest.is_empty() || rest.starts_with('/') => {
                    format!("{}{rest}", std::env::var("HOME").unwrap_or_default()).into()
                }
                _ => p.into(),
            })
        }
        // `object_hook`, `object_pairs_hook`, `parse_float`, `parse_int` and
        // `parse_constant` change what a document DECODES TO, and this parser
        // ignored every one of them — `json.loads(s, object_pairs_hook=list)`
        // answered a dict. Refused rather than implemented: honouring them means
        // calling back into the interpreter from the parser, and a refusal the
        // dispatcher routes onward beats an approximation (invariant 1).
        ("json", "loads" | "load") if !kw.is_empty() => {
            let named: Vec<&str> = kw.iter().map(|(k, _)| k.as_ref()).collect();
            return Err(unsupported(
                "json",
                &format!("json.{name}({}=…)", named.join("=…, ")),
            ));
        }
        ("json", "loads") => {
            let text = match args.first() {
                Some(Value::Bytes(b)) => crate::iter::decode_utf8(b)?,
                Some(v) => fmt::to_str(v)?,
                None => return Err(type_err("loads() missing 1 required positional argument")),
            };
            json::parse(&text)?
        }
        ("json", "load") => {
            let f = args
                .first()
                .cloned()
                .ok_or_else(|| type_err("load() missing 1 required positional argument"))?;
            let text = crate::methods::call_method(it, &f, "read", &mut Args::new(), Vec::new())?;
            json::parse(&fmt::to_str(&text)?)?
        }
        // `args.first()`, not `args[0]`: `json.dumps()` with no argument is a
        // TypeError in CPython and was an index-out-of-bounds PANIC here — an
        // abort in the binary and, embedded, an unexplained failure in somebody
        // else's process. Every neighbour in this table already gets it right.
        ("json", "dumps") => Value::Str(
            json::dumps(
                args.first()
                    .ok_or_else(|| type_err("dumps() missing 1 required positional argument"))?,
                &kw,
            )?
            .into(),
        ),
        ("json", "dump") => {
            let text = json::dumps(
                args.first()
                    .ok_or_else(|| type_err("dump() missing arguments"))?,
                &kw,
            )?;
            let f = args
                .get(1)
                .cloned()
                .ok_or_else(|| type_err("dump() missing 1 required positional argument: 'fp'"))?;
            crate::methods::call_method(
                it,
                &f,
                "write",
                &mut Args::one(Value::Str(text.into())),
                Vec::new(),
            )?;
            Value::None
        }
        _ => {
            return Err(unsupported(
                "module-attr",
                &format!("{m}.{name}()"),
            ))
        }
    })
}

/// `os.path.normpath`. Public to the crate because `glob.rs` needs the same
/// answer to decide which directory a staged write belongs to.
pub(crate) fn normpath(p: &str) -> String {
    let absolute = p.starts_with('/');
    let mut out: Vec<&str> = Vec::new();
    for part in p.split('/') {
        match part {
            "" | "." => {}
            ".." => {
                if matches!(out.last(), Some(&"..")) || (!absolute && out.is_empty()) {
                    out.push("..");
                } else {
                    out.pop();
                }
            }
            other => out.push(other),
        }
    }
    let joined = out.join("/");
    if absolute {
        format!("/{joined}")
    } else if joined.is_empty() {
        ".".into()
    } else {
        joined
    }
}
