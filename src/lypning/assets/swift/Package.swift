// swift-tools-version:5.9
// Package.swift — the Swift binding, its quickstart, and its tests.
//
// THERE ARE TWO SHAPES OF THIS REPOSITORY AND BOTH MUST BUILD (see the C and
// C++ Makefiles beside this tree). A source checkout has a freshly built
// library under ../rust/target/release-lib; a wheel has none, and the library
// only exists where `lypning build --lib` put it, under $LYPNING_HOME/lib
// (~/.lypning/lib). The choice is made here, once, and the checkout wins when
// it exists: a checkout that linked the installed copy is a checkout whose
// tests cannot fail on the change you just made.
//
// The choice is passed as -L and -rpath through `unsafeFlags`, and that word
// means what it says: SwiftPM refuses to build a package that uses unsafe flags
// as a REMOTE dependency. So this package is consumed in-tree or by path
// (`.package(path: ...)`), the way the rest of this asset tree is, and never
// from a registry. There is no other honest way to link a library whose
// location is decided at build time by a machine SwiftPM knows nothing about.
//
// `swift build` writes .build/ beside this file. In a read-only wheel tree
// give it somewhere else: `swift build --scratch-path "$TMPDIR/lypning-swift"`.
//
// ONE CAVEAT, measured rather than assumed: SwiftPM caches what this manifest
// decided, keyed on the manifest's text and the environment, not on the files
// it looked at. So the choice below is frozen for a given tree and shell until
// `swift package purge-cache && swift package reset`. Build the library first
// and there is nothing to notice; build it second and that pair is the fix.
// Passing both directories unconditionally would sidestep the cache, but the
// linker warns about a search path that does not exist, and one of the two
// never exists in either shape of the tree.

import Foundation
import PackageDescription

let checkout = Context.packageDirectory + "/../rust/target/release-lib"
let home = Context.environment["LYPNING_HOME"]
    ?? (Context.environment["HOME"] ?? "") + "/.lypning"
let installed = home + "/lib"

func hasLibrary(_ dir: String) -> Bool {
    let fm = FileManager.default
    return fm.fileExists(atPath: dir + "/liblypning.dylib")
        || fm.fileExists(atPath: dir + "/liblypning.so")
}

let libdir = hasLibrary(checkout) ? checkout : installed
let link: [LinkerSetting] = [
    .unsafeFlags(["-L", libdir, "-Xlinker", "-rpath", "-Xlinker", libdir]),
]

// When neither has it, the honest answer is one line naming the command that
// fixes it, not the page of undefined symbols the linker would print. A
// manifest cannot print, only choose; so it defines a flag and Lypning.swift
// says the line, at the first compile, before the linker is reached.
let missing: [SwiftSetting] = hasLibrary(libdir) ? [] : [.define("LYPNING_LIBRARY_MISSING")]

let package = Package(
    name: "Lypning",
    products: [
        .library(name: "Lypning", targets: ["Lypning"]),
        .executable(name: "quickstart", targets: ["quickstart"]),
    ],
    targets: [
        // The C ABI, by reference: shim.h includes the one header the whole
        // project shares, so nothing here can drift from it.
        .systemLibrary(name: "CLypning", path: "Sources/CLypning"),
        .target(name: "Lypning", dependencies: ["CLypning"],
                swiftSettings: missing, linkerSettings: link),
        .executableTarget(name: "quickstart", dependencies: ["Lypning"]),
        .testTarget(name: "LypningTests", dependencies: ["Lypning"]),
    ]
)
