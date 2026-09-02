// build.rs — let a plain `cargo build --release` link on macOS.
//
// The Node-API symbols this addon calls are declared in src/napi.rs and
// defined in the `node` executable, which resolves them at `process.dlopen`;
// nothing links them, by design (see the header of that file). Linux's ld
// leaves an undefined symbol in a shared object alone. macOS's ld64 does not:
// it refuses the link outright ("symbol(s) not found for architecture arm64")
// unless told that resolving them from the loading process is the plan. It is,
// so say so, and only there: on Linux the flag would be an error of its own.
//
// `rustc-cdylib-link-arg` rather than `rustc-link-arg` because it must reach
// exactly one link: the cdylib. A `cargo test` binary of this crate would
// otherwise carry a flag that hides every genuinely missing symbol.
fn main() {
    println!("cargo:rerun-if-changed=build.rs");
    if std::env::var("CARGO_CFG_TARGET_OS").as_deref() == Ok("macos") {
        println!("cargo:rustc-cdylib-link-arg=-undefined");
        println!("cargo:rustc-cdylib-link-arg=dynamic_lookup");
    }
}
