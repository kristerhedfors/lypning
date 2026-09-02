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
    if std::env::var("CARGO_CFG_TARGET_OS").as_deref() == Ok("macos") {
        println!("cargo:rustc-cdylib-link-arg=-Wl,-install_name,@rpath/liblypning.dylib");
    }
}
