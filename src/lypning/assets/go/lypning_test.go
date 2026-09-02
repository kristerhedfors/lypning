// The refusal contract, pinned on the library this binary actually linked.
//
// The refusal path is the only part of lypning that has ever broken silently:
// a change that turns `unsupported` into a traceback still compiles, still
// links, still answers Version(). So the five properties a host branches on
// are asserted here as values, exactly as the C example asserts them, and the
// rest of the file pins what only a binding can get wrong: a copied buffer, a
// leaked handle, state carried from one run into the next.
package lypning_test

import (
	"bytes"
	"strings"
	"testing"

	"lypning.dev/lypning"
)

const refusalLine = "lypning: unsupported: module: import subprocess\n"

func TestVersionAndABI(t *testing.T) {
	if lypning.Version() == "" {
		t.Fatal("Version() is empty")
	}
	if got := lypning.ABIVersion(); got != 1 {
		t.Fatalf("ABIVersion() = %d, header says 1", got)
	}
}

// The contract: status, exit code, empty stdout, exactly one line on stderr,
// and a request to be routed onward. A binding that cannot demonstrate all
// five is not a binding.
func TestRefusalContract(t *testing.T) {
	r := lypning.Run("import subprocess; print(1)", nil)
	if r.Status != lypning.Unsupported {
		t.Fatalf("status %v, want unsupported", r.Status)
	}
	if r.ExitCode != 90 || lypning.UnsupportedExit != 90 {
		t.Fatalf("exit %d, want 90", r.ExitCode)
	}
	if len(r.Stdout) != 0 {
		t.Fatalf("stdout after a refusal: %q", r.Stdout)
	}
	if string(r.Stderr) != refusalLine {
		t.Fatalf("stderr %q, want %q", r.Stderr, refusalLine)
	}
	if !r.FallOnward {
		t.Fatal("FallOnward false on a refusal")
	}
	if r.Committed {
		t.Fatal("Committed true on a refusal")
	}
	if r.Kind != "module" || r.Detail != "import subprocess" {
		t.Fatalf("kind/detail %q/%q", r.Kind, r.Detail)
	}
}

func TestOK(t *testing.T) {
	r := lypning.Run("print(sum(range(10)))", nil)
	if r.Status != lypning.OK || r.ExitCode != 0 {
		t.Fatalf("status %v exit %d: %s", r.Status, r.ExitCode, r.Stderr)
	}
	if string(r.Stdout) != "45\n" {
		t.Fatalf("stdout %q", r.Stdout)
	}
	if r.FallOnward || !r.Committed {
		t.Fatalf("FallOnward %v Committed %v on an ok run", r.FallOnward, r.Committed)
	}
	if r.Kind != "" || r.Detail != "" {
		t.Fatalf("kind/detail set on an ok run: %q/%q", r.Kind, r.Detail)
	}
}

// A traceback is the program's own answer. Exit 1, never routed onward: a
// dispatcher that retried it would run a half-completed program twice.
func TestTracebackIsTheAnswer(t *testing.T) {
	r := lypning.Run("print('half'); print(1/0)", nil)
	if r.Status != lypning.Error || r.ExitCode != 1 {
		t.Fatalf("status %v exit %d", r.Status, r.ExitCode)
	}
	if string(r.Stdout) != "half\n" {
		t.Fatalf("stdout %q: the half that ran is part of the answer", r.Stdout)
	}
	if !bytes.Contains(r.Stderr, []byte("ZeroDivisionError")) {
		t.Fatalf("stderr %q", r.Stderr)
	}
	if r.FallOnward {
		t.Fatal("FallOnward true on a traceback")
	}
	if !r.Committed {
		t.Fatal("Committed false on a run that printed")
	}
}

func TestSysExit(t *testing.T) {
	r := lypning.Run("import sys; sys.exit(3)", nil)
	if r.Status != lypning.OK || r.ExitCode != 3 || r.FallOnward {
		t.Fatalf("status %v exit %d fall %v", r.Status, r.ExitCode, r.FallOnward)
	}
}

func TestArgv(t *testing.T) {
	r := lypning.Run("import sys; print(sys.argv[1:])", &lypning.Options{Args: []string{"a", "b c", ""}})
	if r.Status != lypning.OK {
		t.Fatalf("status %v: %s", r.Status, r.Stderr)
	}
	if string(r.Stdout) != "['a', 'b c', '']\n" {
		t.Fatalf("stdout %q", r.Stdout)
	}
}

func TestFilename(t *testing.T) {
	r := lypning.Run("import sys; print(sys.argv[0])", &lypning.Options{Filename: "prog.py"})
	if r.Status != lypning.OK || string(r.Stdout) != "prog.py\n" {
		t.Fatalf("status %v stdout %q stderr %q", r.Status, r.Stdout, r.Stderr)
	}
}

// Stdin goes in as bytes and stdout comes out as bytes: the program's output
// is whatever it printed, and a re-encoding on either side would be a wrong
// answer of exactly the kind lypning refuses rather than guesses at.
func TestStdinBytes(t *testing.T) {
	in := []byte("dröm åäö\nsecond\n")
	r := lypning.Run("import sys; sys.stdout.write(sys.stdin.read())", &lypning.Options{Stdin: in})
	if r.Status != lypning.OK {
		t.Fatalf("status %v: %s", r.Status, r.Stderr)
	}
	if !bytes.Equal(r.Stdout, in) {
		t.Fatalf("stdout %q, want the bytes back: %q", r.Stdout, in)
	}
	// Unset is an empty stream, never this process's fd 0.
	r = lypning.Run("import sys; print(repr(sys.stdin.read()))", nil)
	if r.Status != lypning.OK || string(r.Stdout) != "''\n" {
		t.Fatalf("status %v stdout %q", r.Status, r.Stdout)
	}
}

func TestStepLimitRefuses(t *testing.T) {
	r := lypning.Run("while True: pass", &lypning.Options{StepLimit: 1000})
	if r.Status != lypning.Unsupported || !r.FallOnward || r.Committed {
		t.Fatalf("status %v fall %v committed %v: %s", r.Status, r.FallOnward, r.Committed, r.Stderr)
	}
	if r.Kind != "steps" || !strings.HasPrefix(string(r.Stderr), "lypning: unsupported: steps: ") {
		t.Fatalf("kind %q stderr %q", r.Kind, r.Stderr)
	}
}

func TestFilesystemDeniedRefuses(t *testing.T) {
	r := lypning.Run("open('lypning-go-test-litter.txt', 'w').write('x')", &lypning.Options{DenyFilesystem: true})
	if r.Status != lypning.Unsupported || !r.FallOnward {
		t.Fatalf("status %v fall %v: %s", r.Status, r.FallOnward, r.Stderr)
	}
	if r.Kind != "sandbox" {
		t.Fatalf("kind %q, want sandbox: %s", r.Kind, r.Stderr)
	}
	if len(r.Stdout) != 0 {
		t.Fatalf("stdout %q", r.Stdout)
	}
}

func TestOutputLimitRefuses(t *testing.T) {
	r := lypning.Run("print('a' * 1000)", &lypning.Options{OutputLimit: 100})
	if r.Status != lypning.Unsupported || !r.FallOnward {
		t.Fatalf("status %v fall %v: %s", r.Status, r.FallOnward, r.Stderr)
	}
	if len(r.Stdout) != 0 {
		t.Fatalf("stdout %q after a refusal", r.Stdout)
	}
	if r.Kind != "output" {
		t.Fatalf("kind %q, want output: %s", r.Kind, r.Stderr)
	}
	// The same program under a limit it fits in runs.
	r = lypning.Run("print('a' * 1000)", &lypning.Options{OutputLimit: 2000})
	if r.Status != lypning.OK || len(r.Stdout) != 1001 {
		t.Fatalf("status %v len %d", r.Status, len(r.Stdout))
	}
}

// What only a library can get wrong: a name from one run visible in the next,
// or a commit flag latched by a run that finished turning the next run's
// refusal into an error.
func TestTwoRunsDoNotLeak(t *testing.T) {
	first := lypning.Run("leaked = 42; print(leaked)", nil)
	if first.Status != lypning.OK || !first.Committed {
		t.Fatalf("first: %v %s", first.Status, first.Stderr)
	}
	second := lypning.Run("print(leaked)", nil)
	if second.Status != lypning.Error || !bytes.Contains(second.Stderr, []byte("NameError")) {
		t.Fatalf("a name leaked between runs: %v %q", second.Status, second.Stderr)
	}
	third := lypning.Run("import subprocess", nil)
	if third.Status != lypning.Unsupported || third.Committed || !third.FallOnward {
		t.Fatalf("commit latched: %v committed %v fall %v", third.Status, third.Committed, third.FallOnward)
	}
	if string(third.Stderr) != refusalLine {
		t.Fatalf("stderr %q", third.Stderr)
	}
	fourth := lypning.Run("print('still fine')", nil)
	if fourth.Status != lypning.OK || string(fourth.Stdout) != "still fine\n" {
		t.Fatalf("fourth: %v %q", fourth.Status, fourth.Stdout)
	}
}

// The result outlives the handles Run freed on its way out. Enough runs, with
// enough output, that a use-after-free or a leak of the copied buffers would
// show under -race or a memory checker, and the bytes are checked to be
// exactly the program's each time.
func TestResultsAreCopies(t *testing.T) {
	var results []lypning.Result
	for i := 0; i < 200; i++ {
		results = append(results, lypning.Run("print('x' * 4096)", nil))
	}
	want := strings.Repeat("x", 4096) + "\n"
	for i, r := range results {
		if r.Status != lypning.OK || string(r.Stdout) != want {
			t.Fatalf("run %d: %v %d bytes", i, r.Status, len(r.Stdout))
		}
	}
}

func TestRouteAgreesWithRun(t *testing.T) {
	cases := []string{
		"print(sum(range(10)))",
		"import subprocess; print(1)",
		"import sys, json; print(json.dumps(sys.argv))",
		"async def f(): pass",
	}
	for _, src := range cases {
		route := lypning.Route(src)
		run := lypning.Run(src, nil)
		if (route.Engine == "lypning") != (run.Status != lypning.Unsupported) {
			t.Fatalf("%q: route says %q, run says %v (%s)", src, route.Engine, run.Status, run.Stderr)
		}
		if run.Status == lypning.Unsupported && (route.Kind != run.Kind || route.Detail != run.Detail) {
			t.Fatalf("%q: route %q/%q, run %q/%q", src, route.Kind, route.Detail, run.Kind, run.Detail)
		}
	}
	r := lypning.Route("import sys\nimport os, sys\nimport subprocess")
	if strings.Join(r.Imports, ",") != "os,subprocess,sys" {
		t.Fatalf("imports %v, want sorted and deduplicated", r.Imports)
	}
	if r.Engine != "cpython" || r.Kind != "module" {
		t.Fatalf("engine %q kind %q", r.Engine, r.Kind)
	}
}

// A Go string is not always UTF-8. The ABI declines to build a request for one,
// and that is a route, not an error: the bytes still need an answer and CPython
// has its own message for them.
func TestNonUTF8SourceFallsOnward(t *testing.T) {
	r := lypning.Run("print(1) \xff\xfe", nil)
	if !r.FallOnward || r.Status != lypning.Unsupported || r.ExitCode != 90 {
		t.Fatalf("fall %v status %v exit %d", r.FallOnward, r.Status, r.ExitCode)
	}
	if len(r.Stdout) != 0 || r.Kind != "source" {
		t.Fatalf("stdout %q kind %q", r.Stdout, r.Kind)
	}
	if !strings.HasPrefix(string(r.Stderr), "lypning: unsupported: source: ") {
		t.Fatalf("stderr %q", r.Stderr)
	}
	if rt := lypning.Route("\xff"); rt.Engine != "cpython" {
		t.Fatalf("route %q", rt.Engine)
	}
}

// An argument is bytes too. The ABI declines one that is not UTF-8, and the
// binding must route the whole program onward rather than run it with one
// argument fewer than CPython would see.
func TestNonUTF8ArgFallsOnward(t *testing.T) {
	r := lypning.Run("import sys; print(len(sys.argv[1:]))", &lypning.Options{Args: []string{"a\xff", "b"}})
	if !r.FallOnward || r.Status != lypning.Unsupported || r.ExitCode != 90 || len(r.Stdout) != 0 {
		t.Fatalf("fall %v status %v exit %d stdout %q", r.FallOnward, r.Status, r.ExitCode, r.Stdout)
	}
	if r.Kind != "source" || !strings.HasPrefix(string(r.Stderr), "lypning: unsupported: source: ") {
		t.Fatalf("kind %q stderr %q", r.Kind, r.Stderr)
	}
	r = lypning.Run("print(1)", &lypning.Options{Filename: "p\xff.py"})
	if !r.FallOnward || r.Kind != "source" || len(r.Stdout) != 0 {
		t.Fatalf("fall %v kind %q stdout %q", r.FallOnward, r.Kind, r.Stdout)
	}
}

func TestNULInSourceRefuses(t *testing.T) {
	r := lypning.Run("print(1)\x00print(2)", nil)
	if r.Status != lypning.Unsupported || r.Kind != "source" || !r.FallOnward {
		t.Fatalf("status %v kind %q fall %v", r.Status, r.Kind, r.FallOnward)
	}
}

func TestFallOnwardPredicate(t *testing.T) {
	if !lypning.FallOnward(90, []byte(refusalLine)) {
		t.Fatal("exit 90 must fall onward")
	}
	traceback := []byte("Traceback (most recent call last):\n  File \"<string>\", line 1, in <module>\nZeroDivisionError: division by zero\n")
	if lypning.FallOnward(1, traceback) {
		t.Fatal("a traceback with exit 1 is the program's answer")
	}
	if !lypning.FallOnward(0, traceback) {
		t.Fatal("a traceback with exit 0 is not an answer")
	}
	if !lypning.FallOnward(1, []byte("MemoryError\n")) {
		t.Fatal("a MemoryError is the engine's heap, never the program's answer")
	}
	if lypning.FallOnward(0, nil) {
		t.Fatal("exit 0 with nothing on stderr is an answer")
	}
}

func TestStatusString(t *testing.T) {
	for s, want := range map[lypning.Status]string{
		lypning.OK: "ok", lypning.Error: "error", lypning.Unsupported: "unsupported",
		lypning.Busy: "busy", lypning.Panic: "panic", lypning.Status(9): "status(9)",
	} {
		if s.String() != want {
			t.Fatalf("%d.String() = %q, want %q", int32(s), s.String(), want)
		}
	}
}

// Two goroutines are two threads for the duration of a cgo call, so they may
// run two programs at once and neither sees Busy.
func TestConcurrentRuns(t *testing.T) {
	const n = 8
	done := make(chan lypning.Result, n)
	for i := 0; i < n; i++ {
		go func() { done <- lypning.Run("print(sum(range(100000)))", nil) }()
	}
	for i := 0; i < n; i++ {
		r := <-done
		if r.Status != lypning.OK || string(r.Stdout) != "4999950000\n" {
			t.Fatalf("status %v stdout %q stderr %q", r.Status, r.Stdout, r.Stderr)
		}
	}
}
