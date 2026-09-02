# lypning for Swift

Runs the bottom slice of agent-typed Python, the one-liners, *inside* this
process: no child process, no pipe, no serialisation. Everything else it
REFUSES, and the refusal is the design, not a failure.

```swift
import Lypning
let r = Lypning.run(src, Options(stepLimit: 10_000_000))
if r.fallOnward { runOnPython3(src) }  // lypning ran NOTHING; your existing path
else { use(r.stdout, r.exitCode) }     // [UInt8]: output is not always UTF-8
```

`fallOnward` is true exactly when lypning declined *and left nothing behind*:
nothing printed, no file touched, no stdin consumed, which is what makes the
retry safe. Nothing throws. A refusal is a value, `r.kind` / `r.detail` say
what was declined, and a traceback is the program's own answer at exit 1, never
a reason to run it again. `Lypning.route(src)` asks without running.

## Build

```sh
swift build -c release && .build/release/quickstart "print(sum(range(10)))"
```

`Package.swift` links the checkout's `../rust/target/release-lib` when it
exists and `$LYPNING_HOME/lib` (`~/.lypning/lib`) otherwise, which is where
`lypning build --lib` puts the library. If neither has it, the first compile
stops on one line naming `lypning build --lib`, before the linker can print a
page of undefined symbols. Build the library first and that is all there is to
it; build it second and run `swift package purge-cache && swift package reset`
once, because SwiftPM caches what `Package.swift` decided, keyed on its text
and your environment rather than on the files it looked at. The paths go in as
`unsafeFlags`, so this package is consumed in-tree or by `.package(path:)`,
never from a registry; that is the honest way to link a library whose location
is decided by a tool SwiftPM knows nothing about.

`swift build` writes `.build/` beside `Package.swift`. In a read-only wheel
tree pass `--scratch-path "$TMPDIR/lypning-swift"`. Without SwiftPM at all,
`make` does the same in two `swiftc` lines and prints which library it chose.

## Layout

* `Sources/CLypning/` imports the C ABI. `shim.h` is one `#include` of
  `../../../include/lypning.h`, so the header stays single-sourced.
* `Sources/Lypning/Lypning.swift` is the binding: `Status`, `Route`, `Options`,
  `Result`, and the `Lypning` namespace.
* `Sources/quickstart/main.swift` is the complete minimal host, and the exact
  program every other language's quickstart is.
* `Tests/LypningTests/` pins the refusal contract as values (`swift test`).
