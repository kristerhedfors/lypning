//! quickstart.rs - the smallest complete lypning host, and the file to copy:
//! run the program in this process, or fall onward to CPython.
//!   cargo run --release --manifest-path src/lypning/assets/examples/rust/Cargo.toml --example quickstart -- "print(sum(range(10)))"
//! Usage: quickstart "<python source>" [args...]   (args become sys.argv[1:])
//! SPDX-License-Identifier: MIT

use lypning::{run, Request};
use std::io::Write;
use std::os::unix::process::ExitStatusExt;
use std::process::{exit, Command, Stdio};

fn main() {
    let argv: Vec<String> = std::env::args().skip(1).collect();
    let Some((src, args)) = argv.split_first() else {
        eprintln!("usage: quickstart \"<python source>\" [args...]");
        exit(2);
    };
    let r = run(&Request {
        args: args.to_vec(),
        step_limit: 10_000_000, // a call in our own thread has no process to kill
        ..Request::new(src.as_str())
    });
    if r.should_fall_onward() {
        // A refusal is not an error: lypning ran none of it and wrote nothing, so CPython runs it once.
        let _ = std::io::stdout().flush();
        let status = Command::new("python3")
            .arg("-c")
            .arg(src)
            .args(args)
            .stdin(Stdio::null())
            .status();
        exit(match status {
            Ok(st) => st.code().unwrap_or(128 + st.signal().unwrap_or(0)),
            Err(e) => {
                eprintln!("quickstart: python3: {e}");
                127
            }
        });
    }
    let _ = std::io::stdout()
        .write_all(&r.stdout)
        .and_then(|_| std::io::stdout().flush());
    let _ = std::io::stderr().write_all(&r.stderr);
    exit(r.exit_code);
}
