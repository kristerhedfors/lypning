//! The module surface: `MODULES` below — the core's eight, and on the variant
//! built with a `cap-*` feature, the module that feature's `#[cfg]` row names.
//!
//! `math` is here rather than behind a `cap-*` because nothing in it is a
//! capability: the served subset is IEEE-754 and integer arithmetic, and the
//! transcendentals are refused on purpose rather than merely absent, so a
//! larger variant would answer every `math` program exactly as the core does.
//! `math.rs` is the bound, and the reason there has to be one.
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
/// One array, with each capability's module an element behind its own
/// `#[cfg]` — the attribute is stable on array elements, so a `cap-*` step
/// APPENDS one line here and touches no other. The core compiles none of those
/// elements, so its table is the same eight entries, the same bytes, it always
/// was: nothing is added at runtime, and no branch that would add one exists in
/// the smaller variant. `route::CAPS` carries the same claim for the ROUTER,
/// which has to answer for a sibling it is not.
pub const MODULES: &[&str] = &[
    "sys",
    "os",
    "os.path",
    "io",
    "json",
    "math",
    "posixpath",
    "random",
    // Capability rows, in the order they were built. Append; never reorder.
    #[cfg(feature = "cap-collections")]
    "collections",
    #[cfg(feature = "cap-pathlib")]
    "pathlib",
    #[cfg(feature = "cap-re")]
    "re",
    #[cfg(feature = "cap-csv")]
    "csv",
    #[cfg(feature = "cap-glob")]
    "glob",
    #[cfg(feature = "cap-base64")]
    "base64",
    #[cfg(feature = "cap-hashlib")]
    "hashlib",
    #[cfg(feature = "cap-statistics")]
    "statistics",
    #[cfg(feature = "cap-itertools")]
    "itertools",
    #[cfg(feature = "cap-difflib")]
    "difflib",
    #[cfg(feature = "cap-textwrap")]
    "textwrap",
    #[cfg(feature = "cap-time")]
    "time",
    #[cfg(feature = "cap-binascii")]
    "binascii",
];
// A `cap-*` is never built except as part of `variant-l`: `build.rs` refuses
// any `CARGO_FEATURE_CAP_*` without `variant-l`, one rule that needs no list,
// so a new capability adds nothing there either.

pub fn import(path: &str) -> R<Value> {
    match MODULES.iter().find(|m| **m == path) {
        // `posixpath` IS `os.path` — one object in `sys.modules` — and a
        // module's name is its identity here (`==`, `is`, a dict key), so
        // the two spellings must be one value or `os.path is posixpath` is
        // False at exit 0.
        Some(&"posixpath") => Ok(Value::Module("os.path")),
        Some(m) => {
            // See `io::hold`: the core refuses this import, so from here the
            // program is one only a capability answers, and the run must stay
            // reversible.
            #[cfg(any(feature = "cap-itertools", feature = "cap-difflib", feature = "cap-time"))]
            if crate::route::core_refuses_import(m) {
                crate::io::hold();
            }
            Ok(Value::Module(m))
        }
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
        // What the host's CPython would say. Anything but these two refuses.
        ("sys", "platform") => match PLATFORM {
            Some(p) => Value::Str(p.into()),
            None => return Err(unsupported("module-attr", "sys.platform on this host")),
        },
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
            // CPython's `os.environ` is not the process environment this engine
            // sees. PEP 538: when `LC_CTYPE` is unset, empty, `C` or `POSIX`,
            // CPython's startup COERCES the C locale and `setenv`s
            // `LC_CTYPE=C.UTF-8` into its own environment before any Python
            // runs — so `os.environ` there has a key no other process has.
            // Measured on this box: 140 keys under CPython, 139 here, and the
            // one that differs is `LC_CTYPE`.
            //
            // Whether the coercion actually fires depends on whether the target
            // locale can be SET, which needs `setlocale` and therefore libc —
            // so this engine cannot know, and answering a dict that silently
            // lacks the key is a wrong answer at exit 0. It refuses in exactly
            // the state where CPython would have coerced, and serves the exact
            // environment everywhere else. Zero corpus programs read this
            // mapping and 49 model-written ones do, which is the on-policy
            // blind spot in one line.
            if coercible_c_locale() {
                return Err(unsupported(
                    "environ",
                    "os.environ where CPython's startup would coerce the C locale \
                     and add LC_CTYPE (PEP 538), which this engine cannot reproduce",
                ));
            }
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
        // `sample` and `shuffle` on the hidden instance's stream. `Random` is
        // NOT a value here: `eval.rs` resolves `random.Random(…)` at the call,
        // so the class never reaches `isinstance`, `repr` or a subclass, and
        // every other spelling of it refuses through the arm below.
        #[cfg(feature = "cap-random")]
        ("random", "sample") => Value::Bound(Rc::new(m.clone()), "sample"),
        #[cfg(feature = "cap-random")]
        ("random", "shuffle") => Value::Bound(Rc::new(m.clone()), "shuffle"),
        // The exactly-defined subset: five constants and thirteen functions,
        // every one of them IEEE-754 or integer arithmetic. Every other name —
        // `sin`, `log10`, `exp`, `fsum`, `comb` — refuses with the
        // `module-attr` kind, which is a STATIC block: the walk resolves
        // `math.<n>` through this very function, so a program reaching for a
        // transcendental never starts. `math.rs` says why it must not have one.
        ("math", _) => return crate::math::module_attr(name),
        ("json", "loads" | "dumps" | "load" | "dump") => {
            Value::Bound(Rc::new(m.clone()), interned(name)?)
        }
        // Its own name, not `ValueError`'s. It IS a ValueError subclass and
        // `eval::exc_matches` says so, which is what keeps `except ValueError`
        // catching it; what it is not is the same CLASS, and `__name__` and
        // `is` both notice. See `builtins::MODULE_EXCEPTIONS`.
        ("json", "JSONDecodeError") => Value::Builtin("JSONDecodeError"),
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
        // `statistics.mean` / `median` / `median_low` / `median_high`. Every
        // other name — `stdev`, `fmean`, `mode`, `StatisticsError` — refuses
        // with the `module-attr` kind, which `route::MODULE_ATTRS` makes a
        // STATIC block in the core's walk.
        #[cfg(feature = "cap-statistics")]
        ("statistics", _) => return crate::statistics::module_attr(name),
        // `itertools.product` and `itertools.combinations`. Every other name —
        // `chain`, `islice`, `permutations`, `count`, `groupby` — refuses with
        // the `module-attr` kind, which the router blocks on statically out of
        // `route::MODULE_ATTRS`, in the CORE's walk. `difflib` has no arm: its
        // row there is EMPTY, so every `difflib.<name>` is the arm below.
        #[cfg(feature = "cap-itertools")]
        ("itertools", _) => return crate::itertools::module_attr(name),
        // `textwrap.dedent` / `indent` / `wrap` / `fill` / `shorten`. Every
        // other name — `TextWrapper`, `__file__` — refuses with the
        // `module-attr` kind, which `route::MODULE_ATTRS` makes a STATIC block
        // in the core's walk.
        #[cfg(feature = "cap-textwrap")]
        ("textwrap", _) => return crate::textwrap::module_attr(name),
        // `time.time`, `time_ns`, `monotonic`, `monotonic_ns`, `perf_counter`,
        // `perf_counter_ns`, `sleep`, `gmtime`, `strftime`. Every other name —
        // `localtime`, `ctime`, `mktime`, `timezone`, `process_time` — refuses
        // with the `module-attr` kind, blocked statically in the CORE's walk
        // out of `route::MODULE_ATTRS`.
        #[cfg(feature = "cap-time")]
        ("time", _) => return crate::time::module_attr(name),
        // The six served `binascii` functions. `Error`, `crc32` and the rest
        // refuse as `module-attr`, which `route::MODULE_ATTRS` makes a STATIC
        // block in the core's walk.
        #[cfg(feature = "cap-binascii")]
        ("binascii", _) => return crate::binascii::module_attr(name),
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
/// Would CPython's startup have coerced the C locale on this host?
///
/// Mirrors the entry conditions of `_Py_CoerceLegacyLocale` as far as they are
/// visible from the environment alone: `PYTHONCOERCECLOCALE=0` disables it
/// outright, and otherwise it fires only when the effective `LC_CTYPE` is
/// unset, empty, `C` or `POSIX`. `LC_ALL` wins over `LC_CTYPE`, which wins over
/// `LANG`, which is the order `setlocale(LC_CTYPE, "")` reads them in.
///
/// Deliberately conservative: it says "yes" whenever the coercion COULD fire,
/// not only when it would. The cost of a false yes is one refusal and a CPython
/// spawn; the cost of a false no is a dict that silently lacks a key CPython
/// has.
fn coercible_c_locale() -> bool {
    if std::env::var("PYTHONCOERCECLOCALE").as_deref() == Ok("0") {
        return false;
    }
    let effective = ["LC_ALL", "LC_CTYPE", "LANG"]
        .iter()
        .find_map(|k| std::env::var(k).ok().filter(|v| !v.is_empty()))
        .unwrap_or_default();
    matches!(effective.as_str(), "" | "C" | "POSIX")
}

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
        #[cfg(feature = "cap-statistics")]
        ("statistics", _) => return crate::statistics::call(it, name, args, &kw),
        #[cfg(feature = "cap-itertools")]
        ("itertools", _) => return crate::itertools::call(it, name, args, &kw),
        #[cfg(feature = "cap-textwrap")]
        ("textwrap", _) => return crate::textwrap::call(it, name, args, &kw),
        #[cfg(feature = "cap-time")]
        ("time", _) => return crate::time::call(it, name, args, &kw),
        #[cfg(feature = "cap-random")]
        ("random", "sample" | "shuffle") => {
            crate::io::hold();
            return crate::randobj::call(it, name, args, &kw);
        }
        #[cfg(feature = "cap-random")]
        ("random", "Random") => {
            crate::io::hold();
            return crate::randobj::construct(it, args, &kw);
        }
        #[cfg(feature = "cap-binascii")]
        ("binascii", _) => return crate::binascii::call(it, name, args, &kw),
        ("random", _) => return crate::random::call(it, name, args, &kw),
        ("math", _) => return crate::math::call(it, name, args, &kw),
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
            if !kw.is_empty() {
                return Err(type_err("TextIOWrapper.read() takes no keyword arguments"));
            }
            if args.len() > 1 {
                return Err(type_err(format!("read expected at most 1 argument, got {}", args.len())));
            }
            let n = match args.first() {
                None | Some(Value::None) => -1,
                Some(v @ (Value::Int(_) | Value::Bool(_))) => crate::eval::int_val(v)?,
                #[cfg(feature = "cap-re")]
                Some(Value::ReFlag(n)) => *n as i64,
                Some(other) => return Err(type_err(format!(
                    "argument should be integer or None, not '{}'", type_name(other)))),
            };
            // CPython's text/bytes allocation can overflow even when the
            // size fits Py_ssize_t. Apply this check to integer flags too,
            // before converting to a pointer-sized cursor on 32-bit hosts.
            if n > (isize::MAX / 4) as i64 {
                return Err(unsupported("alloc", "oversized stdin text read"));
            }
            let size = if n < 0 { None } else { Some(n as usize) };
            Value::Str(crate::iter::decode_text(&mio::stdin_read(size)?, "non-UTF-8 bytes on stdin (CPython decodes it with surrogateescape)")?)
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
            mio::remove_file(&s(0)?)?;
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
            // After a commit nothing below may refuse; the kernel's rename is
            // CPython's (`io::flushed_after_commit`).
            if mio::flushed_after_commit()? {
                std::fs::rename(&a, &b).map_err(|e| mio::os_error_on(&format!("'{a}' -> '{b}'"), &e))?;
                return Ok(Value::None);
            }
            // A directory or a symbolic link, at either end, is refused and
            // not served. The kernel MOVES those — a whole tree, or the link
            // itself — where this arm copies one file's bytes into the
            // barrier, and a moved tree cannot be staged: every path under it
            // changes spelling, and `rewind` would have to move it back. The
            // commit barrier stays exactly what it is; CPython answers,
            // `ENOTEMPTY` and `EISDIR` included.
            for p in [&a, &b] {
                if std::fs::symlink_metadata(p).is_ok_and(|m| !m.is_file()) {
                    return Err(unsupported("rename", &format!("os.{name}() of '{p}', which is not a regular file")));
                }
            }
            // This arm COPIES: it stages the bytes under the new name and a
            // delete of the old one. That is a rename only between files this
            // run made. A file already on disk at either end carries a mode,
            // an owner and timestamps the kernel's rename keeps and a staged
            // write does not — the copy of an executable lands as 0o644 — and
            // one path under two spellings stages its own delete last and
            // loses the file. A destination whose directory is missing is
            // CPython's two-path `FileNotFoundError`, which the barrier would
            // raise only at commit, after the source was gone. All four go to
            // CPython.
            let on_disk = |p: &str| !mio::is_staged_deleted(p) && std::fs::symlink_metadata(p).is_ok();
            let dir_ok = match std::path::Path::new(&b).parent() {
                Some(d) if !d.as_os_str().is_empty() => d.is_dir(),
                _ => true,
            };
            if on_disk(&a) || on_disk(&b) || mio::same_staged_path(&a, &b) || !dir_ok {
                return Err(unsupported(
                    "rename",
                    &format!("os.{name}('{a}', '{b}') that is not a move between files this run wrote"),
                ));
            }
            let content = match mio::effective_content(&a)? {
                Some(c) => c,
                None => {
                    return Err(LypningError::exc(
                        "FileNotFoundError",
                        format!("[Errno 2] No such file or directory: '{a}' -> '{b}'"),
                    ));
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

#[cfg(target_os = "macos")]
const PLATFORM: Option<&str> = Some("darwin");
#[cfg(target_os = "linux")]
const PLATFORM: Option<&str> = Some("linux");
#[cfg(not(any(target_os = "macos", target_os = "linux")))]
const PLATFORM: Option<&str> = None;
