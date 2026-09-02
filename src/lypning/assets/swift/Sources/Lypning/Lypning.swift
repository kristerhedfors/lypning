// Lypning.swift — the Swift binding over liblypning's C ABI.
//
// The one invariant this file holds: A REFUSAL IS A VALUE. Nothing here throws,
// traps or returns an optional for an outcome the runtime can report. When
// lypning declines a program it says so in `Result.fallOnward`, and the host
// runs the program on CPython; a binding that turned that into a Swift error
// would turn a speedup into a bug, silently, because the program was fine.
//
// Everything the C header says about ownership applies once, at this boundary:
// every handle is freed in a `defer`, and every byte or string the caller gets
// back was copied out of the handle before it died.

import CLypning

#if LYPNING_LIBRARY_MISSING
// Package.swift looked under ../rust/target/release-lib and $LYPNING_HOME/lib
// (~/.lypning/lib) and found no liblypning. Said here, once, instead of by the
// linker as a page of undefined symbols after everything else compiled.
//
// The message names the cache because SwiftPM keeps what a manifest decided,
// keyed on the manifest's text and the environment, not on the files it
// looked at: without the purge + reset this line would still be printed after
// the library was built, and a message that outlives its cause is a lie.
#error("no liblypning to link: run `lypning build --lib` (or point LYPNING_HOME at a tree with lib/liblypning.dylib or .so), then `swift package purge-cache && swift package reset`, because SwiftPM cached this manifest's answer")
#endif

/// What the run was, from the runtime's point of view. Mirrors the LYPNING_*
/// constants; `.unsupported` is NOT an error, it is the route onward.
public enum Status: Equatable {
    case ok
    case error
    case unsupported
    case busy
    case panic

    /// From the C constant. Anything the header does not name is reported as a
    /// panic, which is the honest reading of a status this binding was not
    /// compiled against — and `fallOnward` still decides the branch.
    init(raw: Int32) {
        switch raw {
        case Int32(LYPNING_OK): self = .ok
        case Int32(LYPNING_ERROR): self = .error
        case Int32(LYPNING_UNSUPPORTED): self = .unsupported
        case Int32(LYPNING_BUSY): self = .busy
        default: self = .panic
        }
    }
}

/// Where a program would go, decided by lypning's own front end after one
/// parse and no execution.
public struct Route: Equatable {
    /// "lypning", "lypning-mp" or "cpython".
    public let engine: String
    /// The construct that pushed it past lypning ("module", "async", …), or "".
    public let kind: String
    /// Its detail ("import re"), or "".
    public let detail: String
    /// Every module the program imports, sorted and deduplicated.
    public let imports: [String]
}

/// Everything a host can decide about a run before it starts.
public struct Options {
    /// sys.argv[1:].
    public var args: [String]
    /// sys.argv[0]. nil is CPython's `-c` shape, which is what a one-liner is.
    public var filename: String?
    /// The program's stdin. The library never reads this process's fd 0.
    public var stdin: [UInt8]
    /// false turns every file operation into a refusal, never into a lie.
    public var filesystem: Bool
    /// Refuse past this many statements and iterator advances; 0 is no limit.
    /// Set it for programs a language model wrote: in-process there is no
    /// process to kill, so an unbounded loop is a hang with no way back.
    public var stepLimit: UInt64
    /// Refuse once captured output passes this many bytes; 0 is no limit.
    public var outputLimit: Int

    public init(args: [String] = [], filename: String? = nil, stdin: [UInt8] = [],
                filesystem: Bool = true, stepLimit: UInt64 = 0, outputLimit: Int = 0) {
        self.args = args
        self.filename = filename
        self.stdin = stdin
        self.filesystem = filesystem
        self.stepLimit = stepLimit
        self.outputLimit = outputLimit
    }
}

/// What a run produced. Output is bytes, not String: a program's stdout is
/// whatever it printed, and it is not always UTF-8.
public struct Result {
    public let status: Status
    /// What the `lypning` binary would have exited with: the program's own
    /// code, 1 for an uncaught exception, 90 for a refusal.
    public let exitCode: Int32
    public let stdout: [UInt8]
    /// The traceback, or after a refusal exactly the one
    /// `lypning: unsupported: <kind>: <detail>` line the binary would print.
    public let stderr: [UInt8]
    /// The refusal's two halves; "" when the run was not a refusal.
    public let kind: String
    public let detail: String
    /// Did the run pass the point where its effects stop being reversible?
    public let committed: Bool
    /// THE FIELD TO BRANCH ON. True exactly when lypning did not answer and
    /// left nothing behind, so running the program on CPython is safe. Never
    /// true for `.ok` or `.error`: an uncaught exception is the program's own
    /// answer, and re-running it would repeat whatever it did before raising.
    public let fallOnward: Bool
}

/// The runtime, as a namespace. Nothing here throws.
public enum Lypning {
    /// The runtime version, e.g. "0.1.0".
    public static func version() -> String {
        return String(cString: lypning_version())
    }

    /// The ABI the loaded library implements.
    public static func abiVersion() -> UInt32 {
        return lypning_abi_version()
    }

    /// Checked once, the first time it is needed. A library speaking another
    /// ABI is not one this binding can read handles from, so every call
    /// answers "route onward" naming the mismatch instead of dereferencing
    /// them: the host's existing path still answers, and the message says why.
    static let abiMismatch: String? = {
        let have = lypning_abi_version()
        if have == LYPNING_ABI_VERSION { return nil }
        return "liblypning speaks ABI \(have), this binding was built for \(LYPNING_ABI_VERSION)"
    }()

    /// Which interpreter should run this? One parse, no execution.
    public static func route(_ src: String) -> Route {
        if let why = abiMismatch {
            return Route(engine: "cpython", kind: "abi", detail: why, imports: [])
        }
        // withCString hands over a non-nil pointer even for "", which the ABI
        // needs: (NULL, 0) is a bad argument, not an empty program.
        guard let r = src.withCString({ lypning_route_new($0, src.utf8.count) }) else {
            // Unreachable from a Swift String, which is always UTF-8, but the
            // header says NULL means "not UTF-8" and that is a route too.
            return Route(engine: "cpython", kind: "source", detail: "not UTF-8", imports: [])
        }
        defer { lypning_route_free(r) }
        var imports: [String] = []
        let n = lypning_route_import_count(r)
        imports.reserveCapacity(n)
        for i in 0..<n {
            if let p = lypning_route_import(r, i) { imports.append(String(cString: p)) }
        }
        return Route(engine: String(cString: lypning_route_engine(r)),
                     kind: String(cString: lypning_route_kind(r)),
                     detail: String(cString: lypning_route_detail(r)),
                     imports: imports)
    }

    /// Run the program in THIS thread, capturing its output. Spawns nothing.
    public static func run(_ src: String, _ opts: Options = Options()) -> Result {
        if let why = abiMismatch {
            return hostRefusal(kind: "abi", detail: why)
        }
        guard let q = src.withCString({ lypning_request_new($0, src.utf8.count) }) else {
            return hostRefusal(kind: "source", detail: "not UTF-8")
        }
        defer { lypning_request_free(q) }

        if let name = opts.filename {
            _ = name.withCString { lypning_request_set_filename(q, $0, name.utf8.count) }
        }
        for arg in opts.args {
            _ = arg.withCString { lypning_request_add_arg(q, $0, arg.utf8.count) }
        }
        if !opts.stdin.isEmpty {
            _ = opts.stdin.withUnsafeBufferPointer {
                lypning_request_set_stdin(q, $0.baseAddress, $0.count)
            }
        }
        lypning_request_set_filesystem(q, opts.filesystem ? 1 : 0)
        lypning_request_set_step_limit(q, opts.stepLimit)
        lypning_request_set_output_limit(q, opts.outputLimit)

        // NULL only for a NULL request, which q is not; kept as a value anyway.
        guard let r = lypning_run(q) else {
            return hostRefusal(kind: "run", detail: "lypning_run returned NULL")
        }
        defer { lypning_result_free(r) }

        return Result(status: Status(raw: lypning_result_status(r)),
                      exitCode: lypning_result_exit_code(r),
                      stdout: bytes(r, lypning_result_stdout),
                      stderr: bytes(r, lypning_result_stderr),
                      kind: String(cString: lypning_result_kind(r)),
                      detail: String(cString: lypning_result_detail(r)),
                      committed: lypning_result_committed(r) != 0,
                      fallOnward: lypning_result_should_fall_onward(r) != 0)
    }

    /// The dispatcher's own predicate, for a host that chains OTHER
    /// interpreters too: exit 90, a MemoryError, or a traceback at exit 0.
    public static func fallOnward(exitCode: Int32, stderr: [UInt8]) -> Bool {
        return stderr.withUnsafeBufferPointer {
            lypning_fall_onward(exitCode, $0.baseAddress, $0.count) != 0
        }
    }

    /// A refusal this binding raises on the runtime's behalf, in exactly the
    /// runtime's own shape: exit 90, empty stdout, the one line, route onward.
    /// Nothing ran, so nothing committed.
    private static func hostRefusal(kind: String, detail: String) -> Result {
        return Result(status: .unsupported,
                      exitCode: Int32(LYPNING_UNSUPPORTED_EXIT),
                      stdout: [],
                      stderr: Array("lypning: unsupported: \(kind): \(detail)\n".utf8),
                      kind: kind, detail: detail,
                      committed: false, fallOnward: true)
    }

    /// Copy a (pointer, length) pair out of the handle before it is freed.
    private static func bytes(
        _ r: OpaquePointer,
        _ get: (OpaquePointer?, UnsafeMutablePointer<Int>?) -> UnsafePointer<UInt8>?
    ) -> [UInt8] {
        var n = 0
        guard let p = get(r, &n), n > 0 else { return [] }
        return Array(UnsafeBufferPointer(start: p, count: n))
    }
}
