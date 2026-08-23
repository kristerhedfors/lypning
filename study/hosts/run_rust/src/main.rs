//! The Rust host: every program in a hostset directory, through the crate
//! directly — no C ABI, no FFI, no `unsafe`.
//!
//! Like the C and C++ drivers it deliberately does **not** fall onward to
//! CPython. Falling onward is what a real harness does; what this driver is for
//! is counting what the subset itself takes, and a driver that quietly answered
//! from python3 would report a coverage the subset does not have.
//!
//! It logs each run to `$LYPNING_LOG` in the shim's own record shape, because
//! an in-process call spawns no interpreter and is therefore invisible to both
//! of lypning's capture feeds. See `study/hosts/capture.h` for why that is the
//! host's job rather than the library's.

use std::fs;
use std::path::Path;
use std::time::{Instant, SystemTime, UNIX_EPOCH};

fn json_escape(s: &[u8]) -> String {
    let mut out = String::with_capacity(s.len() + 8);
    for &b in s {
        match b {
            b'"' => out.push_str("\\\""),
            b'\\' => out.push_str("\\\\"),
            b'\n' => out.push_str("\\n"),
            b'\r' => out.push_str("\\r"),
            b'\t' => out.push_str("\\t"),
            0x00..=0x1f | 0x7f => out.push_str(&format!("\\u{:04x}", b)),
            _ => out.push(b as char),
        }
    }
    out
}

/// A UTC stamp with no chrono: seconds since the epoch is all `harvest` needs
/// to order sightings, and it parses the field as an opaque string.
fn stamp() -> String {
    let secs = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0);
    format!("epoch-{secs}")
}

fn capture(host: &str, program: &str, args: &[String], exit_code: i32, wall_ms: u128) {
    let Ok(path) = std::env::var("LYPNING_LOG") else {
        return;
    };
    if path.is_empty() {
        return;
    }
    let session = std::env::var("LYPNING_STUDY_SESSION").unwrap_or_default();
    let session = if session.is_empty() {
        "null".to_string()
    } else {
        format!("\"{}\"", json_escape(session.as_bytes()))
    };
    let tail: Vec<String> = args
        .iter()
        .map(|a| format!("\"{}\"", json_escape(a.as_bytes())))
        .collect();
    let line = format!(
        "{{\"kind\":\"python_invocation\",\"ts\":\"{}\",\"session\":{},\"shim\":\"{}\",\
         \"pid\":{},\"program\":\"{}\",\"module\":null,\"script\":null,\"argv_tail\":[{}],\
         \"stdin_pipe\":true,\"stdin_kind\":\"bytes\",\"exit_code\":{},\"wall_ms\":{}}}\n",
        stamp(),
        session,
        host,
        std::process::id(),
        json_escape(program.as_bytes()),
        tail.join(","),
        exit_code,
        wall_ms
    );
    // Best-effort, exactly like the shim: a log that will not open is a lost
    // sighting and never a failed run.
    use std::io::Write;
    if let Ok(mut fh) = fs::OpenOptions::new().create(true).append(true).open(&path) {
        let _ = fh.write_all(line.as_bytes());
    }
}

fn main() {
    let dir = match std::env::args().nth(1) {
        Some(d) => d,
        None => {
            eprintln!("usage: run_rust <hostset-dir>");
            std::process::exit(2);
        }
    };
    let mut entries: Vec<_> = match fs::read_dir(&dir) {
        Ok(rd) => rd.filter_map(|e| e.ok()).map(|e| e.path()).collect(),
        Err(e) => {
            eprintln!("run_rust: {dir}: {e}");
            std::process::exit(1);
        }
    };
    entries.sort();

    let (mut ran, mut refused, mut other, mut n) = (0u64, 0u64, 0u64, 0u64);
    for d in entries {
        let prog = match fs::read_to_string(Path::new(&d).join("program.py")) {
            Ok(s) => s,
            Err(_) => continue,
        };
        n += 1;
        let stdin = fs::read(Path::new(&d).join("stdin")).unwrap_or_default();
        let args: Vec<String> = fs::read_to_string(Path::new(&d).join("args"))
            .unwrap_or_default()
            .lines()
            .filter(|l| !l.is_empty())
            .map(|l| l.to_string())
            .collect();

        let req = lypning::embed::Request {
            source: prog.clone(),
            args: args.clone(),
            stdin: Some(stdin),
            step_limit: 200_000_000,
            output_limit: 1 << 20,
            ..Default::default()
        };
        // The program runs in THIS process; give it the entry directory, where
        // prepare.py put the fixtures it was written against.
        let home = std::env::current_dir().ok();
        let moved = std::env::set_current_dir(&d).is_ok();

        let t0 = Instant::now();
        let out = lypning::embed::run(&req);
        let ms = t0.elapsed().as_millis();
        if moved {
            if let Some(h) = home {
                let _ = std::env::set_current_dir(h);
            }
        }

        match out.status {
            lypning::embed::Status::Ok => ran += 1,
            lypning::embed::Status::Unsupported => refused += 1,
            _ => other += 1,
        }
        capture("rust-embed", &prog, &args, out.exit_code, ms);
    }
    println!("rust-embed   {n} programs: {ran} ran, {refused} refused, {other} other");
}
