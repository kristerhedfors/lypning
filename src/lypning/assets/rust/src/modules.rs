//! The module surface: `sys`, `os`, `os.path`, `io`, `json`.
//!
//! Chosen from the corpus, in frequency order: `sys` (82 imports), `json` (74),
//! `io` (63 — almost entirely `io.open(p, encoding='utf-8').read()`, which is
//! the file-read idiom agents actually type), `os` (21). `re` is deliberately
//! ABSENT: a regex engine is a large amount of code with deep semantics, and
//! lypning-mp already has one, so `import re` is a routing decision rather than a
//! gap to fill here.

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
pub const MODULES: &[&str] = &["sys", "os", "os.path", "io", "json", "posixpath", "random"];

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
        ("sys", "maxsize") => Value::Int(i64::MAX),
        ("sys", "exit") => Value::Bound(Rc::new(m.clone()), "exit"),
        ("sys", "path") => {
            return Err(unsupported(
                "module-attr",
                "sys.path (lypning has no import machinery)",
            ))
        }
        ("sys.stdin", "read" | "readline" | "readlines" | "buffer") => {
            Value::Bound(Rc::new(m.clone()), interned(name)?)
        }
        ("sys.stdout" | "sys.stderr", "write" | "flush") => {
            Value::Bound(Rc::new(m.clone()), interned(name)?)
        }
        ("os", "path") => Value::Module("os.path"),
        ("os", "sep") => Value::Str("/".into()),
        ("os", "linesep") => Value::Str("\n".into()),
        ("os", "environ") => {
            let mut d = Dict::new();
            for (k, v) in std::env::vars() {
                d.insert(Value::Str(k.into()), Value::Str(v.into()))?;
            }
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
        "split", "relpath", "normpath", "islink", "loads", "dumps", "load", "dump", "buffer",
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
        ("random", _) => return crate::random::call(it, name, args, &kw),
        ("sys", "exit") => {
            let code = match args.first() {
                None | Some(Value::None) => 0,
                Some(Value::Int(i)) => *i as i32,
                Some(Value::Bool(b)) => *b as i32,
                Some(other) => {
                    mio::write_err(fmt::to_str(other)?.as_bytes())?;
                    mio::write_err(b"\n")?;
                    1
                }
            };
            return Err(LypningError::exit(code));
        }
        ("sys.stdin", "read") => Value::Str(crate::iter::decode_text(&mio::stdin_rest()?, "non-UTF-8 bytes on stdin (CPython decodes it with surrogateescape)")?),
        ("sys.stdin", "readline") => match mio::stdin_line()? {
            Some(b) => Value::Str(crate::iter::decode_text(&b, "non-UTF-8 bytes on stdin (CPython decodes it with surrogateescape)")?),
            None => Value::Str("".into()),
        },
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
            Value::Int(text.chars().count() as i64)
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
            let exist_ok = kw
                .iter()
                .find(|(k, _)| k.as_ref() == "exist_ok")
                .map(|(_, v)| truthy(v))
                .transpose()?
                .unwrap_or(false);
            let r = if name == "makedirs" {
                std::fs::create_dir_all(&p)
            } else {
                std::fs::create_dir(&p)
            };
            match r {
                Ok(()) => {
                    // A directory cannot be staged — there is no content to
                    // hold back — so making one is a real effect the barrier
                    // cannot take back. Whether that ends the run's
                    // reversibility depends on whether DOING IT TWICE differs
                    // from doing it once:
                    //
                    //   * `exist_ok=True` is idempotent. A retry on CPython
                    //     makes the same directory and carries on, so the run
                    //     is still routable and must stay that way — the whole
                    //     `os.makedirs(..., exist_ok=True)` / `os.listdir()`
                    //     shape in the corpus depends on falling onward from
                    //     the listdir refusal that comes after it.
                    //   * without it, the retry raises FileExistsError for a
                    //     program that works, so the run has to stop claiming
                    //     it can be re-run.
                    if !exist_ok {
                        mio::mark_committed();
                    }
                    Value::None
                }
                Err(e) if exist_ok && e.kind() == std::io::ErrorKind::AlreadyExists => Value::None,
                Err(e) => return Err(mio::os_error(&p, &e)),
            }
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
            std::fs::remove_dir(&p).map_err(|e| mio::os_error(&p, &e))?;
            // Removing a directory is not staged either, and it is the less
            // recoverable half: the retry would find it already gone.
            mio::mark_committed();
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
                Some(c) => Value::Int(c.len() as i64),
                None => Value::Int(
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

fn normpath(p: &str) -> String {
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
