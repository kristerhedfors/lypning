// The Swift host: every program in a hostset directory, through the Swift
// binding over the C ABI, in this process.
//
// Same walk as the C, C++, Rust, Node and Python drivers, and no fall-onward
// for the same reason: this counts what the subset itself takes, and a driver
// that quietly answered from python3 would report a coverage the subset does
// not have.
//
// It logs each run to $LYPNING_LOG in the shim's own record shape, because an
// in-process call spawns no interpreter and is invisible to both of lypning's
// capture feeds. See study/hosts/capture.h for why that is the host's job.

import Foundation
import Lypning

let log = ProcessInfo.processInfo.environment["LYPNING_LOG"] ?? ""
let session = ProcessInfo.processInfo.environment["LYPNING_STUDY_SESSION"] ?? ""

func jsonString(_ s: String) -> String {
    var out = "\""
    for u in s.unicodeScalars {
        switch u {
        case "\"": out += "\\\""
        case "\\": out += "\\\\"
        case "\n": out += "\\n"
        case "\r": out += "\\r"
        case "\t": out += "\\t"
        default:
            if u.value < 0x20 || u.value == 0x7f {
                out += String(format: "\\u%04x", u.value)
            } else {
                out.unicodeScalars.append(u)
            }
        }
    }
    return out + "\""
}

/// One `python_invocation` record, keys in the shim's order. Best-effort on
/// every path, exactly like the shim: a log that cannot be opened is a lost
/// sighting and never a failed run.
func capture(host: String, program: String, args: [String], exitCode: Int32, wallMs: Int) {
    if log.isEmpty { return }
    let fmt = DateFormatter()
    fmt.dateFormat = "yyyy-MM-dd'T'HH:mm:ss'Z'"
    fmt.timeZone = TimeZone(identifier: "UTC")
    fmt.locale = Locale(identifier: "en_US_POSIX")
    let rec = "{\"kind\":\"python_invocation\""
        + ",\"ts\":" + jsonString(fmt.string(from: Date()))
        + ",\"session\":" + (session.isEmpty ? "null" : jsonString(session))
        + ",\"shim\":" + jsonString(host)
        + ",\"pid\":\(getpid())"
        + ",\"program\":" + jsonString(program)
        + ",\"module\":null,\"script\":null"
        + ",\"argv_tail\":[" + args.map(jsonString).joined(separator: ",") + "]"
        + ",\"stdin_pipe\":true,\"stdin_kind\":\"bytes\""
        + ",\"exit_code\":\(exitCode),\"wall_ms\":\(wallMs)}\n"
    guard let fh = FileHandle(forWritingAtPath: log) ?? {
        FileManager.default.createFile(atPath: log, contents: nil)
        return FileHandle(forWritingAtPath: log)
    }() else { return }
    defer { fh.closeFile() }
    fh.seekToEndOfFile()
    fh.write(Data(rec.utf8))
}

func main() -> Int32 {
    let argv = CommandLine.arguments
    if argv.count < 2 {
        FileHandle.standardError.write(Data("usage: run_swift <hostset-dir>\n".utf8))
        return 2
    }
    let root = argv[1]
    let fm = FileManager.default
    var ran = 0, refused = 0, other = 0, n = 0
    let entries = ((try? fm.contentsOfDirectory(atPath: root)) ?? []).sorted()
    for name in entries {
        let d = root + "/" + name
        var isDir: ObjCBool = false
        guard fm.fileExists(atPath: d, isDirectory: &isDir), isDir.boolValue else { continue }
        guard let program = try? String(contentsOfFile: d + "/program.py", encoding: .utf8) else { continue }
        n += 1
        let stdin = [UInt8](fm.contents(atPath: d + "/stdin") ?? Data())
        let args = ((try? String(contentsOfFile: d + "/args", encoding: .utf8)) ?? "")
            .split(separator: "\n", omittingEmptySubsequences: true).map(String.init)
        // The program runs in THIS process; give it the entry directory, where
        // prepare.py put the fixtures it was written against.
        let home = fm.currentDirectoryPath
        fm.changeCurrentDirectoryPath(d)
        let t0 = DispatchTime.now()
        let out = Lypning.run(program, Options(args: args, stdin: stdin,
                                               stepLimit: 200_000_000, outputLimit: 1 << 20))
        fm.changeCurrentDirectoryPath(home)
        let ms = Int((DispatchTime.now().uptimeNanoseconds - t0.uptimeNanoseconds) / 1_000_000)
        switch out.status {
        case .ok: ran += 1
        case .unsupported: refused += 1
        default: other += 1
        }
        capture(host: "swift-embed", program: program, args: args, exitCode: out.exitCode, wallMs: ms)
    }
    print("swift-embed \(n) programs: \(ran) ran, \(refused) refused, \(other) other")
    return 0
}

exit(main())
