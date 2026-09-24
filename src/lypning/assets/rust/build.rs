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
    println!("cargo:rerun-if-changed=reference_probe.py");
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
    // One rule for every capability, present and future: a `cap-*` is only
    // built as part of `variant-l`, whose feature names the full set. The NAME
    // above is what both dispatchers route on, so a `lypning` carrying a
    // capability would answer for a sibling it is not. Keyed on the feature
    // prefix rather than a list, so adding a `cap-*` adds nothing here.
    if !caps.is_empty() && !on("VARIANT_L") {
        panic!("{} on without variant-l: a cap-* feature is only built as part of variant-l (it names the full set)", caps.join(","))
    }
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
    // `engines.find_cpython()` honours) and then `python3` either way, because a
    // pin that no longer runs is stale and not an instruction to guess. With no
    // python reachable at all, fall back to the newest calibrated version, so
    // such a build answers exactly what this crate answered before this
    // constant existed.
    println!("cargo:rerun-if-env-changed=LYPNING_REF_PY");
    println!("cargo:rerun-if-env-changed=LYPNING_CPYTHON");
    let probed = probe_python();
    let ref_py = std::env::var("LYPNING_REF_PY")
        .ok()
        .filter(|s| parse_minor(s).is_some())
        .or_else(|| probed.as_ref().map(|p| p.0.clone()))
        .unwrap_or_else(|| REF_PY_FALLBACK.to_string());
    println!("cargo:rustc-env=LYPNING_REF_PY={ref_py}");
    // Minor versions are insufficient: patch/vendor builds can change these
    // answers without changing 3.x. Compile the selected oracle's actual
    // behavior into constants; no probing or Python dependency at runtime.
    let flags = match probed {
        Some((version, flags)) if version == ref_py => flags,
        _ => {
            println!("cargo:warning=reference behavior unmeasured; using legacy minor-version defaults");
            let minor = parse_minor(&ref_py).unwrap_or(13);
            [(minor >= 13) as u8, 0, (minor != 12) as u8, 0]
        }
    };
    for (name, flag) in ["NORMPATH_BUILTIN", "REVERSE_TRUTH", "ITER_SHORT", "ZERO_NEGATIVE_BYTES_OVERFLOW"].iter().zip(flags) {
        println!("cargo:rustc-env=LYPNING_REF_{name}={flag}");
    }
    if std::env::var("CARGO_CFG_TARGET_OS").as_deref() == Ok("macos") {
        println!("cargo:rustc-cdylib-link-arg=-Wl,-install_name,@rpath/liblypning.dylib");
    }
}

/// Legacy message-table default for builds without a reachable reference.
/// Such builds warn that their behavior profile is unmeasured.
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

/// Measure the reference interpreter's version and build-sensitive behavior.
/// Missing or unrecognized interpreters return `None`; legacy defaults then
/// permit a bare Cargo build, but do not establish agreement with an oracle.
///
/// A `$LYPNING_CPYTHON` that no longer runs is a STALE PIN, not an instruction
/// to guess, so it is the FIRST candidate and never the only one: dropping
/// straight to the fallback behind a dead pin is how a bare `cargo build` on a
/// 3.11 host compiles 3.13's wordings and `min([])` answers what no host says.
/// The pin, then `python3`, then the fallback — the guess is the last resort,
/// which is the order `engines.find_cpython()` has on the Python side once its
/// own refusal to honour a pin that is not there has been caught.
fn probe_python() -> Option<(String, [u8; 4])> {
    let pin = std::env::var("LYPNING_CPYTHON").ok().filter(|s| !s.trim().is_empty());
    let names: Vec<String> = pin
        .into_iter()
        .chain(["python3", "python"].iter().map(|s| s.to_string()))
        .collect();
    for name in names {
        let out = std::process::Command::new(&name)
            .args(["-c", include_str!("reference_probe.py")])
            .output();
        if let Ok(out) = out {
            if out.status.success() {
                let text = String::from_utf8_lossy(&out.stdout);
                let lines: Vec<&str> = text.trim().lines().collect();
                if lines.len() == 5 && parse_minor(lines[0]).is_some()
                    && lines[1..].iter().all(|v| *v == "0" || *v == "1") {
                    return Some((lines[0].to_string(), [
                        (lines[1] == "1") as u8, (lines[2] == "1") as u8,
                        (lines[3] == "1") as u8, (lines[4] == "1") as u8]));
                }
            }
        }
    }
    None
}
