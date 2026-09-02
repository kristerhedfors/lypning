// LypningTests.swift — what only a binding can get wrong.
//
// The runtime is tested elsewhere; this file pins the BOUNDARY. First the
// refusal contract, asserted as values because a library has no exit code and
// no stderr of its own: status, exit 90, an EMPTY stdout, exactly the one line,
// nothing committed, and a request to be routed onward. Then the things a
// binding can break on its own — a pointer read after its handle died, a byte
// copied through a String, a limit passed as the wrong integer, output from
// one run leaking into the next.

import XCTest
import Lypning

let refusalLine = Array("lypning: unsupported: module: import subprocess\n".utf8)

/// Run with the working directory moved to a fresh temporary directory, so a
/// program that writes a file writes it somewhere disposable (CLAUDE.md
/// invariant 4 applies to a library call exactly as it applies to a spawn).
func inScratch(_ body: (String) -> Void) {
    let fm = FileManager.default
    let dir = NSTemporaryDirectory() + "lypning-swift-\(getpid())-\(UInt32.random(in: 0...UInt32.max))"
    try? fm.createDirectory(atPath: dir, withIntermediateDirectories: true)
    let home = fm.currentDirectoryPath
    fm.changeCurrentDirectoryPath(dir)
    defer {
        fm.changeCurrentDirectoryPath(home)
        try? fm.removeItem(atPath: dir)
    }
    body(dir)
}

final class RefusalContractTests: XCTestCase {
    func testRefusalIsNotAnError() {
        // The print after the import is the point: a refusal is decided before
        // anything runs, so it must leave stdout empty even when the program
        // would have printed.
        let r = Lypning.run("import subprocess; print(1)")
        XCTAssertEqual(r.status, .unsupported)
        XCTAssertEqual(r.exitCode, 90)
        XCTAssertEqual(r.stdout, [], "a refused run produced stdout")
        XCTAssertEqual(r.stderr, refusalLine)
        XCTAssertEqual(r.kind, "module")
        XCTAssertEqual(r.detail, "import subprocess")
        XCTAssertFalse(r.committed, "a refused run reported that it committed")
        XCTAssertTrue(r.fallOnward, "a refused run did not ask to be routed onward")
    }

    func testARefusalWritesNoFile() {
        inScratch { dir in
            let r = Lypning.run("open('written.txt', 'w').write('x')\nimport subprocess")
            XCTAssertTrue(r.fallOnward)
            XCTAssertFalse(FileManager.default.fileExists(atPath: dir + "/written.txt"))
        }
    }

    func testANULInTheSourceIsARefusalNotHalfAProgram() {
        // A lexer that read the zero as end-of-input would run half a program
        // and report success. The runtime refuses; for it to see the byte at
        // all, the binding must have handed over the whole buffer with its
        // length, not a C string that stops at the first NUL.
        let r = Lypning.run("print(1)\u{0}print(2)")
        XCTAssertEqual(r.status, .unsupported)
        XCTAssertEqual(r.kind, "source")
        XCTAssertEqual(r.stdout, [])
        XCTAssertTrue(r.fallOnward)
    }

    func testOkRun() {
        let r = Lypning.run("print(sum(range(10)))")
        XCTAssertEqual(r.status, .ok)
        XCTAssertEqual(r.exitCode, 0)
        XCTAssertEqual(r.stdout, Array("45\n".utf8))
        XCTAssertEqual(r.stderr, [])
        XCTAssertEqual(r.kind, "")
        XCTAssertTrue(r.committed)
        XCTAssertFalse(r.fallOnward)
    }

    func testATracebackIsTheProgramsOwnAnswerAndIsNotRetried() {
        let r = Lypning.run("print(1/0)")
        XCTAssertEqual(r.status, .error)
        XCTAssertEqual(r.exitCode, 1)
        XCTAssertTrue(String(decoding: r.stderr, as: UTF8.self).contains("ZeroDivisionError"))
        XCTAssertFalse(r.fallOnward, "re-running it would repeat what it did before raising")
    }

    func testSysExitIsTheProgramsOwnCode() {
        let r = Lypning.run("import sys; sys.exit(3)")
        XCTAssertEqual(r.status, .ok)
        XCTAssertEqual(r.exitCode, 3)
        XCTAssertFalse(r.fallOnward)
    }
}

final class RequestTests: XCTestCase {
    func testArgvTakesCPythonsDashCShape() {
        let r = Lypning.run("import sys; print(sys.argv)", Options(args: ["a", "b"]))
        XCTAssertEqual(r.stdout, Array("['-c', 'a', 'b']\n".utf8))
    }

    func testArgvTakesTheFileShapeWhenNamed() {
        let r = Lypning.run("import sys; print(sys.argv)", Options(args: ["x"], filename: "prog.py"))
        XCTAssertEqual(r.stdout, Array("['prog.py', 'x']\n".utf8))
    }

    func testStdinIsTheHostsBytes() {
        let r = Lypning.run("import sys; print(sys.stdin.read().upper())",
                            Options(stdin: Array("hi\n".utf8)))
        XCTAssertEqual(r.stdout, Array("HI\n\n".utf8))
    }

    func testStdinDefaultsToEmptyNeverTheProcesssFdZero() {
        let r = Lypning.run("import sys; print(repr(sys.stdin.read()))")
        XCTAssertEqual(r.stdout, Array("''\n".utf8))
    }

    func testOutputIsBytesNotACString() {
        // The runtime's output is always UTF-8, so what a binding can get wrong
        // is not the encoding but the length: a copy through String(cString:)
        // stops at the first NUL and reports "a" for a program that printed
        // more. The bytes must arrive with their length, NUL included.
        let r = Lypning.run("print('a\\x00b')")
        XCTAssertEqual(r.status, .ok)
        XCTAssertEqual(r.stdout, [0x61, 0x00, 0x62, 0x0a])
    }

    func testAStepLimitBoundsAProgramThatWillNotStop() {
        let r = Lypning.run("while True: pass", Options(stepLimit: 10_000))
        XCTAssertEqual(r.status, .unsupported)
        XCTAssertEqual(r.kind, "steps")
        XCTAssertTrue(r.fallOnward)
    }

    func testAStepLimitDoesNotDisturbAnOrdinaryProgram() {
        let r = Lypning.run("print(sum(range(100)))", Options(stepLimit: 10_000))
        XCTAssertEqual(r.stdout, Array("4950\n".utf8))
    }

    func testDenyingTheFilesystemRefusesRatherThanLying() {
        inScratch { _ in
            let r = Lypning.run("print(open('missing.txt').read())", Options(filesystem: false))
            XCTAssertEqual(r.status, .unsupported)
            XCTAssertTrue(r.fallOnward)
            XCTAssertEqual(r.stdout, [])
        }
    }

    func testDenyingTheFilesystemStillRunsOrdinaryPrograms() {
        let r = Lypning.run("print(2 + 2)", Options(filesystem: false))
        XCTAssertEqual(r.stdout, Array("4\n".utf8))
    }

    func testAnOutputLimitRefusesRatherThanFillingTheHost() {
        let r = Lypning.run("for i in range(100000): print('x' * 100)", Options(outputLimit: 4096))
        XCTAssertEqual(r.status, .unsupported)
        XCTAssertEqual(r.stdout, [], "a refused run produced stdout")
        XCTAssertTrue(r.fallOnward)
    }
}

final class IsolationTests: XCTestCase {
    func testOutputDoesNotLeakIntoTheNextRun() {
        _ = Lypning.run("print('first')")
        let r = Lypning.run("print('second')")
        XCTAssertEqual(r.stdout, Array("second\n".utf8))
    }

    func testStdinDoesNotLeakIntoTheNextRun() {
        _ = Lypning.run("import sys; sys.stdin.read()", Options(stdin: Array("leak".utf8)))
        let r = Lypning.run("import sys; print(repr(sys.stdin.read()))")
        XCTAssertEqual(r.stdout, Array("''\n".utf8))
    }

    func testACommittedRunDoesNotPoisonTheNextRefusal() {
        _ = Lypning.run("print('x' * 100000)")
        let r = Lypning.run("import subprocess")
        XCTAssertEqual(r.exitCode, 90)
        XCTAssertFalse(r.committed)
        XCTAssertTrue(r.fallOnward)
    }
}

final class RouteTests: XCTestCase {
    func testRouteAnswersWithoutRunningAnything() {
        inScratch { dir in
            let r = Lypning.route("import re\nopen('written.txt', 'w').write('x')")
            XCTAssertNotEqual(r.engine, "lypning")
            XCTAssertEqual(r.kind, "module")
            XCTAssertTrue(r.detail.contains("re"))
            XCTAssertEqual(r.imports, ["re"])
            XCTAssertFalse(FileManager.default.fileExists(atPath: dir + "/written.txt"))
        }
    }

    func testRouteAgreesWithRun() {
        for src in ["print(1)", "import re", "async def f(): pass", "import subprocess", "import sys, os; print(os.getcwd())"] {
            let routed = Lypning.route(src).engine == "lypning"
            let ran = !Lypning.run(src).fallOnward
            XCTAssertEqual(routed, ran, src)
        }
    }

    func testImportsAreSortedAndDeduplicated() {
        XCTAssertEqual(Lypning.route("import sys, os, sys").imports, ["os", "sys"])
    }

    func testTheDispatchersPredicate() {
        XCTAssertTrue(Lypning.fallOnward(exitCode: 90, stderr: Array("lypning: unsupported: module: import re".utf8)))
        XCTAssertFalse(Lypning.fallOnward(exitCode: 0, stderr: []))
        XCTAssertFalse(Lypning.fallOnward(exitCode: 1, stderr: Array("Traceback (most recent call last):\nValueError".utf8)))
        XCTAssertTrue(Lypning.fallOnward(exitCode: 1, stderr: Array("MemoryError".utf8)))
        XCTAssertTrue(Lypning.fallOnward(exitCode: 0, stderr: Array("Traceback (most recent call last):".utf8)))
    }

    func testVersions() {
        XCTAssertEqual(Lypning.abiVersion(), 1)
        XCTAssertFalse(Lypning.version().isEmpty)
    }
}
