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
    if std::env::var("CARGO_CFG_TARGET_OS").as_deref() == Ok("macos") {
        println!("cargo:rustc-cdylib-link-arg=-Wl,-install_name,@rpath/liblypning.dylib");
    }
}
