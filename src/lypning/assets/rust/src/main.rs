//! lypning — Mixture of Pythons.
//!
//! A from-scratch Python subset in Rust, sized to the BOTTOM of the
//! distribution of one-liners an agentic CLI actually types (harvested in
//! `tests/corpus/corpus.jsonl`), plus the classifier that decides which of the
//! three interpreters — lypning, lypning-mp, CPython — should run a given program.
//!
//! Usage:
//!   lypning run -c PROG [args…]    ROUTE, then run on whichever engine fits
//!   lypning -c PROG [args…]        run a program on lypning alone
//!   lypning FILE [args…]           run a script
//!   lypning -                      run the program on stdin
//!   lypning route -c PROG          print the routing decision (no execution)
//!   lypning route -f FILE          … for a file
//!   lypning --version
//!
//! Exit codes follow lypning-mp's contract exactly, which is what makes the tiers
//! interchangeable: 0/1 as CPython, and **90 with one line on stderr** for a
//! construct outside the subset.

//! The modules themselves live in `lib.rs`: this binary is one consumer of the
//! crate and the C ABI is another, and both run programs through the same
//! `embed`/`io` code so the refusal contract has exactly one implementation.

use lypning::embed::fall_onward;
use lypning::err::{ErrKind, LypningError, UNSUPPORTED_EXIT};
use lypning::{eval, io, parse, route};
use std::io::{Read, Write};

/// Installed for the BINARY only, deliberately — see `alloc.rs` for what it is
/// and why the general allocator is most of this program's instruction stream.
///
/// A `#[global_allocator]` is process-wide, so putting it in `lib.rs` would
/// impose lypning's allocator on any application that merely linked the C ABI to
/// run a one-liner. CLAUDE.md invariant 7 says nothing we write may cost a user
/// something they had, and an application's allocator is very much something it
/// had. A host that wants this can install it itself; the module is public.
#[global_allocator]
static ALLOC: lypning::alloc::Lypalloc = lypning::alloc::Lypalloc::new();

fn main() {
    let argv: Vec<String> = std::env::args().collect();
    let code = run(&argv);
    std::process::exit(code);
}

fn run(argv: &[String]) -> i32 {
    let mut i = 1;
    if argv.len() > 1 && (argv[1] == "--version" || argv[1] == "-V") {
        println!("lypning {}", env!("CARGO_PKG_VERSION"));
        return 0;
    }
    if argv.len() > 1 && argv[1] == "route" {
        return route_cmd(&argv[2..]);
    }
    if argv.len() > 1 && argv[1] == "run" {
        return dispatch(&argv[2..]);
    }
    let mut source = None;
    let mut from_stdin = false;
    while i < argv.len() {
        match argv[i].as_str() {
            "-c" => {
                source = argv.get(i + 1).cloned();
                i += 2;
                break;
            }
            "-" => {
                from_stdin = true;
                i += 1;
                break;
            }
            "-u" | "-B" | "-E" | "-s" | "-S" | "-I" => i += 1,
            other if other.starts_with('-') => {
                eprintln!("lypning: unsupported: cli: option {other}");
                return UNSUPPORTED_EXIT;
            }
            path => {
                source = match std::fs::read_to_string(path) {
                    Ok(s) => Some(s),
                    Err(e) => {
                        eprintln!("lypning: can't open file '{path}': {e}");
                        return 2;
                    }
                };
                i += 1;
                break;
            }
        }
    }
    if from_stdin {
        let mut s = String::new();
        if std::io::stdin().read_to_string(&mut s).is_err() {
            eprintln!("lypning: cannot read program from stdin");
            return 2;
        }
        source = Some(s);
    }
    let Some(src) = source else {
        eprintln!("lypning: no program given (use -c PROG, a FILE, or -)");
        return 2;
    };
    execute(&src)
}

fn execute(src: &str) -> i32 {
    execute_inner(src, true, &mut String::new())
}

/// `kind` comes back holding the refusal's kind when one fired, so the caller
/// can choose the next tier instead of assuming it — see `route::only_cpython`.
/// It is left untouched on any other outcome.
fn execute_inner(src: &str, report_refusal: bool, kind: &mut String) -> i32 {
    let body = match parse::parse(src) {
        Ok(b) => b,
        Err(e) => return finish(Err(e), report_refusal, kind),
    };
    let mut interp = eval::Interp::new();
    let r = interp.run(&body);
    finish(r, report_refusal, kind)
}

/// The exit path, and the other half of the commit barrier (`io.rs`): output
/// staged during the run is written exactly once, on success, and discarded on
/// a capability refusal so the retry on the next tier sees a clean slate.
fn finish(r: Result<(), LypningError>, report_refusal: bool, kind: &mut String) -> i32 {
    match r {
        Ok(()) => {
            if let Err(e) = io::commit() {
                return finish(Err(e), report_refusal, kind);
            }
            0
        }
        Err(ref e) if e.is_exit().is_some() => {
            let _ = io::commit();
            e.is_exit().unwrap_or(0)
        }
        Err(e) if e.is_unsupported() => {
            if let ErrKind::Unsupported { kind: k, .. } = e.kind() {
                kind.clear();
                kind.push_str(k);
            }
            if io::is_committed() {
                // Output already left the process, so this cannot be retried.
                // Say so plainly rather than emitting a 90 the dispatcher would
                // act on by running the program a second time.
                let _ = io::commit();
                let _ = writeln!(
                    std::io::stderr(),
                    "lypning: error: {e} — reached after output was already flushed, so the run \
                     cannot be routed onward"
                );
                return 1;
            }
            io::discard();
            // Under `lypning run` the refusal is an internal routing signal, not
            // something the caller asked to see: the next engine is about to
            // answer the question. Printing it would put a line on stderr that
            // plain `python3` never produces.
            if report_refusal {
                let _ = writeln!(std::io::stderr(), "{e}");
            }
            UNSUPPORTED_EXIT
        }
        Err(e) => {
            let _ = io::commit();
            let _ = writeln!(std::io::stderr(), "Traceback (most recent call last):");
            let _ = writeln!(std::io::stderr(), "{e}");
            1
        }
    }
}

fn route_cmd(args: &[String]) -> i32 {
    let mut src = String::new();
    let mut as_json = false;
    let mut i = 0;
    while i < args.len() {
        match args[i].as_str() {
            "-c" => {
                src = args.get(i + 1).cloned().unwrap_or_default();
                i += 2;
            }
            "-f" => {
                let p = args.get(i + 1).cloned().unwrap_or_default();
                src = std::fs::read_to_string(&p).unwrap_or_default();
                i += 2;
            }
            "--json" => {
                as_json = true;
                i += 1;
            }
            "-" => {
                let _ = std::io::stdin().read_to_string(&mut src);
                i += 1;
            }
            _ => i += 1,
        }
    }
    let r = route::route(&src);
    if as_json {
        println!(
            "{{\"engine\":{},\"kind\":{},\"detail\":{},\"imports\":[{}]}}",
            jstr(r.engine.as_str()),
            jstr(&r.kind),
            jstr(&r.detail),
            r.imports
                .iter()
                .map(|s| jstr(s))
                .collect::<Vec<_>>()
                .join(",")
        );
    } else if r.kind.is_empty() {
        println!("{}", r.engine.as_str());
    } else {
        println!("{}\t{}: {}", r.engine.as_str(), r.kind, r.detail);
    }
    0
}

fn jstr(s: &str) -> String {
    let mut out = String::from("\"");
    for c in s.chars() {
        match c {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            c if (c as u32) < 0x20 => out.push_str(&format!("\\u{:04x}", c as u32)),
            c => out.push(c),
        }
    }
    out.push('"');
    out
}


// ---- the dispatcher: the mixture itself ------------------------------------

/// `lypning run ...` routes the program and runs it on the engine that fits.
///
/// Two properties make this cheap enough to be worth doing at all:
///
///   * **The winning case costs nothing.** A program routed to lypning runs
///     IN THIS PROCESS - no second spawn, no pipe, no serialisation. Since 96%
///     of a one-liner's cost is the OS spawning a process (docs/MICROPYTHON.md
///     section 8c), a dispatcher that spawned a child would give back most of
///     what the fast engine won.
///   * **Falling onward costs one `exec`, not one fork.** `exec` REPLACES this
///     process, so a mis-route costs the target interpreter's startup and
///     nothing else - no extra process ever exists.
///
/// The fallback is safe because of the commit barrier (`io.rs`): a lypning run
/// that ends in exit 90 has written no output and touched no file, so the next
/// engine starts from the same state lypning did.
fn dispatch(args: &[String]) -> i32 {
    let (src, tail, is_file) = match parse_run_args(args) {
        Ok(v) => v,
        Err(code) => return code,
    };
    let r = route::route(&src);
    if r.engine == route::Engine::Lypning {
        // Running in-process also means stdin is still unread at this point,
        // which a spawned child could not be given back.
        let mut kind = String::new();
        let code = execute_inner(&src, false, &mut kind);
        if code != UNSUPPORTED_EXIT {
            return code;
        }
        // The route was optimistic and a value-dependent refusal fired: an
        // integer outgrew 64 bits, or a set's order was asked for. Fall onward
        // — but ASK THE KIND WHERE TO. Some refusals rule out every tier but
        // CPython, because the refusal exists precisely because the behaviour is
        // subtle and a reimplementation gets it wrong; handing those to
        // lypning-mp turns a correct refusal into a silent wrong answer at exit
        // 0. This binary used to hand every refusal to lypning-mp.
        let next = if route::only_cpython(&kind) {
            route::Engine::CPython
        } else {
            route::Engine::MicroPython
        };
        return exec_engine(engine_path(next), &src, &tail, &is_file, next == route::Engine::MicroPython);
    }
    let onward = r.engine == route::Engine::MicroPython;
    exec_engine(engine_path(r.engine), &src, &tail, &is_file, onward)
}

/// Parse the tail of `lypning run ...` into (program source, program args, file?).
fn parse_run_args(args: &[String]) -> Result<(String, Vec<String>, Option<String>), i32> {
    let mut i = 0;
    while i < args.len() {
        match args[i].as_str() {
            "-c" => {
                let Some(src) = args.get(i + 1) else {
                    eprintln!("lypning: run -c needs a program");
                    return Err(2);
                };
                return Ok((src.clone(), args[i + 2..].to_vec(), None));
            }
            "-" => {
                let mut s = String::new();
                if std::io::stdin().read_to_string(&mut s).is_err() {
                    eprintln!("lypning: cannot read program from stdin");
                    return Err(2);
                }
                return Ok((s, args[i + 1..].to_vec(), None));
            }
            f if f.starts_with('-') => i += 1,
            path => match std::fs::read_to_string(path) {
                Ok(s) => return Ok((s, args[i + 1..].to_vec(), Some(path.to_string()))),
                Err(e) => {
                    eprintln!("lypning: can't open file '{path}': {e}");
                    return Err(2);
                }
            },
        }
    }
    eprintln!("lypning: run needs -c PROG, a FILE, or -");
    Err(2)
}

fn engine_path(e: route::Engine) -> String {
    match e {
        route::Engine::MicroPython => std::env::var("LYPNING_MP_BIN").unwrap_or_else(|_| "lypning-mp".into()),
        route::Engine::CPython => std::env::var("LYPNING_CPYTHON").unwrap_or_else(|_| "python3".into()),
        route::Engine::Lypning => std::env::args().next().unwrap_or_else(|| "lypning".into()),
    }
}

/// Hand the program to `bin`.
///
/// The terminal tier (CPython) is `exec`ed: it replaces this process, so no
/// extra process ever exists and nothing needs to be waited on.
///
/// An INTERMEDIATE tier is forked instead, because its own refusal has to be
/// caught. lypning-mp's capability table in `route.rs` is necessarily approximate —
/// it knows lypning-mp HAS `hashlib` and `re`, not that this build lacks
/// `hashlib.md5` or `re.VERBOSE` — and measurement found 14 corpus programs
/// where the difference bites. Forking costs one process on the lypning-mp path and
/// makes the chain converge on CPython every time, which is the property that
/// lets the mixture answer 100% of what CPython answers.
fn exec_engine(
    bin: String,
    src: &str,
    tail: &[String],
    is_file: &Option<String>,
    retry_cpython: bool,
) -> i32 {
    use std::os::unix::process::CommandExt;
    let mut cmd = std::process::Command::new(&bin);
    match is_file {
        Some(p) => {
            cmd.arg(p);
        }
        None => {
            cmd.arg("-c").arg(src);
        }
    }
    cmd.args(tail);

    // A consumed pipe cannot be rewound. If lypning already read stdin before
    // refusing, `exec` would hand the next engine an empty stream — the one
    // side effect the commit barrier cannot roll back. So in that case only,
    // fork instead of exec and replay the captured bytes.
    if let Some(bytes) = io::stdin_consumed() {
        use std::io::Write as _;
        use std::process::Stdio;
        if retry_cpython {
            cmd.stderr(Stdio::piped());
        }
        match cmd.stdin(Stdio::piped()).spawn() {
            Ok(mut child) => {
                if let Some(mut si) = child.stdin.take() {
                    let _ = si.write_all(&bytes);
                }
                let mut errbuf = Vec::new();
                if let Some(mut se) = child.stderr.take() {
                    use std::io::Read as _;
                    let _ = se.read_to_end(&mut errbuf);
                }
                let code = match child.wait() {
                    Ok(st) => st.code().unwrap_or(1),
                    Err(_) => 1,
                };
                if fall_onward(code, &errbuf) && retry_cpython {
                    let cp = engine_path(route::Engine::CPython);
                    if cp != bin {
                        return exec_engine(cp, src, tail, is_file, false);
                    }
                }
                let _ = std::io::stderr().write_all(&errbuf);
                return code;
            }
            Err(e) => {
                if retry_cpython {
                    let cp = engine_path(route::Engine::CPython);
                    if cp != bin {
                        return exec_engine(cp, src, tail, is_file, false);
                    }
                }
                eprintln!("lypning: cannot run {bin}: {e}");
                return 127;
            }
        }
    }
    if retry_cpython {
        // Intermediate tier: fork, so an exit-90 from it can fall onward. Its
        // stderr is buffered because a refusal line is an internal routing
        // signal, and is replayed verbatim when the run is NOT falling onward.
        cmd.stderr(std::process::Stdio::piped());
        // `Command::output()` defaults stdin to /dev/null. Without this the
        // child of the forked tier gets an EMPTY stream and every
        // `stdin -> transform -> stdout` one-liner — the corpus's largest
        // cluster — silently answers about nothing. Caught by the mixture arm
        // of lypning conformance; nothing else would have seen it.
        cmd.stdin(std::process::Stdio::inherit());
        if let Ok(out) = cmd.output() {
            let code = out.status.code().unwrap_or(1);
            if !fall_onward(code, &out.stderr) {
                let _ = std::io::stdout().write_all(&out.stdout);
                let _ = std::io::stderr().write_all(&out.stderr);
                return code;
            }
        }
        let cp = engine_path(route::Engine::CPython);
        if cp != bin {
            return exec_engine(cp, src, tail, is_file, false);
        }
    }
    let err = cmd.exec();
    eprintln!("lypning: cannot run {bin}: {err}");
    127
}
