// build.rs — one link flag, for one artefact, on one platform.
//
// dyld does not find a dylib by the path a host linked it from. It finds it by
// the INSTALL NAME recorded inside the dylib, and cargo's default for that is
// the absolute path of the file in the build tree. So a copy that `lypning
// build --lib` installs under ~/.lypning/lib is never the one a host loads:
// the host's -Wl,-rpath is ignored, because nothing in the image asks dyld to
// consult it, and the build tree is what gets loaded — or, once it is cleaned,
// nothing is, and the host dies at exec with "Library not loaded". An install
// name of @rpath/liblypning.dylib is what makes -rpath decide, which is the
// contract `lypning lib --libs` prints on every platform.
//
// `rustc-cdylib-link-arg` reaches the cdylib and nothing else. The binary and
// the rlib are linked without it, byte for byte, which is why this file is
// allowed to exist next to a crate whose whole shipping argument is its size.
// ELF has no install name; -soname is settled by the file name and the
// linker, so there is nothing to say elsewhere.

fn main() {
    println!("cargo:rerun-if-changed=build.rs");
    println!("cargo:rerun-if-changed=Cargo.toml");
    // The variant this build IS, from the one `variant-*` feature cargo turned
    // on. Emitted as an env var so `err::ENGINE` can be a compile-time constant
    // in library code, where `CARGO_BIN_NAME` does not reach. `rustc-env` is
    // seen by every target of the package, so the bin, the rlib and the cdylib
    // all agree on who they are.
    // A larger variant's feature names the smaller one (`variant-l =
    // ["variant-m"]`), so the LARGEST feature that is on names the binary;
    // none on is a build nobody asked for.
    let on = |f: &str| std::env::var(format!("CARGO_FEATURE_{f}")).is_ok();
    let engine = if on("VARIANT_L") {
        "lypning-l"
    } else if on("VARIANT_M") {
        "lypning"
    } else {
        panic!("no variant feature is on: build with the default features (variant-m) or --features variant-l")
    };
    println!("cargo:rustc-env=LYPNING_ENGINE={engine}");
    // Every capability feature that is on, sorted, so a binary can state what
    // it was built with (`route --spectrum`) and the build can check the claim.
    let mut caps: Vec<String> = std::env::vars()
        .filter_map(|(k, _)| k.strip_prefix("CARGO_FEATURE_CAP_").map(|c| format!("cap-{}", c.to_lowercase().replace('_', "-"))))
        .collect();
    caps.sort();
    println!("cargo:rustc-env=LYPNING_CAPS={}", caps.join(","));
    // The REFERENCE CPython's minor version, because a handful of CPython's own
    // answers are not the same on every version this package supports
    // (`pyproject.toml`: `requires-python = ">=3.9"`), and the interpreter this
    // binary stands in front of is the one it has to agree with. `err::REF_PY_MINOR`
    // reads it back; `builtins` and `value::bound_kind` are the sites that
    // branch on it, each with the measured version boundary written down.
    //
    // `lypning build --rust` passes it, computed from `engines.find_cpython()` — the
    // very interpreter `conformance` grades against and the dispatcher falls
    // through to. A bare `cargo build` has no such caller, so ask the same
    // question here rather than guess: `$LYPNING_CPYTHON` if it is set (the pin
    // `engines.find_cpython()` honours), otherwise `python3`. With neither, fall back
    // to the newest calibrated version, so a build on a host with no python at
    // all answers exactly what this crate answered before this constant existed.
    println!("cargo:rerun-if-env-changed=LYPNING_REF_PY");
    println!("cargo:rerun-if-env-changed=LYPNING_CPYTHON");
    let ref_py = std::env::var("LYPNING_REF_PY")
        .ok()
        .filter(|s| parse_minor(s).is_some())
        .or_else(probe_python)
        .unwrap_or_else(|| REF_PY_FALLBACK.to_string());
    println!("cargo:rustc-env=LYPNING_REF_PY={ref_py}");
    if std::env::var("CARGO_CFG_TARGET_OS").as_deref() == Ok("macos") {
        println!("cargo:rustc-cdylib-link-arg=-Wl,-install_name,@rpath/liblypning.dylib");
    }
}

/// The version this crate's message tables were read off, and what a build with
/// no reachable python answers. Every site that branches on the reference
/// version treats this as "newest", so such a build keeps the behaviour that
/// shipped before the branch existed.
const REF_PY_FALLBACK: &str = "3.13";

/// `3.11` -> `Some(11)`. Anything that is not `3.<digits>` is not a version
/// this can act on, and goes to the fallback rather than being parsed half-way.
fn parse_minor(s: &str) -> Option<u32> {
    let rest = s.trim().strip_prefix("3.")?;
    let digits: String = rest.chars().take_while(|c| c.is_ascii_digit()).collect();
    if digits.is_empty() {
        return None;
    }
    digits.parse().ok()
}

/// Ask the reference interpreter what it is. A missing python, a python that
/// does not run, or an answer this cannot parse all come back `None` — a build
/// is never FAILED over this, because the fallback is the version the tables
/// were written against and so is a correct answer, not a guess.
fn probe_python() -> Option<String> {
    let pin = std::env::var("LYPNING_CPYTHON").ok().filter(|s| !s.trim().is_empty());
    let names: Vec<String> = match pin {
        Some(p) => vec![p],
        None => vec!["python3".to_string(), "python".to_string()],
    };
    for name in names {
        let out = std::process::Command::new(&name)
            .args(["-c", "import sys;print('%d.%d' % sys.version_info[:2])"])
            .output();
        if let Ok(out) = out {
            if out.status.success() {
                let text = String::from_utf8_lossy(&out.stdout).trim().to_string();
                if parse_minor(&text).is_some() {
                    return Some(text);
                }
            }
        }
    }
    None
}
