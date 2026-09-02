// quickstart: lypning in this process, CPython for whatever it refuses.
// Usage: quickstart "<python source>" [args...]   (args become sys.argv[1:])
// Build+run, from the repository root:
//   swift build -c release --package-path src/lypning/assets/swift && src/lypning/assets/swift/.build/release/quickstart "print(sum(range(10)))"
// No SwiftPM: make -C src/lypning/assets/swift quickstart && src/lypning/assets/swift/quickstart "print(sum(range(10)))"
import Foundation
import Lypning

let argv = CommandLine.arguments
if argv.count < 2 {
    FileHandle.standardError.write(Data("usage: quickstart \"<python source>\" [args...]\n".utf8))
    exit(2)
}
let src = argv[1]
let args = Array(argv.dropFirst(2))
let r = Lypning.run(src, Options(args: args, stepLimit: 10_000_000))  // in-process there is no process to kill; past this it is a refusal

if r.fallOnward {
    // A refusal is not an error: lypning ran none of it and wrote nothing, so CPython runs it once.
    fflush(stdout)
    let null = open("/dev/null", O_RDONLY)
    if null >= 0 { dup2(null, 0) }
    var cargv: [UnsafeMutablePointer<CChar>?] = (["python3", "-c", src] + args).map { strdup($0) }
    cargv.append(nil)
    execvp("python3", cargv)
    perror("quickstart: execvp python3")
    exit(127)
}
r.stdout.withUnsafeBufferPointer { _ = fwrite($0.baseAddress, 1, $0.count, stdout) }
r.stderr.withUnsafeBufferPointer { _ = fwrite($0.baseAddress, 1, $0.count, stderr) }
exit(r.exitCode)
